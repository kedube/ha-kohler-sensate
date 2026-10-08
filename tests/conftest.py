"""Fixtures for Kohler Sensate tests.

``FakeKohler`` stands in for the B2C token endpoint and the Konnect API at the
HTTP level, so the real client in ``api.py`` is exercised end to end.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Generator
from datetime import timedelta
import json
import re
import time
from typing import Any, ClassVar
from unittest.mock import patch

from aiohttp import ClientError
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.kohler_sensate.const import (
    API_BASE,
    CONF_DEVICE_ID,
    CONF_UNIT_SYSTEM,
    DOMAIN,
    TOKEN_URL,
    UNIT_SYSTEM_METRIC,
)

DEVICE_ID = "sen-test123456"
TENANT_ID = "0f0f0f0f-1111-2222-3333-444444444444"
USERNAME = "owner@example.com"
PASSWORD = "correct-horse-battery-staple"


def make_jwt(claims: dict[str, Any]) -> str:
    def b64(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.signature"


class FakeKohler:
    """Programmable fake of Kohler's token endpoint and cloud API."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self.mocker = mocker
        self.issued = 0
        self.token_requests: list[dict[str, str]] = []
        # Queued token responses: (status, json) tuples or exceptions.
        self.token_queue: list[Any] = []
        # Account ids of other accounts, by email; others sign in as TENANT_ID.
        self.tenants: dict[str, str] = {}
        self._refresh_tenants: dict[str, str] = {}
        # Queued API responses, keyed by path suffix: (status, json) or exceptions.
        self.api_queue: dict[str, list[Any]] = {}
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.devices = [
            {"deviceId": DEVICE_ID, "sku": "SEN", "logicalName": "Kitchen"},
            {"deviceId": "gcs-shower", "sku": "GCS", "logicalName": "Shower"},
        ]
        self.state: dict[str, Any] = {
            "status": "Off",
            "progress": "NotStarted",
            "handleState": "OPEN",
            "quantity": None,
        }
        # Shaped like a real Sensate's reply (firmware 16.0), which repeats
        # the device id under "id".
        self.config: dict[str, Any] = {
            "id": DEVICE_ID,
            "deviceId": DEVICE_ID,
            "configuration": {
                "about": {
                    "name": "SENSATE",
                    "model": "SEN",
                    "serialNumber": "SN-SECRET-1",
                    "firmware": {"version": "16.0", "latestVersion": "16.0"},
                    "hardware": "CC3235SF",
                }
            },
            "leakDetectionHistory": [],
        }
        # None leaves connectionState out of the reply.
        self.connection: str | None = "Connected"
        # The SKU in the faucet-state reply; None leaves it out.
        self.state_sku: str | None = "SEN"
        # This faucet's entries in customer-experience's "sensateExperiences".
        self.presets: list[dict[str, Any]] = []
        # Its entries in faucet-experience?DeviceIds=; None answers 404, as
        # the app-only route did before it was known.
        self.faucet_presets: list[dict[str, Any]] | None = None
        # The firmware check's reply; None answers 404.
        self.firmware: dict[str, Any] | None = None
        # Instant-updates registrations removed with DELETE, by identity.
        self.unregistered: list[str] = []
        # Liters per period, as faucet-usage reports them.
        self.usage_months: dict[str, float] = {"2025-04": 1.593, "2026-10": 1.6144}
        self.usage_days: dict[str, float] = {"2026-10-07": 1.5274}
        self.push_registrations: list[dict[str, Any]] = []
        mocker.post(TOKEN_URL, side_effect=self._token)
        mocker.request("get", re.compile(re.escape(API_BASE)), side_effect=self._api)
        mocker.request("post", re.compile(re.escape(API_BASE)), side_effect=self._api)
        mocker.request("delete", re.compile(re.escape(API_BASE)), side_effect=self._api)

    @property
    def access_tokens(self) -> list[str]:
        return [make_jwt({"oid": TENANT_ID, "n": n}) for n in range(1, self.issued + 1)]

    def _respond(self, method: str, url: Any, item: Any) -> AiohttpClientMockResponse:
        if isinstance(item, BaseException):
            return AiohttpClientMockResponse(method, url, exc=item)
        status, body, *rest = item
        headers = rest[0] if rest else None
        if isinstance(body, str):
            return AiohttpClientMockResponse(
                method, url, status=status, text=body, headers=headers
            )
        return AiohttpClientMockResponse(
            method, url, status=status, json=body, headers=headers
        )

    async def _token(
        self, method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        self.token_requests.append(dict(data))
        if self.token_queue:
            return self._respond(method, url, self.token_queue.pop(0))
        self.issued += 1
        form = dict(data)
        if form.get("grant_type") == "refresh_token":
            tenant = self._refresh_tenants.get(form.get("refresh_token", ""), TENANT_ID)
        else:
            tenant = self.tenants.get(form.get("username", ""), TENANT_ID)
        refresh = f"refresh-{self.issued}"
        self._refresh_tenants[refresh] = tenant
        return self._respond(
            method,
            url,
            (
                200,
                {
                    "access_token": make_jwt({"oid": tenant, "n": self.issued}),
                    "refresh_token": refresh,
                    "expires_in": "3600",
                },
            ),
        )

    async def _api(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        path = url.path
        for suffix, queue in self.api_queue.items():
            if path.endswith(suffix) and queue:
                return self._respond(method, url, queue.pop(0))
        if method.lower() == "delete":
            if "/mobile/settings/" in path:
                self.unregistered.append(path.rsplit("/", 1)[-1])
                return self._respond(method, url, (200, {}))
            return self._respond(method, url, (404, {"message": "not found"}))
        if path.endswith("/mobile/settings"):
            self.push_registrations.append(data)
            n = len(self.push_registrations)
            return self._respond(
                method,
                url,
                (
                    200,
                    {
                        "ioTHubSettings": {
                            "ioTHub": "hub.example.net",
                            "deviceId": f"mobile-{data['mobileDeviceId']}",
                            "username": "hub.example.net/user",
                            "password": f"SharedAccessSignature sig-{n}",
                        }
                    },
                ),
            )
        if method.lower() == "post":
            self.commands.append((path.rsplit("/", 1)[-1], data))
            return self._respond(
                method, url, (200, {"correlationId": "c", "timestamp": 1})
            )
        if "/customer-device/" in path:
            return self._respond(
                method,
                url,
                (
                    200,
                    {
                        "customerHome": [
                            {"address": "1 Main St", "devices": self.devices}
                        ]
                    },
                ),
            )
        if "/faucet-state/" in path:
            body: dict[str, Any] = {"state": dict(self.state)}
            if self.state_sku is not None:
                body["sku"] = self.state_sku
            if self.connection is not None:
                body["connectionState"] = self.connection
                body["lastConnected"] = "2026-10-06T12:00:00Z"
            return self._respond(method, url, (200, body))
        if "/customer-experience/" in path:
            # Presets of every device on the account, a shower's among them.
            body = {
                "experiences": [],
                "sensateExperiences": [
                    {"deviceId": DEVICE_ID, "sku": "SEN", **p} for p in self.presets
                ],
                "gcsExperiences": [
                    {
                        "deviceId": "gcs-shower",
                        "experienceId": "20",
                        "title": "Cool Down",
                    }
                ],
            }
            return self._respond(method, url, (200, body))
        if "/faucet-usage/" in path:
            interval = url.query.get("Interval")
            usage = {"MONTH": self.usage_months, "DAY": self.usage_days}.get(interval)
            if usage is None or not url.query.get("FromDate"):
                return self._respond(method, url, (400, {"message": "Bad Request"}))
            rows = [
                {"intervalKey": key, "quantity": liters, "waterUsage": liters}
                for key, liters in usage.items()
            ]
            return self._respond(
                method, url, (200, {"faucetUsageDataDetailsList": rows})
            )
        if "/faucet-configuration/" in path:
            return self._respond(method, url, (200, self.config))
        if path.endswith("/faucet-experience") and self.faucet_presets is not None:
            if url.query.get("DeviceIds") != DEVICE_ID:
                return self._respond(method, url, (400, {"message": "Bad Request"}))
            group = {"deviceId": DEVICE_ID, "experience": self.faucet_presets}
            return self._respond(method, url, (200, {"faucetExperienceList": [group]}))
        if "/firmware/sensate/" in path and self.firmware is not None:
            return self._respond(method, url, (200, self.firmware))
        return self._respond(method, url, (404, {"message": "not found"}))

    def fail_api(self, suffix: str, *items: Any) -> None:
        self.api_queue.setdefault(suffix, []).extend(items)

    def network_error(self) -> ClientError:
        return ClientError("connection reset")

    @property
    def state_polls(self) -> int:
        return sum(
            1 for _, url, _, _ in self.mocker.mock_calls if "/faucet-state/" in str(url)
        )

    def auth_headers(self) -> list[str]:
        return [
            headers["Authorization"]
            for _, url, _, headers in self.mocker.mock_calls
            if headers and "Authorization" in headers
        ]


def get_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    """Return the faucet device of ``entry`` (works across HA versions)."""
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device


class Reason:
    def __init__(self, failure: bool = False) -> None:
        self.is_failure = failure

    def __str__(self) -> str:
        return "failure" if self.is_failure else "success"


class Message:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class FakeMqttClient:
    """Stands in for paho; connects instantly unless told to refuse."""

    instances: ClassVar[list[FakeMqttClient]] = []
    refuse: ClassVar[bool] = False

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.published: list[tuple[str, bytes]] = []
        self.subscribed: list[str] = []
        self.stopped = False
        FakeMqttClient.instances.append(self)

    def username_pw_set(self, username: str, password: str) -> None:
        self.username, self.password = username, password

    def tls_set_context(self, context: Any) -> None:
        self.tls = context

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        self.host, self.port = host, port

    def loop_start(self) -> None:
        self.on_connect(self, None, None, Reason(FakeMqttClient.refuse), None)

    def subscribe(self, topic: str, qos: int) -> None:
        self.subscribed.append(topic)
        self.on_subscribe(self, None, 1, [Reason()], None)

    def publish(self, topic: str, payload: bytes, qos: int) -> None:
        self.published.append((topic, payload))

    def disconnect(self) -> None:
        pass

    def loop_stop(self) -> None:
        # Real paho joins its network thread here; take real time like a slow
        # CI runner would, so tests can't depend on it finishing instantly.
        time.sleep(0.05)
        self.stopped = True

    # helpers for tests
    def deliver(self, payload: dict[str, Any], rid: str = "1") -> None:
        self.on_message(
            self,
            None,
            Message(
                f"$iothub/methods/POST/statusUpdate/?$rid={rid}",
                json.dumps(payload).encode(),
            ),
        )

    def drop(self) -> None:
        self.on_disconnect(self, None, None, Reason(True), None)


def feed_message(code: str, **attributes: Any) -> dict[str, Any]:
    """A feed message shaped like those a real Sensate sends."""
    return {
        "sysid": "SEN-TEST",
        "deviceid": DEVICE_ID,
        "tenantid": TENANT_ID,
        "sku": "SEN",
        "type": "STS",
        "timestamp": "1791397638",
        "data": {
            "type": "Status",
            "code": code,
            "attributes": [{"code": code, **attributes}],
        },
    }


def water_message(status: str, handle: str = "OPEN") -> dict[str, Any]:
    return feed_message("SENSATE_STS", status=status, handle=handle)


def preset_message(name: str, status: str) -> dict[str, Any]:
    return feed_message(
        "SENSATE_EXP_STS", name=name, experienceid="exp-1", status=status
    )


@pytest.fixture(autouse=True)
def fake_mqtt() -> Generator[type[FakeMqttClient]]:
    """Instant updates are on by default; never open a real connection."""
    FakeMqttClient.instances = []
    FakeMqttClient.refuse = False
    with patch("custom_components.kohler_sensate.push.mqtt.Client", FakeMqttClient):
        yield FakeMqttClient


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    yield


@pytest.fixture
def kohler(aioclient_mock: AiohttpClientMocker) -> FakeKohler:
    return FakeKohler(aioclient_mock)


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen",
        unique_id=DEVICE_ID,
        data={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_DEVICE_ID: DEVICE_ID,
        },
        options={CONF_UNIT_SYSTEM: UNIT_SYSTEM_METRIC},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, kohler: FakeKohler
) -> MockConfigEntry:
    """The faucet, set up and connected to the (fake) instant-updates feed.

    Instant updates are always on. Until a message about the faucet arrives,
    polling runs at its usual pace, as it would without them.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    push = config_entry.runtime_data.push
    await wait_for(hass, lambda: push.connected, "the first connection")
    # Connecting re-reads the faucet; let that re-read's 1 s cooldown pass, so
    # the re-read after a test's first command isn't held back by it.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    return config_entry


async def wait_for(
    hass: HomeAssistant, condition: Callable[[], bool], what: str
) -> None:
    """Wait for the push loop, a never-ending background task, to reach a state.

    Its teardown runs on a worker thread, so allow real time, not just
    event-loop turns: on a slow CI runner a fixed number of turns isn't enough.
    """
    for _ in range(200):
        await hass.async_block_till_done()
        if condition():
            return
        await hass.async_add_executor_job(time.sleep, 0.01)
    pytest.fail(f"timed out waiting for {what}")


@pytest.fixture
async def push_entry(setup_entry: MockConfigEntry) -> MockConfigEntry:
    """``setup_entry``, by the name the instant-updates tests use."""
    return setup_entry
