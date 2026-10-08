"""Tests for adaptive polling and clearing leak alerts."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.const import (
    CONFIG_REFRESH_INTERVAL,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_IDLE,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from custom_components.kohler_sensate.coordinator import leak_fingerprint, leak_key

from .conftest import DEVICE_ID, FakeKohler

LEAK = "binary_sensor.kitchen_leak"
CLEAR = "button.kitchen_clear_leak_alert"


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


# --- adaptive polling ---------------------------------------------------------


async def test_idle_faucet_polls_slowly(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    polls = kohler.state_polls
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls

    await _advance(hass, freezer, SCAN_INTERVAL_IDLE - SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls + 1


async def test_running_water_polls_quickly(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.state["status"] = "On"
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)
    polls = kohler.state_polls

    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls + 2

    # Back to slow polling once the water is off.
    kohler.state["status"] = "Off"
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    polls = kohler.state_polls
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls


async def test_command_polls_quickly_until_cloud_catches_up(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    """Even if the cloud still says "Off", watch closely after a command."""
    await _press(hass, "button.kitchen_dispense_250_ml")
    polls = kohler.state_polls

    for _ in range(3):
        await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls + 3

    # After the follow-up window it slows down again.
    await _advance(hass, freezer, timedelta(seconds=30))
    polls = kohler.state_polls
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls


async def test_errors_poll_slowly(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.state["status"] = "On"
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)
    kohler.fail_api(
        f"/faucet-state/{DEVICE_ID}", *[kohler.network_error() for _ in range(5)]
    )
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)  # fails
    polls = kohler.state_polls
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls


# --- leak alerts --------------------------------------------------------------


async def _refresh_config(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await _advance(hass, freezer, CONFIG_REFRESH_INTERVAL)


async def test_clear_leak_alert(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    freezer: FrozenDateTimeFactory,
) -> None:
    kohler.config["leakDetectionHistory"] = [{"time": "2026-10-01T08:00:00Z"}]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LEAK).state == "on"

    await _press(hass, CLEAR)
    state = hass.states.get(LEAK)
    assert state.state == "off"
    assert state.attributes["events"] == 1
    assert state.attributes["uncleared_events"] == 0

    # Stays cleared across a reload.
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LEAK).state == "off"

    # A new leak turns it back on.
    kohler.config["leakDetectionHistory"].append({"time": "2026-10-05T09:30:00Z"})
    await _refresh_config(hass, freezer)
    state = hass.states.get(LEAK)
    assert state.state == "on"
    assert state.attributes["uncleared_events"] == 1


async def test_leak_history_emptied_by_kohler(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    freezer: FrozenDateTimeFactory,
) -> None:
    kohler.config["leakDetectionHistory"] = [{"time": "2026-10-01T08:00:00Z"}]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get(LEAK).state == "on"

    kohler.config["leakDetectionHistory"] = []
    await _refresh_config(hass, freezer)
    assert hass.states.get(LEAK).state == "off"


async def test_cleared_event_with_id_survives_extra_fields(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    freezer: FrozenDateTimeFactory,
) -> None:
    """If Kohler later annotates a cleared event, it stays cleared."""
    kohler.config["leakDetectionHistory"] = [{"id": "leak-1", "status": "Active"}]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _press(hass, CLEAR)

    kohler.config["leakDetectionHistory"] = [
        {"id": "leak-1", "status": "Resolved", "resolvedAt": "2026-10-02"}
    ]
    await _refresh_config(hass, freezer)
    assert hass.states.get(LEAK).state == "off"


async def test_removing_entry_deletes_stored_leaks(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.config["leakDetectionHistory"] = [{"time": "2026-10-01T08:00:00Z"}]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _press(hass, CLEAR)
    key = STORAGE_KEY.format(entry_id=config_entry.entry_id)
    assert hass_storage[key]["data"]["cleared_leaks"]

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


# Kohler's leak events, as the Konnect app reads them: epoch seconds.
def _leak(seconds_ago: float) -> dict[str, Any]:
    return {"leakDetectionTime": int(dt_util.utcnow().timestamp() - seconds_ago)}


def test_leak_keys() -> None:
    """One second apart is a different leak; extra fields are the same one."""
    first = {"leakDetectionTime": 1791463790}
    assert leak_key(first) != leak_key({"leakDetectionTime": 1791463791})
    assert leak_key({**first, "status": "Resolved"}) == leak_key(first)
    # Events of another shape keep the key older versions used.
    assert leak_key({"id": "leak-1"}) == leak_fingerprint({"id": "leak-1"})


async def test_leaks_cleared_before_upgrading_stay_cleared(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Events cleared by older versions were keyed by a hash of the event."""
    old = _leak(86400)
    kohler.config["leakDetectionHistory"] = [old]
    key = STORAGE_KEY.format(entry_id=config_entry.entry_id)
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {"cleared_leaks": [leak_fingerprint(old)], "push_identity": None},
    }
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get(LEAK).state == "off"

    kohler.config["leakDetectionHistory"].append(_leak(0))
    await _refresh_config(hass, freezer)
    assert hass.states.get(LEAK).state == "on"


async def test_leak_reported_late_stays_cleared(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A leak detected before "Clear leak alert" but listed after it is cleared."""
    kohler.config["leakDetectionHistory"] = [_leak(600)]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await _press(hass, CLEAR)
    detected_before_clearing = _leak(60)

    kohler.config["leakDetectionHistory"].append(detected_before_clearing)
    await _refresh_config(hass, freezer)
    assert hass.states.get(LEAK).state == "off"

    kohler.config["leakDetectionHistory"].append(_leak(0))  # detected since
    await _refresh_config(hass, freezer)
    state = hass.states.get(LEAK)
    assert state.state == "on"
    assert state.attributes["uncleared_events"] == 1


async def test_leak_attributes(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    newest, older = _leak(60), _leak(86400)
    # Kohler's order isn't the app's: it sorts by time itself.
    kohler.config["leakDetectionHistory"] = [newest, older]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    state = hass.states.get(LEAK)
    assert state.attributes["latest"] == newest
    assert (
        state.attributes["last_detected"]
        == dt_util.utc_from_timestamp(newest["leakDetectionTime"]).isoformat()
    )
