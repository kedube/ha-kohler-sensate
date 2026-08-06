"""Sensor platform: faucet status, dispense progress, handle position, amount."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SensateConfigEntry
from .coordinator import SensateCoordinator
from .entity import SensateEntity


@dataclass(frozen=True, kw_only=True)
class SensateSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SensateCoordinator], Any]


def _last_quantity_liters(c: SensateCoordinator) -> float | None:
    q = (c.data or {}).get("quantity")
    return float(q) if q is not None else None


SENSORS: tuple[SensateSensorDescription, ...] = (
    SensateSensorDescription(
        key="status",
        name="Status",
        icon="mdi:faucet",
        value_fn=lambda c: (c.data or {}).get("status"),
    ),
    SensateSensorDescription(
        key="progress",
        name="Dispense progress",
        icon="mdi:progress-clock",
        value_fn=lambda c: (c.data or {}).get("progress"),
    ),
    SensateSensorDescription(
        key="handle_state",
        name="Handle",
        icon="mdi:gesture-tap-button",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: (c.data or {}).get("handleState"),
    ),
    SensateSensorDescription(
        key="last_quantity",
        name="Last dispensed",
        icon="mdi:cup-water",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=2,
        value_fn=_last_quantity_liters,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SensateSensor(coordinator, desc) for desc in SENSORS)


class SensateSensor(SensateEntity, SensorEntity):
    entity_description: SensateSensorDescription

    def __init__(
        self, coordinator: SensateCoordinator, description: SensateSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)
