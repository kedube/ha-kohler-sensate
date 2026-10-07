"""Button platform: one-tap quick-dispense amounts + dispense the set amount."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SensateConfigEntry
from .api import SensatePreset
from .const import DISPENSE_MAX_ML, DISPENSE_MIN_ML, DOMAIN
from .coordinator import SensateCoordinator
from .entity import SensateEntity
from .units import ALL_QUICK_KEYS, QuickAmount, from_ml

# Send one command at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    quick = coordinator.profile.quick_amounts

    # Drop the other unit system's quick buttons left over from an options change.
    keep = {f"{coordinator.device_id}_dispense_{q.key}" for q in quick}
    stale = {f"{coordinator.device_id}_dispense_{key}" for key in ALL_QUICK_KEYS} - keep
    ent_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if reg_entry.domain == "button" and reg_entry.unique_id in stale:
            ent_reg.async_remove(reg_entry.entity_id)

    entities: list[ButtonEntity] = [
        SensateQuickDispenseButton(coordinator, amount) for amount in quick
    ]
    entities.append(SensateDispenseSetAmountButton(coordinator))
    entities.append(SensateClearLeakButton(coordinator))
    async_add_entities(entities)

    # Konnect presets come and go; add a button for each new one.
    known: set[str] = set()

    @callback
    def _add_presets() -> None:
        new = [p for p in coordinator.presets.values() if p.preset_id not in known]
        if new:
            known.update(p.preset_id for p in new)
            async_add_entities(SensatePresetButton(coordinator, p) for p in new)

    _add_presets()
    entry.async_on_unload(coordinator.async_add_listener(_add_presets))


class SensateQuickDispenseButton(SensateEntity, ButtonEntity):
    """Dispense a fixed amount with one tap."""

    def __init__(self, coordinator: SensateCoordinator, amount: QuickAmount) -> None:
        super().__init__(coordinator, f"dispense_{amount.key}")
        self._ml = amount.ml
        self._attr_translation_key = amount.translation_key
        self._attr_translation_placeholders = amount.placeholders

    async def async_press(self) -> None:
        await self.coordinator.async_dispense(self._ml / 1000)


class SensateDispenseSetAmountButton(SensateEntity, ButtonEntity):
    """Dispense whatever amount the 'Dispense amount' number is set to."""

    _attr_translation_key = "dispense_set_amount"

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "dispense_set_amount")

    async def async_press(self) -> None:
        await self.coordinator.async_dispense(
            self.coordinator.dispense_amount_ml / 1000
        )


class SensateClearLeakButton(SensateEntity, ButtonEntity):
    """Acknowledge the leak events Kohler currently reports."""

    _requires_online = False
    _attr_translation_key = "clear_leak"

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "clear_leak")

    async def async_press(self) -> None:
        await self.coordinator.async_clear_leaks()


class SensatePresetButton(SensateEntity, ButtonEntity):
    """Dispense a preset saved in the Konnect app.

    Uses the verified dispense command with the preset's amount, not Kohler's
    untested preset command. Kohler's preset format isn't documented, so these
    start disabled: check the amount attribute against the app, then enable.
    """

    _attr_translation_key = "preset"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SensateCoordinator, preset: SensatePreset) -> None:
        super().__init__(coordinator, f"preset_{preset.preset_id}")
        self._preset_id = preset.preset_id
        self._attr_translation_placeholders = {"title": preset.title}

    @property
    def _preset(self) -> SensatePreset | None:
        return self.coordinator.presets.get(self._preset_id)

    @property
    def available(self) -> bool:
        return self._preset is not None and super().available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if (preset := self._preset) is None:
            return {}
        profile = self.coordinator.profile
        return {
            "amount": round(from_ml(preset.liters * 1000, profile.number_unit), 2),
            "unit": profile.number_native_unit,
        }

    async def async_press(self) -> None:
        if (preset := self._preset) is None:
            return
        if not DISPENSE_MIN_ML <= preset.liters * 1000 <= DISPENSE_MAX_ML:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="preset_out_of_range",
                translation_placeholders={"title": preset.title},
            )
        await self.coordinator.async_dispense(preset.liters)
