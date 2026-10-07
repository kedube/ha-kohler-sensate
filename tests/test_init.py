"""Tests for setup, unload, polling and authentication recovery."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.const import (
    CONF_DEVICE_ID,
    DOMAIN,
    SCAN_INTERVAL_IDLE,
)

from .conftest import DEVICE_ID, PASSWORD, USERNAME, FakeKohler

STATUS = "sensor.kitchen_status"


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(SCAN_INTERVAL_IDLE)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_setup_and_unload(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(STATUS).state == "off"
    assert hass.services.has_service(DOMAIN, "dispense")

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    assert setup_entry.state is ConfigEntryState.NOT_LOADED


async def test_v01_entry_without_device_id(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        unique_id=DEVICE_ID,
        data={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert CONF_DEVICE_ID not in entry.data
    assert hass.states.get(STATUS).state == "off"


async def test_setup_bad_password_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.token_queue.append((400, {"error": "access_denied"}))
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


async def test_setup_cloud_down_retries(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.token_queue.append(kohler.network_error())
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not _reauth_flows(hass)


async def test_transient_errors_keep_polling(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Network trouble, even while renewing the token, never needs the user."""
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", kohler.network_error())
    await _tick(hass, freezer)
    assert hass.states.get(STATUS).state == STATE_UNAVAILABLE

    # Token renewal fails with a 503 on the next poll.
    setup_entry.runtime_data.api._token.expires_at = 0
    kohler.token_queue.append((503, "busy"))
    await _tick(hass, freezer)
    assert hass.states.get(STATUS).state == STATE_UNAVAILABLE

    kohler.state["status"] = "On"
    await _tick(hass, freezer)
    assert hass.states.get(STATUS).state == "on"
    assert not _reauth_flows(hass)


async def test_password_changed_while_running(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
    freezer: FrozenDateTimeFactory,
) -> None:
    api = setup_entry.runtime_data.api
    api._token.expires_at = 0
    kohler.token_queue += [
        (400, {"error": "invalid_grant"}),  # refresh token revoked
        (400, {"error": "access_denied"}),  # and the password no longer works
    ]
    await _tick(hass, freezer)

    assert hass.states.get(STATUS).state == STATE_UNAVAILABLE
    assert len(_reauth_flows(hass)) == 1

    # Polling stops, so a bad password isn't retried until the account locks.
    attempts = len(kohler.token_requests)
    for _ in range(3):
        await _tick(hass, freezer)
    assert len(kohler.token_requests) == attempts


async def test_config_failure_does_not_block_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.fail_api(f"/faucet-configuration/{DEVICE_ID}", (400, {"message": "bad"}))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "off"
    assert hass.states.get("binary_sensor.kitchen_leak").state == "unknown"
