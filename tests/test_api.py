"""Tests for the Kohler cloud client: token lifecycle, errors and logging."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.kohler_sensate.api import (
    FirmwareInfo,
    SensateApi,
    SensateApiError,
    SensateAuthError,
    decode_tenant_id,
    parse_display_quantity,
    parse_firmware,
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


async def test_get_faucets_includes_both_konnect_faucets(
    api: SensateApi, kohler: FakeKohler
) -> None:
    kohler.devices += [
        {"deviceId": "set-1", "sku": "SET", "logicalName": "Bar"},
        # Not a Kohler SKU; the app has no such device.
        {"deviceId": "faucet-1", "sku": "FAUCET", "logicalName": "Other"},
    ]
    faucets = await api.async_get_faucets()
    assert [(f.device_id, f.sku) for f in faucets] == [
        (DEVICE_ID, "SEN"),
        ("set-1", "SET"),
    ]


async def test_commands_send_the_device_sku(
    api: SensateApi, kohler: FakeKohler
) -> None:
    await api.async_dispense("set-1", 0.25, "SET")
    await api.async_set_water("set-1", False, "SET")
    assert [body["sku"] for _, body in kohler.commands] == ["SET", "SET"]


@pytest.mark.parametrize("code", ["906", 906, "900", " 903 "])
async def test_command_refused_in_a_200_reply(
    api: SensateApi, kohler: FakeKohler, code: object
) -> None:
    """HTTP 200 with a failure statusCode means the command did nothing."""
    kohler.fail_api("/faucet/dispense", (200, {"statusCode": code, "message": "x"}))
    with pytest.raises(SensateApiError) as err:
        await api.async_dispense(DEVICE_ID, 0.25)
    assert err.value.code == str(code).strip()
    assert err.value.status == 200


async def test_command_refusal_is_explained(
    api: SensateApi, kohler: FakeKohler
) -> None:
    kohler.fail_api(
        "/faucet/dispense",
        (400, {"statusCode": "906", "message": "Something went wrong"}),
    )
    with pytest.raises(SensateApiError) as err:
        await api.async_dispense(DEVICE_ID, 0.25)
    assert err.value.code == "906"
    assert "water could not be dispensed (statusCode 906)" in str(err.value)


async def test_harmless_codes_and_reads_are_not_refusals(
    api: SensateApi, kohler: FakeKohler
) -> None:
    kohler.fail_api("/faucet/onoff", (200, {"statusCode": "200", "correlationId": "c"}))
    await api.async_set_water(DEVICE_ID, False)
    # Reads are taken as they come, whatever their body says.
    kohler.fail_api(
        f"/faucet-state/{DEVICE_ID}",
        (200, {"statusCode": "900", "state": {"status": "Off"}}),
    )
    assert (await api.async_get_state(DEVICE_ID)).state == {"status": "Off"}


async def test_state_reports_the_sku(api: SensateApi, kohler: FakeKohler) -> None:
    kohler.state_sku = "set"
    assert (await api.async_get_state(DEVICE_ID)).sku == "SET"
    kohler.state_sku = None
    assert (await api.async_get_state(DEVICE_ID)).sku is None


async def test_unregister_push(api: SensateApi, kohler: FakeKohler) -> None:
    await api.async_unregister_push("ha-identity")
    assert kohler.unregistered == ["ha-identity"]


@pytest.mark.parametrize(
    ("text", "liters"),
    [
        ("750 Milliliters", 0.75),
        ("¾ Liters", 0.75),
        ("1¾ Quarts", 1.75 * 0.946353),
        ("1 Cups", 0.2365880012512207),
        ("3 Gallons", 3 * 3.785411784),
        ("1.5 liters", 1.5),
        ("Cups", None),
        ("0 Liters", None),
        ("2 Pints", None),
        (None, None),
    ],
)
def test_parse_display_quantity(text: object, liters: float | None) -> None:
    assert parse_display_quantity(text) == (
        pytest.approx(liters) if liters is not None else None
    )


def test_parse_firmware() -> None:
    assert parse_firmware(
        {
            "firmwareUpdateAvailable": True,
            "firmware": "17.1",
            "currentFirmware": 16.0,
            "mandatoryUpdate": True,
            "otaStatus": "NotStarted",
        }
    ) == FirmwareInfo(available=True, latest="17.1", current="16.0", mandatory=True)
    assert parse_firmware({"firmwareUpdateAvailable": False, "firmware": ""}) == (
        FirmwareInfo(available=False, latest=None, current=None, mandatory=False)
    )
    # Without a clear yes or no, the answer is unknown.
    assert parse_firmware({"firmwareUpdateAvailable": "true"}) is None
    assert parse_firmware(["junk"]) is None


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
    await api.async_unregister_push("ha-identity")
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
