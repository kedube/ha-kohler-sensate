"""Tests for the config, reauth and options flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kohler_sensate.const import (
    CONF_DEVICE_ID,
    CONF_MAX_RUN_MINUTES,
    CONF_SKU,
    CONF_UNIT_SYSTEM,
    DOMAIN,
    UNIT_SYSTEM_IMPERIAL,
    UNIT_SYSTEM_METRIC,
)

from .conftest import DEVICE_ID, PASSWORD, USERNAME, FakeKohler

CREDENTIALS = {CONF_USERNAME: f"  {USERNAME} ", CONF_PASSWORD: PASSWORD}


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow(hass: HomeAssistant, kohler: FakeKohler) -> None:
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen"
    assert result["result"].unique_id == DEVICE_ID
    assert result["data"] == {
        CONF_USERNAME: USERNAME,  # whitespace trimmed
        CONF_PASSWORD: PASSWORD,
        CONF_DEVICE_ID: DEVICE_ID,
        CONF_SKU: "SEN",
    }
    assert result["options"] == {CONF_UNIT_SYSTEM: UNIT_SYSTEM_METRIC}


async def test_user_flow_defaults_to_imperial_for_us_customary(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["options"] == {CONF_UNIT_SYSTEM: UNIT_SYSTEM_IMPERIAL}


@pytest.mark.parametrize(
    ("token_response", "error"),
    [
        ((400, {"error": "access_denied"}), "invalid_auth"),
        ((503, "busy"), "cannot_connect"),
    ],
)
async def test_user_flow_errors_then_recover(
    hass: HomeAssistant, kohler: FakeKohler, token_response, error: str
) -> None:
    flow_id = await _start(hass)
    kohler.token_queue.append(token_response)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected_error(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    flow_id = await _start(hass)
    with patch(
        "custom_components.kohler_sensate.config_flow.SensateApi.async_get_faucets",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_no_faucet(hass: HomeAssistant, kohler: FakeKohler) -> None:
    kohler.devices = [{"deviceId": "gcs-1", "sku": "GCS"}]
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["errors"] == {"base": "no_device"}


async def test_user_flow_multiple_faucets(
    hass: HomeAssistant, kohler: FakeKohler
) -> None:
    kohler.devices.append({"deviceId": "sen-bar", "sku": "SEN", "logicalName": "Bar"})
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick_device"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_DEVICE_ID: "sen-bar"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bar"
    assert result["data"][CONF_DEVICE_ID] == "sen-bar"


async def test_second_faucet_skips_configured_one(
    hass: HomeAssistant, kohler: FakeKohler, config_entry: MockConfigEntry
) -> None:
    kohler.devices.append({"deviceId": "sen-bar", "sku": "SEN", "logicalName": "Bar"})
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == "sen-bar"


async def test_already_configured(
    hass: HomeAssistant, kohler: FakeKohler, config_entry: MockConfigEntry
) -> None:
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, CREDENTIALS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, kohler: FakeKohler, setup_entry: MockConfigEntry
) -> None:
    result = await setup_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    kohler.token_queue.append((400, {"error": "access_denied"}))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "new-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_wrong_account(
    hass: HomeAssistant, kohler: FakeKohler, setup_entry: MockConfigEntry
) -> None:
    result = await setup_entry.start_reauth_flow(hass)
    kohler.devices = [{"deviceId": "sen-other", "sku": "SEN"}]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "other@example.com", CONF_PASSWORD: "x"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert setup_entry.data[CONF_PASSWORD] == PASSWORD


async def test_reconfigure_changes_account_details(
    hass: HomeAssistant, kohler: FakeKohler, setup_entry: MockConfigEntry
) -> None:
    result = await setup_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    kohler.token_queue.append((400, {"error": "access_denied"}))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "new@example.com", CONF_PASSWORD: "x"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: " new@example.com ", CONF_PASSWORD: "new-password"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_entry.data[CONF_USERNAME] == "new@example.com"
    assert setup_entry.data[CONF_PASSWORD] == "new-password"


async def test_reconfigure_wrong_account(
    hass: HomeAssistant, kohler: FakeKohler, setup_entry: MockConfigEntry
) -> None:
    result = await setup_entry.start_reconfigure_flow(hass)
    kohler.devices = [{"deviceId": "sen-other", "sku": "SEN"}]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "other@example.com", CONF_PASSWORD: "x"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert setup_entry.data[CONF_USERNAME] == USERNAME


async def test_options_flow_switches_units(
    hass: HomeAssistant, kohler: FakeKohler, setup_entry: MockConfigEntry
) -> None:
    assert hass.states.get("button.kitchen_dispense_250_ml") is not None

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UNIT_SYSTEM: UNIT_SYSTEM_IMPERIAL}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {
        CONF_UNIT_SYSTEM: UNIT_SYSTEM_IMPERIAL,
        CONF_MAX_RUN_MINUTES: 10,
    }
    # The entry reloaded with imperial buttons and dropped the metric ones.
    assert hass.states.get("button.kitchen_dispense_1_cup") is not None
    assert hass.states.get("button.kitchen_dispense_250_ml") is None
    assert (
        hass.states.get("number.kitchen_dispense_amount").attributes[
            "unit_of_measurement"
        ]
        == "fl. oz."
    )
