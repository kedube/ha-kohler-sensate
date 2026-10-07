"""Tests for the translated faucet-state sensors."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.const import SCAN_INTERVAL_IDLE
from custom_components.kohler_sensate.sensor import state_key

from .conftest import FakeKohler

STATUS = "sensor.kitchen_status"
HANDLE = "sensor.kitchen_handle"
PROGRESS = "sensor.kitchen_dispense_progress"


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Off", "off"),
        ("OPEN", "open"),
        ("NotStarted", "not_started"),
        ("Dispense In-Progress", "dispense_in_progress"),
        ("  ", None),
        (None, None),
        (3, None),
    ],
)
def test_state_key(raw: object, key: str | None) -> None:
    assert state_key(raw) == key


async def test_translated_states(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    kohler.state["handleState"] = "CLOSED"
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    status = hass.states.get(STATUS)
    assert status.state == "off"
    assert status.attributes["device_class"] == "enum"
    assert status.attributes["options"] == ["off", "on"]
    assert hass.states.get(HANDLE).state == "closed"
    # The Sensate never moves progress off "NotStarted", so it starts disabled.
    entry = er.async_get(hass).async_get(PROGRESS)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_new_value_shows_untranslated(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.state["status"] = "WarmingUp"
    freezer.tick(SCAN_INTERVAL_IDLE)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    status = hass.states.get(STATUS)
    assert status.state == "warming_up"
    assert status.attributes["options"] == ["off", "on", "warming_up"]
