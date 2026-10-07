"""Actions for Kohler Sensate: dispense a measured amount of water.

Usable from automations, scripts and voice assistants, e.g. "dispense 300 mL",
"dispense 2 cups" or a preset saved in the Konnect app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .const import (
    ATTR_AMOUNT,
    ATTR_AMOUNT_L,
    ATTR_AMOUNT_ML,
    ATTR_PRESET,
    ATTR_UNIT,
    DISPENSE_MAX_ML,
    DISPENSE_MIN_ML,
    DOMAIN,
    SERVICE_DISPENSE,
)
from .units import ML_PER_UNIT, UNIT_LABELS, from_ml, to_ml

if TYPE_CHECKING:
    from . import SensateConfigEntry
    from .coordinator import SensateCoordinator

_POSITIVE = vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False))

DISPENSE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(ATTR_AMOUNT): _POSITIVE,
            vol.Optional(ATTR_UNIT): vol.In(list(ML_PER_UNIT)),
            vol.Optional(ATTR_PRESET): vol.All(cv.string, vol.Strip, vol.Length(min=1)),
            # v0.1 fields, still accepted.
            vol.Optional(ATTR_AMOUNT_ML): _POSITIVE,
            vol.Optional(ATTR_AMOUNT_L): _POSITIVE,
        }
    ),
    cv.has_at_least_one_key(ATTR_AMOUNT, ATTR_PRESET, ATTR_AMOUNT_ML, ATTR_AMOUNT_L),
    cv.has_at_most_one_key(ATTR_AMOUNT, ATTR_PRESET, ATTR_AMOUNT_ML, ATTR_AMOUNT_L),
)


def _target_entries(hass: HomeAssistant, call: ServiceCall) -> list[SensateConfigEntry]:
    """Return the loaded faucet entries the call is aimed at."""
    loaded: list[SensateConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )
    if device_ids := call.data.get(ATTR_DEVICE_ID):
        dev_reg = dr.async_get(hass)
        entry_ids = {
            entry_id
            for device_id in device_ids
            if (device := dev_reg.async_get(device_id))
            for entry_id in device.config_entries
        }
        targets = [entry for entry in loaded if entry.entry_id in entry_ids]
        if not targets:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="device_not_found"
            )
        return targets
    if len(loaded) > 1:
        # Never run water on every faucet in the house by accident.
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="device_required"
        )
    return loaded


def _requested_ml(call: ServiceCall, default_unit: str) -> tuple[float, str]:
    """Return (milliliters, unit the caller used)."""
    if ATTR_AMOUNT_ML in call.data:
        return call.data[ATTR_AMOUNT_ML], "ml"
    if ATTR_AMOUNT_L in call.data:
        return to_ml(call.data[ATTR_AMOUNT_L], "l"), "l"
    unit = call.data.get(ATTR_UNIT, default_unit)
    return to_ml(call.data[ATTR_AMOUNT], unit), unit


def _requested(
    call: ServiceCall, coordinator: SensateCoordinator
) -> tuple[float, str | None]:
    """Return (liters, preset name or None) for one faucet, or raise why not."""
    if (name := call.data.get(ATTR_PRESET)) is not None:
        if (preset := coordinator.find_preset(name)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_not_found",
                translation_placeholders={
                    "name": coordinator.config_entry.title,
                    "preset": name,
                    "presets": ", ".join(coordinator.preset_options) or "—",
                },
            )
        if not DISPENSE_MIN_ML <= preset.liters * 1000 <= DISPENSE_MAX_ML:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_out_of_range",
                translation_placeholders={"title": preset.title},
            )
        return preset.liters, preset.title
    ml, unit = _requested_ml(call, coordinator.profile.service_unit)
    if not DISPENSE_MIN_ML <= ml <= DISPENSE_MAX_ML:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="amount_out_of_range",
            translation_placeholders={
                "min": f"{from_ml(DISPENSE_MIN_ML, unit):.3g}",
                "max": f"{from_ml(DISPENSE_MAX_ML, unit):.3g}",
                "unit": UNIT_LABELS[unit],
            },
        )
    return ml / 1000, None


def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration actions."""

    async def _async_dispense(call: ServiceCall) -> None:
        # Check every faucet before running water at any of them.
        requests = [
            (entry.runtime_data, _requested(call, entry.runtime_data))
            for entry in _target_entries(hass, call)
        ]
        for coordinator, (liters, preset) in requests:
            await coordinator.async_dispense(liters, preset)

    hass.services.async_register(
        DOMAIN, SERVICE_DISPENSE, _async_dispense, schema=DISPENSE_SCHEMA
    )
