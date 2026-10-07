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
    [{}, {"amount": 1, "amount_ml": 1}, {"amount": -1}, {"amount": 1, "unit": "pint"}],
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
