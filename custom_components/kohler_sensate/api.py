"""Async client for the Kohler Konnect cloud API used by the Sensate faucet.

Authentication is Azure AD B2C ROPC (the Konnect email + password); the
Sensate accepts command writes with that token, so no browser sign-in is
needed. Token lifecycle:

* Access tokens are renewed ahead of expiry with the refresh token.
* If B2C rejects the refresh token (expired/revoked), the client signs in
  again with the stored password.
* If the API rejects a token with HTTP 401, it is renewed and the request is
  retried once.
* Only a rejected *password* raises :class:`SensateAuthError`. Network
  errors, timeouts, throttling and 5xx responses raise
  :class:`SensateApiError` so callers retry instead of asking the user to
  sign in again.

Credentials, tokens, the account id and raw account payloads are never
logged or put into exception messages.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    API_CUSTOMER_DEVICES,
    API_FAUCET_CONFIG,
    API_FAUCET_PRESETS,
    API_FAUCET_STATE,
    API_MOBILE_SETTINGS,
    APIM_KEY,
    AUTH_SCOPE,
    CLIENT_ID,
    CMD_DISPENSE,
    CMD_ONOFF,
    FAUCET_SKUS,
    SKU,
    TOKEN_URL,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
# Renew the access token this long before it actually expires.
TOKEN_REFRESH_MARGIN = 300.0
DEFAULT_TOKEN_LIFETIME = 3600.0


class SensateError(Exception):
    """Base error for the Sensate client."""


class SensateAuthError(SensateError):
    """Kohler rejected the account credentials."""


class SensateApiError(SensateError):
    """Kohler's cloud failed, returned an error, or could not be reached."""

    def __init__(
        self,
        message: str,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Seconds Kohler asked us to wait (HTTP 429/503 Retry-After).
        self.retry_after = retry_after

    @property
    def rejected(self) -> bool:
        """True if Kohler refused the request, rather than failing to answer."""
        return (
            self.status is not None
            and 400 <= self.status < 500
            and (self.status not in (401, 408, 429))
        )


class SensateResponseError(SensateApiError):
    """Kohler answered with something this integration can't read."""

    @property
    def rejected(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class SensateDevice:
    """A faucet found on the account."""

    device_id: str
    name: str


@dataclass(frozen=True, slots=True)
class FaucetSnapshot:
    """The faucet's live state plus whether it's connected to Kohler."""

    state: dict[str, Any]
    connection_state: str | None
    last_connected: Any


@dataclass(frozen=True, slots=True)
class SensatePreset:
    """A dispense preset saved in the Konnect app."""

    preset_id: str
    title: str
    liters: float


@dataclass(frozen=True, slots=True)
class PushSettings:
    """Azure IoT Hub credentials for instant updates."""

    host: str
    client_id: str
    username: str
    password: str


@dataclass(slots=True)
class _Token:
    access_token: str
    refresh_token: str | None
    expires_at: float  # time.monotonic() deadline

    @property
    def is_valid(self) -> bool:
        return time.monotonic() < self.expires_at


def _token_from_response(payload: dict[str, Any]) -> _Token:
    try:
        lifetime = float(payload.get("expires_in") or DEFAULT_TOKEN_LIFETIME)
    except TypeError, ValueError:
        lifetime = DEFAULT_TOKEN_LIFETIME
    margin = min(TOKEN_REFRESH_MARGIN, lifetime / 2)
    return _Token(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token") or None,
        expires_at=time.monotonic() + lifetime - margin,
    )


def decode_tenant_id(access_token: str) -> str | None:
    """Extract the account id (``oid`` claim) from a B2C access token."""
    try:
        segment = access_token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment))
    except IndexError, ValueError, binascii.Error:
        return None
    if not isinstance(claims, dict):
        return None
    return claims.get("oid") or claims.get("sub")


async def _read_json(response: aiohttp.ClientResponse) -> Any:
    """Return the decoded body, or ``None`` if it is empty or not JSON."""
    try:
        return await response.json(content_type=None)
    except ValueError:
        return None


def _describe(err: BaseException) -> str:
    # str() of aiohttp errors names the host but, unlike repr(), never any
    # proxy credentials.
    return str(err) or type(err).__name__


def _b2c_error(payload: Any) -> str:
    """Summarize a B2C token error without echoing anything account-specific."""
    if not isinstance(payload, dict):
        return "unknown error"
    description = str(payload.get("error_description") or "")
    # B2C appends "Correlation ID: …" and "Timestamp: …" lines; keep the first.
    summary = description.splitlines()[0].strip() if description else ""
    return summary[:160] or str(payload.get("error") or "unknown error")


