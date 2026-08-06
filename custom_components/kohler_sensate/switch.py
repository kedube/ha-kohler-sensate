"""Switch platform: turn the faucet water on/off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SensateConfigEntry
from .api import SensateApiError
from .entity import SensateEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([SensateWaterSwitch(entry.runtime_data)])


class SensateWaterSwitch(SensateEntity, SwitchEntity):
    """On/off control for the faucet water."""

    _attr_name = "Water"
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_water"

    @property
    def is_on(self) -> bool | None:
        status = (self.coordinator.data or {}).get("status")
        if status is None:
            return None
        return status.lower() not in ("off", "notstarted")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        try:
            await self.coordinator.api.set_power(on)
        except SensateApiError as err:
            raise HomeAssistantError(f"Kohler command failed: {err}") from err
        await self.coordinator.async_request_refresh()
