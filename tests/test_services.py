"""Tests for the kohler_sensate.dispense action."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.kohler_sensate.const import (
    CONF_DEVICE_ID,
    CONF_UNIT_SYSTEM,
    DOMAIN,
    UNIT_SYSTEM_IMPERIAL,
)

from .conftest import DEVICE_ID, PASSWORD, USERNAME, FakeKohler, get_device


async def _dispense(hass: HomeAssistant, **data) -> None:
    await hass.services.async_call(DOMAIN, "dispense", data, blocking=True)


def _quantities(kohler: FakeKohler) -> list[tuple[str, float]]:
    return [(body["deviceId"], body["quantity"]) for cmd, body in kohler.commands]


GLASS = {"experienceId": "g", "title": "A Glass of Water", "dispenseAmount": 0.236588}
CUPS = [
    {"experienceId": "a", "title": "One Cup", "dispenseAmount": 0.236588},
    {"experienceId": "b", "title": "One Cup", "dispenseAmount": 0.25},
]


def _second_faucet(hass: HomeAssistant) -> MockConfigEntry:
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Bar",
        unique_id="sen-bar",
        data={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_DEVICE_ID: "sen-bar",
        },
    )
    second.add_to_hass(hass)
    return second


@pytest.mark.parametrize(
    ("data", "liters"),
    [
        ({"amount": 300}, 0.3),  # metric default unit is mL
        ({"amount": 1.5, "unit": "l"}, 1.5),
        ({"amount": 2, "unit": "cup"}, 0.4732),
        ({"amount": 16, "unit": "fl_oz"}, 0.4732),
        ({"amount": 1, "unit": "qt"}, 0.9464),
        ({"amount": 0.5, "unit": "gal"}, 1.8927),
        ({"amount_ml": 250}, 0.25),
        ({"amount_l": 0.5}, 0.5),
    ],
)
async def test_dispense_units(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler, data, liters
) -> None:
    await _dispense(hass, **data)
    assert _quantities(kohler) == [(DEVICE_ID, liters)]


async def test_imperial_default_unit(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_UNIT_SYSTEM: UNIT_SYSTEM_IMPERIAL}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _dispense(hass, amount=8)  # fl oz
    assert _quantities(kohler) == [(DEVICE_ID, 0.2366)]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"amount": 5}, "between 10 and 4e+03 mL"),
        ({"amount": 2, "unit": "gal"}, "between 0.00264 and 1.06 gal"),
        ({"amount_l": 4.5}, "between 0.01 and 4 L"),
    ],
)
async def test_dispense_out_of_range(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler, data, message
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, **data)
    assert err.value.translation_key == "amount_out_of_range"
    assert kohler.commands == []


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"amount": 1, "amount_ml": 1},
        {"amount": -1},
        {"amount": 1, "unit": "pint"},
        {"amount": 1, "preset": "Tea"},
        {"preset": "  "},
    ],
)
async def test_dispense_invalid_input(
    hass: HomeAssistant, setup_entry: MockConfigEntry, data
) -> None:
    with pytest.raises(vol.Invalid):
        await _dispense(hass, **data)


async def test_dispense_not_loaded(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    await hass.config_entries.async_unload(setup_entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, amount=100)
    assert err.value.translation_key == "not_loaded"


async def test_dispense_targets_one_of_several_faucets(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    second = _second_faucet(hass)
    assert await hass.config_entries.async_setup(second.entry_id)

    # Without a target, refuse rather than run water at every faucet.
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, amount=100)
    assert err.value.translation_key == "device_required"

    await _dispense(hass, amount=100, device_id=get_device(hass, second).id)
    assert _quantities(kohler) == [("sen-bar", 0.1)]

    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, amount=100, device_id="not-a-device")
    assert err.value.translation_key == "device_not_found"


@pytest.mark.parametrize(
    ("preset", "liters"),
    [
        ("A Glass of Water", 0.2366),
        ("a glass of water", 0.2366),  # case doesn't matter
        ("  A Glass of Water ", 0.2366),
        ("One Cup (2)", 0.25),  # as the Preset select lists repeats
    ],
)
async def test_dispense_preset(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    preset: str,
    liters: float,
) -> None:
    kohler.presets = [GLASS, *CUPS]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    await _dispense(hass, preset=preset)
    assert _quantities(kohler) == [(DEVICE_ID, liters)]
    dispensing = hass.states.get("binary_sensor.kitchen_dispensing")
    assert dispensing.attributes["preset"] in ("A Glass of Water", "One Cup")


async def test_dispense_unknown_preset(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [GLASS, *CUPS]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, preset="Two Cups")
    assert err.value.translation_key == "preset_not_found"
    assert err.value.translation_placeholders == {
        "name": "Kitchen",
        "preset": "Two Cups",
        "presets": "A Glass of Water, One Cup, One Cup (2)",
    }
    assert kohler.commands == []


async def test_dispense_preset_without_presets(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, preset="One Cup")
    assert err.value.translation_placeholders["presets"] == "—"


async def test_dispense_preset_out_of_range(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.presets = [{"experienceId": "p", "title": "Pasta pot", "dispenseAmount": 12}]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, preset="Pasta pot")
    assert err.value.translation_key == "preset_out_of_range"
    assert kohler.commands == []


async def test_dispense_preset_checks_every_faucet_first(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    # Only the kitchen faucet has the preset.
    kohler.presets = [GLASS]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    second = _second_faucet(hass)
    assert await hass.config_entries.async_setup(second.entry_id)

    devices = [get_device(hass, config_entry).id, get_device(hass, second).id]
    with pytest.raises(ServiceValidationError) as err:
        await _dispense(hass, preset="A Glass of Water", device_id=devices)
    assert err.value.translation_placeholders["name"] == "Bar"
    # Not even at the faucet that has it.
    assert kohler.commands == []
