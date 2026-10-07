"""Tests for instant updates over Kohler's IoT Hub feed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging
import time

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.const import (
    CONF_PUSH_UPDATES,
    CONF_UNIT_SYSTEM,
    PUSH_GRACE,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_IDLE,
    SCAN_INTERVAL_PUSH,
    SCAN_INTERVAL_PUSH_ACTIVE,
    UNIT_SYSTEM_METRIC,
)

from .conftest import DEVICE_ID, TENANT_ID, FakeKohler, FakeMqttClient


async def _wait_for(
    hass: HomeAssistant, condition: Callable[[], bool], what: str
) -> None:
    """Wait for the push loop, a never-ending background task, to reach a state.

    Its teardown runs on a worker thread, so allow real time, not just
    event-loop turns: on a slow CI runner a fixed number of turns isn't enough.
    """
    for _ in range(200):
        await hass.async_block_till_done()
        if condition():
            return
        await hass.async_add_executor_job(time.sleep, 0.01)
    pytest.fail(f"timed out waiting for {what}")


@pytest.fixture
async def push_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> MockConfigEntry:
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_PUSH_UPDATES: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await _wait_for(hass, lambda: push.connected, "the first connection")
    return config_entry


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _faucet_message(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, rid: str = "1"
) -> None:
    """Deliver a message about the faucet and let its refresh run."""
    FakeMqttClient.instances[-1].deliver(
        {"sku": "SEN", "deviceid": DEVICE_ID, "data": {}}, rid=rid
    )
    # At most the 1 s refresh cooldown after the previous refresh.
    await _advance(hass, freezer, timedelta(seconds=1))


async def test_on_by_default(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    # Options as a new faucet gets them, without the instant-updates choice.
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_UNIT_SYSTEM: UNIT_SYSTEM_METRIC}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    assert push is not None
    await _wait_for(hass, lambda: push.connected, "the connection")
    assert len(kohler.push_registrations) == 1


async def test_can_be_turned_off(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    assert setup_entry.options[CONF_PUSH_UPDATES] is False
    assert setup_entry.runtime_data.push is None
    assert kohler.push_registrations == []
    assert FakeMqttClient.instances == []


async def test_connects_with_registered_credentials(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_PUSH_UPDATES: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await _wait_for(hass, lambda: push.connected, "the connection")

    (registration,) = kohler.push_registrations
    assert registration["tenantId"] == TENANT_ID
    (client,) = FakeMqttClient.instances
    assert client.kwargs["client_id"] == f"mobile-{registration['mobileDeviceId']}"
    assert client.kwargs["reconnect_on_failure"] is False
    assert (client.host, client.port) == ("hub.example.net", 8883)
    assert client.password == "SharedAccessSignature sig-1"
    assert client.subscribed == ["$iothub/methods/POST/#"]
    assert config_entry.runtime_data.push.connected
    # IoT Hub credentials and the account id never reach the logs.
    assert "Instant updates connected" in caplog.text
    for secret in ("SharedAccessSignature", "hub.example.net/user", TENANT_ID):
        assert secret not in caplog.text


async def test_message_for_faucet_refreshes_and_relaxes_polling(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = push_entry.runtime_data
    (client,) = FakeMqttClient.instances
    polls = kohler.state_polls
    kohler.state["status"] = "On"

    client.deliver({"sku": "SEN", "deviceid": DEVICE_ID, "data": {}}, rid="42")
    # At most the 1 s refresh cooldown after the connect-time refresh.
    await _advance(hass, freezer, timedelta(seconds=1))

    assert client.published == [
        ("$iothub/methods/res/200/?$rid=42", b'{"status":"received"}')
    ]
    assert kohler.state_polls == polls + 1
    assert hass.states.get("switch.kitchen_water").state == "on"
    assert coordinator.push_verified

    kohler.state["status"] = "Off"
    await coordinator.async_refresh()
    assert coordinator.update_interval == SCAN_INTERVAL_PUSH


async def test_other_devices_are_ignored(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    (client,) = FakeMqttClient.instances
    polls = kohler.state_polls
    client.deliver({"sku": "GCS", "deviceid": "gcs-shower"}, rid="7")
    await hass.async_block_till_done()

    assert len(client.published) == 1  # still acknowledged
    assert kohler.state_polls == polls
    assert not push_entry.runtime_data.push_verified
    assert push_entry.runtime_data.update_interval == SCAN_INTERVAL_IDLE


async def test_reconnects_with_fresh_credentials_and_same_identity(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    push = push_entry.runtime_data.push
    first = FakeMqttClient.instances[0]
    first.drop()
    # Move the clock only once the reconnect is scheduled, or it never fires.
    await _wait_for(hass, lambda: push.next_retry_at is not None, "a reconnect")
    assert first.stopped
    assert not push.connected

    await _advance(hass, freezer, timedelta(seconds=11))
    await _wait_for(hass, lambda: push.connected, "the reconnection")
    assert len(FakeMqttClient.instances) == 2
    assert FakeMqttClient.instances[1].password == "SharedAccessSignature sig-2"
    ids = {r["mobileDeviceId"] for r in kohler.push_registrations}
    assert len(ids) == 1


async def test_identity_reused_after_reload(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    assert await hass.config_entries.async_reload(push_entry.entry_id)
    await hass.async_block_till_done()
    assert FakeMqttClient.instances[0].stopped
    first, second = kohler.push_registrations
    assert first["mobileDeviceId"] == second["mobileDeviceId"]


async def test_refused_connection_retries_with_backoff(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    FakeMqttClient.refuse = True
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_PUSH_UPDATES: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await _wait_for(hass, lambda: push.next_retry_at is not None, "a retry")
    assert not push.connected
    assert "refused" in push.last_error

    FakeMqttClient.refuse = False
    await _advance(hass, freezer, timedelta(seconds=11))
    await _wait_for(hass, lambda: push.connected, "the retry to connect")
    # Polling kept working throughout.
    assert hass.states.get("sensor.kitchen_status").state == "Off"


async def test_registration_failure_keeps_polling(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.fail_api("/mobile/settings", (500, {}))
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_PUSH_UPDATES: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await _wait_for(hass, lambda: push.next_retry_at is not None, "a retry")

    assert FakeMqttClient.instances == []
    assert "HTTP 500" in push.last_error
    assert hass.states.get("sensor.kitchen_status").state == "Off"


async def test_unload_stops_the_connection(
    hass: HomeAssistant, push_entry: MockConfigEntry
) -> None:
    assert await hass.config_entries.async_unload(push_entry.entry_id)
    assert FakeMqttClient.instances[0].stopped


# --- relying on the feed ------------------------------------------------------


async def test_trusted_feed_relaxes_polling_while_water_runs(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = push_entry.runtime_data
    kohler.state["status"] = "On"
    await _faucet_message(hass, freezer)
    assert hass.states.get("switch.kitchen_water").state == "on"
    assert coordinator.update_interval == SCAN_INTERVAL_PUSH_ACTIVE

    polls = kohler.state_polls
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls
    await _advance(hass, freezer, SCAN_INTERVAL_PUSH_ACTIVE - SCAN_INTERVAL_ACTIVE)
    assert kohler.state_polls == polls + 1


async def test_unannounced_change_stops_relying_on_the_feed(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = push_entry.runtime_data
    await _faucet_message(hass, freezer)
    assert coordinator.update_interval == SCAN_INTERVAL_PUSH

    # Water turned on by hand, and the feed says nothing.
    kohler.state["status"] = "On"
    await _advance(hass, freezer, SCAN_INTERVAL_PUSH)
    assert hass.states.get("switch.kitchen_water").state == "on"
    assert coordinator.push_trusted  # its message may still be on the way

    await _advance(hass, freezer, PUSH_GRACE)
    assert not coordinator.push_trusted
    assert coordinator.push_missed == 1
    assert coordinator.update_interval == SCAN_INTERVAL_ACTIVE

    # The next message earns the trust back.
    await _faucet_message(hass, freezer, rid="2")
    assert coordinator.push_trusted
    assert coordinator.update_interval == SCAN_INTERVAL_PUSH_ACTIVE


async def test_change_announced_just_after_a_poll_keeps_trust(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = push_entry.runtime_data
    await _faucet_message(hass, freezer)
    await _advance(hass, freezer, timedelta(seconds=5))

    # A command re-reads the faucet at once, before the feed announces it.
    kohler.state["status"] = "On"
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.kitchen_water"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("switch.kitchen_water").state == "on"
    await _faucet_message(hass, freezer, rid="2")

    await _advance(hass, freezer, PUSH_GRACE)
    assert coordinator.push_trusted
    assert coordinator.push_missed == 0


async def test_dropped_feed_falls_back_and_catches_up(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    coordinator = push_entry.runtime_data
    push = coordinator.push
    await _faucet_message(hass, freezer)
    assert coordinator.update_interval == SCAN_INTERVAL_PUSH

    polls = kohler.state_polls
    FakeMqttClient.instances[0].drop()
    await _wait_for(hass, lambda: push.next_retry_at is not None, "a reconnect")
    await _advance(hass, freezer, timedelta(seconds=1))  # refresh cooldown
    # Re-read at once, and poll as usual until the feed is back.
    assert kohler.state_polls == polls + 1
    assert coordinator.update_interval == SCAN_INTERVAL_IDLE

    # A change while the feed was down isn't one it missed.
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=10))
    await _wait_for(hass, lambda: push.connected, "the reconnection")
    await _advance(hass, freezer, timedelta(seconds=1))
    assert hass.states.get("switch.kitchen_water").state == "on"
    await _advance(hass, freezer, PUSH_GRACE)
    assert coordinator.push_trusted
    assert coordinator.push_missed == 0
