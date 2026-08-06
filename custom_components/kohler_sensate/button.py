"""Button platform: one-tap quick-dispense amounts + dispense the set amount."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SensateConfigEntry
from .api import SensateApiError
from .const import QUICK_AMOUNTS_ML
from .entity import SensateEntity


def _label(ml: int) -> str:
    return f"{ml / 1000:g} L" if ml >= 1000 else f"{ml} mL"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        SensateQuickDispenseButton(coordinator, ml) for ml in QUICK_AMOUNTS_ML
    ]
    entities.append(SensateDispenseSetAmountButton(coordinator))
    async_add_entities(entities)


class _BaseDispenseButton(SensateEntity, ButtonEntity):
    _attr_icon = "mdi:cup-water"

    async def _dispense_ml(self, ml: float) -> None:
        try:
            await self.coordinator.api.dispense_liters(ml / 1000.0)
        except SensateApiError as err:
            raise HomeAssistantError(f"Kohler dispense failed: {err}") from err
        await self.coordinator.async_request_refresh()


class SensateQuickDispenseButton(_BaseDispenseButton):
    """Dispense a fixed amount with one tap."""

    def __init__(self, coordinator, ml: int) -> None:
        super().__init__(coordinator)
        self._ml = ml
        self._attr_name = f"Dispense {_label(ml)}"
        self._attr_unique_id = f"{self._device_id}_dispense_{ml}ml"

    async def async_press(self) -> None:
        await self._dispense_ml(self._ml)


class SensateDispenseSetAmountButton(_BaseDispenseButton):
    """Dispense whatever amount the 'Dispense amount' number is set to."""

    _attr_name = "Dispense set amount"
    _attr_icon = "mdi:water-plus"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_id}_dispense_set_amount"

    async def async_press(self) -> None:
        # The 'Dispense amount' number entity keeps its value on the coordinator.
        ml = getattr(self.coordinator, "dispense_amount_ml", None)
        if not ml:
            raise HomeAssistantError("Set an amount on 'Dispense amount' first.")
        await self._dispense_ml(ml)
