"""Sensor platform: faucet status, dispense progress, handle position, amount."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SensateConfigEntry
from .coordinator import SensateCoordinator
from .entity import SensateEntity
from .units import HA_UNIT_KEYS, from_ml, to_ml

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SensateSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[SensateSensorDescription, ...] = (
    SensateSensorDescription(
        key="status",
        translation_key="status",
        value_fn=lambda state: state.get("status"),
    ),
    SensateSensorDescription(
        key="progress",
        translation_key="progress",
        value_fn=lambda state: state.get("progress"),
    ),
    SensateSensorDescription(
        key="handle_state",
        translation_key="handle_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.get("handleState"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SensateSensor(coordinator, description) for description in SENSORS
    ]
    entities.append(SensateLastDispenseSensor(coordinator))
    async_add_entities(entities)


class SensateSensor(SensateEntity, SensorEntity):
    entity_description: SensateSensorDescription

    def __init__(
        self, coordinator: SensateCoordinator, description: SensateSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})


class SensateLastDispenseSensor(SensateEntity, RestoreSensor):
    """Most recent dispense amount, in the unit system chosen in the options.

    The API only reports ``quantity`` while a dispense is running, so this
    also records amounts dispensed from Home Assistant and survives restarts.
    It deliberately has no volume device class: with one, Home Assistant pins
    the display unit on first registration and the metric/imperial option
    would stop applying after the first change.
    """

    _requires_online = False

    _attr_translation_key = "last_dispense"

    def __init__(self, coordinator: SensateCoordinator) -> None:
        # "last_quantity" keeps the v0.1 unique id.
        super().__init__(coordinator, "last_quantity")
        profile = coordinator.profile
        self._unit = profile.number_unit
        self._attr_native_unit_of_measurement = profile.number_native_unit
        self._attr_suggested_display_precision = profile.precision

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.last_dispense_liters is not None:
            return
        last = await self.async_get_last_sensor_data()
        if last is None or not isinstance(last.native_value, (int, float)):
            return
        # Convert from whatever unit the value was stored in.
        unit = HA_UNIT_KEYS.get(str(last.native_unit_of_measurement))
        if unit is not None:
            self.coordinator.last_dispense_liters = (
                to_ml(float(last.native_value), unit) / 1000
            )

    @property
    def native_value(self) -> float | None:
        liters = self.coordinator.last_dispense_liters
        if liters is None:
            return None
        return round(from_ml(liters * 1000, self._unit), 2)
