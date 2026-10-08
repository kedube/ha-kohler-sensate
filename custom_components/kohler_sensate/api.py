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
from datetime import date
from email.utils import parsedate_to_datetime
import json
import logging
import re
import time
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    API_CUSTOMER_DEVICES,
    API_CUSTOMER_EXPERIENCE,
    API_FAUCET_CONFIG,
    API_FAUCET_EXPERIENCE,
    API_FAUCET_STATE,
    API_FAUCET_USAGE,
    API_FIRMWARE,
    API_MOBILE_SETTINGS,
    APIM_KEY,
    AUTH_SCOPE,
    CLIENT_ID,
    CMD_DISPENSE,
    CMD_ONOFF,
    COMMAND_FAILURES,
    COMMAND_PREFIX,
    DEFAULT_SKU,
    FAUCET_SKUS,
    STATUS_MESSAGES,
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
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        # Seconds Kohler asked us to wait (HTTP 429/503 Retry-After).
        self.retry_after = retry_after
        # Kohler's own statusCode from the reply body, such as "906".
        self.code = code

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
    sku: str = DEFAULT_SKU


@dataclass(frozen=True, slots=True)
class FaucetSnapshot:
    """The faucet's live state plus whether it's connected to Kohler."""

    state: dict[str, Any]
    connection_state: str | None
    last_connected: Any
    sku: str | None = None


@dataclass(frozen=True, slots=True)
class SensatePreset:
    """A dispense preset saved in the Konnect app."""

    preset_id: str
    title: str
    liters: float


@dataclass(frozen=True, slots=True)
class FirmwareInfo:
    """Kohler's answer to "is there newer firmware for this faucet?"."""

    available: bool
    latest: str | None
    current: str | None
    mandatory: bool


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


# The Konnect app's preset units, in liters per unit; it converts in single
# precision, hence the cup factor.
_DISPLAY_UNITS = {
    "milliliters": 0.001,
    "liters": 1.0,
    "cups": 0.2365880012512207,
    "quarts": 0.946353,
    "gallons": 3.785411784,
}
_FRACTIONS = {"¼": 0.25, "½": 0.5, "¾": 0.75}
_DISPLAY_QUANTITY = re.compile(r"^\s*(\d+(?:\.\d+)?)?\s*([¼½¾])?\s+([A-Za-z]+)\s*$")


def parse_display_quantity(text: Any) -> float | None:
    """Liters from a preset's ``displayQuantity``, such as "1¾ Quarts"."""
    if not isinstance(text, str) or not (match := _DISPLAY_QUANTITY.match(text)):
        return None
    whole, fraction, unit = match.groups()
    factor = _DISPLAY_UNITS.get(unit.lower())
    if factor is None or (whole is None and fraction is None):
        return None
    amount = float(whole or 0) + _FRACTIONS.get(fraction or "", 0.0)
    return amount * factor if amount > 0 else None


