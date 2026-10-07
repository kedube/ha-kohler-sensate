"""DataUpdateCoordinator for the Kohler Sensate faucet."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from functools import partial
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SensateApi,
    SensateApiError,
    SensateAuthError,
    SensateError,
    SensatePreset,
)
from .const import (
    COMMAND_FOLLOW_UP,
    CONF_DEVICE_ID,
    CONF_MAX_RUN_MINUTES,
    CONFIG_REFRESH_INTERVAL,
    DEFAULT_MAX_RUN_MINUTES,
    DISPENSE_MAX,
    DISPENSE_SETTLE,
    DOMAIN,
    ISSUE_API_CHANGED,
    ISSUE_FAUCET_NOT_FOUND,
    MAX_CLEARED_LEAKS,
    PUSH_GRACE,
    REJECTIONS_BEFORE_ISSUE,
    RETRY_AFTER_MAX,
    RETRY_AFTER_MIN,
    SCAN_INTERVAL_ACTIVE,
    SCAN_INTERVAL_IDLE,
    SCAN_INTERVAL_PUSH,
    SCAN_INTERVAL_PUSH_ACTIVE,
    STORAGE_KEY,
    STORAGE_VERSION,
    USAGE_HISTORY_START,
    USAGE_REFRESH_INTERVAL,
    USAGE_SETTLE,
)
from .units import UnitProfile, get_profile, to_ml

if TYPE_CHECKING:
    from . import SensateConfigEntry
    from .push import FaucetEvent, SensatePush

_LOGGER = logging.getLogger(__name__)

# Kohler doesn't document leak events; if one carries an id, use it so the
# event stays cleared even if Kohler later adds fields to it.
_LEAK_ID_KEYS = ("id", "eventId", "leakId")

# Only statuses known for certain count. Anything else is "unknown", recorded
# in diagnostics so it can be added here.
STATUS_OFF = frozenset({"off", "notstarted"})
STATUS_ON = frozenset({"on"})
# Fields whose values are recorded for diagnostics.
TRACKED_FIELDS = ("status", "progress", "handleState")
AUTO_OFF_RETRY = 60


def leak_fingerprint(event: Any) -> str:
    """Return a stable identifier for a leak event of unknown shape."""
    if isinstance(event, dict):
        for key in _LEAK_ID_KEYS:
            if event.get(key) not in (None, ""):
                return f"{key}:{event[key]}"
    raw = json.dumps(event, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _dispensing(state: dict[str, Any]) -> bool:
    progress = state.get("progress")
    # The Sensate leaves this at "NotStarted", even mid-dispense, so dispenses
    # are tracked from commands and the feed instead. Kept for any firmware
    # that does report "…InProgress".
    return isinstance(progress, str) and progress.lower().endswith("inprogress")


def _water_running(state: dict[str, Any]) -> bool | None:
    """True/False for statuses known for certain, None for anything else."""
    if _dispensing(state):
        return True
    status = state.get("status")
    if not isinstance(status, str):
        return None
    if status.lower() in STATUS_ON:
        return True
    if status.lower() in STATUS_OFF:
        return False
    return None


def _push_signature(state: dict[str, Any]) -> tuple[bool | None, bool]:
    """The changes instant updates must announce: water running, dispensing."""
    return _water_running(state), _dispensing(state)


class SensateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls faucet state and, less often, configuration and leak history."""

    config_entry: SensateConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SensateConfigEntry, api: SensateApi
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL_IDLE,
            # The default 10 s cooldown would delay an instant update that
            # arrives right after a command; 1 s still collapses bursts.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=1.0, immediate=True
            ),
        )
        self.api = api
        # Entries created by v0.1 only stored the device id as the unique id.
        self.device_id: str = entry.data.get(CONF_DEVICE_ID) or entry.unique_id or ""
        self.profile: UnitProfile = get_profile(hass, entry)
        self.max_run_minutes: int = int(
            entry.options.get(CONF_MAX_RUN_MINUTES, DEFAULT_MAX_RUN_MINUTES)
        )
        self.config: dict[str, Any] = {}
        self._config_fetched_at: float | None = None
        self._config_due = False
        self.presets: dict[str, SensatePreset] = {}
        # The preset id chosen in the Preset select; see chosen_preset.
        self.preset_choice: str | None = None
        self.connection_state: str | None = None
        self.last_connected: Any = None
        self.seen_values: dict[str, set[str]] = {
            field: set() for field in (*TRACKED_FIELDS, "connectionState")
        }
        self._fast_poll_until = 0.0
        self._rejections = 0
        self._check_issues = True
        # Shared between the "Dispense amount" number and the button that uses it.
        self.dispense_amount_ml: float = to_ml(
            self.profile.number_default, self.profile.number_unit
        )
        self.last_dispense_liters: float | None = None
        # Dispenses: one Home Assistant started (until the faucet reports the
        # water off) and a preset run from the app (until the feed says so).
        self._dispense_started: float | None = None
        self._dispense_seen_on = False
        self._dispense_feed_on = False
        self._dispense_preset: str | None = None
        self._preset_since: float | None = None
        self._app_preset: str | None = None
        self._water_was_running: bool | None = None
        # Ends a dispense whose end never arrives, at DISPENSE_MAX.
        self._dispense_timer: CALLBACK_TYPE | None = None
        # Water usage in liters: everything Kohler has counted, and today.
        self.usage_total_liters: float | None = None
        self.usage_today_liters: float | None = None
        self._usage_fetched_at: float | None = None
        self._usage_due_at: float | None = None
        self._usage_day: date | None = None
        # Optional instant updates; set up by __init__.py when enabled.
        self.push: SensatePush | None = None
        self.push_verified = False
        # Self-check of instant updates: the feed has accounted for every
        # change up to _push_synced_at; polls that find a change it never
        # announced count as missed and stop polling from relying on it.
        self._push_synced_at: float | None = None
        self._polled_at: float | None = None
        self._push_check: CALLBACK_TYPE | None = None
        self.push_missed = 0
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY.format(entry_id=entry.entry_id)
        )
        self._cleared_leaks: list[str] = []
        self._push_identity: str | None = None
        # Safety auto-off: wall-clock deadline (persisted) and its timer.
        self._auto_off_at: float | None = None
        self._auto_off_timer: CALLBACK_TYPE | None = None
        self._seen_running = False

    async def _async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        self._cleared_leaks = list(stored.get("cleared_leaks", []))
        self._push_identity = stored.get("push_identity")
        # A reload or restart must not lose the safety limit.
        if isinstance(deadline := stored.get("auto_off_at"), (int, float)):
            self._auto_off_at = float(deadline)
            self._schedule_auto_off(max(0.0, deadline - time.time()))

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "cleared_leaks": self._cleared_leaks,
                "push_identity": self._push_identity,
                "auto_off_at": self._auto_off_at,
            }
        )

    async def async_shutdown(self) -> None:
        # The persisted deadline is picked up again by the next setup.
        if self._auto_off_timer is not None:
            self._auto_off_timer()
            self._auto_off_timer = None
        for cancel in (self._push_check, self._dispense_timer):
            if cancel is not None:
                cancel()
        self._push_check = self._dispense_timer = None
        await super().async_shutdown()

    # --- polling --------------------------------------------------------------

    @property
    def push_trusted(self) -> bool:
        """Instant updates are connected and have proven they report changes."""
        return self.push is not None and self.push.connected and self.push_verified

    @property
    def _idle_interval(self) -> timedelta:
        return SCAN_INTERVAL_PUSH if self.push_trusted else SCAN_INTERVAL_IDLE

    async def _async_update_data(self) -> dict[str, Any]:
        # Errors fall back to slow polling; no point hammering a failing cloud.
        self.update_interval = self._idle_interval
        polled_at = time.monotonic()
        try:
            snapshot = await self.api.async_get_state(self.device_id)
        except SensateAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SensateApiError as err:
            await self._async_note_rejection(err)
            if err.retry_after is not None:
                self.update_interval = timedelta(
                    seconds=min(
                        max(err.retry_after, RETRY_AFTER_MIN.total_seconds()),
                        RETRY_AFTER_MAX.total_seconds(),
                    )
                )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        self._clear_issues()
        state = snapshot.state
        self._check_push_announced(state)
        self._polled_at = polled_at
        self.connection_state = snapshot.connection_state
        self.last_connected = snapshot.last_connected
        self._record_values(state)
        self._note_water(_water_running(state), from_feed=False)
        await self._async_refresh_config()
        await self._async_refresh_usage()

        _LOGGER.debug("Faucet state: %s (%s)", state, self.connection_state)
        quantity = state.get("quantity")
        if isinstance(quantity, (int, float)) and quantity > 0:
            self.last_dispense_liters = float(quantity)

        running = _water_running(state)
        if running:
            self._seen_running = True
        elif running is False and self._seen_running and self._auto_off_at:
            # Seen running, now off: no need for the safety limit any more.
            await self._async_cancel_auto_off()
        if running or time.monotonic() < self._fast_poll_until:
            # A trusted feed reports the change the moment it happens.
            self.update_interval = (
                SCAN_INTERVAL_PUSH_ACTIVE if self.push_trusted else SCAN_INTERVAL_ACTIVE
            )
        return state

    def _record_values(self, state: dict[str, Any]) -> None:
        values = {field: state.get(field) for field in TRACKED_FIELDS}
        values["connectionState"] = self.connection_state
        for field, value in values.items():
            if isinstance(value, str) and value not in self.seen_values[field]:
                self.seen_values[field].add(value)
                _LOGGER.debug("New faucet %s value: %s", field, value)

    async def _async_refresh_config(self) -> None:
        """Refresh firmware, leak history and presets; failures keep the old copy."""
        now = time.monotonic()
        if (
            not self._config_due
            and self._config_fetched_at is not None
            and now - self._config_fetched_at < CONFIG_REFRESH_INTERVAL.total_seconds()
        ):
            return
        try:
            self.config = await self.api.async_get_config(self.device_id)
        except SensateAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SensateError as err:
            _LOGGER.debug("Could not refresh faucet configuration: %s", err)
            # Try again next poll only if we have never succeeded.
            if self._config_fetched_at is None:
                return
        try:
            self.presets = {
                p.preset_id: p for p in await self.api.async_get_presets(self.device_id)
            }
        except SensateAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SensateError as err:
            _LOGGER.debug("Could not refresh Konnect presets: %s", err)
        self._config_fetched_at = now
        self._config_due = False

    async def _async_refresh_usage(self) -> None:
        """Refresh water usage now and then, and soon after the water stops."""
        now = time.monotonic()
        today = dt_util.now().date()
        if not (
            self._usage_fetched_at is None
            or now - self._usage_fetched_at >= USAGE_REFRESH_INTERVAL.total_seconds()
            or (self._usage_due_at is not None and now >= self._usage_due_at)
            # A new day starts "today" over, without waiting out the interval.
            or today != self._usage_day
        ):
            return
        # A failure waits for the next interval rather than retrying each poll.
        self._usage_fetched_at = now
        self._usage_due_at = None
        self._usage_day = today
        try:
            months = await self.api.async_get_usage(
                self.device_id, USAGE_HISTORY_START, today, "MONTH"
            )
            days = await self.api.async_get_usage(self.device_id, today, today, "DAY")
        except SensateAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SensateError as err:
            _LOGGER.debug("Could not refresh water usage: %s", err)
            return
        self.usage_total_liters = round(sum(months.values()), 4)
        self.usage_today_liters = round(sum(days.values()), 4)

    # --- dispenses ------------------------------------------------------------

    def _note_water(self, running: bool | None, *, from_feed: bool) -> None:
        """Follow dispenses and usage from the water turning on and off."""
        if running is None:
            return
        now = time.monotonic()
        if running:
            if self._dispense_started is not None:
                self._dispense_seen_on = True
                self._dispense_feed_on |= from_feed
        else:
            if self._water_was_running:
                # Kohler counts the water used shortly after it stops.
                self._usage_due_at = now + USAGE_SETTLE.total_seconds()
            # Polls can lag the feed, so only the feed ends an app preset.
            if from_feed:
                self._preset_since = None
                self._app_preset = None
            # Ended, once the water was seen running or had time to start;
            # after the feed saw it running, only the feed can say it stopped.
            if (
                self._dispense_started is not None
                and (
                    self._dispense_seen_on
                    or now - self._dispense_started >= DISPENSE_SETTLE.total_seconds()
                )
                and (from_feed or not self._dispense_feed_on)
            ):
                self._dispense_started = None
                self._dispense_preset = None
        self._water_was_running = running

    def _start_dispense_timer(self) -> None:
        if self._dispense_timer is not None:
            self._dispense_timer()
        self._dispense_timer = async_call_later(
            self.hass, DISPENSE_MAX, self._async_dispense_timeout
        )

    @callback
    def _async_dispense_timeout(self, _now: datetime) -> None:
        self._dispense_timer = None
        self.async_update_listeners()

    @callback
    def _note_event(self, event: FaucetEvent) -> None:
        if event.preset_on is not None:
            self._preset_since = time.monotonic() if event.preset_on else None
            self._app_preset = event.preset if event.preset_on else None
            if event.preset_on:
                self._start_dispense_timer()
        if event.status is not None:
            status = event.status.lower()
            if status in STATUS_ON or status in STATUS_OFF:
                self._note_water(status in STATUS_ON, from_feed=True)
        self.async_update_listeners()

    # --- repairs --------------------------------------------------------------

    def _issue_id(self, issue: str) -> str:
        return f"{issue}_{self.config_entry.entry_id}"

    async def _async_note_rejection(self, err: SensateApiError) -> None:
        """Raise a repair issue when Kohler keeps refusing to answer."""
        if not err.rejected:
            return  # outages and throttling fix themselves
        self._rejections += 1
        if self._rejections != REJECTIONS_BEFORE_ISSUE:
            return
        issue = ISSUE_API_CHANGED
        if err.status == 404:
            # Tell "faucet removed from the account" apart from "API moved".
            try:
                faucets = await self.api.async_get_faucets()
            except SensateError:
                faucets = None
            if faucets is not None and all(
                f.device_id != self.device_id for f in faucets
            ):
                issue = ISSUE_FAUCET_NOT_FOUND
        _LOGGER.debug("Raising repair issue %s after: %s", issue, err)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(issue),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=issue,
            translation_placeholders={
                "name": self.config_entry.title,
                "error": str(err),
            },
        )

    def _clear_issues(self) -> None:
        if not self._rejections and not self._check_issues:
            return
        self._rejections = 0
        self._check_issues = False
        for issue in (ISSUE_API_CHANGED, ISSUE_FAUCET_NOT_FOUND):
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(issue))

    # --- commands -------------------------------------------------------------

    async def async_dispense(self, liters: float, preset: str | None = None) -> None:
        """Dispense ``liters`` (for ``preset``, if any) and refresh the state."""
        await self._async_command(
            lambda: self.api.async_dispense(self.device_id, liters)
        )
        self.last_dispense_liters = liters
        self._dispense_started = time.monotonic()
        self._dispense_seen_on = self._dispense_feed_on = False
        self._dispense_preset = preset
        self._start_dispense_timer()
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_set_water(self, on: bool) -> None:
        """Turn the water on or off and refresh the state."""
        await self._async_command(lambda: self.api.async_set_water(self.device_id, on))
        if on and self.max_run_minutes > 0:
            self._seen_running = False
            self._auto_off_at = time.time() + self.max_run_minutes * 60
            self._schedule_auto_off(self.max_run_minutes * 60)
            await self._async_save()
        elif not on:
            await self._async_cancel_auto_off()
        await self.async_request_refresh()

    async def _async_command(self, send: Callable[[], Awaitable[None]]) -> None:
        if not self.faucet_online:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="faucet_offline",
                translation_placeholders={"name": self.config_entry.title},
            )
        try:
            await send()
        except SensateAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SensateApiError as err:
            if err.retry_after is not None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="rate_limited",
                    translation_placeholders={"seconds": str(round(err.retry_after))},
                ) from err
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # The cloud can lag the command; watch closely until it catches up.
        self._fast_poll_until = time.monotonic() + COMMAND_FOLLOW_UP.total_seconds()

    # --- safety auto-off -------------------------------------------------------

    def _schedule_auto_off(self, delay: float) -> None:
        if self._auto_off_timer is not None:
            self._auto_off_timer()
        self._auto_off_timer = async_call_later(self.hass, delay, self._async_auto_off)

    async def _async_cancel_auto_off(self) -> None:
        if self._auto_off_timer is not None:
            self._auto_off_timer()
            self._auto_off_timer = None
        if self._auto_off_at is not None:
            self._auto_off_at = None
            await self._async_save()

    async def _async_auto_off(self, _now: datetime) -> None:
        """Turn off water Home Assistant turned on and nobody turned off."""
        self._auto_off_timer = None
        _LOGGER.warning(
            "Turning off the water on %s: it was turned on from Home Assistant "
            "%s minutes ago, the limit set in the integration options",
            self.config_entry.title,
            self.max_run_minutes,
        )
        try:
            await self.api.async_set_water(self.device_id, False)
        except SensateError as err:
            _LOGGER.warning(
                "Could not turn off the water on %s: %s. Trying again in a minute",
                self.config_entry.title,
                err,
            )
            self._schedule_auto_off(AUTO_OFF_RETRY)
            return
        await self._async_cancel_auto_off()
        self._fast_poll_until = time.monotonic() + COMMAND_FOLLOW_UP.total_seconds()
        await self.async_request_refresh()

    # --- leaks -----------------------------------------------------------------

    async def async_clear_leaks(self) -> None:
        """Mark every leak event Kohler currently reports as cleared."""
        new = sorted({leak_fingerprint(e) for e in self.active_leaks})
        if not new:
            return
        self._cleared_leaks = (self._cleared_leaks + new)[-MAX_CLEARED_LEAKS:]
        await self._async_save()
        self.async_update_listeners()

    # --- instant updates ------------------------------------------------------

    async def async_push_identity(self) -> str:
        """Return the persisted instant-updates identity, creating it once."""
        if not self._push_identity:
            self._push_identity = uuid.uuid4().hex[:16]
            await self._async_save()
        return self._push_identity

    @callback
    def async_push_activity(
        self, about_faucet: bool, event: FaucetEvent | None = None
    ) -> None:
        """Instant updates saw a change, connected or dropped: re-read the faucet."""
        # In every case, the refresh below catches up on everything until now.
        self._push_synced_at = time.monotonic()
        if about_faucet:
            # Proof the feed delivers for this faucet; polling can relax.
            self.push_verified = True
            self._config_due = True  # it may be a leak alert
        if event is not None:
            self._note_event(event)
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), "kohler_sensate push refresh"
        )

    def _check_push_announced(self, state: dict[str, Any]) -> None:
        """Start the self-check when a poll finds a change the feed hasn't announced."""
        if (
            not self.push_trusted
            or self.data is None
            or self._polled_at is None
            or self._push_check is not None
            or _push_signature(state) == _push_signature(self.data)
        ):
            return
        since = self._polled_at  # the change happened after the previous poll
        if self._push_synced_at is not None and self._push_synced_at > since:
            return
        # Its announcement may still be on the way, as after a command.
        self._push_check = async_call_later(
            self.hass, PUSH_GRACE, partial(self._async_push_check, since)
        )

    @callback
    def _async_push_check(self, since: float, _now: datetime) -> None:
        self._push_check = None
        if self._push_synced_at is not None and self._push_synced_at > since:
            return
        self.push_missed += 1
        if not self.push_verified:
            return
        _LOGGER.debug(
            "Instant updates missed a change on %s; polling as usual until "
            "they deliver again",
            self.config_entry.title,
        )
        self.push_verified = False
        # Apply the faster polling now, not after the current long interval.
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), "kohler_sensate push check"
        )

    # --- convenience accessors used by entities -----------------------------

    @property
    def faucet_online(self) -> bool:
        """False when Kohler reports the faucet isn't connected to the cloud."""
        return self.connection_state is None or (
            self.connection_state.lower() == "connected"
        )

    @property
    def config_loaded(self) -> bool:
        return self._config_fetched_at is not None

    @property
    def leak_history(self) -> list[Any]:
        history = self.config.get("leakDetectionHistory")
        return history if isinstance(history, list) else []

    @property
    def active_leaks(self) -> list[Any]:
        """Leak events that haven't been cleared in Home Assistant."""
        cleared = set(self._cleared_leaks)
        return [e for e in self.leak_history if leak_fingerprint(e) not in cleared]

    @property
    def about(self) -> dict[str, Any]:
        about = (self.config.get("configuration") or {}).get("about")
        return about if isinstance(about, dict) else {}

    @property
    def water_running(self) -> bool | None:
        return _water_running(self.data or {})

    def is_dispensing(self) -> bool:
        now = time.monotonic()
        limit = DISPENSE_MAX.total_seconds()
        return (
            _dispensing(self.data or {})
            or (
                self._dispense_started is not None
                and now - self._dispense_started < limit
            )
            or (self._preset_since is not None and now - self._preset_since < limit)
        )

    @property
    def chosen_preset(self) -> SensatePreset | None:
        """The preset "Dispense preset" pours: the one chosen, else the first.

        A chosen preset that's deleted in the app falls back to the first,
        and comes back if it reappears.
        """
        if (preset := self.presets.get(self.preset_choice or "")) is not None:
            return preset
        return next(iter(self.presets.values()), None)

    @property
    def dispensing_preset(self) -> str | None:
        """The preset being dispensed, from the app or Home Assistant."""
        if not self.is_dispensing():
            return None
        return self._app_preset or self._dispense_preset
