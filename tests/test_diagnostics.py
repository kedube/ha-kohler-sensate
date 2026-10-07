"""Tests for diagnostics redaction."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import DEVICE_ID, PASSWORD, TENANT_ID, USERNAME, FakeKohler


async def test_diagnostics_redacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, setup_entry)
    dumped = json.dumps(diagnostics)

    for secret in (PASSWORD, USERNAME, DEVICE_ID, "SN-SECRET-1"):
        assert secret not in dumped
    assert diagnostics["state"]["status"] == "Off"
    assert diagnostics["entry"]["options"] == {
        "unit_system": "metric",
        "push_updates": False,
    }
    about = diagnostics["configuration"]["configuration"]["about"]
    assert about["firmware"]["version"] == "16.0"


async def test_identifiers_redacted_under_any_name(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    kohler: FakeKohler,
) -> None:
    # Fields the key-based list doesn't know, as Kohler might add them.
    kohler.config["owner"] = {"contact": USERNAME.upper(), "tenant": TENANT_ID}
    kohler.config["leakDetectionHistory"] = [
        {"id": "leak-1", "source": f"devices/{DEVICE_ID}/leaks"}
    ]
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )
    dumped = json.dumps(diagnostics).lower()

    for secret in (USERNAME, DEVICE_ID, TENANT_ID):
        assert secret.lower() not in dumped
    leak = diagnostics["configuration"]["leakDetectionHistory"][0]
    assert leak == {"id": "leak-1", "source": "devices/**REDACTED**/leaks"}
