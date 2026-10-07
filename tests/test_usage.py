"""Tests for the water usage sensors."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.kohler_sensate.const import (
    CONF_UNIT_SYSTEM,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_IDLE,
    UNIT_SYSTEM_IMPERIAL,
    USAGE_REFRESH_INTERVAL,
    USAGE_SETTLE,
)

from .conftest import DEVICE_ID, FakeKohler

TOTAL = "sensor.kitchen_total_water_used"
TODAY = "sensor.kitchen_water_used_today"
USAGE_PATH = f"/faucet-usage/{DEVICE_ID}"


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _usage_queries(kohler: FakeKohler) -> list[dict[str, str]]:
    return [
        dict(url.query)
        for _, url, _, _ in kohler.mocker.mock_calls
        if "/faucet-usage/" in str(url)
    ]


async def test_usage_sensors(
    hass: HomeAssistant, setup_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    total = hass.states.get(TOTAL)
    assert float(total.state) == pytest.approx(1.593 + 1.6144)
    assert total.attributes["device_class"] == "water"
    assert total.attributes["state_class"] == "total_increasing"
    assert total.attributes["unit_of_measurement"] == "L"

    today = hass.states.get(TODAY)
    assert float(today.state) == pytest.approx(1.5274)
    assert today.attributes["device_class"] == "water"
    assert "state_class" not in today.attributes

    # Every month since the start of the history, and today alone.
    month, day = _usage_queries(kohler)
    assert month["FromDate"] == "2019-01-01"
    assert month["Interval"] == "MONTH"
    assert day["FromDate"] == day["ToDate"] == month["ToDate"]
    assert day["Interval"] == "DAY"


async def test_imperial_shows_gallons(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    hass.config_entries.async_update_entry(
        config_entry,
        options={**config_entry.options, CONF_UNIT_SYSTEM: UNIT_SYSTEM_IMPERIAL},
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    total = hass.states.get(TOTAL)
    assert total.attributes["unit_of_measurement"] == "gal"
    assert float(total.state) == pytest.approx((1.593 + 1.6144) / 3.785411784, 1e-3)


async def test_refreshed_soon_after_the_water_stops(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.state["status"] = "On"
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)
    kohler.state["status"] = "Off"
    kohler.usage_days = {"2026-10-07": 2.0}
    await _advance(hass, freezer, SCAN_INTERVAL_ACTIVE)
    # Kohler needs a moment to count it.
    assert float(hass.states.get(TODAY).state) == pytest.approx(1.5274)

    await _advance(hass, freezer, USAGE_SETTLE)
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)
    assert float(hass.states.get(TODAY).state) == pytest.approx(2.0)


async def test_today_starts_over_at_midnight(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    await hass.config.async_set_time_zone("America/Los_Angeles")
    freezer.move_to("2026-10-07T23:59:00-07:00")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert float(hass.states.get(TODAY).state) == pytest.approx(1.5274)

    kohler.usage_days = {"2026-10-08": 0.0}
    await _advance(hass, freezer, timedelta(minutes=1))
    # The first poll of the new day, not the next 30-minute refresh.
    assert float(hass.states.get(TODAY).state) == 0.0
    assert _usage_queries(kohler)[-1]["FromDate"] == "2026-10-08"


async def test_refreshed_now_and_then(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.usage_months["2026-11"] = 1.0
    await _advance(hass, freezer, SCAN_INTERVAL_IDLE)
    assert len(_usage_queries(kohler)) == 2  # not on every poll

    await _advance(hass, freezer, USAGE_REFRESH_INTERVAL)
    assert float(hass.states.get(TOTAL).state) == pytest.approx(4.2074)


async def test_total_never_goes_down(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    # A reply missing a month must not read as a meter reset.
    kohler.usage_months = {"2026-10": 1.6144}
    await _advance(hass, freezer, USAGE_REFRESH_INTERVAL)
    assert float(hass.states.get(TOTAL).state) == pytest.approx(3.2074)


async def test_total_restored_after_restart(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(TOTAL, "5.0"),
                {"native_value": 5.0, "native_unit_of_measurement": "L"},
            ),
        ),
    )
    kohler.fail_api(USAGE_PATH, (500, {}))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert float(hass.states.get(TOTAL).state) == 5.0
    assert hass.states.get(TODAY).state == "unknown"


async def test_usage_failure_keeps_the_last_values(
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    kohler.fail_api(USAGE_PATH, (500, {}), (500, {}))
    await _advance(hass, freezer, USAGE_REFRESH_INTERVAL)
    assert float(hass.states.get(TOTAL).state) == pytest.approx(3.2074)
    assert float(hass.states.get(TODAY).state) == pytest.approx(1.5274)
    # The faucet itself is unaffected.
    assert hass.states.get("switch.kitchen_water").state == "off"
