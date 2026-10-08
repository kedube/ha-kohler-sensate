# Kohler Sensate (SKU `SEN`, family `faucet`) — cloud protocol

What this integration relies on, and what's been seen live from a Sensate
(firmware 16.0). Base URL: `https://api-kohler-us.kohler.io`.

The full reference, from a decompile of Konnect for Android **3.0.6** and
shared with the Anthem integration, is
[ha-kohler-anthem/docs/protocol](https://github.com/kedube/ha-kohler-anthem/tree/main/docs/protocol)
([faucet chapter](https://github.com/kedube/ha-kohler-anthem/blob/main/docs/protocol/sensate_faucet.md)).
Look there for request and response models; this file doesn't repeat them.
Items below marked *(app)* come from that decompile and haven't been seen
live yet.

The app handles a second faucet SKU, `SET` (probably the Setra), exactly like
`SEN`, and always sends the device's own `sku`; so does the integration.

## Auth
- Azure AD B2C, tenant `konnectkohler.onmicrosoft.com`.
- **Reads**: ROPC token (username + password + app client_id/apim_key/api_resource).
- **Writes**: ★ **VALIDATED LIVE — the faucet (SEN) ACCEPTS writes with the ROPC
  token too. No B2C_1A_signin browser token is needed.** (`onoff` → HTTP 200
  `{correlationId, timestamp}`; `dispense` 250 mL → ran water, stopped at ~250 mL.)
  This differs from the Anthem shower (gcs), which the community found rejects
  ROPC on writes with 403. So for the Sensate, username+password is enough.
  - The B2C_1A_signin flow (Authorization-Code + PKCE) exists in
    `recon/get_write_token.py` as a manual fallback, but is unnecessary. Its
    redirect is now `msauth.com.kohler.hermoth://auth`; the older
    `msauth://com.kohler.hermoth/…` is no longer registered (AADB2C90006). The
    Anthem integration drives this policy server-side, without a browser
    (`anthem/auth.py`), should Kohler ever start refusing ROPC writes for
    faucets too. The integration reports an HTTP 403 on a command as exactly
    that.
- App-global (non-secret) values: client_id `8caf9530-1d13-48e6-867c-0f082878debc`,
  api_resource `f5d87f3d-bdeb-4933-ab70-ef56cc343744`, APIM key `429ecb1d0b5e4258aa0a2bfadd82a493`.
- `tenantId` = `oid` claim in the access token.

## Reads (GET, ROPC token OK)
| Purpose | Path |
|---|---|
| All devices | `/devices/api/v1/device-management/customer-device/{tenantId}` |
| Faucet state | `/devices/api/v1/device-management/faucet-state/{deviceId}` |
| Water usage | `/devices/api/v1/device-management/faucet-usage/{deviceId}?FromDate=…&ToDate=…&Interval=…` |
| Configuration | `/devices/api/v1/device-management/faucet-configuration/{deviceId}` |
| Presets, every device | `/devices/api/v1/device-management/customer-experience/{tenantId}` |
| Presets, one faucet *(app)* | `/devices/api/v1/device-management/faucet-experience?DeviceIds={deviceId}` |
| Firmware check *(app)* | `/platform/api/v1/firmware/sensate/{deviceId}` (no `releasetarget` query) |

An unknown route answers `{ "statusCode": 404, "message": "Resource not
found" }` from the API gateway; a known route with an unknown device answers
`{"detail":null,"error":null,"statusCode":404,"message":"Not Found"}`.
`faucet-experience/{deviceId}` answered as an unknown route when probed; the
app's preset list passes the id as the PascalCase query `DeviceIds` instead.

`faucet-state` → `state` object (VERIFIED live, firmware 16.0, polled every
second through dispenses, an app preset and use by hand):
- `status` — `"On"` while water runs, however it was started (dispense,
  preset, hand), else `"Off"`. Follows the faucet within about a second.
- `progress` — always `"NotStarted"`, even mid-dispense and mid-preset. In the
  app it's the **firmware download**: `"Downloading"` locks the controls
  *(app)*. The integration never reads it as water.
- `handleState` — `"OPEN"` or `"CLOSED"` (manual-handle position). The app
  refuses remote dispenses, on/off and presets while it's `CLOSED`: "Open the
  handle to remote dispense water." *(app)* So does the integration (except
  turning water off).
- `quantity` — always `null`, even mid-dispense; the app never reads it.
Envelope also carries `connectionState` ("Connected"), `lastConnected`, `sku`,
and an `updatedTimestamp` that doesn't change with the state.

`faucet-configuration` (VERIFIED): `configuration.about` (name "SENSATE", model
"SEN", serial, firmware 16.0, hardware "CC3235SF") + **`leakDetectionHistory`**
(array; empty = no leaks) → this is the leak-alert source. Each entry is
`{"leakDetectionTime": <epoch seconds>}` *(app)*; the app sorts them newest
first and has no call to clear them.

Real-time updates arrive over **MQTT**; see Instant updates below.

## Writes (POST `/platform/api/v1/commands/faucet/{cmd}`, ROPC token OK)

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
`action` ∈ `"ON"` | `"OFF"` (the app also sends `On`/`Off`). The app stops a
running dispense or preset with `onoff` `OFF`.

### `experience` / `presetexperience` — run a saved preset *(app; not used)*
Two different bodies:
- `experience` (the faucet's own screens): `{ status: "ON", experienceId,
  experienceTitle, experienceQuantity: "<liters as a string>", deviceId,
  tenantId, sku }`, all strings.
- `presetexperience` (the account-wide preset screen): `{ deviceId,
  experienceId, sku, status: "ON" | "OFF", tenantId }`.

The integration dispenses a preset's amount with the verified `dispense`
command instead. Only preset runs produce `SENSATE_EXP_STS`.

### `factoryreset` — (destructive; not used)

### Errors in the reply body
Konnect replies carry a `statusCode` beside `message`, which the app compares
as a **string**. A command can answer HTTP 200 and still be refused this way.
The integration treats these codes as a refused command, whatever the HTTP
status: `900` offline, `903` firmware update in progress, `906` "Water could
not be dispensed. Please turn on your faucet manually.", and the generic
`904`, `909`, `911`, `917`, `918`. The full table is in the shared reference.

## Example device
- deviceId `sen-xxxxxxxxxx`, sku `SEN`, account `waterUnits: "Metric"`.
- tenantId `<your-account-oid>` (the `oid` claim from your own token).

## Connection state
`faucet-state` also returns `connectionState` (`"Connected"` seen live) and
`lastConnected`. The integration treats any other `connectionState` as
offline, and a missing one as online. (The app's faucet screens treat a
missing one as offline; it has always been present.)

## Presets
The integration reads the faucet's own list first, as the app's faucet screen
does *(app)*: `GET …/faucet-experience?DeviceIds={deviceId}` →
`{faucetExperienceList: [{deviceId, experience: [...]}]}`, with entries shaped
like the ones below. If that fails or is empty, it falls back to
`customer-experience`, which the app 3.0.6 no longer reads for faucets.
Diagnostics show which list was used.

`GET /devices/api/v1/device-management/customer-experience/{tenantId}` (VERIFIED)
lists the presets of every device on the account, in several shapes. The
Sensate's details are in `sensateExperiences`:

```json
{ "deviceId": "sen-…", "sku": "SEN", "experienceId": "<uuid>",
  "title": "A Glass of Water", "unit": "Metric", "dispenseAmount": 0.236588,
  "displayQuantity": "1 Cups", "lastUsedTime": 1791397632, "state": "OFF",
  "progress": null, "createdTime": 1744504652 }
```

`dispenseAmount` is in liters. `displayQuantity` is `"<amount> <Unit>"`
with Unit one of Milliliters, Liters, Cups, Quarts or Gallons, and may use
`¼ ½ ¾`; the integration reads it if `dispenseAmount` is missing.
`experiences` repeats id and title only, and `faucetPresetsExperiences` /
`recentlyUsedPresetsExperiences` add the device's name but no amount.

The app's dispense and preset amounts run from 1 cup (≈0.2366 L) to 3 gallons
(≈11.36 L) *(app)*; the integration accepts 10 mL to 11.36 L. At most 10
presets per faucet.

## Firmware
`GET /platform/api/v1/firmware/sensate/{deviceId}` *(app)* →
`{firmwareUpdateAvailable, firmware (latest), currentFirmware,
mandatoryUpdate, otaStatus, …}`. The app decides "update available" from
`firmwareUpdateAvailable` alone; the integration checks hourly, and falls
back to `about.firmware.latestVersion` if the check doesn't answer. Install
is a `POST` to the same path with `{tenantId, firmwareNumber,
releaseTarget: "Public"}` *(app)*; the integration doesn't send it until it
has been tried against a faucet.

## Water usage (`faucet-usage`)
`GET …/faucet-usage/{deviceId}?FromDate=2026-10-01&ToDate=2026-10-07&Interval=DAY`
(VERIFIED). The parameter names are PascalCase, unlike the rest of the API; a
wrong case answers the same generic 400 as no parameters. `Interval` is `DAY`
or `MONTH`; `WEEK` and `YEAR` answer 400. Dates are `YYYY-MM-DD`; there's no
range limit (2020 to now in months works).

```json
{ "deviceId": "sen-…", "interval": "Day",
  "faucetUsageDataDetailsList": [
    { "intervalKey": "2026-10-07", "quantity": 1.5274, "waterUsage": 1.5274,
      "hotWaterUsage": 0, "coldWaterUsage": 1.5274, "usageDuration": 11,
      "temperature": 17.24 } ],
  "maxWaterUsage": 1.5274, "avgWaterUsage": 0.1076, … }
```

Volumes are liters (a day's buckets sum exactly to that month's). Month keys
look like `2026-10`. `usageDuration` is seconds but has implausible outliers
(535428 in one month), so the integration ignores it.

## Instant updates (Azure IoT Hub)
Confirmed for Anthem showers by kohler-anthem and kohler-anthem-plus, and for
the Sensate (firmware 16.0): messages carrying the faucet's `deviceid` arrive
when it changes.

1. `POST /platform/api/v1/mobile/settings` (ROPC token OK) with
   `{tenantId, mobileDeviceId, username: "HomeAssistant", os: "Android",
   devicePlatform: "FirebaseCloudMessagingV1", deviceHandle: "ha_<id>",
   tags: ["FirmwareUpdate"]}` returns `ioTHubSettings` with `ioTHub` (host),
   `deviceId`, `username` and `password` (a SAS token). Reuse one
   `mobileDeviceId`; each call returns fresh credentials.
2. MQTT 3.1.1 over TLS to `ioTHub:8883`, client id `deviceId`.
3. Subscribe to `$iothub/methods/POST/#`. Events arrive there as direct
   methods (`…/POST/ExecuteControlCommand/?$rid=N`) for every device on the
   account (top-level `sku` and `deviceid`), and must be acknowledged on
   `$iothub/methods/res/200/?$rid=<rid>`.
4. Nothing is replayed on connect, so re-read the state after connecting.
   On disconnect, register again for fresh credentials.
5. Since one subscription receives every device on the account, the
   integration holds **one** registration and connection per account and
   routes each message by its `deviceid`, however many faucets are set up.
6. Removing the last faucet entry on an account sends `DELETE
   /platform/api/v1/mobile/settings/{tenantId}/{mobileDeviceId}` *(app)*, as
   the app does on sign-out, so the registration doesn't linger.

Sensate messages seen live (envelope trimmed):

```json
{"deviceid": "sen-…", "sku": "SEN", "type": "STS", "timestamp": "1791397638",
 "data": {"type": "Status", "code": "SENSATE_STS",
          "attributes": [{"code": "SENSATE_STS", "status": "On", "handle": "OPEN"}]}}

{"deviceid": "sen-…", "sku": "SEN", "type": "STS",
 "data": {"type": "Status", "code": "SENSATE_EXP_STS",
          "attributes": [{"code": "SENSATE_EXP_STS", "name": "A Glass of Water",
                          "experienceid": "<uuid>", "status": "ON"}]}}
```

`SENSATE_STS` comes with every water on/off: dispenses, presets and use by
hand. `SENSATE_EXP_STS` comes when a preset run from the app starts (`ON`) and
ends (`OFF`). A dispense from the API sends only `SENSATE_STS`, and no
message carries the dispensed amount. Opening the app's presets sends
nothing.

The app also acts on *(app)*:
- `SENSATE_LEAK_DETECTED_ALT`: a leak alert; only its `code` is checked. The
  integration turns *Leak* on at once, until cleared.
- `INSTALL_FIRMWARE_STS` `{status: "Installed" | "Aborted", version}`: a
  firmware install ended. The integration re-reads the firmware.

Messages that don't name a `deviceid` are ignored.

## Throttling
429 and 503 replies may carry `Retry-After` (seconds or an HTTP date). The
integration waits that long, clamped to 30 s–15 min.

## Token lifecycle (as implemented in the integration)
- ROPC sign-in: `POST https://konnectkohler.b2clogin.com/tfp/konnectkohler.onmicrosoft.com/B2C_1_ROPC_Auth/oauth2/v2.0/token`
  with `grant_type=password`, `client_id`, `username`, `password`,
  `scope=openid offline_access https://konnectkohler.onmicrosoft.com/{api_resource}/apiaccess`.
- Renewal: same endpoint, `grant_type=refresh_token`. Done 5 min before
  `expires_in`; if B2C rejects the refresh token the integration signs in
  again with the password.
- API calls send `Authorization: Bearer …`, `Ocp-Apim-Subscription-Key`, and a
  Konnect-style `User-Agent`. An HTTP 401 renews the token and retries once.

## Confidence / still to verify live
- Payloads are exact (Gson `@SerializedName` from the APK models).
- Verified live: the HTTP 200 from `dispense` is enough to run the water and
  stop at the requested amount; no MQTT listener is needed.
- Seen live (firmware 16.0): `status` `On`/`Off`, `progress` only
  `NotStarted`, `handleState` `OPEN`/`CLOSED`, `connectionState` `Connected`.
  The configuration reply repeats the device id under `id`, and
  `about.firmware` is an object (`{"version": "16.0", "latestVersion": …}`).
- Still unknown: whether `dispense` ever needs a prior `onoff:ON`, other
  `connectionState` values, what Kohler answers when commanded with the
  handle closed, and how accurately amounts under 1 cup are poured.
- To confirm live: the `leakDetectionHistory` entry shape and the leak
  alert's full payload, the `faucet-experience?DeviceIds=` and firmware-check
  replies, and `progress` during an update (expected `Downloading`). The
  integration keys each leak event by its `leakDetectionTime`; events cleared
  by older versions, keyed by an `id` or a hash of the event, stay cleared.
