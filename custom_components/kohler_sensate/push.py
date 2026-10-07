"""Optional instant updates over Kohler's Azure IoT Hub feed.

The Konnect app gets real-time events by registering as a "mobile device" and
holding an MQTT connection to Azure IoT Hub. Kohler sends events as IoT Hub
direct methods on one account-wide topic. Any message for this faucet makes
the coordinator re-read the state over HTTPS. Two kinds of message also say
what happened, and are passed on as a ``FaucetEvent``: ``SENSATE_STS`` (water
on/off and handle position) and ``SENSATE_EXP_STS`` (a preset started or
finished). If the feed is quiet or down, polling carries on as usual.

The connection details follow the field notes of the MIT-licensed
kohler-anthem-plus project (Anthem shower, same cloud):

* Only ``$iothub/methods/POST/#`` delivers, and each message must be
  acknowledged or Kohler treats it as unhandled.
* Reuse one registered identity; every connect needs fresh SAS credentials,
  so paho's own reconnect (which reuses them) is disabled.
* Nothing is replayed on connect, so the state is re-read after connecting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.ssl import get_default_context
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .api import SensateApi, SensateAuthError, SensateError
from .const import MQTT_BACKOFF, MQTT_PORT, MQTT_RESPONSE_TOPIC, MQTT_SUBSCRIBE_TOPIC

_LOGGER = logging.getLogger(__name__)

_RID = re.compile(r"\$rid=([^&]+)")
CONNECT_TIMEOUT = 30
KEEPALIVE = 60


@dataclass(frozen=True, slots=True)
class FaucetEvent:
    """What a feed message says happened, as far as it's understood."""

    status: str | None = None  # water "On"/"Off"
    handle: str | None = None  # "OPEN"/"CLOSED"
    preset: str | None = None  # name of the preset that started or finished
    preset_on: bool | None = None


def parse_event(payload: bytes) -> FaucetEvent | None:
    """Read a ``SENSATE_STS`` or ``SENSATE_EXP_STS`` message; None for others."""
    try:
        message = json.loads(payload)
    except ValueError:
        return None
    data = message.get("data") if isinstance(message, dict) else None
    attributes = data.get("attributes") if isinstance(data, dict) else None
    if not isinstance(attributes, list):
        return None
    for item in attributes:
        if not isinstance(item, dict):
            continue
        status = item.get("status") if isinstance(item.get("status"), str) else None
        if item.get("code") == "SENSATE_STS":
            handle = item.get("handle")
            return FaucetEvent(
                status=status, handle=handle if isinstance(handle, str) else None
            )
        if item.get("code") == "SENSATE_EXP_STS" and status:
            name = item.get("name")
            return FaucetEvent(
                preset=name if isinstance(name, str) and name else None,
                preset_on=status.lower() == "on",
            )
    return None


