"""Number platform: the free-choice dispense amount (mL)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SensateConfigEntry
from .const import DISPENSE_MAX_ML, DISPENSE_MIN_ML, DISPENSE_STEP_ML
from .entity import SensateEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([SensateDispenseAmount(entry.runtime_data)])


class SensateDispenseAmount(SensateEntity, NumberEntity, RestoreEntity):
    """How much the 'Dispense set amount' button will pour."""

    _attr_name = "Dispense amount"
    _attr_icon = "mdi:beaker-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = DISPENSE_MIN_ML
    _attr_native_max_value = DISPENSE_MAX_ML
    _attr_native_step = DISPENSE_STEP_ML
    _attr_native_unit_of_measurement = UnitOfVolume.MILLILITERS

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_dispense_amount"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            try:
                self.coordinator.dispense_amount_ml = float(last.state)
            except ValueError:
                pass

    @property
    def native_value(self) -> float:
        return self.coordinator.dispense_amount_ml

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.dispense_amount_ml = value
        self.async_write_ha_state()
