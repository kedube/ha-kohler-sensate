"""Binary sensor platform: leak detection + dispensing activity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SensateConfigEntry
from .coordinator import SensateCoordinator
from .entity import SensateEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            SensateLeakSensor(coordinator),
            SensateDispensingSensor(coordinator),
            SensateConnectedSensor(coordinator),
        ]
    )


class SensateLeakSensor(SensateEntity, BinarySensorEntity):
    """On while Kohler reports a leak event that hasn't been cleared.

    Kohler keeps leak events in the faucet's history, so "any event" would
    stay on for good. Press "Clear leak alert" to acknowledge the current
    events; a new one turns the sensor back on.
    """

    _requires_online = False

    _attr_translation_key = "leak"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "leak")

    @property
    def is_on(self) -> bool | None:
        # Unknown, not "dry", until the configuration has been read once.
        if not self.coordinator.config_loaded:
            return None
        return bool(self.coordinator.active_leaks)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self.coordinator.leak_history
        return {
            "events": len(history),
            "uncleared_events": len(self.coordinator.active_leaks),
            "latest": history[-1] if history else None,
        }


class SensateDispensingSensor(SensateEntity, BinarySensorEntity):
    """On while a measured amount is being dispensed.

    Covers dispenses started from Home Assistant and presets run from the
    Konnect app (the latter only with instant updates). Kohler doesn't report
    other dispenses, such as by voice, apart from the water turning on.
    """

    _attr_translation_key = "dispensing"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "dispensing")

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_dispensing()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        preset = self.coordinator.dispensing_preset
        return {"preset": preset} if preset else {}


class SensateConnectedSensor(SensateEntity, BinarySensorEntity):
    """Whether Kohler's cloud can currently reach the faucet."""

    _requires_online = False
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.connection_state is None:
            return None  # Kohler didn't say
        return self.coordinator.faucet_online

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_connected": self.coordinator.last_connected}
