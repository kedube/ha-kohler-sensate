"""Diagnostics for Kohler Sensate, with account and location data redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
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


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SensateConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
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
        "instant_updates": None
        if (push := coordinator.push) is None
        else {
            "connected": push.connected,
            "messages_for_this_faucet": push.messages,
            "verified": coordinator.push_verified,
            "last_error": push.last_error,
        },
    }
