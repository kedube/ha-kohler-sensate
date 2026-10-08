"""Optional instant updates over Kohler's Azure IoT Hub feed.

The Konnect app gets real-time events by registering as a "mobile device" and
holding an MQTT connection to Azure IoT Hub. Kohler sends events as IoT Hub
direct methods on one account-wide topic, so the faucets on one account share
one registration and one connection: each message goes to the faucet it
names. Any message for a faucet makes its coordinator re-read the state over
HTTPS. The messages the app acts on
also say what happened, and are passed on as a ``FaucetEvent``:
``SENSATE_STS`` (water on/off and handle position), ``SENSATE_EXP_STS`` (a
preset started or finished), ``SENSATE_LEAK_DETECTED_ALT`` (a leak) and
``INSTALL_FIRMWARE_STS`` (a firmware install ended). If the feed is quiet or
down, polling carries on as usual.

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
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey
from homeassistant.util.ssl import get_default_context
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .api import SensateApi, SensateAuthError, SensateError
from .const import (
    DOMAIN,
    MQTT_BACKOFF,
    MQTT_PORT,
    MQTT_RESPONSE_TOPIC,
    MQTT_SUBSCRIBE_TOPIC,
)

_LOGGER = logging.getLogger(__name__)

# The running feeds, one per Kohler account (by account id).
FEEDS: HassKey[dict[str, SensatePush]] = HassKey(f"{DOMAIN}_feeds")

# Called on the event loop with True for a message about the faucet, False for
# a (re)connect or a drop, when the state should be re-read anyway.
type ActivityCallback = Callable[[bool, FaucetEvent | None], None]

_RID = re.compile(r"\$rid=([^&]+)")
CONNECT_TIMEOUT = 30
KEEPALIVE = 60

CODE_STATUS = "SENSATE_STS"
CODE_PRESET = "SENSATE_EXP_STS"
CODE_LEAK = "SENSATE_LEAK_DETECTED_ALT"
CODE_FIRMWARE = "INSTALL_FIRMWARE_STS"


@dataclass(frozen=True, slots=True)
class FaucetEvent:
    """What a feed message says happened, as far as it's understood."""

    status: str | None = None  # water "On"/"Off"
    handle: str | None = None  # "OPEN"/"CLOSED"
    preset: str | None = None  # name of the preset that started or finished
    preset_id: str | None = None
    preset_on: bool | None = None
    leak: bool = False
    firmware: str | None = None  # install result: "Installed"/"Aborted"


def _text(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) and value else None


def parse_event(payload: bytes) -> FaucetEvent | None:
    """Read a faucet message the Konnect app acts on; None for others."""
    try:
        message = json.loads(payload)
    except ValueError:
        return None
    data = message.get("data") if isinstance(message, dict) else None
    if not isinstance(data, dict):
        return None
    attributes = data.get("attributes")
    items = [i for i in attributes if isinstance(i, dict)] if attributes else []
    # The app checks only the code of a leak alert, wherever it is.
    if CODE_LEAK in (data.get("code"), *(item.get("code") for item in items)):
        return FaucetEvent(leak=True)
    for item in items:
        status = _text(item, "status")
        code = item.get("code")
        if code == CODE_STATUS:
            return FaucetEvent(status=status, handle=_text(item, "handle"))
        if code == CODE_PRESET and status:
            return FaucetEvent(
                preset=_text(item, "name"),
                preset_id=_text(item, "experienceid"),
                # Like the app: anything but "OFF" means running.
                preset_on=status.upper() != "OFF",
            )
        if code == CODE_FIRMWARE and status:
            return FaucetEvent(firmware=status)
    return None


def new_identity() -> str:
    """A new identity to register the feed under."""
    return uuid.uuid4().hex[:16]


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
    """Holds one IoT Hub connection for a Kohler account, for all its faucets."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: SensateApi,
        identity: str,
        client_factory: Callable[..., mqtt.Client] | None = None,
    ) -> None:
        self._hass = hass
        self._api = api
        self.identity = identity
        # Each faucet's callback and sign-in, by lowercase device id.
        self._listeners: dict[str, tuple[ActivityCallback, SensateApi]] = {}
        self._messages: dict[str, int] = {}
        self._client_factory = client_factory or mqtt.Client
        self._client: mqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self._lost: asyncio.Event = asyncio.Event()
        self.connected = False
        self.last_message_at: float | None = None
        self.last_error: str | None = None
        # Wall-clock time of the next reconnect attempt, while waiting for it.
        self.next_retry_at: float | None = None

    @property
    def faucets(self) -> int:
        """How many faucets the connection reports for."""
        return len(self._listeners)

    def messages_for(self, device_id: str) -> int:
        """How many messages about ``device_id`` have arrived."""
        return self._messages.get(device_id.lower(), 0)

    def add_listener(
        self, device_id: str, on_activity: ActivityCallback, api: SensateApi
    ) -> None:
        """Report the faucet's messages, and every (re)connect, to ``on_activity``."""
        self._listeners[device_id.lower()] = (on_activity, api)
        # Register with the newest sign-in: it has the freshest password.
        self._api = api

    def remove_listener(self, device_id: str) -> bool:
        """Stop reporting for a faucet; True if no faucet is left."""
        removed = self._listeners.pop(device_id.lower(), None)
        if removed is not None and removed[1] is self._api and self._listeners:
            self._api = next(reversed(self._listeners.values()))[1]
        return not self._listeners

    def _notify_all(self) -> None:
        for on_activity, _api in list(self._listeners.values()):
            on_activity(False, None)

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
                self._notify_all()
            delay = MQTT_BACKOFF[min(attempt, len(MQTT_BACKOFF) - 1)]
            attempt += 1
            self.next_retry_at = time.time() + delay
            try:
                await asyncio.sleep(delay)
            finally:
                self.next_retry_at = None

    async def _async_connect(self) -> None:
        settings = await self._api.async_register_push(self.identity)
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
        self._notify_all()

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
        if device is None or device.lower() not in self._listeners:
            return  # another device on the account, such as a shower
        self._call(self._message_received, device.lower(), parse_event(message.payload))

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

    def _message_received(self, device: str, event: FaucetEvent | None) -> None:
        # Its faucet may have left since the message arrived.
        if (listener := self._listeners.get(device)) is None:
            return
        self._messages[device] = self._messages.get(device, 0) + 1
        self.last_message_at = time.time()
        listener[0](True, event)
