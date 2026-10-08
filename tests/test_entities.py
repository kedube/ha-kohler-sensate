"""Tests for the faucet entities in metric and imperial mode."""

from __future__ import annotations

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.kohler_sensate.const import (
    CONF_UNIT_SYSTEM,
    UNIT_SYSTEM_IMPERIAL,
)

from .conftest import DEVICE_ID, FakeKohler, get_device

METRIC_BUTTONS = ["50_ml", "250_ml", "500_ml", "750_ml", "1_l", "2_l", "3_l"]
IMPERIAL_BUTTONS = [
    "1_4_cup",
    "1_2_cup",
    "1_cup",
    "2_cups",
    "1_quart",
    "1_2_gallon",
    "1_gallon",
]


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


def _quantities(kohler: FakeKohler) -> list[float]:
    return [body["quantity"] for cmd, body in kohler.commands if cmd == "dispense"]


async def _setup_imperial(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_UNIT_SYSTEM: UNIT_SYSTEM_IMPERIAL}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_device_info(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    device = get_device(hass, setup_entry)
    assert device.identifiers == {("kohler_sensate", DEVICE_ID)}
    assert device.name == "Kitchen"
    assert device.manufacturer == "Kohler"
    assert device.model_id == "SEN"
    assert device.sw_version == "16.0"
    assert device.hw_version == "CC3235SF"
    assert device.serial_number == "SN-SECRET-1"


async def test_firmware_as_text(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.config["configuration"]["about"]["firmware"] = "17.1"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert get_device(hass, config_entry).sw_version == "17.1"


async def test_metric_quick_buttons(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    for suffix in METRIC_BUTTONS:
        assert hass.states.get(f"button.kitchen_dispense_{suffix}"), suffix
    assert (
        hass.states.get("button.kitchen_dispense_250_ml").name
        == "Kitchen Dispense 250 mL"
    )

    await _press(hass, "button.kitchen_dispense_250_ml")
    await _press(hass, "button.kitchen_dispense_2_l")
    assert _quantities(kohler) == [0.25, 2.0]

    last = hass.states.get("sensor.kitchen_last_dispensed")
    assert last.state == "2000.0"
    assert last.attributes[ATTR_UNIT_OF_MEASUREMENT] == "mL"


async def test_imperial_quick_buttons(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    await _setup_imperial(hass, config_entry)
    for suffix in IMPERIAL_BUTTONS:
        assert hass.states.get(f"button.kitchen_dispense_{suffix}"), suffix
    assert hass.states.get("button.kitchen_dispense_250_ml") is None

    await _press(hass, "button.kitchen_dispense_1_cup")
    await _press(hass, "button.kitchen_dispense_1_gallon")
    assert _quantities(kohler) == [0.2366, 3.7854]

    last = hass.states.get("sensor.kitchen_last_dispensed")
    assert last.state == "128.0"
    assert last.attributes[ATTR_UNIT_OF_MEASUREMENT] == "fl. oz."


@pytest.mark.parametrize(
    ("imperial", "value", "unit", "expected_liters"),
    [(False, 330, "mL", 0.33), (True, 12, "fl. oz.", 0.3549)],
)
async def test_dispense_set_amount(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    imperial: bool,
    value: float,
    unit: str,
    expected_liters: float,
) -> None:
    if imperial:
        await _setup_imperial(hass, config_entry)
    else:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    number = "number.kitchen_dispense_amount"
    assert hass.states.get(number).attributes[ATTR_UNIT_OF_MEASUREMENT] == unit

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: number, ATTR_VALUE: value},
        blocking=True,
    )
    assert float(hass.states.get(number).state) == value
    await _press(hass, "button.kitchen_dispense_set_amount")
    assert _quantities(kohler) == [expected_liters]


async def test_number_restores_v01_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """v0.1 saved only the state, in mL; it keeps its volume in imperial."""
    mock_restore_cache(
        hass,
        [
            State(
                "number.kitchen_dispense_amount",
                "473.176",
                {"unit_of_measurement": "mL"},
            )
        ],
    )
    await _setup_imperial(hass, config_entry)
    assert hass.states.get("number.kitchen_dispense_amount").state == "16.0"


async def test_number_restores_after_unit_change(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """A value saved in fl oz comes back as the same volume in mL."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.kitchen_dispense_amount", "16"),
                {
                    "native_max_value": 135,
                    "native_min_value": 0.5,
                    "native_step": 0.5,
                    "native_unit_of_measurement": "fl. oz.",
                    "native_value": 16,
                },
            )
        ],
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get("number.kitchen_dispense_amount").state == "473.18"


async def test_water_switch(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    switch = "switch.kitchen_water"
    assert hass.states.get(switch).state == "off"

    kohler.state["status"] = "On"
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: switch}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "on"

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: switch}, blocking=True
    )
    assert [body["action"] for cmd, body in kohler.commands] == ["ON", "OFF"]


async def test_command_failure_is_reported(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.fail_api("/onoff", (500, {"message": "device offline"}))
    with pytest.raises(HomeAssistantError, match="device offline"):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "switch.kitchen_water"},
            blocking=True,
        )


async def test_leak_and_dispensing_sensors(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.config["leakDetectionHistory"] = [{"time": "2026-10-01T00:00:00Z"}]
    # "progress" is the firmware download; an update's status must never
    # read as running water.
    kohler.state["progress"] = "ConnectedDeviceOTAInProgress"
    kohler.state["quantity"] = 0.5
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    leak = hass.states.get("binary_sensor.kitchen_leak")
    assert leak.state == "on"
    assert leak.attributes["events"] == 1
    assert hass.states.get("binary_sensor.kitchen_dispensing").state == "off"
    assert hass.states.get("switch.kitchen_water").state == "off"
    # A dispense started outside Home Assistant is picked up from the state.
    assert hass.states.get("sensor.kitchen_last_dispensed").state == "500.0"
