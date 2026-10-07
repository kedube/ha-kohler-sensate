"""Select platform: the Konnect preset that "Dispense preset" pours."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SensateConfigEntry
from .api import SensatePreset
from .coordinator import SensateCoordinator
from .entity import SensateEntity, add_with_presets
from .units import from_ml

# Only stores a choice locally.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    add_with_presets(
        coordinator,
        entry,
        lambda: async_add_entities([SensatePresetSelect(coordinator)]),
    )


def preset_labels(presets: Iterable[SensatePreset]) -> dict[str, SensatePreset]:
    """Each preset by a distinct name; repeats get " (2)", " (3)" and so on."""
    labels: dict[str, SensatePreset] = {}
    for preset in presets:
        label, n = preset.title, 1
        while label in labels:
            n += 1
            label = f"{preset.title} ({n})"
        labels[label] = preset
    return labels


class SensatePresetSelect(SensateEntity, SelectEntity, RestoreEntity):
    """Which Konnect preset the "Dispense preset" button pours.

    One dropdown, in the app's order, however many presets there are. The
    choice is kept by preset rather than by name, so renaming a preset in
    the app keeps it chosen.
    """

    _attr_translation_key = "preset"
    # A local choice from Kohler's cloud list; the faucet needn't be online.
    _requires_online = False

    def __init__(self, coordinator: SensateCoordinator) -> None:
        super().__init__(coordinator, "preset")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.preset_choice is not None:
            return
        state = await self.async_get_last_state()
        if state is not None and (preset := self._labels.get(state.state)):
            self.coordinator.preset_choice = preset.preset_id

    @property
    def _labels(self) -> dict[str, SensatePreset]:
        return preset_labels(self.coordinator.presets.values())

    @property
    def available(self) -> bool:
        return bool(self.coordinator.presets) and super().available

    @property
    def options(self) -> list[str]:
        return list(self._labels)

    @property
    def current_option(self) -> str | None:
        chosen = self.coordinator.chosen_preset
        if chosen is None:
            return None
        return next(
            label
            for label, preset in self._labels.items()
            if preset.preset_id == chosen.preset_id
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if (preset := self.coordinator.chosen_preset) is None:
            return {}
        profile = self.coordinator.profile
        return {
            "amount": round(from_ml(preset.liters * 1000, profile.number_unit), 2),
            "unit": profile.number_native_unit,
        }

    async def async_select_option(self, option: str) -> None:
        self.coordinator.preset_choice = self._labels[option].preset_id
        self.async_write_ha_state()
