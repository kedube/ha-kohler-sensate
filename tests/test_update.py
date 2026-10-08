"""Tests for the firmware update entity."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.update import ATTR_IN_PROGRESS, UpdateEntityFeature
from homeassistant.const import ATTR_ENTITY_PICTURE, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.const import (
    CONFIG_REFRESH_INTERVAL,
    FIRMWARE_CHECK_INTERVAL,
)

from .conftest import DEVICE_ID, FakeKohler

ENTITY_ID = "update.kitchen_firmware"


def _firmware(kohler: FakeKohler) -> dict:
    return kohler.config["configuration"]["about"]["firmware"]


async def test_up_to_date(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "16.0"
    assert state.attributes["latest_version"] == "16.0"
    assert state.attributes[ATTR_IN_PROGRESS] is False
    # Updates are installed from the Konnect app, not Home Assistant.
    assert not state.attributes[ATTR_SUPPORTED_FEATURES] & UpdateEntityFeature.INSTALL
    # An icon, not the integration's logo.
    assert ATTR_ENTITY_PICTURE not in state.attributes


async def test_newer_firmware_available(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    _firmware(kohler)["latestVersion"] = "17.0"
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    state = hass.states.get(ENTITY_ID)
    assert state.state == "on"
    assert state.attributes["latest_version"] == "17.0"


async def test_update_in_progress(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    _firmware(kohler)["latestVersion"] = "17.0"
    kohler.config["configuration"]["otaInProgress"] = True
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get(ENTITY_ID).attributes[ATTR_IN_PROGRESS] is True


async def test_version_as_text_without_latest(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.config["configuration"]["about"]["firmware"] = "15.2"
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "15.2"
    assert state.attributes["latest_version"] == "15.2"


async def test_available_while_faucet_offline(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.connection = "Disconnected"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get("switch.kitchen_water").state == "unavailable"
    assert hass.states.get(ENTITY_ID).state == "off"


def _check(available: bool, latest: str = "17.1") -> dict:
    """A firmware check reply, as the Konnect app reads it."""
    return {
        "firmwareUpdateAvailable": available,
        "firmware": latest,
        "currentFirmware": "16.0",
        "mandatoryUpdate": False,
        "otaStatus": "NotStarted",
        "skip": False,
    }


def _checks(kohler: FakeKohler) -> int:
    path = f"/firmware/sensate/{DEVICE_ID}"
    return sum(1 for _, url, _, _ in kohler.mocker.mock_calls if path in str(url))


async def test_firmware_check_offers_update(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.firmware = _check(True)
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    state = hass.states.get(ENTITY_ID)
    assert state.state == "on"
    assert state.attributes["installed_version"] == "16.0"
    assert state.attributes["latest_version"] == "17.1"


async def test_firmware_check_overrides_configuration(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    """The app decides from the check alone, not the configuration."""
    _firmware(kohler)["latestVersion"] = "17.0"
    kohler.firmware = _check(False, latest="16.0")
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes["latest_version"] == "16.0"


async def test_unreadable_firmware_check_falls_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    _firmware(kohler)["latestVersion"] = "17.0"
    kohler.firmware = {"status": "unexpected"}
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert hass.states.get(ENTITY_ID).attributes["latest_version"] == "17.0"


async def test_firmware_checked_hourly(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.firmware = _check(False)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert _checks(kohler) == 1

    # The configuration is re-read every few minutes; the check isn't.
    freezer.tick(CONFIG_REFRESH_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _checks(kohler) == 1

    kohler.firmware = _check(True)
    freezer.tick(FIRMWARE_CHECK_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _checks(kohler) == 2
    assert hass.states.get(ENTITY_ID).state == "on"
