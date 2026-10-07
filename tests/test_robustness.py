"""Tests for offline detection, status handling, the water safety limit,
repair issues, rate limiting and failed commands."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.const import (
    CONF_MAX_RUN_MINUTES,
    CONF_UNIT_SYSTEM,
    DOMAIN,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_IDLE,
)

from .conftest import DEVICE_ID, FakeKohler

SWITCH = "switch.kitchen_water"
STATUS = "sensor.kitchen_status"
STATE_PATH = f"/faucet-state/{DEVICE_ID}"


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )


def _actions(kohler: FakeKohler) -> list[str]:
    return [body["action"] for cmd, body in kohler.commands if cmd == "onoff"]


# --- offline faucet -------------------------------------------------------------


async def test_offline_faucet(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    assert hass.states.get("binary_sensor.kitchen_connected").state == "on"

    kohler.connection = "Disconnected"
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)

    # Live data and controls go unavailable rather than showing stale values.
    for entity_id in (STATUS, SWITCH, "button.kitchen_dispense_250_ml"):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    # Cloud-side and local entities stay usable.
    assert hass.states.get("binary_sensor.kitchen_connected").state == "off"
    assert hass.states.get("binary_sensor.kitchen_leak").state == "off"
    assert hass.states.get("number.kitchen_dispense_amount").state != STATE_UNAVAILABLE

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, "dispense", {"amount": 100}, blocking=True
        )
    assert err.value.translation_key == "faucet_offline"
    assert kohler.commands == []

    kohler.connection = "Connected"
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)
    assert hass.states.get(STATUS).state == "off"


async def test_connection_state_not_reported(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.connection = None
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get("binary_sensor.kitchen_connected").state == STATE_UNKNOWN
    assert hass.states.get(STATUS).state == "off"  # assume online


# --- unknown statuses --------------------------------------------------------------


async def test_unknown_status_is_not_running(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.state["status"] = "Error"
    kohler.state["progress"] = "Paused"
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)

    assert hass.states.get(SWITCH).state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.kitchen_dispensing").state == "off"
    coordinator = setup_entry.runtime_data
    assert coordinator.update_interval == SCAN_INTERVAL_IDLE  # no fast polling
    assert "Error" in coordinator.seen_values["status"]
    assert "Paused" in coordinator.seen_values["progress"]


# --- water safety limit ---------------------------------------------------------


async def test_safety_limit_turns_water_off(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _switch(hass, SERVICE_TURN_ON)
    kohler.state["status"] = "On"

    await _advance(hass, freezer, timedelta(minutes=9))
    assert _actions(kohler) == ["ON"]

    await _advance(hass, freezer, timedelta(minutes=1, seconds=1))
    assert _actions(kohler) == ["ON", "OFF"]
    assert "Turning off the water on Kitchen" in caplog.text


async def test_safety_limit_cancelled_when_turned_off(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _switch(hass, SERVICE_TURN_ON)
    await _switch(hass, SERVICE_TURN_OFF)
    await _advance(hass, freezer, timedelta(minutes=11))
    assert _actions(kohler) == ["ON", "OFF"]


async def test_safety_limit_cancelled_when_water_stops(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    """Turned off at the faucet: seen running, then seen off."""
    await _switch(hass, SERVICE_TURN_ON)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    kohler.state["status"] = "Off"
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)

    await _advance(hass, freezer, timedelta(minutes=11))
    assert _actions(kohler) == ["ON"]


async def test_safety_limit_survives_reload(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _switch(hass, SERVICE_TURN_ON)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(minutes=5))

    # Changing an option reloads the entry.
    hass.config_entries.async_update_entry(
        setup_entry, options={**setup_entry.options, CONF_UNIT_SYSTEM: "imperial"}
    )
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()

    await _advance(hass, freezer, timedelta(minutes=5, seconds=1))
    assert _actions(kohler) == ["ON", "OFF"]


async def test_safety_limit_retries_when_command_fails(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _switch(hass, SERVICE_TURN_ON)
    kohler.state["status"] = "On"
    kohler.fail_api("/onoff", kohler.network_error())

    await _advance(hass, freezer, timedelta(minutes=10, seconds=1))
    assert _actions(kohler) == ["ON"]  # the OFF attempt failed
    await _advance(hass, freezer, timedelta(minutes=1))
    assert _actions(kohler) == ["ON", "OFF"]


async def test_safety_limit_disabled(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MAX_RUN_MINUTES: 0}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _switch(hass, SERVICE_TURN_ON)
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(minutes=30))
    assert _actions(kohler) == ["ON"]


# --- repair issues -------------------------------------------------------------------


async def _fail_polls(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    kohler: FakeKohler,
    item,
    n: int,
) -> None:
    kohler.fail_api(STATE_PATH, *[item] * n)
    for _ in range(n):
        await _advance(hass, freezer, SCAN_INTERVAL_IDLE)


async def test_repeated_rejections_raise_repair_issue(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    issues = ir.async_get(hass)
    issue_id = f"api_changed_{setup_entry.entry_id}"

    await _fail_polls(hass, freezer, kohler, (400, {"message": "bad request"}), 2)
    assert issues.async_get_issue(DOMAIN, issue_id) is None  # one-offs are normal

    await _fail_polls(hass, freezer, kohler, (400, {"message": "bad request"}), 1)
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders["name"] == "Kitchen"

    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)  # recovers
    assert issues.async_get_issue(DOMAIN, issue_id) is None


async def test_faucet_removed_from_account(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.devices = [d for d in kohler.devices if d["deviceId"] != DEVICE_ID]
    await _fail_polls(hass, freezer, kohler, (404, {"message": "not found"}), 3)

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, f"faucet_not_found_{setup_entry.entry_id}")
    assert not issues.async_get_issue(DOMAIN, f"api_changed_{setup_entry.entry_id}")


async def test_outages_never_raise_repair_issues(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _fail_polls(hass, freezer, kohler, kohler.network_error(), 2)
    await _fail_polls(hass, freezer, kohler, (503, "busy"), 3)
    assert not ir.async_get(hass).issues


async def test_unreadable_state_counts_as_rejection(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _fail_polls(hass, freezer, kohler, (200, {"unexpected": True}), 3)
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, f"api_changed_{setup_entry.entry_id}"
    )


async def test_removing_entry_deletes_repair_issues(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _fail_polls(hass, freezer, kohler, (400, {}), 3)
    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert not ir.async_get(hass).issues


# --- rate limiting -------------------------------------------------------------------


async def test_retry_after_is_honored(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.fail_api(STATE_PATH, (429, {}, {"Retry-After": "120"}))
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)
    polls = kohler.state_polls

    await _advance(hass, freezer, timedelta(seconds=90))
    assert kohler.state_polls == polls
    await _advance(hass, freezer, timedelta(seconds=31))
    assert kohler.state_polls == polls + 1
    assert not ir.async_get(hass).issues


async def test_rate_limited_command(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.fail_api("/onoff", (429, {}, {"Retry-After": "45"}))
    with pytest.raises(HomeAssistantError) as err:
        await _switch(hass, SERVICE_TURN_ON)
    assert err.value.translation_key == "rate_limited"
    assert err.value.translation_placeholders == {"seconds": "45"}


# --- failed commands ------------------------------------------------------------------


async def test_rejected_password_during_command_starts_reauth(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    api = setup_entry.runtime_data.api
    api._token.expires_at = 0
    api._token.refresh_token = None
    kohler.token_queue.append((400, {"error": "access_denied"}))

    with pytest.raises(HomeAssistantError) as err:
        await _switch(hass, SERVICE_TURN_ON)
    await hass.async_block_till_done()

    assert err.value.translation_key == "auth_failed"
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
