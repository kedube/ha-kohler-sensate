"""Tests for instant updates over Kohler's IoT Hub feed."""

from __future__ import annotations

from datetime import timedelta
import logging

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.kohler_sensate.const import (
    CONF_DEVICE_ID,
    CONF_UNIT_SYSTEM,
    DOMAIN,
    PUSH_GRACE,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_IDLE,
    SCAN_INTERVAL_PUSH,
    SCAN_INTERVAL_PUSH_ACTIVE,
    STORAGE_KEY,
    STORAGE_VERSION,
    UNIT_SYSTEM_METRIC,
)
from custom_components.kohler_sensate.push import FEEDS, FaucetEvent

from .conftest import (
    DEVICE_ID,
    PASSWORD,
    TENANT_ID,
    USERNAME,
    FakeKohler,
    FakeMqttClient,
    feed_message,
    wait_for,
)


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


async def test_always_on(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Like the Konnect app; there's no option to turn them off."""
    assert setup_entry.runtime_data.push.connected
    assert len(kohler.push_registrations) == 1
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert "push_updates" not in result["data_schema"].schema


async def test_old_off_option_is_dropped(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Faucets that had the old "Instant updates" option off get them too."""
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, "push_updates": False}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await wait_for(hass, lambda: push.connected, "the connection")
    assert config_entry.options == {CONF_UNIT_SYSTEM: UNIT_SYSTEM_METRIC}
    assert (config_entry.version, config_entry.minor_version) == (1, 2)


async def test_connects_with_registered_credentials(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await wait_for(hass, lambda: push.connected, "the connection")

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


async def test_messages_without_a_device_are_ignored(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Only a message naming this faucet proves the feed reports it."""
    (client,) = FakeMqttClient.instances
    polls = kohler.state_polls
    client.deliver({"sku": "SEN", "data": {"code": "SENSATE_STS"}}, rid="8")
    await hass.async_block_till_done()

    assert len(client.published) == 1  # still acknowledged
    assert kohler.state_polls == polls
    assert not push_entry.runtime_data.push_verified


async def test_feed_state_applies_at_once(
    hass: HomeAssistant, push_entry: MockConfigEntry
) -> None:
    """Like the app, the feed's water and handle state count before the re-read."""
    coordinator = push_entry.runtime_data
    # The event alone, without the re-read that follows it.
    coordinator._note_event(FaucetEvent(status="On", handle="CLOSED"))
    assert coordinator.handle_closed
    assert coordinator.water_running
    assert hass.states.get("switch.kitchen_water").state == "on"


async def test_feed_leak_alert(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    """A real-time leak alert turns Leak on before the history lists it."""
    leak = "binary_sensor.kitchen_leak"
    assert hass.states.get(leak).state == "off"
    FakeMqttClient.instances[-1].deliver(feed_message("SENSATE_LEAK_DETECTED_ALT"))
    await _advance(hass, freezer, timedelta(seconds=1))
    state = hass.states.get(leak)
    assert state.state == "on"
    assert state.attributes["last_detected"] is not None

    # Kept across a reload until it's cleared.
    assert await hass.config_entries.async_reload(push_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(leak).state == "on"

    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.kitchen_clear_leak_alert"},
        blocking=True,
    )
    assert hass.states.get(leak).state == "off"


async def test_firmware_install_result_rereads_firmware(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    update = "update.kitchen_firmware"
    kohler.config["configuration"]["about"]["firmware"]["version"] = "17.1"
    kohler.firmware = {"firmwareUpdateAvailable": False, "firmware": "17.1"}

    FakeMqttClient.instances[-1].deliver(
        feed_message("INSTALL_FIRMWARE_STS", status="Installed", version="17.1")
    )
    await _advance(hass, freezer, timedelta(seconds=1))
    state = hass.states.get(update)
    assert state.attributes["installed_version"] == "17.1"
    assert state.state == "off"


async def test_removing_entry_unregisters_from_kohler(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """Like the app on sign-out, so no stale registration is left on the account."""
    identity = kohler.push_registrations[0]["mobileDeviceId"]
    await hass.config_entries.async_remove(push_entry.entry_id)
    await hass.async_block_till_done()
    assert kohler.unregistered == [identity]


async def test_removal_survives_unregister_failure(
    hass: HomeAssistant,
    hass_storage: dict,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    identity = kohler.push_registrations[0]["mobileDeviceId"]
    kohler.fail_api(f"/{identity}", kohler.network_error())
    await hass.config_entries.async_remove(push_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(push_entry.entry_id) is None
    assert not [key for key in hass_storage if push_entry.entry_id in key]


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
    await wait_for(hass, lambda: push.next_retry_at is not None, "a reconnect")
    assert first.stopped
    assert not push.connected

    await _advance(hass, freezer, timedelta(seconds=11))
    await wait_for(hass, lambda: push.connected, "the reconnection")
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
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await wait_for(hass, lambda: push.next_retry_at is not None, "a retry")
    assert not push.connected
    assert "refused" in push.last_error

    FakeMqttClient.refuse = False
    await _advance(hass, freezer, timedelta(seconds=11))
    await wait_for(hass, lambda: push.connected, "the retry to connect")
    # Polling kept working throughout.
    assert hass.states.get("sensor.kitchen_status").state == "off"


async def test_registration_failure_keeps_polling(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.fail_api("/mobile/settings", (500, {}))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await wait_for(hass, lambda: push.next_retry_at is not None, "a retry")

    assert FakeMqttClient.instances == []
    assert "HTTP 500" in push.last_error
    assert hass.states.get("sensor.kitchen_status").state == "off"


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
    await wait_for(hass, lambda: push.next_retry_at is not None, "a reconnect")
    await _advance(hass, freezer, timedelta(seconds=1))  # refresh cooldown
    # Re-read at once, and poll as usual until the feed is back.
    assert kohler.state_polls == polls + 1
    assert coordinator.update_interval == SCAN_INTERVAL_IDLE

    # A change while the feed was down isn't one it missed.
    kohler.state["status"] = "On"
    await _advance(hass, freezer, timedelta(seconds=10))
    await wait_for(hass, lambda: push.connected, "the reconnection")
    await _advance(hass, freezer, timedelta(seconds=1))
    assert hass.states.get("switch.kitchen_water").state == "on"
    await _advance(hass, freezer, PUSH_GRACE)
    assert coordinator.push_trusted
    assert coordinator.push_missed == 0


# --- several faucets ----------------------------------------------------------

SECOND = "sen-bar"


def _second_faucet(
    hass: HomeAssistant, kohler: FakeKohler, username: str = USERNAME
) -> MockConfigEntry:
    kohler.devices.append({"deviceId": SECOND, "sku": "SEN", "logicalName": "Bar"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Bar",
        unique_id=SECOND,
        data={
            CONF_USERNAME: username,
            CONF_PASSWORD: PASSWORD,
            CONF_DEVICE_ID: SECOND,
        },
        options={CONF_UNIT_SYSTEM: UNIT_SYSTEM_METRIC},
    )
    entry.add_to_hass(hass)
    return entry


async def _add_second_faucet(
    hass: HomeAssistant, kohler: FakeKohler
) -> MockConfigEntry:
    second = _second_faucet(hass, kohler)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    return second


def _polls(kohler: FakeKohler, device: str) -> int:
    path = f"/faucet-state/{device}"
    return sum(1 for _, url, _, _ in kohler.mocker.mock_calls if path in str(url))


def _store_identity(hass_storage: dict, entry: MockConfigEntry, identity: str) -> None:
    key = STORAGE_KEY.format(entry_id=entry.entry_id)
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {"cleared_leaks": [], "push_identity": identity},
    }


async def test_faucets_on_one_account_share_the_feed(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    """Kohler's feed covers the account: one registration, one connection."""
    second = await _add_second_faucet(hass, kohler)
    kitchen, bar = push_entry.runtime_data, second.runtime_data
    assert kitchen.push is bar.push
    (client,) = FakeMqttClient.instances
    (registration,) = kohler.push_registrations
    assert kitchen.push_identity == bar.push_identity == registration["mobileDeviceId"]

    # Each message goes to the faucet it names, and only to it.
    kitchen_polls, bar_polls = _polls(kohler, DEVICE_ID), _polls(kohler, SECOND)
    client.deliver({"sku": "SEN", "deviceid": SECOND, "data": {}}, rid="5")
    await _advance(hass, freezer, timedelta(seconds=1))
    assert bar.push_verified
    assert not kitchen.push_verified
    assert _polls(kohler, SECOND) == bar_polls + 1
    assert _polls(kohler, DEVICE_ID) == kitchen_polls

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, second)
    assert diagnostics["instant_updates"]["faucets_on_connection"] == 2
    assert diagnostics["instant_updates"]["messages_for_this_faucet"] == 1


async def test_feed_outlives_one_faucet(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    second = await _add_second_faucet(hass, kohler)
    (client,) = FakeMqttClient.instances

    # Reloading one faucet doesn't reconnect the other.
    assert await hass.config_entries.async_reload(push_entry.entry_id)
    await hass.async_block_till_done()
    assert FakeMqttClient.instances == [client]
    assert not client.stopped
    assert push_entry.runtime_data.push is second.runtime_data.push

    assert await hass.config_entries.async_unload(push_entry.entry_id)
    await hass.async_block_till_done()
    assert not client.stopped
    assert second.runtime_data.push.connected
    # Messages for the faucet that left are ignored.
    polls = _polls(kohler, DEVICE_ID)
    client.deliver({"sku": "SEN", "deviceid": DEVICE_ID, "data": {}}, rid="6")
    await _advance(hass, freezer, timedelta(seconds=1))
    assert _polls(kohler, DEVICE_ID) == polls

    # The last one to leave closes it.
    assert await hass.config_entries.async_unload(second.entry_id)
    assert client.stopped
    assert not hass.data.get(FEEDS)


async def test_dropped_feed_rereads_every_faucet(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    second = await _add_second_faucet(hass, kohler)
    kitchen_polls, bar_polls = _polls(kohler, DEVICE_ID), _polls(kohler, SECOND)
    push = second.runtime_data.push
    FakeMqttClient.instances[0].drop()
    await wait_for(hass, lambda: push.next_retry_at is not None, "a reconnect")
    await _advance(hass, freezer, timedelta(seconds=1))
    assert _polls(kohler, DEVICE_ID) == kitchen_polls + 1
    assert _polls(kohler, SECOND) == bar_polls + 1


async def test_separate_registrations_from_older_versions_are_merged(
    hass: HomeAssistant,
    hass_storage: dict,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    """Before 0.10, each faucet registered and connected on its own."""
    second = _second_faucet(hass, kohler)
    _store_identity(hass_storage, config_entry, "old-kitchen")
    _store_identity(hass_storage, second, "old-bar")
    # Sets up both, side by side.
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    if second.state is not ConfigEntryState.LOADED:
        assert await hass.config_entries.async_setup(second.entry_id)
    push = config_entry.runtime_data.push
    await wait_for(hass, lambda: push.connected, "the connection")
    await hass.async_block_till_done()

    shared = config_entry.runtime_data.push_identity
    assert shared in ("old-kitchen", "old-bar")
    assert second.runtime_data.push_identity == shared
    assert len(FakeMqttClient.instances) == 1
    assert {r["mobileDeviceId"] for r in kohler.push_registrations} == {shared}
    # The other registration is removed from the account.
    assert kohler.unregistered == list({"old-kitchen", "old-bar"} - {shared})


async def test_removing_one_faucet_keeps_the_shared_registration(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    second = await _add_second_faucet(hass, kohler)
    identity = second.runtime_data.push_identity

    await hass.config_entries.async_remove(push_entry.entry_id)
    await hass.async_block_till_done()
    assert kohler.unregistered == []
    assert second.runtime_data.push.connected

    await hass.config_entries.async_remove(second.entry_id)
    await hass.async_block_till_done()
    assert kohler.unregistered == [identity]


async def test_each_account_has_its_own_feed(
    hass: HomeAssistant, push_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.tenants["other@example.com"] = "1f1f1f1f-5555-6666-7777-888888888888"
    second = _second_faucet(hass, kohler, username="other@example.com")
    assert await hass.config_entries.async_setup(second.entry_id)
    push = second.runtime_data.push
    await wait_for(hass, lambda: push.connected, "the second account's connection")

    assert push is not push_entry.runtime_data.push
    assert len(FakeMqttClient.instances) == 2
    tenants = {r["tenantId"] for r in kohler.push_registrations}
    assert tenants == {TENANT_ID, "1f1f1f1f-5555-6666-7777-888888888888"}
