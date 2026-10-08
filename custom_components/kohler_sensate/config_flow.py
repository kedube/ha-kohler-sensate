"""Config flow for Kohler Sensate."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import SensateApi, SensateApiError, SensateAuthError, SensateDevice
from .const import (
    CONF_DEVICE_ID,
    CONF_MAX_RUN_MINUTES,
    CONF_SKU,
    CONF_UNIT_SYSTEM,
    DEFAULT_MAX_RUN_MINUTES,
    DOMAIN,
    UNIT_SYSTEMS,
)
from .units import default_unit_system

_LOGGER = logging.getLogger(__name__)

USERNAME_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
UNIT_SYSTEM_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=UNIT_SYSTEMS,
        mode=SelectSelectorMode.LIST,
        translation_key=CONF_UNIT_SYSTEM,
    )
)

MAX_RUN_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0,
        max=120,
        step=1,
        mode=NumberSelectorMode.BOX,
        unit_of_measurement="min",
    )
)


def _credentials_schema(username: str | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=username or vol.UNDEFINED): (
                USERNAME_SELECTOR
            ),
            vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        }
    )


class SensateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kohler Sensate."""

    VERSION = 1
    # 1.2: the "Instant updates" option was removed.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._faucets: dict[str, SensateDevice] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SensateOptionsFlow:
        return SensateOptionsFlow()

    async def _async_fetch_faucets(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> list[SensateDevice] | None:
        """Sign in and list faucets; fills ``errors`` and returns None on failure."""
        api = SensateApi(
            async_get_clientsession(self.hass),
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
        )
        try:
            return await api.async_get_faucets()
        except SensateAuthError:
            errors["base"] = "invalid_auth"
        except SensateApiError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while signing in to Kohler")
            errors["base"] = "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {
                **user_input,
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
            }
            faucets = await self._async_fetch_faucets(user_input, errors)
            if faucets is not None and not faucets:
                errors["base"] = "no_device"
            elif faucets:
                configured = self._async_current_ids(include_ignore=False)
                available = [f for f in faucets if f.device_id not in configured]
                if not available:
                    return self.async_abort(reason="already_configured")
                self._credentials = user_input
                self._faucets = {f.device_id: f for f in available}
                if len(available) == 1:
                    return await self._async_create(available[0])
                return await self.async_step_pick_device()

        return self.async_show_form(
            step_id="user",
            # Re-fill the email after an error, but never echo the password.
            data_schema=self.add_suggested_values_to_schema(
                _credentials_schema(),
                {CONF_USERNAME: user_input[CONF_USERNAME]} if user_input else None,
            ),
            errors=errors,
        )

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a faucet when the account has more than one."""
        if user_input is not None:
            return await self._async_create(self._faucets[user_input[CONF_DEVICE_ID]])

        options = [
            SelectOptionDict(value=f.device_id, label=f.name)
            for f in self._faucets.values()
        ]
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def _async_create(self, faucet: SensateDevice) -> ConfigFlowResult:
        await self.async_set_unique_id(faucet.device_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=faucet.name,
            data={
                **self._credentials,
                CONF_DEVICE_ID: faucet.device_id,
                CONF_SKU: faucet.sku,
            },
            options={CONF_UNIT_SYSTEM: default_unit_system(self.hass)},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Kohler rejected the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_update_credentials(
            self._get_reauth_entry(), "reauth_confirm", user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the account email or password."""
        return await self._async_update_credentials(
            self._get_reconfigure_entry(), "reconfigure", user_input
        )

    async def _async_update_credentials(
        self, entry: ConfigEntry, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Check new credentials still own this faucet, then save and reload."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {
                **user_input,
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
            }
            faucets = await self._async_fetch_faucets(user_input, errors)
            if faucets is not None:
                device_id = entry.data.get(CONF_DEVICE_ID) or entry.unique_id
                faucet = next((f for f in faucets if f.device_id == device_id), None)
                if faucet is None:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DEVICE_ID: device_id,
                        CONF_SKU: faucet.sku,
                    },
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=_credentials_schema(entry.data[CONF_USERNAME]),
            description_placeholders={"name": entry.title},
            errors=errors,
        )


class SensateOptionsFlow(OptionsFlowWithReload):
    """Units and the water safety limit."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_MAX_RUN_MINUTES] = int(user_input[CONF_MAX_RUN_MINUTES])
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UNIT_SYSTEM,
                        default=options.get(
                            CONF_UNIT_SYSTEM, default_unit_system(self.hass)
                        ),
                    ): UNIT_SYSTEM_SELECTOR,
                    vol.Required(
                        CONF_MAX_RUN_MINUTES,
                        default=options.get(
                            CONF_MAX_RUN_MINUTES, DEFAULT_MAX_RUN_MINUTES
                        ),
                    ): MAX_RUN_SELECTOR,
                }
            ),
        )
