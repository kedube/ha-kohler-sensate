"""Tests for the firmware update entity."""

from __future__ import annotations

from homeassistant.components.update import ATTR_IN_PROGRESS, UpdateEntityFeature
from homeassistant.const import ATTR_ENTITY_PICTURE, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeKohler

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
