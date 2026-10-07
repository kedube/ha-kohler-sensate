"""Tests for Konnect preset buttons."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.api import SensatePreset, parse_presets
from custom_components.kohler_sensate.const import CONFIG_REFRESH_INTERVAL

from .conftest import FakeKohler

PASTA = "button.kitchen_preset_pasta_pot"
PRESETS = [
    {"experienceId": "p1", "experienceTitle": "Pasta pot", "experienceQuantity": 3.0},
    {"experienceId": "p2", "experienceTitle": "Kettle", "experienceQuantity": 1.2},
]


async def _setup_with_enabled(
    hass: HomeAssistant, entry: MockConfigEntry, *entity_ids: str
) -> None:
    """Set up, then enable the (disabled by default) preset buttons."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for entity_id in entity_ids:
        registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


def test_parse_presets() -> None:
    assert parse_presets(PRESETS) == [
        SensatePreset("p1", "Pasta pot", 3.0),
        SensatePreset("p2", "Kettle", 1.2),
    ]
    assert parse_presets({"experiences": PRESETS[:1]}) == [
        SensatePreset("p1", "Pasta pot", 3.0)
    ]
    assert parse_presets({"id": 7, "title": "x"}) == []  # not a list
    assert parse_presets(None) == []
    assert parse_presets(
        [
            {"experienceId": "a", "experienceTitle": "No amount"},
            {"experienceId": "b", "experienceTitle": "Zero", "experienceQuantity": 0},
            {"experienceTitle": "No id", "experienceQuantity": 1},
            {"experienceId": "c", "experienceQuantity": 1},
            {"id": 9, "name": "Alt keys", "quantity": "0.5"},
            "junk",
        ]
    ) == [SensatePreset("9", "Alt keys", 0.5)]


async def test_no_presets(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    entries = er.async_entries_for_config_entry(
        er.async_get(hass), setup_entry.entry_id
    )
    assert not [e for e in entries if "_preset_" in e.unique_id]


async def test_preset_buttons_start_disabled(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = PRESETS
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    entry = er.async_get(hass).async_get(PASTA)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(PASTA) is None


async def test_press_preset_dispenses_its_amount(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = PRESETS
    await _setup_with_enabled(hass, config_entry, PASTA)

    state = hass.states.get(PASTA)
    assert state.name == "Kitchen Preset: Pasta pot"
    assert state.attributes["amount"] == 3000
    assert state.attributes["unit"] == "mL"

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: PASTA}, blocking=True
    )
    assert [b["quantity"] for c, b in kohler.commands if c == "dispense"] == [3.0]


async def test_presets_follow_the_app(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.presets = PRESETS[:1]
    await _setup_with_enabled(hass, config_entry, PASTA)
    registry = er.async_get(hass)
    assert registry.async_get("button.kitchen_preset_kettle") is None

    # A preset added in the Konnect app shows up; a deleted one goes unavailable.
    kohler.presets = PRESETS[1:]
    freezer.tick(CONFIG_REFRESH_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert registry.async_get("button.kitchen_preset_kettle") is not None
    assert hass.states.get(PASTA).state == STATE_UNAVAILABLE


async def test_preset_outside_dispense_range(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [
        {"experienceId": "p9", "experienceTitle": "Pasta pot", "experienceQuantity": 12}
    ]
    await _setup_with_enabled(hass, config_entry, PASTA)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: PASTA}, blocking=True
        )
    assert err.value.translation_key == "preset_out_of_range"
    assert kohler.commands == []
