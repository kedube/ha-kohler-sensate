"""Sensor platform: faucet status, handle, dispenses and water usage."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SensateConfigEntry
from .const import UNIT_SYSTEM_IMPERIAL
from .coordinator import SensateCoordinator
from .entity import SensateEntity
from .units import HA_UNIT_KEYS, from_ml, to_ml

PARALLEL_UPDATES = 0


def state_key(value: Any) -> str | None:
    """Kohler's text as a translatable state: "NotStarted" -> "not_started"."""
    if not isinstance(value, str) or not value.strip():
        return None
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip()).lower()
    return re.sub(r"[^a-z0-9]+", "_", words).strip("_") or None


@dataclass(frozen=True, kw_only=True)
class SensateSensorDescription(SensorEntityDescription):
    field: str  # key in Kohler's faucet state
    known: tuple[str, ...]  # states with translations


SENSORS: tuple[SensateSensorDescription, ...] = (
    SensateSensorDescription(
        key="status",
        translation_key="status",
        field="status",
        known=("off", "on"),
    ),
    SensateSensorDescription(
        key="handle_state",
        translation_key="handle_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        field="handleState",
        known=("open", "closed"),
    ),
    # The Sensate leaves this at "NotStarted" even mid-dispense; see the
    # Dispensing binary sensor instead.
    SensateSensorDescription(
        key="progress",
        translation_key="progress",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        field="progress",
        known=("not_started",),
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
    entities += [
        SensateLastDispenseSensor(coordinator),
        SensateWaterTotalSensor(coordinator),
        SensateWaterTodaySensor(coordinator),
    ]
    async_add_entities(entities)


class SensateSensor(SensateEntity, SensorEntity):
    """One of Kohler's state texts, as translated states.

    A value Kohler adds later still shows, untranslated, rather than failing.
    """

    entity_description: SensateSensorDescription
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(
        self, coordinator: SensateCoordinator, description: SensateSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def options(self) -> list[str]:
        description = self.entity_description
        seen = {
            key
            for value in self.coordinator.seen_values[description.field]
            if (key := state_key(value)) is not None
        }
        return [*description.known, *sorted(seen - set(description.known))]

    @property
    def native_value(self) -> str | None:
        state = self.coordinator.data or {}
        return state_key(state.get(self.entity_description.field))


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


class _WaterSensor(SensateEntity, SensorEntity):
    """Water usage as Kohler counts it, in liters or as chosen in the options."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 1
    # Kohler's cloud keeps the history, whether or not the faucet is online.
    _requires_online = False

    def __init__(self, coordinator: SensateCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        if coordinator.profile.unit_system == UNIT_SYSTEM_IMPERIAL:
            self._attr_suggested_unit_of_measurement = UnitOfVolume.GALLONS


class SensateWaterTotalSensor(_WaterSensor, RestoreSensor):
    """Every liter Kohler has counted for the faucet; for the Energy dashboard."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "water_total")
        self._highest: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, (int, float)):
            self._highest = float(last.native_value)

    @property
    def native_value(self) -> float | None:
        total = self.coordinator.usage_total_liters
        # The total only grows. A lower figure, as from a partial reply, would
        # read as a meter reset and count the whole history twice.
        if total is not None and (self._highest is None or total > self._highest):
            self._highest = total
        return self._highest


class SensateWaterTodaySensor(_WaterSensor):
    """Water used today, as the Konnect app's daily chart shows it."""

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "water_today")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.usage_today_liters
