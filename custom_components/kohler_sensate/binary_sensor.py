"""Binary sensor platform: leak detection + dispensing activity."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SensateConfigEntry
from .entity import SensateEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [SensateLeakSensor(coordinator), SensateDispensingSensor(coordinator)]
    )


class SensateLeakSensor(SensateEntity, BinarySensorEntity):
    """On when the faucet has reported a leak."""

    _attr_name = "Leak"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_leak"

    @property
    def is_on(self) -> bool:
        return len(self.coordinator.leak_history) > 0

    @property
    def extra_state_attributes(self) -> dict:
        history = self.coordinator.leak_history
        return {"events": len(history), "latest": history[-1] if history else None}


class SensateDispensingSensor(SensateEntity, BinarySensorEntity):
    """On while the faucet is actively dispensing."""

    _attr_name = "Dispensing"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:water"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_dispensing"

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_dispensing()
