# Kohler Sensate (SKU `SEN`, family `faucet`) — cloud protocol

Reverse-engineered from Konnect Android app v3.0.1 (static decompile, jadx) and
confirmed against the live account. Base URL: `https://api-kohler-us.kohler.io`.

## Auth
- Azure AD B2C, tenant `konnectkohler.onmicrosoft.com`.
- **Reads**: ROPC token (username + password + app client_id/apim_key/api_resource).
- **Writes**: ★ **VALIDATED LIVE — the faucet (SEN) ACCEPTS writes with the ROPC
  token too. No B2C_1A_signin browser token is needed.** (`onoff` → HTTP 200
  `{correlationId, timestamp}`; `dispense` 250 mL → ran water, stopped at ~250 mL.)
  This differs from the Anthem shower (gcs), which the community found rejects
  ROPC on writes with 403. So for the Sensate, username+password is enough.
  - The B2C_1A_signin browser flow (Authorization-Code + PKCE, redirect
    `msauth://com.kohler.hermoth/…`, manual paste-back) exists in
    `recon/get_write_token.py` as a fallback, but is unnecessary and in practice
    was unworkable (Chrome hangs on Kohler's login page; Safari completes login
    but discards the `msauth://` redirect).
- App-global (non-secret) values: client_id `8caf9530-1d13-48e6-867c-0f082878debc`,
  api_resource `f5d87f3d-bdeb-4933-ab70-ef56cc343744`, APIM key `429ecb1d0b5e4258aa0a2bfadd82a493`.
- `tenantId` = `oid` claim in the access token.

## Reads (GET, ROPC token OK)
| Purpose | Path |
|---|---|
| All devices | `/devices/api/v1/device-management/customer-device/{tenantId}` |
| Faucet state | `/devices/api/v1/device-management/faucet-state/{deviceId}` |
| Water usage | `/devices/api/v1/device-management/faucet-usage/{deviceId}` |
| Configuration | `/devices/api/v1/device-management/faucet-configuration/{deviceId}` |
| Presets/experiences | `/devices/api/v1/device-management/faucet-experience/{deviceId}` |

`faucet-state` → `state` object (VERIFIED live):
- `status` — e.g. `"Off"` (idle). Also `"On"` / dispensing states.
- `progress` — e.g. `"NotStarted"` (dispense progress: NotStarted / …InProgress / …).
- `handleState` — e.g. `"OPEN"` (physical manual-handle position).
- `quantity` — double or `null` when no dispense active.
Envelope also carries `connectionState` ("Connected"), `lastConnected`, `sku`.

`faucet-configuration` (VERIFIED): `configuration.about` (name "SENSATE", model
"SEN", serial, firmware 16.0, hardware "CC3235SF") + **`leakDetectionHistory`**
(array; empty = no leaks) → this is the leak-alert source.

Real-time updates also arrive over **MQTT** (`faucet/preset/PresetMqttData`);
after a dispense the app waits on MQTT for progress/completion.

`faucet-usage` returned HTTP 400 without extra params (needs a date range or
similar — TBD). `faucet-experience` returned 404 (likely no presets saved yet,
or the path needs a suffix — TBD).

## Writes (POST `/platform/api/v1/commands/faucet/{cmd}`, B2C_1A_signin token)

### `dispense` — dispense a measured amount  ← headline feature
```json
{ "deviceId": "sen-xxxxxxxxxx", "quantity": 0.25, "sku": "SEN", "tenantId": "<oid>" }
```
`quantity` is in **LITERS** (double). The app converts the user's unit before
sending: mL × 0.001, cups × 0.2365880, quarts × 0.946353, gallons × 3.785411784,
liters × 1. So 250 mL → `0.25`; 1 cup → `0.236588`.

### `onoff` — turn water on/off
```json
{ "action": "ON", "deviceId": "sen-xxxxxxxxxx", "sku": "SEN", "tenantId": "<oid>" }
```
`action` ∈ `"ON"` | `"OFF"`.

### `presetexperience` / `experience` — run a saved preset
`FaucetExperienceRequest`: `{ deviceId, experienceId, experienceQuantity,
experienceTitle, sku, status, tenantId }`.

### `factoryreset` — (destructive; not used)

## Example device
- deviceId `sen-xxxxxxxxxx`, sku `SEN`, account `waterUnits: "Metric"`.
- tenantId `<your-account-oid>` (the `oid` claim from your own token).

## Confidence / still to verify live
- Payloads are exact (Gson `@SerializedName` from the APK models).
- Unverified against the running device yet: (a) whether `dispense` alone starts
  water or needs a prior `onoff:ON`; (b) the `faucet-state`/`faucet-usage`
  response shapes (need one authenticated GET); (c) whether writes require the
  MQTT listener or the HTTP 201 is enough. All resolved by one B2C write test.