def _message_device(payload: bytes) -> str | None:
    """Return the device id a message is about, if it names one."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    device = data.get("deviceid") or data.get("deviceId")
    return str(device) if device else None


class SensatePush:
    """Holds one IoT Hub connection and reports activity for one faucet."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: SensateApi,
        device_id: str,
        identity: str,
        on_activity: Callable[[bool, FaucetEvent | None], None],
        client_factory: Callable[..., mqtt.Client] | None = None,
    ) -> None:
        self._hass = hass
        self._api = api
        self._device_id = device_id.lower()
        self._identity = identity
        # Called on the event loop with True for a message about this faucet,
        # False for a (re)connect, when the state should be re-read anyway.
        self._on_activity = on_activity
        self._client_factory = client_factory or mqtt.Client
        self._client: mqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self._lost: asyncio.Event = asyncio.Event()
        self.connected = False
        self.messages = 0
        self.last_message_at: float | None = None
        self.last_error: str | None = None
        # Wall-clock time of the next reconnect attempt, while waiting for it.
        self.next_retry_at: float | None = None

    def start(self) -> None:
        """Connect in the background; never blocks setup."""
        self._task = self._hass.async_create_background_task(
            self._async_run(), "kohler_sensate instant updates"
        )

    async def async_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._async_close()

    async def _async_run(self) -> None:
        attempt = 0
        while True:
            dropped = False
            try:
                await self._async_connect()
            except SensateAuthError as err:
                # Polling raises the re-login prompt; wait the longest delay.
                self.last_error = str(err)
                attempt = len(MQTT_BACKOFF) - 1
            except Exception as err:
                self.last_error = str(err) or type(err).__name__
                _LOGGER.debug(
                    "Instant updates unavailable: %s",
                    self.last_error,
                    exc_info=not isinstance(err, (SensateError, OSError)),
                )
            else:
                attempt = 0
                await self._lost.wait()
                _LOGGER.debug("Instant updates disconnected; reconnecting")
                dropped = True
            await self._async_close()
            if dropped:
                # Polling stands in until the feed is back: catch up now, and
                # at the pace used without the feed.
                self._on_activity(False, None)
            delay = MQTT_BACKOFF[min(attempt, len(MQTT_BACKOFF) - 1)]
            attempt += 1
            self.next_retry_at = time.time() + delay
            try:
                await asyncio.sleep(delay)
            finally:
                self.next_retry_at = None

    async def _async_connect(self) -> None:
        settings = await self._api.async_register_push(self._identity)
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._lost = asyncio.Event()
        client = self._client_factory(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=settings.client_id,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
        client.username_pw_set(settings.username, settings.password)
        client.tls_set_context(get_default_context())
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        client.connect_async(settings.host, MQTT_PORT, keepalive=KEEPALIVE)
        client.loop_start()
        async with asyncio.timeout(CONNECT_TIMEOUT):
            await self._ready
        self.connected = True
        self.last_error = None
        _LOGGER.debug("Instant updates connected")
        self._on_activity(False, None)

    async def _async_close(self) -> None:
        self.connected = False
        client, self._client = self._client, None
        if client is not None:
            client.disconnect()
            await self._hass.async_add_executor_job(client.loop_stop)

    # --- paho callbacks: these run on paho's network thread -------------------

    def _call(self, func: Callable[..., Any], *args: Any) -> None:
        self._hass.loop.call_soon_threadsafe(func, *args)

    def _on_connect(
        self, client: mqtt.Client, _userdata: Any, _flags: Any, reason: Any, _props: Any
    ) -> None:
        if client is not self._client:
            return
        if reason.is_failure:
            self._call(self._settle, ConnectionError(f"IoT Hub refused: {reason}"))
            return
        client.subscribe(MQTT_SUBSCRIBE_TOPIC, qos=1)

    def _on_subscribe(
        self, client: mqtt.Client, _userdata: Any, _mid: int, reasons: Any, _props: Any
    ) -> None:
        if client is not self._client:
            return
        if not reasons or any(reason.is_failure for reason in reasons):
            self._call(
                self._settle, ConnectionError("IoT Hub refused the subscription")
            )
        else:
            self._call(self._settle, None)

    def _on_message(
        self, client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        if client is not self._client:
            return
        # Acknowledge first, even if the payload is unreadable.
        if match := _RID.search(message.topic):
            client.publish(
                MQTT_RESPONSE_TOPIC.format(rid=match.group(1)),
                b'{"status":"received"}',
                qos=1,
            )
        device = _message_device(message.payload)
        if device is not None and device.lower() != self._device_id:
            return  # another device on the account, such as a shower
        self._call(self._message_received, parse_event(message.payload))

    def _on_disconnect(
        self, client: mqtt.Client, _userdata: Any, _flags: Any, reason: Any, _props: Any
    ) -> None:
        if client is not self._client:
            return
        self._call(self._settle, ConnectionError(f"IoT Hub disconnected: {reason}"))
        self._call(self._lost.set)

    # --- back on the event loop -----------------------------------------------

    def _settle(self, error: Exception | None) -> None:
        ready = self._ready
        if ready is not None and not ready.done():
            if error is None:
                ready.set_result(None)
            else:
                ready.set_exception(error)

    def _message_received(self, event: FaucetEvent | None) -> None:
        self.messages += 1
        self.last_message_at = time.time()
        self._on_activity(True, event)
