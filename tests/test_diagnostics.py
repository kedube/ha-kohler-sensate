"""Tests for diagnostics redaction."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import DEVICE_ID, PASSWORD, USERNAME, FakeKohler


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
    assert diagnostics["entry"]["options"] == {"unit_system": "metric"}
    assert diagnostics["configuration"]["configuration"]["about"]["firmware"] == "16.0"
