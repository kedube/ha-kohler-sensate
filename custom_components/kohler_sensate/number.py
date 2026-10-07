"""Number platform: the free-choice dispense amount."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SensateConfigEntry
from .const import DISPENSE_MAX_ML, DISPENSE_MIN_ML
from .coordinator import SensateCoordinator
from .entity import SensateEntity
from .units import HA_UNIT_KEYS, from_ml, to_ml

# Only stores a value locally.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SensateDispenseAmount(entry.runtime_data)])


class SensateDispenseAmount(SensateEntity, RestoreNumber):
    """How much the 'Dispense set amount' button will pour.

    Shown in mL (metric) or fl oz (imperial) per the integration options;
    stored on the coordinator in mL so a unit change keeps the same volume.
    """

    _requires_online = False

    _attr_translation_key = "dispense_amount"
    _attr_device_class = NumberDeviceClass.VOLUME
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "dispense_amount")
        profile = coordinator.profile
        self._unit = profile.number_unit
        self._attr_native_unit_of_measurement = profile.number_native_unit
        self._attr_native_min_value = profile.number_min
        self._attr_native_max_value = profile.number_max
        self._attr_native_step = profile.number_step

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (data := await self.async_get_last_number_data()) and (
            data.native_value is not None
        ):
            value, unit = data.native_value, data.native_unit_of_measurement
        elif state := await self.async_get_last_state():
            # v0.1 saved only the state, in mL.
            try:
                value = float(state.state)
            except ValueError:
                return
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        else:
            return
        # The value may be in the other unit system's unit.
        ml = to_ml(float(value), HA_UNIT_KEYS.get(str(unit), "ml"))
        self.coordinator.dispense_amount_ml = min(
            max(ml, DISPENSE_MIN_ML), DISPENSE_MAX_ML
        )

    @property
    def native_value(self) -> float:
        return round(from_ml(self.coordinator.dispense_amount_ml, self._unit), 2)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.dispense_amount_ml = to_ml(value, self._unit)
        self.async_write_ha_state()
