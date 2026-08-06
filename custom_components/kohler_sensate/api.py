"""Thin async client for the Kohler Sensate faucet cloud API.

Wraps the ``kohler-anthem`` client purely for its Azure B2C **ROPC** auth (the
username/password token). The Sensate accepts command writes with that token —
unlike the Anthem shower — so no interactive browser token is needed.

All faucet-specific calls go through the wrapped client's ``_request`` because
the upstream library only models the shower's (``gcs``) endpoints.
"""

from __future__ import annotations

import base64
import json
import logging

from kohler_anthem import KohlerAnthemClient, KohlerConfig
from kohler_anthem.exceptions import AuthenticationError, KohlerAnthemError

from .const import (
    API_CUSTOMER_DEVICES,
    API_FAUCET_CONFIG,
    API_FAUCET_STATE,
    CMD_DISPENSE,
    CMD_ONOFF,
    DEFAULT_API_RESOURCE,
    DEFAULT_APIM_KEY,
    DEFAULT_CLIENT_ID,
    SKU,
)

_LOGGER = logging.getLogger(__name__)


class SensateAuthError(Exception):
    """Bad credentials / auth failure."""


class SensateApiError(Exception):
    """Any other API failure."""


class SensateNoDeviceError(Exception):
    """No Sensate faucet found on the account."""


def _decode_oid(access_token: str | None) -> str | None:
    """Extract the tenant/customer id (``oid`` claim) from the B2C JWT."""
    if not access_token:
        return None
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    return claims.get("oid") or claims.get("sub")


class SensateApi:
    """Authenticated session for one Konnect account's Sensate faucet."""

    def __init__(self, email: str, password: str) -> None:
        self._client = KohlerAnthemClient(
            KohlerConfig(
                username=email,
                password=password,
                client_id=DEFAULT_CLIENT_ID,
                apim_subscription_key=DEFAULT_APIM_KEY,
                api_resource=DEFAULT_API_RESOURCE,
            )
        )
        self.tenant_id: str | None = None
        self.device_id: str | None = None
        self.device_name: str | None = None

    async def connect(self) -> None:
        """Sign in (ROPC) and locate the faucet. Raises on failure."""
        try:
            # session=None → the library creates and owns its own aiohttp session.
            await self._client.connect()
        except AuthenticationError as err:
            raise SensateAuthError(str(err)) from err
        except KohlerAnthemError as err:
            raise SensateApiError(str(err)) from err

        token = self._client._auth.token.access_token if self._client._auth.token else None
        self.tenant_id = _decode_oid(token)
        if not self.tenant_id:
            raise SensateAuthError("Could not read tenant id from the sign-in token.")

        raw = await self._raw("GET", API_CUSTOMER_DEVICES.format(cid=self.tenant_id))
        for home in raw.get("customerHome") or []:
            for dev in home.get("devices") or []:
                if (dev.get("sku") or "").upper() in (SKU, "FAUCET"):
                    self.device_id = dev.get("deviceId")
                    self.device_name = dev.get("logicalName") or "Sensate"
        if not self.device_id:
            raise SensateNoDeviceError("No Kohler Sensate faucet on this account.")

    async def _raw(self, method: str, endpoint: str, **kwargs) -> dict:
        try:
            return await self._client._request(method, endpoint, **kwargs)
        except AuthenticationError as err:
            raise SensateAuthError(str(err)) from err
        except KohlerAnthemError as err:
            raise SensateApiError(str(err)) from err

    async def get_state(self) -> dict:
        """Return the faucet-state ``state`` object (status/progress/handle/qty)."""
        data = await self._raw("GET", API_FAUCET_STATE.format(did=self.device_id))
        return data.get("state") or {}

    async def get_config(self) -> dict:
        """Return the faucet-configuration payload (firmware + leak history)."""
        return await self._raw("GET", API_FAUCET_CONFIG.format(did=self.device_id))

    async def dispense_liters(self, liters: float) -> dict:
        """Dispense a measured amount. ``liters`` is the canonical API unit."""
        payload = {
            "deviceId": self.device_id,
            "quantity": round(float(liters), 4),
            "sku": SKU,
            "tenantId": self.tenant_id,
        }
        _LOGGER.debug("Dispense %s L: %s", liters, payload)
        return await self._raw("POST", CMD_DISPENSE, json=payload)

    async def set_power(self, on: bool) -> dict:
        """Turn the water on or off."""
        payload = {
            "action": "ON" if on else "OFF",
            "deviceId": self.device_id,
            "sku": SKU,
            "tenantId": self.tenant_id,
        }
        return await self._raw("POST", CMD_ONOFF, json=payload)

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
