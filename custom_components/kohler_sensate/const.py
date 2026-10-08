"""Constants for the Kohler Sensate integration."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Final

DOMAIN: Final = "kohler_sensate"

# Poll the faucet's live state slowly while it's idle, and quickly while water
# is running or right after a command, when the state is about to change.
SCAN_INTERVAL_IDLE: Final = timedelta(seconds=30)
SCAN_INTERVAL_ACTIVE: Final = timedelta(seconds=5)
# How long to keep polling quickly after a command, while the cloud catches up.
COMMAND_FOLLOW_UP: Final = timedelta(seconds=30)
# Once instant updates have proven they report this faucet's changes, polling
# is only a safety net: rarely while idle, now and then while water runs.
SCAN_INTERVAL_PUSH: Final = timedelta(minutes=5)
SCAN_INTERVAL_PUSH_ACTIVE: Final = timedelta(seconds=30)
# How long instant updates get to announce a change that a poll saw first,
# before polling stops relying on them.
PUSH_GRACE: Final = timedelta(seconds=15)
# Refresh the configuration (firmware, leak history, presets) less often.
CONFIG_REFRESH_INTERVAL: Final = timedelta(minutes=5)
# Ask Kohler whether newer firmware is available this often.
FIRMWARE_CHECK_INTERVAL: Final = timedelta(hours=1)
# Water usage: re-read this often, and this long after the water stops, once
# Kohler has counted it. The total covers every month since this date.
USAGE_REFRESH_INTERVAL: Final = timedelta(minutes=30)
USAGE_SETTLE: Final = timedelta(minutes=2)
USAGE_HISTORY_START: Final = date(2019, 1, 1)
# A dispense from Home Assistant counts as running until the faucet reports
# the water off, once it has reported it on or this long after the command.
DISPENSE_SETTLE: Final = timedelta(seconds=3)
# A dispense whose end is never reported counts as over after DISPENSE_MAX, or
# longer for large amounts: the time to pour them at DISPENSE_SLOWEST_FLOW
# (liters per minute, well under the Sensate's rated flow) plus a minute.
DISPENSE_MAX: Final = timedelta(minutes=2)
DISPENSE_SLOWEST_FLOW: Final = 3.0
# Bounds for honoring Kohler's Retry-After when it throttles us.
RETRY_AFTER_MIN: Final = timedelta(seconds=30)
RETRY_AFTER_MAX: Final = timedelta(minutes=15)
# Consecutive rejected requests (4xx or unreadable replies) before a repair
# issue is raised; one-offs are normal.
REJECTIONS_BEFORE_ISSUE: Final = 3
ISSUE_FAUCET_NOT_FOUND: Final = "faucet_not_found"
ISSUE_API_CHANGED: Final = "api_changed"

# Cleared leak events and the instant-updates identity, per config entry.
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = DOMAIN + ".{entry_id}"
# Cap on remembered cleared events; old ones have long left Kohler's history.
MAX_CLEARED_LEAKS: Final = 200

# --- Kohler Konnect cloud --------------------------------------------------
# App-global values baked into the public Konnect mobile app; not secrets.
API_BASE: Final = "https://api-kohler-us.kohler.io"
B2C_TENANT: Final = "konnectkohler.onmicrosoft.com"
B2C_ROPC_POLICY: Final = "B2C_1_ROPC_Auth"
TOKEN_URL: Final = (
    f"https://konnectkohler.b2clogin.com/tfp/{B2C_TENANT}/{B2C_ROPC_POLICY}"
    "/oauth2/v2.0/token"
)
CLIENT_ID: Final = "8caf9530-1d13-48e6-867c-0f082878debc"
API_RESOURCE: Final = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"
APIM_KEY: Final = "429ecb1d0b5e4258aa0a2bfadd82a493"
AUTH_SCOPE: Final = (
    f"openid offline_access https://{B2C_TENANT}/{API_RESOURCE}/apiaccess"
)
USER_AGENT: Final = "Kohler-Konnect/3.0.0 (Android 14; HomeAssistant)"

API_CUSTOMER_DEVICES: Final = (
    "/devices/api/v1/device-management/customer-device/{tenant_id}"
)
API_FAUCET_STATE: Final = "/devices/api/v1/device-management/faucet-state/{device_id}"
API_FAUCET_CONFIG: Final = (
    "/devices/api/v1/device-management/faucet-configuration/{device_id}"
)
API_FAUCET_USAGE: Final = "/devices/api/v1/device-management/faucet-usage/{device_id}"
# One faucet's presets ("experiences"), as the app's faucet screen lists them;
# takes the device id as the PascalCase query parameter DeviceIds.
API_FAUCET_EXPERIENCE: Final = "/devices/api/v1/device-management/faucet-experience"
# Every preset on the account, for all of its devices.
API_CUSTOMER_EXPERIENCE: Final = (
    "/devices/api/v1/device-management/customer-experience/{tenant_id}"
)
# Firmware check. Faucets use the "sensate" type and, unlike Anthem showers,
# no ?releasetarget query.
API_FIRMWARE: Final = "/platform/api/v1/firmware/sensate/{device_id}"
# Registers a "mobile device" and returns Azure IoT Hub credentials; DELETE
# with "/{tenant_id}/{identity}" unregisters it.
API_MOBILE_SETTINGS: Final = "/platform/api/v1/mobile/settings"
COMMAND_PREFIX: Final = "/platform/api/v1/commands/"
CMD_DISPENSE: Final = COMMAND_PREFIX + "faucet/dispense"
CMD_ONOFF: Final = COMMAND_PREFIX + "faucet/onoff"

# Konnect faucet SKUs: the Sensate, and a second kitchen faucet (probably the
# Setra) that the app treats identically. Commands send the device's own SKU.
DEFAULT_SKU: Final = "SEN"
FAUCET_SKUS: Final = frozenset({"SEN", "SET"})

# Kohler's "statusCode", inside the reply body, compared as text. A command
# can be refused with one of these even when the HTTP status is 200.
STATUS_OFFLINE: Final = "900"
STATUS_FIRMWARE_UPDATING: Final = "903"
STATUS_NOT_DISPENSED: Final = "906"
STATUS_MESSAGES: Final = {
    STATUS_OFFLINE: "the faucet is offline",
    STATUS_FIRMWARE_UPDATING: "a firmware update is in progress",
    "904": "the faucet reported an error",
    "905": "preparing to retry the update",
    STATUS_NOT_DISPENSED: "water could not be dispensed",
    "908": "the firmware is up to date",
    "909": "the faucet reported an error",
    "911": "the faucet reported an error",
    "915": "the maximum number of presets is reached",
    "916": "the name is already in use",
    "917": "something went wrong",
    "918": "the faucet reported an error",
}
# Codes that mean a command was not carried out, whatever the HTTP status.
COMMAND_FAILURES: Final = frozenset(
    {STATUS_OFFLINE, STATUS_FIRMWARE_UPDATING, STATUS_NOT_DISPENSED}
    | {"904", "909", "911", "917", "918"}
)

# Faucet state values that block remote water, as in the Konnect app: a
# closed handle, and a firmware download (faucet-state "progress").
HANDLE_CLOSED: Final = "closed"
PROGRESS_DOWNLOADING: Final = "downloading"

# --- Instant updates (Azure IoT Hub over MQTT) ---------------------------
MQTT_PORT: Final = 8883
# Kohler delivers events as IoT Hub direct methods on this account-wide topic,
# and each must be acknowledged.
MQTT_SUBSCRIBE_TOPIC: Final = "$iothub/methods/POST/#"
MQTT_RESPONSE_TOPIC: Final = "$iothub/methods/res/200/?$rid={rid}"
# Reconnect delays in seconds; the last one repeats.
MQTT_BACKOFF: Final = (10, 60, 300, 900)

# --- Config entry ------------------------------------------------------------
CONF_DEVICE_ID: Final = "device_id"
CONF_SKU: Final = "sku"
CONF_UNIT_SYSTEM: Final = "unit_system"
CONF_MAX_RUN_MINUTES: Final = "max_run_minutes"
# Water turned on from Home Assistant is turned off after this long; 0 = never.
DEFAULT_MAX_RUN_MINUTES: Final = 10
UNIT_SYSTEM_METRIC: Final = "metric"
UNIT_SYSTEM_IMPERIAL: Final = "imperial"
UNIT_SYSTEMS: Final = [UNIT_SYSTEM_METRIC, UNIT_SYSTEM_IMPERIAL]

# --- Dispensing --------------------------------------------------------------
# Hard limits for a single dispense, in milliliters, whatever unit is used.
# The Konnect app dispenses and saves presets up to 3 gallons (12 quarts, 48
# cups: 11.356 L, a little more after its single-precision conversion).
DISPENSE_MIN_ML: Final = 10.0
DISPENSE_MAX_ML: Final = 11360.0

SERVICE_DISPENSE: Final = "dispense"
ATTR_AMOUNT: Final = "amount"
ATTR_UNIT: Final = "unit"
ATTR_AMOUNT_ML: Final = "amount_ml"
ATTR_AMOUNT_L: Final = "amount_l"
ATTR_PRESET: Final = "preset"