def _parse_preset_items(items: list[Any], device_id: str | None) -> list[SensatePreset]:
    """Presets from Konnect experience entries; anything malformed is skipped.

    ``dispenseAmount`` is in liters; ``displayQuantity`` stands in if it's
    missing. With ``device_id``, entries for other devices are skipped.
    """
    presets: list[SensatePreset] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if (
            device_id is not None
            and str(item.get("deviceId") or "").lower() != device_id.lower()
        ):
            continue
        preset_id = item.get("experienceId")
        title = item.get("title")
        amount = item.get("dispenseAmount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            amount = parse_display_quantity(item.get("displayQuantity"))
        if preset_id in (None, "") or not title or amount is None or amount <= 0:
            continue
        presets.append(SensatePreset(str(preset_id), str(title), float(amount)))
    return presets


def parse_presets(payload: Any, device_id: str) -> list[SensatePreset]:
    """Read one faucet's Konnect presets from ``customer-experience``.

    The reply lists the presets of every device on the account; the Sensate's
    are in ``sensateExperiences``.
    """
    items = payload.get("sensateExperiences") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return _parse_preset_items(items, device_id)


def parse_faucet_experiences(
    payload: Any, device_id: str
) -> list[SensatePreset] | None:
    """Read one faucet's presets from ``faucet-experience?DeviceIds=``.

    The reply is ``{faucetExperienceList: [{deviceId, experience: [...]}]}``.
    Returns None if it isn't shaped like that.
    """
    groups = payload.get("faucetExperienceList") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        return None
    groups = [group for group in groups if isinstance(group, dict)]
    # The app reads the first entry; prefer one that names this faucet.
    group = next(
        (
            group
            for group in groups
            if str(group.get("deviceId") or "").lower() == device_id.lower()
        ),
        groups[0] if groups else None,
    )
    if group is None:
        return []
    items = group.get("experience")
    if not isinstance(items, list):
        return None
    return _parse_preset_items(items, None)


def parse_firmware(payload: Any) -> FirmwareInfo | None:
    """Read the firmware check; None if it doesn't say whether one is available."""
    if not isinstance(payload, dict) or not isinstance(
        available := payload.get("firmwareUpdateAvailable"), bool
    ):
        return None

    def text(key: str) -> str | None:
        value = payload.get(key)
        return str(value) if value not in (None, "") else None

    return FirmwareInfo(
        available=available,
        latest=text("firmware"),
        current=text("currentFirmware"),
        mandatory=payload.get("mandatoryUpdate") is True,
    )


def parse_usage(payload: Any) -> dict[str, float]:
    """Read ``faucet-usage`` into liters per period, keyed like "2026-10-07"."""
    items = (
        payload.get("faucetUsageDataDetailsList") if isinstance(payload, dict) else None
    )
    if not isinstance(items, list):
        raise SensateResponseError("Unexpected water usage reply from Kohler")
    usage: dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(
            key := item.get("intervalKey"), str
        ):
            continue
        liters = item.get("waterUsage", item.get("quantity"))
        if isinstance(liters, (int, float)) and not isinstance(liters, bool):
            usage[key] = usage.get(key, 0.0) + max(0.0, float(liters))
    return usage


def _api_error_detail(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message", "title", "error"):
            if isinstance(value := payload.get(key), str) and value:
                return f": {value[:120]}"
    return ""


def _status_code(payload: Any) -> str | None:
    """Kohler's in-body ``statusCode``, as text: Konnect models it as a string."""
    raw = payload.get("statusCode") if isinstance(payload, dict) else None
    if raw is None or isinstance(raw, bool):
        return None
    return str(raw).strip() or None


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
        params: dict[str, str] | None = None,
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
                params=params,
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
                method, path, json_body, params=params, retry_on_401=False
            )
        code = _status_code(payload)
        # A command can be refused in the body of an HTTP 200, and then did
        # nothing. Reads are taken as they come.
        refused = path.startswith(COMMAND_PREFIX) and code in COMMAND_FAILURES
        if status >= 400 or refused:
            meaning = STATUS_MESSAGES.get(code or "")
            raise SensateApiError(
                # Kohler's message could echo the account id back.
                f"Kohler cloud returned HTTP {status}"
                + (
                    f": {meaning} (statusCode {code})"
                    if meaning
                    else self._redact(_api_error_detail(payload))
                ),
                status=status,
                retry_after=retry_after,
                code=code,
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
                sku = str(device.get("sku") or "").upper()
                if device_id and sku in FAUCET_SKUS:
                    faucets.append(
                        SensateDevice(
                            device_id=device_id,
                            name=device.get("logicalName") or "Sensate",
                            sku=sku,
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
        sku = data.get("sku")
        return FaucetSnapshot(
            state=state,
            connection_state=connection if isinstance(connection, str) else None,
            last_connected=data.get("lastConnected"),
            sku=sku.upper() if isinstance(sku, str) and sku else None,
        )

    async def async_get_config(self, device_id: str) -> dict[str, Any]:
        """Return the faucet configuration (firmware, leak history)."""
        return await self._async_get_dict(API_FAUCET_CONFIG.format(device_id=device_id))

    async def async_get_presets(
        self, device_id: str
    ) -> tuple[list[SensatePreset], str]:
        """Return the faucet's Konnect presets, and which list they came from.

        The faucet's own list (``faucet-experience``) is what the app's faucet
        screen reads. The account-wide ``customer-experience`` list stands in
        if that fails or is empty.
        """
        await self.async_login()
        try:
            presets = parse_faucet_experiences(
                await self._async_request(
                    "GET", API_FAUCET_EXPERIENCE, params={"DeviceIds": device_id}
                ),
                device_id,
            )
        except SensateAuthError:
            raise
        except SensateApiError as err:
            _LOGGER.debug("Faucet preset list unavailable: %s", err)
            presets = None
        if presets:
            _LOGGER.debug("Konnect presets (faucet-experience): %s", presets)
            return presets, "faucet-experience"
        payload = await self._async_request(
            "GET", API_CUSTOMER_EXPERIENCE.format(tenant_id=self.tenant_id)
        )
        presets = parse_presets(payload, device_id)
        _LOGGER.debug("Konnect presets (customer-experience): %s", presets)
        return presets, "customer-experience"

    async def async_get_firmware(self, device_id: str) -> FirmwareInfo | None:
        """Ask Kohler whether newer firmware is available for the faucet."""
        return parse_firmware(
            await self._async_request("GET", API_FIRMWARE.format(device_id=device_id))
        )

    async def async_get_usage(
        self, device_id: str, start: date, end: date, interval: str
    ) -> dict[str, float]:
        """Return liters used per ``interval`` ("DAY" or "MONTH"), both ends included.

        The parameter names are PascalCase, unlike the rest of Kohler's API.
        """
        payload = await self._async_request(
            "GET",
            API_FAUCET_USAGE.format(device_id=device_id),
            params={
                "FromDate": start.isoformat(),
                "ToDate": end.isoformat(),
                "Interval": interval,
            },
        )
        return parse_usage(payload)

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

    async def async_unregister_push(self, identity: str) -> None:
        """Remove this client's instant-updates registration from the account."""
        await self.async_login()
        await self._async_request(
            "DELETE", f"{API_MOBILE_SETTINGS}/{self.tenant_id}/{identity}"
        )

    async def async_dispense(
        self, device_id: str, liters: float, sku: str = DEFAULT_SKU
    ) -> None:
        """Dispense a measured amount of water. The API unit is liters."""
        await self.async_login()
        await self._async_request(
            "POST",
            CMD_DISPENSE,
            {
                "deviceId": device_id,
                "quantity": round(liters, 4),
                "sku": sku,
                "tenantId": self.tenant_id,
            },
        )

    async def async_set_water(
        self, device_id: str, on: bool, sku: str = DEFAULT_SKU
    ) -> None:
        """Turn the water on or off."""
        await self.async_login()
        await self._async_request(
            "POST",
            CMD_ONOFF,
            {
                "action": "ON" if on else "OFF",
                "deviceId": device_id,
                "sku": sku,
                "tenantId": self.tenant_id,
            },
        )
