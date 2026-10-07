"""Diagnostics for Kohler Sensate, with account and location data redacted."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import SensateConfigEntry
from .const import CONF_DEVICE_ID

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_DEVICE_ID,
    "deviceId",
    "tenantId",
    "customerId",
    "serialNumber",
    "serial",
    "macAddress",
    "mac",
    "ssid",
    "address",
    "email",
    "latitude",
    "longitude",
    "homeLatitude",
    "homeLongitude",
}


def _redact_values(data: Any, pattern: re.Pattern[str]) -> Any:
    """Redact identifiers wherever they appear, whatever the field is called.

    Kohler echoes the device id in fields like "id", which key-based redaction
    can't catch without hiding every other "id".
    """
    if isinstance(data, str):
        return pattern.sub(REDACTED, data)
    if isinstance(data, dict):
        return {key: _redact_values(value, pattern) for key, value in data.items()}
    if isinstance(data, list):
        return [_redact_values(item, pattern) for item in data]
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SensateConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    identifiers = [
        re.escape(value)
        for value in (
            entry.data.get(CONF_DEVICE_ID),
            entry.data.get(CONF_USERNAME),
            coordinator.api.tenant_id,
        )
        if value
    ]
    diagnostics = _diagnostics(entry)
    if not identifiers:
        return diagnostics
    pattern = re.compile("|".join(identifiers), re.IGNORECASE)
    redacted: dict[str, Any] = _redact_values(diagnostics, pattern)
    return redacted


def _diagnostics(entry: SensateConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "state": async_redact_data(coordinator.data or {}, TO_REDACT),
        "configuration": async_redact_data(coordinator.config, TO_REDACT),
        "uncleared_leak_events": len(coordinator.active_leaks),
        "update_interval": str(coordinator.update_interval),
        "connection_state": coordinator.connection_state,
        # Every status/progress/handle value seen since startup, to extend the
        # integration's list of known values.
        "seen_values": {
            field: sorted(values) for field, values in coordinator.seen_values.items()
        },
        "presets": [
            {"title": p.title, "liters": p.liters} for p in coordinator.presets.values()
        ],
        "water_safety_limit_minutes": coordinator.max_run_minutes,
        "water_usage_liters": {
            "total": coordinator.usage_total_liters,
            "today": coordinator.usage_today_liters,
        },
        "dispensing": coordinator.is_dispensing(),
        "instant_updates": None
        if (push := coordinator.push) is None
        else {
            "connected": push.connected,
            "messages_for_this_faucet": push.messages,
            "verified": coordinator.push_verified,
            "missed_changes": coordinator.push_missed,
            "last_error": push.last_error,
            "next_retry_at": push.next_retry_at,
        },
    }
