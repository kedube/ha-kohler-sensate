"""DataUpdateCoordinator for the Kohler Sensate faucet."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SensateApi, SensateAuthError, SensateApiError
from .const import CONFIG_REFRESH_CYCLES, DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SensateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls faucet state (fast) and configuration/leak history (slow)."""

    def __init__(self, hass: HomeAssistant, api: SensateApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.api = api
        self._config_countdown = 0
        self.config: dict[str, Any] = {}
        # Chosen amount (mL) for the free-amount "Dispense set amount" button;
        # kept here so the number and button entities can share it.
        self.dispense_amount_ml: float = 250.0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            if self._config_countdown <= 0:
                self.config = await self.api.get_config()
                self._config_countdown = CONFIG_REFRESH_CYCLES
            self._config_countdown -= 1
            return await self.api.get_state()
        except SensateAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SensateApiError as err:
            raise UpdateFailed(f"Error talking to Kohler: {err}") from err

    # --- convenience accessors used by entities ---
    @property
    def leak_history(self) -> list:
        return self.config.get("leakDetectionHistory") or []

    @property
    def about(self) -> dict[str, Any]:
        return (self.config.get("configuration") or {}).get("about") or {}

    def is_dispensing(self) -> bool:
        progress = (self.data or {}).get("progress")
        return bool(progress) and progress not in ("NotStarted", "Completed", "Stopped")
