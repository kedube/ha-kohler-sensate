"""The Kohler Sensate Faucet integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import SensateApi, SensateError
from .const import (
    DOMAIN,
    ISSUE_API_CHANGED,
    ISSUE_FAUCET_NOT_FOUND,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import SensateCoordinator
from .push import FEEDS, SensatePush, new_identity
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

# How long removing an entry waits for Kohler to drop its registration.
UNREGISTER_TIMEOUT = 20
# The "Instant updates" option, removed in 0.10: they're always on now.
_OLD_PUSH_OPTION = "push_updates"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SensateConfigEntry = ConfigEntry[SensateCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SensateConfigEntry) -> bool:
    """Set up Kohler Sensate from a config entry."""
    api = SensateApi(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = SensateCoordinator(hass, entry, api)
    # Signs in on the first request: a rejected password starts reauth, any
    # other failure retries setup later.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Always, like the Konnect app; polling carries on beside it.
    await _async_join_feed(hass, entry, coordinator)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: SensateConfigEntry) -> bool:
    """Bring entries from older versions up to date."""
    if entry.version == 1 and entry.minor_version < 2:
        # 1.2: the "Instant updates" option is gone.
        options = {k: v for k, v in entry.options.items() if k != _OLD_PUSH_OPTION}
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    return True


async def _async_join_feed(
    hass: HomeAssistant, entry: SensateConfigEntry, coordinator: SensateCoordinator
) -> None:
    """Report the faucet from its account's feed, starting the feed if needed.

    Kohler's feed covers the whole account, so the faucets on one account
    share one registration and one connection. It stops with the last one.
    """
    api = coordinator.api
    feeds = hass.data.setdefault(FEEDS, {})
    account = api.tenant_id or entry.data[CONF_USERNAME].casefold()
    # No await between finding the feed and joining it: faucets on one
    # account are set up side by side.
    if (feed := feeds.get(account)) is None:
        feed = feeds[account] = SensatePush(
            hass, api, coordinator.push_identity or new_identity()
        )
        feed.start()
    feed.add_listener(coordinator.device_id, coordinator.async_push_activity, api)
    coordinator.push = feed

    async def _async_leave() -> None:
        if feed.remove_listener(coordinator.device_id):
            if feeds.get(account) is feed:
                del feeds[account]
            await feed.async_stop()

    entry.async_on_unload(_async_leave)
    await coordinator.async_use_push_identity(feed.identity)


async def async_unload_entry(hass: HomeAssistant, entry: SensateConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, STORAGE_VERSION, STORAGE_KEY.format(entry_id=entry_id))


async def async_remove_entry(hass: HomeAssistant, entry: SensateConfigEntry) -> None:
    """Delete what was stored for a removed entry, and its repair issues.

    The instant-updates registration is removed from the Kohler account too,
    as the Konnect app does on sign-out, so it doesn't linger there, unless
    another faucet on the account still shares it.
    """
    store = _store(hass, entry.entry_id)
    stored = await store.async_load() or {}
    identity = stored.get("push_identity")
    for other in hass.config_entries.async_entries(DOMAIN):
        if identity and other.entry_id != entry.entry_id:
            others = await _store(hass, other.entry_id).async_load() or {}
            if others.get("push_identity") == identity:
                identity = None  # still in use
    if identity:
        api = SensateApi(
            async_get_clientsession(hass),
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
        try:
            async with asyncio.timeout(UNREGISTER_TIMEOUT):
                await api.async_unregister_push(identity)
        except (SensateError, TimeoutError) as err:
            _LOGGER.debug("Could not remove the instant-updates registration: %s", err)
    await store.async_remove()
    for issue in (ISSUE_API_CHANGED, ISSUE_FAUCET_NOT_FOUND):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")
