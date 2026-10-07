"""Tests for Konnect presets: the Preset select and the Dispense preset button."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.kohler_sensate.api import SensatePreset, parse_presets
from custom_components.kohler_sensate.const import CONFIG_REFRESH_INTERVAL, DOMAIN
from custom_components.kohler_sensate.coordinator import preset_labels

from .conftest import DEVICE_ID, FakeKohler

PRESET = "select.kitchen_preset"
DISPENSE = "button.kitchen_dispense_preset"
# Shaped like real "sensateExperiences" entries; the fake adds the device id.
PRESETS = [
    {
        "experienceId": "p1",
        "title": "Pasta pot",
        "unit": "Metric",
        "dispenseAmount": 3.0,
        "displayQuantity": "3 L",
        "state": "OFF",
    },
    {
        "experienceId": "p2",
        "title": "Kettle",
        "unit": "Standard",
        "dispenseAmount": 1.2,
        "displayQuantity": "5 Cups",
        "state": "OFF",
    },
]


async def _setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    presets: list[dict[str, Any]],
) -> None:
    kohler.presets = presets
    assert await hass.config_entries.async_setup(config_entry.entry_id)


async def _choose(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: PRESET, ATTR_OPTION: option},
        blocking=True,
    )


async def _press(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: DISPENSE}, blocking=True
    )


async def _refresh_presets(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, kohler: FakeKohler, presets
) -> None:
    kohler.presets = presets
    freezer.tick(CONFIG_REFRESH_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _dispensed(kohler: FakeKohler) -> list[float]:
    return [b["quantity"] for c, b in kohler.commands if c == "dispense"]


def test_parse_presets() -> None:
    def entry(device: str, **fields: object) -> dict[str, object]:
        return {"deviceId": device, "sku": "SEN", **fields}

    payload = {
        "sensateExperiences": [
            entry(DEVICE_ID.upper(), **PRESETS[0]),  # ids compare case-blind
            entry("sen-other", experienceId="x", title="Other", dispenseAmount=1),
            entry(DEVICE_ID, experienceId="a", title="No amount"),
            entry(DEVICE_ID, experienceId="b", title="Zero", dispenseAmount=0),
            entry(DEVICE_ID, experienceId="c", title="Flag", dispenseAmount=True),
            entry(DEVICE_ID, title="No id", dispenseAmount=1),
            entry(DEVICE_ID, experienceId="d", dispenseAmount=1),
            "junk",
        ],
        "gcsExperiences": [entry(DEVICE_ID, experienceId="20", title="Cool Down")],
    }
    assert parse_presets(payload, DEVICE_ID) == [SensatePreset("p1", "Pasta pot", 3.0)]
    assert parse_presets({"sensateExperiences": None}, DEVICE_ID) == []
    assert parse_presets(None, DEVICE_ID) == []


def test_preset_labels() -> None:
    def labels(*titles: str) -> list[str]:
        presets = [SensatePreset(str(n), title, 0.25) for n, title in enumerate(titles)]
        return list(preset_labels(presets))

    assert labels("Cup", "Tea", "Cup") == ["Cup", "Tea", "Cup (2)"]
    assert labels("Cup", "Cup (2)", "Cup") == ["Cup", "Cup (2)", "Cup (3)"]


async def test_no_presets(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    entries = er.async_entries_for_config_entry(
        er.async_get(hass), setup_entry.entry_id
    )
    assert not [e for e in entries if "preset" in e.unique_id]


async def test_choose_and_dispense(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await _setup(hass, config_entry, kohler, PRESETS)

    state = hass.states.get(PRESET)
    assert state.name == "Kitchen Preset"
    # The first preset until one is chosen. The shower's presets on the same
    # account aren't offered.
    assert state.state == "Pasta pot"
    assert state.attributes["options"] == ["Pasta pot", "Kettle"]
    assert state.attributes["amount"] == 3000
    assert state.attributes["unit"] == "mL"

    await _choose(hass, "Kettle")
    assert hass.states.get(PRESET).attributes["amount"] == 1200
    await _press(hass)
    assert _dispensed(kohler) == [1.2]
    dispensing = hass.states.get("binary_sensor.kitchen_dispensing")
    assert dispensing.state == "on"
    assert dispensing.attributes["preset"] == "Kettle"


async def test_presets_follow_the_app(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _setup(hass, config_entry, kohler, PRESETS)
    await _choose(hass, "Kettle")

    # Renamed in the app: still chosen.
    renamed = {**PRESETS[1], "title": "Tea kettle"}
    await _refresh_presets(hass, freezer, kohler, [PRESETS[0], renamed])
    state = hass.states.get(PRESET)
    assert state.state == "Tea kettle"
    assert state.attributes["options"] == ["Pasta pot", "Tea kettle"]

    # Deleted: the first preset instead.
    soup = {"experienceId": "p3", "title": "Soup", "dispenseAmount": 2.0}
    await _refresh_presets(hass, freezer, kohler, [PRESETS[0], soup])
    state = hass.states.get(PRESET)
    assert state.state == "Pasta pot"
    assert state.attributes["options"] == ["Pasta pot", "Soup"]

    # None left.
    await _refresh_presets(hass, freezer, kohler, [])
    assert hass.states.get(PRESET).state == STATE_UNAVAILABLE
    assert hass.states.get(DISPENSE).state == STATE_UNAVAILABLE


async def test_first_preset_added_later(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    assert hass.states.get(PRESET) is None
    await _refresh_presets(hass, freezer, kohler, PRESETS[:1])
    assert hass.states.get(PRESET).state == "Pasta pot"
    await _press(hass)
    assert _dispensed(kohler) == [3.0]


async def test_same_names(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    cups = [
        {"experienceId": "a", "title": "One Cup", "dispenseAmount": 0.236588},
        {"experienceId": "b", "title": "One Cup", "dispenseAmount": 0.25},
    ]
    await _setup(hass, config_entry, kohler, cups)
    assert hass.states.get(PRESET).attributes["options"] == ["One Cup", "One Cup (2)"]

    await _choose(hass, "One Cup (2)")
    await _press(hass)
    assert _dispensed(kohler) == [0.25]


async def test_choice_restored(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    mock_restore_cache(hass, (State(PRESET, "Kettle"),))
    await _setup(hass, config_entry, kohler, PRESETS)
    assert hass.states.get(PRESET).state == "Kettle"


async def test_choice_while_faucet_offline(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.connection = "Disconnected"
    await _setup(hass, config_entry, kohler, PRESETS)
    assert hass.states.get(PRESET).state == "Pasta pot"
    assert hass.states.get(DISPENSE).state == STATE_UNAVAILABLE


async def test_old_preset_buttons_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create(
        BUTTON_DOMAIN, DOMAIN, f"{DEVICE_ID}_preset_p1", config_entry=config_entry
    )
    await _setup(hass, config_entry, kohler, PRESETS)
    assert (
        registry.async_get_entity_id(BUTTON_DOMAIN, DOMAIN, f"{DEVICE_ID}_preset_p1")
        is None
    )
    assert hass.states.get(DISPENSE) is not None


async def test_preset_outside_dispense_range(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await _setup(
        hass,
        config_entry,
        kohler,
        [{"experienceId": "p9", "title": "Pasta pot", "dispenseAmount": 12}],
    )
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass)
    assert err.value.translation_key == "preset_out_of_range"
    assert kohler.commands == []
