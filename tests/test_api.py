"""Tests for the Kohler cloud client: token lifecycle, errors and logging."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.kohler_sensate.api import (
    SensateApi,
    SensateApiError,
    SensateAuthError,
    decode_tenant_id,
)
from custom_components.kohler_sensate.const import AUTH_SCOPE, CLIENT_ID

from .conftest import DEVICE_ID, PASSWORD, TENANT_ID, USERNAME, FakeKohler, make_jwt


@pytest.fixture
def api(hass: HomeAssistant, kohler: FakeKohler) -> SensateApi:
    return SensateApi(async_get_clientsession(hass), USERNAME, PASSWORD)


def _expire(api: SensateApi) -> None:
    assert api._token is not None
    api._token.expires_at = 0.0


async def test_password_sign_in(api: SensateApi, kohler: FakeKohler) -> None:
    await api.async_login()

    assert kohler.token_requests == [
        {
            "client_id": CLIENT_ID,
            "scope": AUTH_SCOPE,
            "grant_type": "password",
            "username": USERNAME,
            "password": PASSWORD,
        }
    ]
    assert api.tenant_id == TENANT_ID


async def test_token_reused_while_valid(api: SensateApi, kohler: FakeKohler) -> None:
    await api.async_get_state(DEVICE_ID)
    await api.async_get_state(DEVICE_ID)
    assert len(kohler.token_requests) == 1


async def test_expired_token_renewed_with_refresh_token(
    api: SensateApi, kohler: FakeKohler
) -> None:
    await api.async_get_state(DEVICE_ID)
    _expire(api)
    await api.async_get_state(DEVICE_ID)

    assert kohler.token_requests[1]["grant_type"] == "refresh_token"
    assert kohler.token_requests[1]["refresh_token"] == "refresh-1"
    assert "password" not in kohler.token_requests[1]
    assert kohler.auth_headers()[-1] == f"Bearer {kohler.access_tokens[1]}"


async def test_rejected_refresh_token_falls_back_to_password(
    api: SensateApi, kohler: FakeKohler
) -> None:
    await api.async_login()
    _expire(api)
    kohler.token_queue.append(
        (400, {"error": "invalid_grant", "error_description": "AADB2C90080: expired"})
    )

    await api.async_get_state(DEVICE_ID)

    grants = [r["grant_type"] for r in kohler.token_requests]
    assert grants == ["password", "refresh_token", "password"]


async def test_refresh_network_error_is_transient(
    api: SensateApi, kohler: FakeKohler
) -> None:
    """A network blip while renewing must not look like a bad password."""
    await api.async_login()
    _expire(api)
    kohler.token_queue.append(kohler.network_error())

    with pytest.raises(SensateApiError) as err:
        await api.async_get_state(DEVICE_ID)
    assert not isinstance(err.value, SensateAuthError)

    # The refresh token is kept and used on the next attempt.
    await api.async_get_state(DEVICE_ID)
    assert kohler.token_requests[-1]["grant_type"] == "refresh_token"


async def test_invalid_password(api: SensateApi, kohler: FakeKohler) -> None:
    kohler.token_queue.append(
        (
            400,
            {
                "error": "access_denied",
                "error_description": (
                    "AADB2C90225: The username or password provided in the request "
                    "are invalid.\r\nCorrelation ID: abc\r\nTimestamp: now"
                ),
            },
        )
    )
    with pytest.raises(SensateAuthError, match="AADB2C90225") as err:
        await api.async_login()
    assert "Correlation" not in str(err.value)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_sign_in_server_errors_are_transient(
    api: SensateApi, kohler: FakeKohler, status: int
) -> None:
    kohler.token_queue.append((status, "<html>busy</html>"))
    with pytest.raises(SensateApiError) as err:
        await api.async_login()
    assert not isinstance(err.value, SensateAuthError)


async def test_sign_in_timeout_is_transient(
    api: SensateApi, kohler: FakeKohler
) -> None:
    kohler.token_queue.append(TimeoutError())
    with pytest.raises(SensateApiError, match="TimeoutError"):
        await api.async_login()


async def test_api_401_renews_token_and_retries_once(
    api: SensateApi, kohler: FakeKohler
) -> None:
    await api.async_login()
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (401, {"message": "expired"}))

    snapshot = await api.async_get_state(DEVICE_ID)

    assert snapshot.state["status"] == "Off"
    assert [r["grant_type"] for r in kohler.token_requests] == [
        "password",
        "refresh_token",
    ]
    assert kohler.auth_headers()[-1] == f"Bearer {kohler.access_tokens[1]}"


async def test_api_repeated_401_is_an_api_error(
    api: SensateApi, kohler: FakeKohler
) -> None:
    """A fresh token being refused is not fixable by re-entering the password."""
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (401, {}), (401, {}))
    with pytest.raises(SensateApiError, match="HTTP 401") as err:
        await api.async_get_state(DEVICE_ID)
    assert not isinstance(err.value, SensateAuthError)


async def test_api_error_message_redacts_account_id(
    api: SensateApi, kohler: FakeKohler
) -> None:
    kohler.fail_api(
        f"/faucet-state/{DEVICE_ID}", (403, {"message": f"tenant {TENANT_ID} denied"})
    )
    with pytest.raises(SensateApiError) as err:
        await api.async_get_state(DEVICE_ID)
    assert str(err.value) == "Kohler cloud returned HTTP 403: tenant <account> denied"


async def test_api_non_json_error(api: SensateApi, kohler: FakeKohler) -> None:
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", (502, "<html>Bad gateway</html>"))
    with pytest.raises(SensateApiError, match="HTTP 502"):
        await api.async_get_state(DEVICE_ID)


async def test_api_network_error(api: SensateApi, kohler: FakeKohler) -> None:
    kohler.fail_api(f"/faucet-state/{DEVICE_ID}", kohler.network_error())
    with pytest.raises(SensateApiError, match="connection reset"):
        await api.async_get_state(DEVICE_ID)


async def test_concurrent_requests_share_one_sign_in(
    api: SensateApi, kohler: FakeKohler
) -> None:
    await asyncio.gather(*(api.async_get_state(DEVICE_ID) for _ in range(5)))
    assert len(kohler.token_requests) == 1


async def test_get_faucets_filters_by_sku(api: SensateApi) -> None:
    faucets = await api.async_get_faucets()
    assert [(f.device_id, f.name) for f in faucets] == [(DEVICE_ID, "Kitchen")]


async def test_commands(api: SensateApi, kohler: FakeKohler) -> None:
    await api.async_dispense(DEVICE_ID, 0.236588237)
    await api.async_set_water(DEVICE_ID, True)
    await api.async_set_water(DEVICE_ID, False)

    assert kohler.commands == [
        (
            "dispense",
            {
                "deviceId": DEVICE_ID,
                "quantity": 0.2366,
                "sku": "SEN",
                "tenantId": TENANT_ID,
            },
        ),
        (
            "onoff",
            {
                "action": "ON",
                "deviceId": DEVICE_ID,
                "sku": "SEN",
                "tenantId": TENANT_ID,
            },
        ),
        (
            "onoff",
            {
                "action": "OFF",
                "deviceId": DEVICE_ID,
                "sku": "SEN",
                "tenantId": TENANT_ID,
            },
        ),
    ]


async def test_secrets_never_logged(
    api: SensateApi, kohler: FakeKohler, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await api.async_get_faucets()
    await api.async_dispense(DEVICE_ID, 0.25)
    _expire(api)
    kohler.token_queue.append((400, {"error": "invalid_grant"}))
    await api.async_get_state(DEVICE_ID)
    kohler.token_queue.append((400, {"error": "access_denied"}))
    _expire(api)
    api._token.refresh_token = None  # force a password sign-in that fails
    with pytest.raises(SensateAuthError) as err:
        await api.async_get_state(DEVICE_ID)

    assert caplog.text  # debug logging did happen
    for secret in (PASSWORD, USERNAME, TENANT_ID, "refresh-1", *kohler.access_tokens):
        assert secret not in caplog.text
        assert secret not in str(err.value)
    assert "1 Main St" not in caplog.text  # account payloads are not logged


def test_decode_tenant_id() -> None:
    assert decode_tenant_id(make_jwt({"oid": "abc"})) == "abc"
    assert decode_tenant_id(make_jwt({"sub": "xyz"})) == "xyz"
    assert decode_tenant_id("not-a-jwt") is None
    assert decode_tenant_id("a.!!!.c") is None
    assert decode_tenant_id(f"a.{'W10'}.c") is None  # JSON list, not claims