def _retry_after(response: aiohttp.ClientResponse) -> float | None:
    """Parse Retry-After (seconds or an HTTP date) from a 429/503 reply."""
    if response.status not in (429, 503):
        return None
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(header).timestamp() - time.time())
    except TypeError, ValueError, OverflowError:
        return None


def parse_presets(payload: Any) -> list[SensatePreset]:
    """Read Konnect presets from an undocumented response, skipping anything odd.

    Field names follow the app's ``FaucetExperienceRequest`` model
    (``experienceId``, ``experienceTitle``, ``experienceQuantity`` in liters).
    """
    items: Any = payload
    if isinstance(payload, dict):
        items = next(
            (
                payload[key]
                for key in ("experiences", "faucetExperiences", "presets", "data")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
    if not isinstance(items, list):
        return []
    presets: list[SensatePreset] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        preset_id = item.get("experienceId") or item.get("id")
        title = item.get("experienceTitle") or item.get("title") or item.get("name")
        quantity = item.get("experienceQuantity", item.get("quantity"))
        if not isinstance(quantity, (int, float, str)):
            continue
        try:
            liters = float(quantity)
        except ValueError:
            continue
        if preset_id in (None, "") or not title or liters <= 0:
            continue
        presets.append(SensatePreset(str(preset_id), str(title), liters))
    return presets


def _api_error_detail(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message", "title", "error"):
            if isinstance(value := payload.get(key), str) and value:
                return f": {value[:120]}"
    return ""


class SensateApi:
    """Authenticated session for one Kohler Konnect account."""

    def __init__(
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._token: _Token | None = None
        self._token_lock = asyncio.Lock()
        self.tenant_id: str | None = None

    # --- authentication -----------------------------------------------------

    async def async_login(self) -> None:
        """Make sure there is a valid token and a known account id."""
        await self._async_access_token()

    async def _async_access_token(self) -> str:
        async with self._token_lock:
            token = self._token
            if token is not None and token.is_valid:
                return token.access_token
            if token is not None and token.refresh_token:
                try:
                    return self._store_token(
                        await self._async_token_request(
                            {
                                "grant_type": "refresh_token",
                                "refresh_token": token.refresh_token,
                            }
                        )
                    )
                except SensateAuthError:
                    _LOGGER.debug("Refresh token rejected; signing in again")
                    self._token = None
            return self._store_token(
                await self._async_token_request(
                    {
                        "grant_type": "password",
                        "username": self._username,
                        "password": self._password,
                    }
                )
            )

    def _store_token(self, token: _Token) -> str:
        tenant_id = decode_tenant_id(token.access_token)
        if not tenant_id:
            raise SensateApiError("Kohler sign-in token has no account id")
        self._token = token
        self.tenant_id = tenant_id
        return token.access_token

    async def _async_invalidate(self, access_token: str) -> None:
        """Force renewal of ``access_token``, keeping its refresh token."""
        async with self._token_lock:
            if self._token is not None and self._token.access_token == access_token:
                self._token.expires_at = 0.0

    async def _async_token_request(self, form: dict[str, str]) -> _Token:
        data = {"client_id": CLIENT_ID, "scope": AUTH_SCOPE, **form}
        try:
            async with self._session.post(
                TOKEN_URL, data=data, timeout=REQUEST_TIMEOUT
            ) as response:
                status = response.status
                retry_after = _retry_after(response)
                payload = await _read_json(response)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SensateApiError(
                f"Could not reach the Kohler sign-in service: {_describe(err)}"
            ) from err

        if status == 200 and isinstance(payload, dict) and payload.get("access_token"):
            _LOGGER.debug("Obtained Kohler access token (%s)", form["grant_type"])
            return _token_from_response(payload)
        if status in (400, 401, 403):
            raise SensateAuthError(f"Kohler sign-in rejected: {_b2c_error(payload)}")
        raise SensateApiError(
            f"Kohler sign-in service returned HTTP {status}",
            status=status,
            retry_after=retry_after,
        )

    # --- requests -----------------------------------------------------------

    async def _async_request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        *,
        retry_on_401: bool = True,
    ) -> Any:
        token = await self._async_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Ocp-Apim-Subscription-Key": APIM_KEY,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        try:
            async with self._session.request(
                method,
                f"{API_BASE}{path}",
                headers=headers,
                json=json_body,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                status = response.status
                retry_after = _retry_after(response)
                payload = await _read_json(response)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SensateApiError(
                f"Could not reach the Kohler cloud: {_describe(err)}"
            ) from err

        _LOGGER.debug("%s %s -> HTTP %s", method, self._redact(path), status)
        if status == 401 and retry_on_401:
            await self._async_invalidate(token)
            return await self._async_request(
                method, path, json_body, retry_on_401=False
            )
        if status >= 400:
            raise SensateApiError(
                # Kohler's message could echo the account id back.
                f"Kohler cloud returned HTTP {status}"
                f"{self._redact(_api_error_detail(payload))}",
                status=status,
                retry_after=retry_after,
            )
        return payload

    def _redact(self, path: str) -> str:
        return path.replace(self.tenant_id, "<account>") if self.tenant_id else path

    async def _async_get_dict(self, path: str) -> dict[str, Any]:
        payload = await self._async_request("GET", path)
        if not isinstance(payload, dict):
            raise SensateResponseError("Unexpected response from the Kohler cloud")
        return payload

    # --- faucet API ---------------------------------------------------------

    async def async_get_faucets(self) -> list[SensateDevice]:
        """Return every Sensate faucet on the account."""
        await self.async_login()
        data = await self._async_get_dict(
            API_CUSTOMER_DEVICES.format(tenant_id=self.tenant_id)
        )
        faucets: list[SensateDevice] = []
        for home in data.get("customerHome") or []:
            for device in home.get("devices") or []:
                device_id = device.get("deviceId")
                if device_id and str(device.get("sku") or "").upper() in FAUCET_SKUS:
                    faucets.append(
                        SensateDevice(
                            device_id=device_id,
                            name=device.get("logicalName") or "Sensate",
                        )
                    )
        return faucets

    async def async_get_state(self, device_id: str) -> FaucetSnapshot:
        """Return the live state (status/progress/handle/quantity) and connection."""
        data = await self._async_get_dict(API_FAUCET_STATE.format(device_id=device_id))
        state = data.get("state")
        if not isinstance(state, dict):
            raise SensateResponseError("Kohler's faucet state has no 'state' object")
        connection = data.get("connectionState")
        return FaucetSnapshot(
            state=state,
            connection_state=connection if isinstance(connection, str) else None,
            last_connected=data.get("lastConnected"),
        )

    async def async_get_config(self, device_id: str) -> dict[str, Any]:
        """Return the faucet configuration (firmware, leak history)."""
        return await self._async_get_dict(API_FAUCET_CONFIG.format(device_id=device_id))

    async def async_get_presets(self, device_id: str) -> list[SensatePreset]:
        """Return the faucet's Konnect presets; none saved answers HTTP 404."""
        try:
            payload = await self._async_request(
                "GET", API_FAUCET_PRESETS.format(device_id=device_id)
            )
        except SensateApiError as err:
            if err.status == 404:
                return []
            raise
        presets = parse_presets(payload)
        _LOGGER.debug("Konnect presets: %s", presets)
        return presets

    async def async_register_push(self, identity: str) -> PushSettings:
        """Register this client for instant updates; returns IoT Hub credentials.

        Reusing ``identity`` keeps one registration on the account instead of
        adding one per connect.
        """
        await self.async_login()
        data = await self._async_request(
            "POST",
            API_MOBILE_SETTINGS,
            {
                "tenantId": self.tenant_id,
                "mobileDeviceId": identity,
                "username": "HomeAssistant",
                "os": "Android",
                "devicePlatform": "FirebaseCloudMessagingV1",
                "deviceHandle": f"ha_{identity}",
                "tags": ["FirmwareUpdate"],
            },
        )
        settings = data.get("ioTHubSettings") if isinstance(data, dict) else None
        if not isinstance(settings, dict) or not all(
            isinstance(settings.get(key), str) and settings[key]
            for key in ("ioTHub", "deviceId", "username", "password")
        ):
            raise SensateResponseError("Kohler returned no IoT Hub settings")
        return PushSettings(
            host=settings["ioTHub"],
            client_id=settings["deviceId"],
            username=settings["username"],
            password=settings["password"],
        )

    async def async_dispense(self, device_id: str, liters: float) -> None:
        """Dispense a measured amount of water. The API unit is liters."""
        await self.async_login()
        await self._async_request(
            "POST",
            CMD_DISPENSE,
            {
                "deviceId": device_id,
                "quantity": round(liters, 4),
                "sku": SKU,
                "tenantId": self.tenant_id,
            },
        )

    async def async_set_water(self, device_id: str, on: bool) -> None:
        """Turn the water on or off."""
        await self.async_login()
        await self._async_request(
            "POST",
            CMD_ONOFF,
            {
                "action": "ON" if on else "OFF",
                "deviceId": device_id,
                "sku": SKU,
                "tenantId": self.tenant_id,
            },
        )
