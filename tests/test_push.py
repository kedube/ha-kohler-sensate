"""Tests for instant updates over Kohler's IoT Hub feed."""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import timedelta
import json
import logging
import time
from typing import Any, ClassVar
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.const import (
    CONF_PUSH_UPDATES,
    SCAN_INTERVAL_IDLE,
    SCAN_INTERVAL_PUSH,
)

from .conftest import DEVICE_ID, TENANT_ID, FakeKohler


class Reason:
    def __init__(self, failure: bool = False) -> None:
        self.is_failure = failure

    def __str__(self) -> str:
        return "failure" if self.is_failure else "success"


class Message:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class FakeMqttClient:
    """Stands in for paho; connects instantly unless told to refuse."""

    instances: ClassVar[list[FakeMqttClient]] = []
    refuse: ClassVar[bool] = False

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.published: list[tuple[str, bytes]] = []
        self.subscribed: list[str] = []
        self.stopped = False
        FakeMqttClient.instances.append(self)

    def username_pw_set(self, username: str, password: str) -> None:
        self.username, self.password = username, password

    def tls_set_context(self, context: Any) -> None:
        self.tls = context

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        self.host, self.port = host, port

    def loop_start(self) -> None:
        self.on_connect(self, None, None, Reason(FakeMqttClient.refuse), None)

    def subscribe(self, topic: str, qos: int) -> None:
        self.subscribed.append(topic)
        self.on_subscribe(self, None, 1, [Reason()], None)

    def publish(self, topic: str, payload: bytes, qos: int) -> None:
        self.published.append((topic, payload))

    def disconnect(self) -> None:
        pass

    def loop_stop(self) -> None:
        # Real paho joins its network thread here; take real time like a slow
        # CI runner would, so tests can't depend on it finishing instantly.
        time.sleep(0.05)
        self.stopped = True

    # helpers for tests
    def deliver(self, payload: dict[str, Any], rid: str = "1") -> None:
        self.on_message(
            self,
            None,
            Message(
                f"$iothub/methods/POST/statusUpdate/?$rid={rid}",
                json.dumps(payload).encode(),
            ),
        )

    def drop(self) -> None:
        self.on_disconnect(self, None, None, Reason(True), None)


@pytest.fixture(autouse=True)
def fake_mqtt() -> Generator[type[FakeMqttClient]]:
    FakeMqttClient.instances = []
    FakeMqttClient.refuse = False
    with patch("custom_components.kohler_sensate.push.mqtt.Client", FakeMqttClient):
        yield FakeMqttClient


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


async def test_disabled_by_default(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
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
