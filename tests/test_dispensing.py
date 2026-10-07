"""Tests for following dispenses, from commands and the instant-updates feed."""

from __future__ import annotations

from datetime import timedelta
import json

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kohler_sensate.const import (
    DISPENSE_MAX,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_PUSH_ACTIVE,
)
from custom_components.kohler_sensate.push import FaucetEvent, parse_event

from .conftest import (
    FakeKohler,
    FakeMqttClient,
    feed_message,
    preset_message,
    water_message,
)

DISPENSING = "binary_sensor.kitchen_dispensing"
CUP = "button.kitchen_dispense_250_ml"


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


async def _deliver(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, message: dict
) -> None:
    FakeMqttClient.instances[-1].deliver(message)
    await _advance(hass, freezer, timedelta(seconds=1))  # refresh cooldown


def _event(message: dict) -> FaucetEvent | None:
    return parse_event(json.dumps(message).encode())


def test_parse_event() -> None:
    assert _event(water_message("On")) == FaucetEvent(status="On", handle="OPEN")
    assert _event(preset_message("Tea", "ON")) == FaucetEvent(
        preset="Tea", preset_on=True
    )
    assert _event(preset_message("Tea", "OFF")) == FaucetEvent(
        preset="Tea", preset_on=False
    )
    assert _event(feed_message("FirmwareUpdate", version="17.0")) is None
    assert _event({"data": {"attributes": "junk"}}) is None
    assert parse_event(b"not json") is None


async def test_dispense_without_the_feed(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _press(hass, CUP)
    # The re-read right after the command still says "Off": too soon to tell.
    assert hass.states.get(DISPENSING).state == "on"

    kohler.state["status"] = "On"
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert hass.states.get(DISPENSING).state == "on"

    kohler.state["status"] = "Off"
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert hass.states.get(DISPENSING).state == "off"


async def test_short_dispense_between_polls(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    # A quarter cup runs for about a second; no poll ever sees the water on.
    await _press(hass, CUP)
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    assert hass.states.get(DISPENSING).state == "off"


async def test_dispense_with_the_feed(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _press(hass, CUP)
    await _deliver(hass, freezer, water_message("On"))
    # A poll that lags the feed doesn't end it.
    await _advance(hass, freezer, SCAN_INTERVAL_PUSH_ACTIVE)
    assert hass.states.get(DISPENSING).state == "on"

    await _deliver(hass, freezer, water_message("Off"))
    assert hass.states.get(DISPENSING).state == "off"


async def test_preset_from_the_app(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _deliver(hass, freezer, preset_message("A Glass of Water", "ON"))
    state = hass.states.get(DISPENSING)
    assert state.state == "on"
    assert state.attributes["preset"] == "A Glass of Water"

    await _deliver(hass, freezer, preset_message("A Glass of Water", "OFF"))
    state = hass.states.get(DISPENSING)
    assert state.state == "off"
    assert "preset" not in state.attributes


async def test_preset_ends_when_the_water_stops(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _deliver(hass, freezer, preset_message("One Cup", "ON"))
    await _deliver(hass, freezer, water_message("On"))
    await _deliver(hass, freezer, water_message("Off"))
    assert hass.states.get(DISPENSING).state == "off"


async def test_gives_up_if_the_end_never_arrives(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    push_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await _deliver(hass, freezer, preset_message("One Cup", "ON"))
    await _advance(hass, freezer, DISPENSE_MAX - timedelta(seconds=5))
    assert hass.states.get(DISPENSING).state == "on"
    # Not left on until the next poll, minutes later.
    await _advance(hass, freezer, timedelta(seconds=5))
    assert hass.states.get(DISPENSING).state == "off"
