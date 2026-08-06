"""Constants for the Kohler Sensate integration."""

from __future__ import annotations

DOMAIN = "kohler_sensate"

# How often to poll the faucet's live state (seconds).
SCAN_INTERVAL = 10
# Refresh the configuration (firmware + leak history) less often.
CONFIG_REFRESH_CYCLES = 30

# --- App-global credentials (baked into the Konnect mobile app; not secrets) ---
DEFAULT_CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
DEFAULT_API_RESOURCE = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"
DEFAULT_APIM_KEY = "429ecb1d0b5e4258aa0a2bfadd82a493"

# Config-entry data keys
CONF_TENANT_ID = "tenant_id"
CONF_DEVICE_ID = "device_id"

# --- API endpoints ---
API_CUSTOMER_DEVICES = "/devices/api/v1/device-management/customer-device/{cid}"
API_FAUCET_STATE = "/devices/api/v1/device-management/faucet-state/{did}"
API_FAUCET_CONFIG = "/devices/api/v1/device-management/faucet-configuration/{did}"
CMD_DISPENSE = "/platform/api/v1/commands/faucet/dispense"
CMD_ONOFF = "/platform/api/v1/commands/faucet/onoff"

# Device SKU / family for the Sensate faucet.
SKU = "SEN"

# Quick one-tap dispense amounts (milliliters) → dashboard buttons.
QUICK_AMOUNTS_ML = [50, 250, 500, 750, 1000, 2000, 3000]

# Bounds for the free-amount dispense number entity (milliliters).
DISPENSE_MIN_ML = 10
DISPENSE_MAX_ML = 4000
DISPENSE_STEP_ML = 10

# Service name for dispensing an arbitrary amount.
SERVICE_DISPENSE = "dispense"
ATTR_AMOUNT_ML = "amount_ml"
