# Kohler Sensate for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/kedube/ha-kohler-sensate)](https://github.com/kedube/ha-kohler-sensate/releases)
[![Validate](https://github.com/kedube/ha-kohler-sensate/actions/workflows/validate.yml/badge.svg)](https://github.com/kedube/ha-kohler-sensate/actions/workflows/validate.yml)
[![CI](https://github.com/kedube/ha-kohler-sensate/actions/workflows/ci.yml/badge.svg)](https://github.com/kedube/ha-kohler-sensate/actions/workflows/ci.yml)

Unofficial Home Assistant integration for the **Kohler Sensate** touchless
kitchen faucet with Konnect (SKU `SEN`). Its headline feature is **dispensing
a measured amount of water**, in metric or imperial units, from dashboards,
automations, scripts and voice assistants. It also turns the water on and off
and reports the faucet's status and leak alerts.

Kohler dropped HomeKit support for the Sensate and never shipped a Home
Assistant integration, so this one talks to the Kohler Konnect cloud
directly, signing in with your normal Konnect email and password.

> [!WARNING]
> This is not affiliated with or endorsed by Kohler. It relies on Kohler's
> undocumented cloud API, which can change without notice.

## Features

- **Water switch**: turn the water on and off.
- **Quick-dispense buttons**: one tap for a fixed amount.
  - Metric: 50 mL, 250 mL, 500 mL, 750 mL, 1 L, 2 L, 3 L
  - Imperial: ¼ cup, ½ cup, 1 cup, 2 cups, 1 quart, ½ gallon, 1 gallon
- **Any amount**: a *Dispense amount* number plus a *Dispense set amount*
  button, and the `kohler_sensate.dispense` action for automations, scripts
  and voice, which also dispenses presets by name.
- **Metric or imperial**: chosen per faucet in the integration options.
- **Water usage**: a lifetime total and today's use, as Kohler counts them,
  ready for Home Assistant's Energy dashboard.
- **Sensors**: status, handle position, last amount dispensed, leak detected,
  currently dispensing (including presets run from the app), connection, and
  firmware updates.
- **Leak alerts you can clear**: acknowledge a leak once it's dealt with; a
  new leak turns the alert back on.
- **Water safety limit**: water turned on from Home Assistant turns off by
  itself after 10 minutes (adjustable), even across restarts.
- **Offline detection**: when the faucet loses its connection, its entities
  show as unavailable instead of stale values, and commands give a clear
  error.
- **Instant updates**: listens to Kohler's real-time feed like the Konnect
  app does, so changes show up within a second or two, with light polling as
  a safety net.
- **Konnect presets**: choose any preset saved in the app from a dropdown
  and dispense it, however many presets you have.
- **Automatic sign-in**: sessions renew on their own; you're only asked to
  sign in again if Kohler rejects your password.
- **Repair notices** when the faucet disappears from your account or Kohler's
  API changes, and **diagnostics** with personal data redacted.
- **In your language**: English, Dutch, French, German, Italian, Polish,
  Portuguese (Brazil), Spanish (Spain and Latin America) and Swedish. Other
  languages show English.

## Requirements

- Home Assistant **2026.3** or newer.
- A Kohler Konnect account with the Sensate faucet already set up in the
  Konnect app.
- Works with any Konnect account, as far as is known: the Konnect app itself
  only uses Kohler's US cloud. Accounts outside the US haven't been confirmed;
  please [report](https://github.com/kedube/ha-kohler-sensate/issues) yours.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=kedube&repository=ha-kohler-sensate&category=integration)

Or add it by hand:

1. In Home Assistant, open **HACS**, select **⋮ → Custom repositories**.
2. Add `https://github.com/kedube/ha-kohler-sensate` with type
   **Integration**.
3. Search for **Kohler Sensate Faucet**, select **Download**.
4. Restart Home Assistant.

### Manual

1. Download the [latest release](https://github.com/kedube/ha-kohler-sensate/releases).
2. Copy `custom_components/kohler_sensate/` into the `custom_components/`
   folder of your Home Assistant configuration directory.
3. Restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up Kohler Sensate.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=kohler_sensate)

1. Go to **Settings → Devices & services → Add integration** and choose
   **Kohler Sensate Faucet**.
2. Enter your Konnect account details:

   | Field    | Description                                        |
   | -------- | -------------------------------------------------- |
   | Email    | The email you use to sign in to the Konnect app.   |
   | Password | Your Konnect password.                             |

3. If the account has more than one Sensate, pick the faucet to add. Repeat
   the steps to add the others.

The faucet's name comes from the Konnect app (for example *Kitchen*), so its
entities are named like `button.kitchen_dispense_250_ml`.

To change the Konnect email or password later, go to the integration's page
and select **⋮ → Reconfigure** next to the faucet. The account must still
own the faucet.

### Options

Go to **Settings → Devices & services → Kohler Sensate → Configure**:

| Option                            | Default                  | Description |
| --------------------------------- | ------------------------ | ----------- |
| Units                             | Home Assistant's system  | Metric or imperial, for dispensing (see below). |
| Water safety limit                | 10 minutes               | Turns the water off this long after it was turned on from Home Assistant, unless it was turned off in the meantime. `0` turns the limit off. |
| Instant updates                   | On                       | See [Instant updates](#instant-updates). |

#### Units

| Units    | Quick buttons            | Dispense amount | Last dispensed | Water usage | Default action unit |
| -------- | ------------------------ | --------------- | -------------- | ----------- | ------------------- |
| Metric   | 50 mL … 3 L              | mL              | mL             | L           | mL                  |
| Imperial | ¼ cup … 1 gallon         | fl oz           | fl oz          | gal         | fl oz               |

New faucets default to your Home Assistant unit system. Changing the option
reloads the faucet and replaces the quick-dispense buttons with the other
set. The *Dispense amount* keeps the same volume, converted to the new unit.
Imperial units are US customary (1 cup = 236.6 mL), the same as the Konnect
app.

## Entities

Each faucet is a device with the entities below. Entity IDs follow the
faucet's name; the examples are for a faucet named *Kitchen*.

### Sensors

| Sensor | Entity ID | State | Updates | Description |
| ------ | --------- | ----- | ------- | ----------- |
| Status | `sensor.kitchen_status` | `on` or `off` | Live | Whether water is flowing, however it started: by hand, hands-free, a dispense or Home Assistant. |
| Total water used | `sensor.kitchen_total_water_used` | L or gal | Every 30 min | Lifetime total: all the water Kohler has recorded for the faucet, up to now. Only goes up. For the [Energy dashboard](#water-usage-and-the-energy-dashboard). |
| Water used today | `sensor.kitchen_water_used_today` | L or gal | Every 30 min | Water used today, the same figure as the Konnect app's daily chart. Starts over at midnight. |
| Last dispensed | `sensor.kitchen_last_dispensed` | mL or fl oz | Each dispense | The most recent amount dispensed from Home Assistant: a button, a preset or the action. Kept across restarts. Kohler doesn't report amounts, so dispenses started elsewhere aren't included. |
| Handle | `sensor.kitchen_handle` | `open` or `closed` | Live | Diagnostic. Position of the manual handle. |
| Dispense progress | `sensor.kitchen_dispense_progress` | `not_started` | Live | Diagnostic, disabled by default. Kohler's progress field, which the Sensate never moves, even mid-dispense. Use *Dispensing* instead. |

*Live* means within a second or two with [instant updates](#instant-updates),
otherwise within 30 seconds. Water usage is also read about 2 minutes after
the water stops, and just after midnight. *Status* and *Handle* show their
states in your language; automations and templates use the values above.

### Binary sensors

| Binary sensor | Entity ID | On when | Updates | Attributes |
| ------------- | --------- | ------- | ------- | ---------- |
| Leak | `binary_sensor.kitchen_leak` | Kohler reports a leak that hasn't been cleared with *Clear leak alert*. | Every 5 min | `events`: leak events in Kohler's history. `uncleared_events`: those not cleared yet. `latest`: the newest event. |
| Dispensing | `binary_sensor.kitchen_dispensing` | A measured amount is dispensing: from Home Assistant, or a preset run from the Konnect app (needs instant updates). Turns off when the water stops, or after 2 minutes at most. | Live | `preset`: the preset's name, when it's a preset. |
| Connected | `binary_sensor.kitchen_connected` | Kohler's cloud can reach the faucet. Diagnostic. | Each poll | `last_connected`: when the faucet last connected, as Kohler reports it. |

### Controls

| Control | Entity ID | Type | Description |
| ------- | --------- | ---- | ----------- |
| Water | `switch.kitchen_water` | Switch | Turns the water on or off. Water turned on here turns off by itself after the [water safety limit](#options). |
| Dispense *amount* | `button.kitchen_dispense_250_ml`, … | Button (7) | Dispenses a fixed amount. The amounts follow the [units](#units) option. |
| Dispense amount | `number.kitchen_dispense_amount` | Number (configuration) | How much *Dispense set amount* pours: 10–4000 mL or 0.5–135 fl oz. Kept across restarts. |
| Dispense set amount | `button.kitchen_dispense_set_amount` | Button | Dispenses the *Dispense amount*. |
| Preset | `select.kitchen_preset` | Select (configuration) | Which preset saved in the Konnect app *Dispense preset* pours. Attributes: `amount` and `unit`. See [Konnect presets](#konnect-presets). |
| Dispense preset | `button.kitchen_dispense_preset` | Button | Dispenses the chosen *Preset*. |
| Clear leak alert | `button.kitchen_clear_leak_alert` | Button | Marks the current leak events as dealt with, which turns *Leak* off. |

*Dispense amount* and *Preset* are settings for the two buttons, so the
device page lists them under **Configuration**, and the buttons under
**Controls**. Home Assistant's auto-generated dashboard leaves configuration
entities out. To keep each setting next to its button, add a card like this
to a dashboard:

```yaml
type: entities
title: Kitchen faucet
entities:
  - number.kitchen_dispense_amount
  - button.kitchen_dispense_set_amount
  - select.kitchen_preset
  - button.kitchen_dispense_preset
```

### Firmware

| Update | Entity ID | On when | Description |
| ------ | --------- | ------- | ----------- |
| Firmware | `update.kitchen_firmware` | Kohler has newer firmware than the faucet's. | Diagnostic. Installed and latest versions, checked every 5 minutes. Install updates from the Konnect app; Home Assistant shows when one is in progress. |

While *Connected* is off, entities that show or control the live faucet are
unavailable. *Leak*, *Clear leak alert*, *Dispense amount*, *Preset*,
*Last dispensed*, the water usage sensors and *Firmware* stay available.

## Actions

### `kohler_sensate.dispense`

Dispenses a measured amount of water, or a preset saved in the Konnect app.
Every dispense must be between 10 mL and 4 L.

| Field       | Required | Description |
| ----------- | -------- | ----------- |
| `amount`    | Yes\*    | How much to dispense, in `unit`. |
| `unit`      | No       | `ml`, `l`, `fl_oz`, `cup`, `qt` or `gal`. Defaults to `ml` (metric) or `fl_oz` (imperial), per the faucet's options. |
| `preset`    | Yes\*    | A preset's name, as the *Preset* dropdown lists it (for example `One Cup (2)` for a second "One Cup"). Case doesn't matter. |
| `device_id` | No\*\*   | The faucet to use. |
| `amount_ml` | No       | Legacy: amount in mL, instead of `amount`. |
| `amount_l`  | No       | Legacy: amount in liters, instead of `amount`. |

\* Give exactly one of `amount`, `preset`, `amount_ml` or `amount_l`. An
unknown preset is refused with a list of the faucet's presets, and nothing is
dispensed; with several faucets, each must have it.
\*\* Required when more than one faucet is set up. The action won't guess
and run water at every faucet.

```yaml
# Metric
action: kohler_sensate.dispense
data:
  amount: 500
  unit: ml
```

```yaml
# Imperial, aimed at one faucet
action: kohler_sensate.dispense
data:
  device_id: 0123456789abcdef0123456789abcdef
  amount: 2
  unit: cup
```

```yaml
# A preset saved in the Konnect app
action: kohler_sensate.dispense
data:
  preset: A Glass of Water
```

### Examples

**Voice ("Fill the pasta pot")**: create a script and expose it to Assist,
Google or Alexa.

```yaml
script:
  fill_pasta_pot:
    alias: Fill the pasta pot
    sequence:
      - action: kohler_sensate.dispense
        data:
          amount: 4
          unit: qt
```

**Leak alert**: notify on a new leak. Once you've dealt with it, press
*Clear leak alert* so the next leak triggers this again.

```yaml
automation:
  - alias: Sensate leak alert
    triggers:
      - trigger: state
        entity_id: binary_sensor.kitchen_leak
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: The kitchen faucet reported a leak.
```

## How data is updated

With instant updates on (the default), the integration listens to Kohler's
real-time feed and re-reads the faucet the moment it changes, whether from
Home Assistant, the Konnect app or by hand. It still polls Kohler's cloud as
a safety net, adjusting how often to what the faucet is doing:

| When                                                   | With the feed | Without it   |
| ------------------------------------------------------ | ------------- | ------------ |
| Idle                                                   | Every 5 min   | Every 30 s   |
| Water running, dispensing, or within 30 s of a command | Every 30 s    | Every 5 s    |
| Kohler's cloud is unreachable                          | Every 30 s    | Every 30 s   |

"Without it" covers instant updates being off, still connecting, reconnecting
after a drop, or not yet trusted (see below).

Firmware and leak history are checked every 5 minutes, and whenever the feed
reports a change, so a leak shows up within about 5 minutes of Kohler
reporting it.

Water usage is read every 30 minutes, about 2 minutes after the water stops
(once Kohler has counted it), and at the first poll after midnight, so
*Water used today* starts over on time.

Without the feed, if someone uses the faucet by hand, Home Assistant can take
up to 30 seconds to notice, and presets run from the app don't show as
*Dispensing*. *Last dispensed* always records dispenses started from Home
Assistant.

### Instant updates

The integration holds a connection to Kohler's real-time feed (Azure IoT
Hub), the way the Konnect app does. Any message about your faucet makes Home
Assistant re-read its state right away.

Polling relaxes only once the feed has delivered for your faucet, and the
integration keeps checking that it keeps up: if a poll finds a change the
feed never announced, polling goes back to its usual pace until the feed
delivers again. If the connection drops, the integration re-reads the faucet
at once and polls as usual while it reconnects.

The feed has been confirmed with a Sensate on firmware 16.0. Kohler doesn't
document it and could change it; if it stops working, nothing breaks, and
polling carries on.

The connection adds a "HomeAssistant" entry to the notification devices on
your Kohler account. To rely on polling alone, turn off **Instant updates**
under **Configure**. **Download diagnostics** shows whether the feed is
connected, how many messages it has received, and how many changes it missed.

### Konnect presets

Presets saved in the Konnect app (such as "A Glass of Water") are listed in
the *Preset* dropdown, in the app's order, so any number of them fits in two
entities. Choose one, then press *Dispense preset*: it dispenses the preset's
amount using the same command as the other dispense buttons. The dropdown's
`amount` and `unit` attributes show how much.

- Until you choose, the first preset is selected. Your choice is kept across
  restarts, and follows the preset if you rename it in the app.
- If the chosen preset is deleted in the app, the first one is selected
  instead.
- Presets with the same name are listed as "One Cup", "One Cup (2)" and so
  on.
- Presets added, renamed or deleted in the app show up within 5 minutes. A
  faucet without presets gets neither entity until the first one is saved.

In an automation or script, dispense a preset by name with the
[`kohler_sensate.dispense`](#kohler_sensatedispense) action:

```yaml
action: kohler_sensate.dispense
data:
  preset: A Glass of Water
```

### Water usage and the Energy dashboard

Both water sensors show Kohler's own figures, the same as the Konnect app's
charts:

- *Total water used* is the faucet's lifetime total. The integration adds up
  every month Kohler has on record for the faucet, from its first recorded
  use through the current month so far. If Kohler ever reports less, as from
  a partial reply, the sensor keeps its highest reading, so the Energy
  dashboard never mistakes it for a meter reset.
- *Water used today* is today's use, by Home Assistant's time zone. It starts
  over at midnight.

Because the total only ever goes up, it works as a water meter: go to
**Settings → Dashboards → Energy**, then under **Water consumption** select
**Add water source** and pick *Total water used*. The dashboard shows usage
per hour, day, week and month from when the sensor first appeared in Home
Assistant; Kohler's earlier history isn't imported.

It counts water used any way: by hand, hands-free or dispensed. Imperial
faucets show gallons; to change the unit, open the sensor's settings.

## Known limitations

- **Cloud only.** The faucet is controlled through Kohler's cloud, so it
  needs internet access and Kohler's service to be up.
- **Unofficial API.** Kohler can change it at any time.
- **Leak history.** Kohler keeps leak events in the faucet's history and
  doesn't document their format, so the integration can't tell when a leak is
  fixed. Press *Clear leak alert* once it is. If the faucet already had leak
  events when you set up the integration, *Leak* starts on until you clear
  it.
- **New status values.** If Kohler ever reports a status or handle value
  besides those above, the sensor shows it untranslated (for example
  `warming_up`) and the *Water* switch shows unknown. Diagnostics list every
  value seen, so it can be added.
- **Dispenses Home Assistant doesn't start.** Kohler reports a dispense by
  voice, say, only as the water turning on and off, so *Dispensing* doesn't
  show it. Presets run from the Konnect app do show.

## Troubleshooting

- **Re-authentication prompt**: Kohler rejected the saved password, for
  example after you changed it. Open the prompt under **Settings → Devices &
  services** and enter your current password. Polling stops until you do, so
  a wrong password can't lock your Kohler account.
- **Entities are unavailable**: either Kohler's cloud didn't answer, or the
  faucet is offline (check the *Connected* sensor and the faucet's power and
  Wi-Fi). The integration keeps retrying and recovers on its own. If Kohler
  asks it to slow down, it waits as long as Kohler says, up to 15 minutes.
- **Repair notice "no longer on your Kohler account"**: the faucet was removed
  or replaced in the Konnect app. Delete it from Home Assistant and add it
  again.
- **Repair notice "Kohler keeps rejecting requests"**: Kohler probably changed
  its API. Check for an update, or open an issue with diagnostics attached.
- **Water turned off by itself**: the water safety limit did it. The log
  says so. Raise or turn off the limit in the options.
- **Debug logs**: on the integration's page, select **⋮ → Enable debug
  logging**, reproduce the problem, then select **Disable debug logging** to
  download the log. Or add this to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.kohler_sensate: debug
  ```

- **Diagnostics**: on the integration's page, select **⋮** next to the
  faucet, then **Download diagnostics**. Your email, password, account ID,
  device ID, serial number and location are redacted. Attach the file when
  you open an [issue](https://github.com/kedube/ha-kohler-sensate/issues).

## Privacy and security

- Your Konnect password is stored in Home Assistant's config entry storage,
  like other cloud integrations. It's needed to sign in again automatically
  when Kohler's session expires. Keep your Home Assistant backups private.
- Logs never contain your email, password, tokens, account ID or account
  details. Diagnostics redact them too.
- The integration only talks to `konnectkohler.b2clogin.com` (sign-in) and
  `api-kohler-us.kohler.io` (faucet) over HTTPS. With instant updates on, it
  also connects over TLS to the Azure IoT Hub host Kohler assigns
  (`*.azure-devices.net`).
- The client ID and API key in the source are public values built into the
  Konnect app, not secrets.

## Removing the integration

1. Go to **Settings → Devices & services → Kohler Sensate**, select
   **⋮ → Delete** for each faucet.
2. If you installed with HACS, open **HACS**, find **Kohler Sensate Faucet**
   and select **⋮ → Remove**. Otherwise, delete
   `custom_components/kohler_sensate/`.
3. Restart Home Assistant.

Instant updates registered a "HomeAssistant" entry among your Kohler
account's notification devices; deleting the faucet doesn't remove it.

## Upgrading

### From 0.7

- *Dispense amount* and *Preset* moved to the device page's
  **Configuration** section, so Home Assistant's auto-generated dashboard no
  longer shows them. Dashboards you built yourself are unaffected; see
  [Controls](#controls) for a card that keeps each setting next to its
  button.

### From 0.4 or 0.5

- The *Preset: name* buttons are replaced by the *Preset* dropdown and one
  *Dispense preset* button, and the old buttons are removed. Update
  dashboards that showed them. In automations and scripts, use the
  `kohler_sensate.dispense` action with `preset:` instead; see
  [Konnect presets](#konnect-presets).

### From 0.3

- *Status* and *Handle* report `on`/`off` and `open`/`closed` instead of
  `On`/`Off` and `OPEN`/`CLOSED`. Update automations and templates that
  compare them with the old text. The *Water* switch is unchanged.
- *Dispense progress* is disabled for new installs. If you already have it,
  it stays enabled; disable it under the entity's settings, since it never
  changes.

### From 0.1

- Home Assistant 2026.3 or newer is required (0.1 allowed 2024.8).
- Entity IDs are unchanged.
- The `dispense` action now takes `amount` and `unit`. `amount_ml` and
  `amount_l` still work.
- With more than one faucet, the `dispense` action needs `device_id`.
- The `kohler-anthem` Python package is no longer required.

See the [changelog](CHANGELOG.md) for the full list.

## Development

- `PROTOCOL.md` documents the reverse-engineered Kohler cloud API.
- `recon/` has the command-line tools used to explore it. They need
  `pip install kohler-anthem` and your credentials in `recon/.env`. Their
  captures contain personal data, so redact them before sharing.
- Translations live in `custom_components/kohler_sensate/translations/`.
  `strings.json` is the English source and `en.json` must match it. When you
  change a string, update every language; `tests/test_translations.py` fails
  if a language is missing a key or a `{placeholder}`. Corrections from
  native speakers are welcome.
- Run the checks:

  ```sh
  pip install -r requirements_test.txt
  pytest
  ruff check . && ruff format --check .
  python script/release.py check
  ```

### Releasing

Releases are automatic. When CI passes on `main` (lint, tests on both Home
Assistant versions, HACS and hassfest), the release job reads the top of
[CHANGELOG.md](CHANGELOG.md):

- `## Unreleased`: it picks the next version, renames the heading to it, sets
  `manifest.json` to match, commits *Release X.Y.Z* to `main`, tags `vX.Y.Z`,
  and publishes a GitHub release with that section as the notes.
- `## X.Y.Z` that isn't tagged yet: the same, with exactly that version. Use
  this to choose a version yourself, such as 1.0.0.
- Anything else, such as an already released version on top: nothing happens.

The headings under `## Unreleased` decide the bump:

| Heading                                      | Bump                                   |
| -------------------------------------------- | -------------------------------------- |
| `### Breaking changes`, `### Removed`        | major (minor while the version is 0.x) |
| `### Added`, `### Changed`, `### Deprecated` | minor                                  |
| `### Fixed`, `### Security`                  | patch                                  |

So to release, add your entries under `## Unreleased` and push. For changes that
don't need a release, like docs or CI tweaks, leave the changelog alone.
CI checks the changelog on every push and pull request, so an unknown heading
fails early. The release commit is made by GitHub Actions, so `git pull`
before your next push.

## Credits

- Forked from [jose-gonzalez-valdez/kohler-sensate-ha](https://github.com/jose-gonzalez-valdez/kohler-sensate-ha),
  which reverse-engineered the Sensate's cloud API.
- The instant-updates connection follows the field notes of
  [frozenmartini/kohler-anthem-plus](https://github.com/frozenmartini/kohler-anthem-plus)
  and [yon/kohler-anthem](https://github.com/yon/kohler-anthem) (both MIT),
  which work with the same Kohler cloud.

## License

MIT. See [LICENSE](LICENSE).
