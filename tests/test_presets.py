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

from .conftest import DEVICE_ID, FakeKohler

PASTA = "button.kitchen_preset_pasta_pot"
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


async def test_no_presets(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    entries = er.async_entries_for_config_entry(
        er.async_get(hass), setup_entry.entry_id
    )
    assert not [e for e in entries if "_preset_" in e.unique_id]


async def test_press_preset_dispenses_its_amount(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = PRESETS
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    state = hass.states.get(PASTA)
    assert state.name == "Kitchen Preset: Pasta pot"
    assert state.attributes["amount"] == 3000
    assert state.attributes["unit"] == "mL"
    # The shower's presets on the same account get no buttons.
    assert hass.states.get("button.kitchen_preset_cool_down") is None

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: PASTA}, blocking=True
    )
    assert [b["quantity"] for c, b in kohler.commands if c == "dispense"] == [3.0]
    dispensing = hass.states.get("binary_sensor.kitchen_dispensing")
    assert dispensing.state == "on"
    assert dispensing.attributes["preset"] == "Pasta pot"


async def test_presets_follow_the_app(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.presets = PRESETS[:1]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    registry = er.async_get(hass)
    assert registry.async_get("button.kitchen_preset_kettle") is None

    # A preset added in the Konnect app shows up; a deleted one goes unavailable.
    kohler.presets = PRESETS[1:]
    freezer.tick(CONFIG_REFRESH_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("button.kitchen_preset_kettle") is not None
    assert hass.states.get(PASTA).state == STATE_UNAVAILABLE


async def test_preset_outside_dispense_range(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [
        {"experienceId": "p9", "title": "Pasta pot", "dispenseAmount": 12}
    ]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: PASTA}, blocking=True
        )
    assert err.value.translation_key == "preset_out_of_range"
    assert kohler.commands == []
