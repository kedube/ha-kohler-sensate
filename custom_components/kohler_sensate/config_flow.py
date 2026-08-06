"""Config flow for Kohler Sensate."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api import SensateApi, SensateAuthError, SensateApiError, SensateNoDeviceError
from .const import DOMAIN

STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


class SensateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kohler Sensate."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api = SensateApi(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            try:
                await api.connect()
            except SensateAuthError:
                errors["base"] = "invalid_auth"
            except SensateNoDeviceError:
                errors["base"] = "no_device"
            except SensateApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(api.device_id)
                self._abort_if_unique_id_configured()
                title = api.device_name or "Kohler Sensate"
                await api.close()
                return self.async_create_entry(title=title, data=user_input)
            await api.close()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )
