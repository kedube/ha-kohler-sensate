"""The Kohler Sensate Faucet integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import SensateApi, SensateAuthError, SensateApiError, SensateNoDeviceError
from .const import DOMAIN
from .coordinator import SensateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

type SensateConfigEntry = ConfigEntry[SensateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SensateConfigEntry) -> bool:
    """Set up Kohler Sensate from a config entry."""
    api = SensateApi(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    try:
        await api.connect()
    except SensateAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SensateNoDeviceError as err:
        await api.close()
        raise ConfigEntryNotReady(str(err)) from err
    except SensateApiError as err:
        await api.close()
        raise ConfigEntryNotReady(f"Unable to reach Kohler: {err}") from err

    coordinator = SensateCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the dispense service once (shared across entries).
    from .services import async_setup_services

    async_setup_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SensateConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.api.close()
    return unload_ok
