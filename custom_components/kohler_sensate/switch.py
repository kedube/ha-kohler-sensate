"""Switch platform: turn the faucet water on/off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SensateConfigEntry
from .coordinator import SensateCoordinator
from .entity import SensateEntity

# Send one command at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SensateWaterSwitch(entry.runtime_data)])


class SensateWaterSwitch(SensateEntity, SwitchEntity):
    """On/off control for the faucet water."""

    _attr_translation_key = "water"

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "water")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.water_running

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_water(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_water(False)
