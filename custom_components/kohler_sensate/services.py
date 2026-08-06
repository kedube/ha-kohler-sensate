"""Services for Kohler Sensate: dispense an arbitrary measured amount.

Exposed so automations, scripts, and voice assistants can say e.g.
"dispense 300 mL" or "dispense 0.5 L".
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import SensateApiError
from .const import ATTR_AMOUNT_ML, DISPENSE_MAX_ML, DISPENSE_MIN_ML, DOMAIN, SERVICE_DISPENSE

ATTR_AMOUNT_L = "amount_l"

DISPENSE_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional(ATTR_AMOUNT_ML): vol.All(
                vol.Coerce(float), vol.Range(min=DISPENSE_MIN_ML, max=DISPENSE_MAX_ML)
            ),
            vol.Optional(ATTR_AMOUNT_L): vol.All(
                vol.Coerce(float), vol.Range(min=0.01, max=DISPENSE_MAX_ML / 1000)
            ),
        },
        cv.has_at_least_one_key(ATTR_AMOUNT_ML, ATTR_AMOUNT_L),
    )
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_DISPENSE):
        return

    async def _handle_dispense(call: ServiceCall) -> None:
        if ATTR_AMOUNT_L in call.data:
            liters = float(call.data[ATTR_AMOUNT_L])
        else:
            liters = float(call.data[ATTR_AMOUNT_ML]) / 1000.0

        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("No Kohler Sensate faucet is set up.")
        for entry in entries:
            coordinator = entry.runtime_data
            try:
                await coordinator.api.dispense_liters(liters)
            except SensateApiError as err:
                raise HomeAssistantError(f"Kohler dispense failed: {err}") from err
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_DISPENSE, _handle_dispense, schema=DISPENSE_SCHEMA
    )
