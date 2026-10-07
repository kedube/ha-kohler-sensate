"""The Kohler Sensate Faucet integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import SensateApi
from .const import (
    CONF_PUSH_UPDATES,
    DEFAULT_PUSH_UPDATES,
    DOMAIN,
    ISSUE_API_CHANGED,
    ISSUE_FAUCET_NOT_FOUND,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import SensateCoordinator
from .push import SensatePush
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
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

    if entry.options.get(CONF_PUSH_UPDATES, DEFAULT_PUSH_UPDATES):
        push = SensatePush(
            hass,
            api,
            coordinator.device_id,
            await coordinator.async_push_identity(),
            coordinator.async_push_activity,
        )
        coordinator.push = push
        push.start()
        entry.async_on_unload(push.async_stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SensateConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SensateConfigEntry) -> None:
    """Delete what was stored for a removed entry, and its repair issues."""
    await Store(
        hass, STORAGE_VERSION, STORAGE_KEY.format(entry_id=entry.entry_id)
    ).async_remove()
    for issue in (ISSUE_API_CHANGED, ISSUE_FAUCET_NOT_FOUND):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")
