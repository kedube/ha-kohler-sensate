"""Tests for when commands are refused, by the integration or by Kohler."""

from __future__ import annotations

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.update import ATTR_IN_PROGRESS
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kohler_sensate.const import CONF_SKU, DOMAIN

from .conftest import FakeKohler

CUP = "button.kitchen_dispense_250_ml"
WATER = "switch.kitchen_water"


async def _press(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: CUP}, blocking=True
    )


async def _water(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: WATER}, blocking=True
    )


async def _dispense(hass: HomeAssistant) -> None:
    await hass.services.async_call(DOMAIN, "dispense", {"amount": 250}, blocking=True)


async def _assert_refused(hass: HomeAssistant, key: str) -> None:
    for start_water in (_press, lambda hass: _water(hass, SERVICE_TURN_ON), _dispense):
        with pytest.raises(HomeAssistantError) as err:
            await start_water(hass)
        assert err.value.translation_key == key


async def test_closed_handle_blocks_remote_water(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """As in the Konnect app: "Open the handle to remote dispense water."."""
    kohler.state["handleState"] = "CLOSED"
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    await _assert_refused(hass, "handle_closed")
    # Turning the water off is never refused.
    await _water(hass, SERVICE_TURN_OFF)
    assert [(c, b["action"]) for c, b in kohler.commands] == [("onoff", "OFF")]


async def test_firmware_download_blocks_remote_water(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.state["progress"] = "Downloading"
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    await _assert_refused(hass, "firmware_updating")
    assert kohler.commands == []
    update = hass.states.get("update.kitchen_firmware")
    assert update.attributes[ATTR_IN_PROGRESS] is True


@pytest.mark.parametrize(
    ("reply", "key"),
    [
        ((200, {"statusCode": "906"}), "not_dispensed"),
        (
            (400, {"statusCode": "906", "message": "Something went wrong"}),
            "not_dispensed",
        ),
        ((200, {"statusCode": "900"}), "faucet_offline"),
        ((400, {"statusCode": 903}), "firmware_updating"),
        ((403, {"message": "Forbidden"}), "command_forbidden"),
    ],
)
async def test_kohler_refusals(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
    reply: tuple,
    key: str,
) -> None:
    kohler.fail_api("/faucet/dispense", reply)
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass)
    assert err.value.translation_key == key
    assert err.value.translation_placeholders == {"name": "Kitchen"}
    # A refused dispense isn't followed as if it were running.
    assert hass.states.get("binary_sensor.kitchen_dispensing").state == "off"


async def test_entry_without_sku_uses_the_faucets(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Entries from before 0.10 didn't store the SKU; the faucet state has it."""
    assert CONF_SKU not in config_entry.data
    kohler.state_sku = "SET"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _press(hass)
    await _water(hass, SERVICE_TURN_OFF)
    assert [body["sku"] for _, body in kohler.commands] == ["SET", "SET"]


async def test_stored_sku_is_sent(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_SKU: "SET"}
    )
    kohler.state_sku = "SEN"  # the account's device list wins, as in the app
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _press(hass)
    assert [body["sku"] for _, body in kohler.commands] == ["SET"]
