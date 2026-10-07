# Kohler Sensate for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/kedube/ha-kohler-sensate)](https://github.com/kedube/ha-kohler-sensate/releases)
[![Validate](https://github.com/kedube/ha-kohler-sensate/actions/workflows/validate.yml/badge.svg)](https://github.com/kedube/ha-kohler-sensate/actions/workflows/validate.yml)
[![Tests](https://github.com/kedube/ha-kohler-sensate/actions/workflows/tests.yml/badge.svg)](https://github.com/kedube/ha-kohler-sensate/actions/workflows/tests.yml)

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
  and voice.
- **Metric or imperial**: chosen per faucet in the integration options.
- **Sensors**: status, dispense progress, handle position, last amount
  dispensed, leak detected, currently dispensing.
- **Leak alerts you can clear**: acknowledge a leak once it's dealt with; a
  new leak turns the alert back on.
- **Water safety limit**: water turned on from Home Assistant turns off by
  itself after 10 minutes (adjustable), even across restarts.
- **Offline detection**: when the faucet loses its connection, its entities
  show as unavailable instead of stale values, and commands give a clear
  error.
- **Instant updates** (experimental, opt-in): listens to Kohler's real-time
  feed like the Konnect app does.
- **Konnect presets** (experimental): a button for each preset saved in the
  app.
- **Automatic sign-in**: sessions renew on their own; you're only asked to
  sign in again if Kohler rejects your password.
- **Repair notices** when the faucet disappears from your account or Kohler's
  API changes, and **diagnostics** with personal data redacted.
- **In your language**: English, Dutch, French, German, Italian, Polish,
  Portuguese (Brazil), Spanish and Swedish. Other languages show English.

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
| Instant updates (experimental)    | Off                      | See [Instant updates](#instant-updates-experimental). |

#### Units

| Units    | Quick buttons            | Dispense amount | Last dispensed | Default action unit |
| -------- | ------------------------ | --------------- | -------------- | ------------------- |
| Metric   | 50 mL … 3 L              | mL              | mL             | mL                  |
| Imperial | ¼ cup … 1 gallon         | fl oz           | fl oz          | fl oz               |

New faucets default to your Home Assistant unit system. Changing the option
reloads the faucet and replaces the quick-dispense buttons with the other
set. The *Dispense amount* keeps the same volume, converted to the new unit.
Imperial units are US customary (1 cup = 236.6 mL), the same as the Konnect
app.

## Entities

| Entity                       | Type                     | Description |
| ---------------------------- | ------------------------ | ----------- |
| Water                        | Switch                   | Turns the water on or off. |
| Dispense *amount*            | Button (7)               | Dispenses a fixed amount (see the table above). |
| Dispense amount              | Number                   | The amount for *Dispense set amount*: 10–4000 mL or 0.5–135 fl oz. Kept across restarts. |
| Dispense set amount          | Button                   | Dispenses the *Dispense amount*. |
| Status                       | Sensor                   | Kohler's status text, for example `Off`. |
| Dispense progress            | Sensor                   | Kohler's progress text, for example `NotStarted` or `Completed`. |
| Last dispensed               | Sensor                   | The most recent dispense amount, from Home Assistant or the faucet. |
| Handle                       | Sensor (diagnostic)      | Position of the manual handle, for example `OPEN`. |
| Leak                         | Binary sensor (moisture) | On while Kohler reports a leak that hasn't been cleared. Attributes: `events` (all events in Kohler's history), `uncleared_events`, and `latest`. |
| Clear leak alert             | Button                   | Marks the current leak events as dealt with, which turns *Leak* off. |
| Dispensing                   | Binary sensor (running)  | On while a dispense is in progress. |
| Connected                    | Binary sensor (diagnostic) | Whether Kohler's cloud can reach the faucet. Attribute: `last_connected`. |
| Preset: *name*               | Button, disabled at first | One per preset saved in the Konnect app. See [Presets](#konnect-presets-experimental). |

While *Connected* is off, entities that show or control the live faucet are
unavailable. *Leak*, *Clear leak alert*, *Dispense amount* and *Last
dispensed* stay available.

## Actions

### `kohler_sensate.dispense`

Dispenses a measured amount of water. Every dispense must be between 10 mL
and 4 L.

| Field       | Required | Description |
| ----------- | -------- | ----------- |
| `amount`    | Yes\*    | How much to dispense, in `unit`. |
| `unit`      | No       | `ml`, `l`, `fl_oz`, `cup`, `qt` or `gal`. Defaults to `ml` (metric) or `fl_oz` (imperial), per the faucet's options. |
| `device_id` | No\*\*   | The faucet to use. |
| `amount_ml` | No       | Legacy: amount in mL, instead of `amount`. |
| `amount_l`  | No       | Legacy: amount in liters, instead of `amount`. |

\* Give exactly one of `amount`, `amount_ml` or `amount_l`.
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

The integration polls Kohler's cloud, adjusting how often to what the
faucet is doing:

| When                                                   | Faucet state checked |
| ------------------------------------------------------ | -------------------- |
| Idle                                                   | Every 30 seconds     |
| Water running, dispensing, or within 30 s of a command | Every 5 seconds      |
| Kohler's cloud is unreachable                          | Every 30 seconds     |

Firmware and leak history are checked every 5 minutes, so a leak shows up
within about 5 minutes of Kohler reporting it.

If someone uses the faucet by hand, Home Assistant can take up to 30 seconds
to notice. Short dispenses can finish between polls, so *Dispense progress*
and *Dispensing* may not show every dispense. *Last dispensed* always records
dispenses started from Home Assistant.

### Instant updates (experimental)

With this option on, the integration also holds a connection to Kohler's
real-time feed (Azure IoT Hub), the way the Konnect app does. Any message
about your faucet makes Home Assistant re-read its state right away. Once the
feed has delivered for your faucet, idle polling relaxes to every 5 minutes;
if the feed drops, polling goes back to every 30 seconds while it reconnects.

It's experimental because the feed is confirmed for Kohler's Anthem showers
but not yet for the Sensate. If nothing ever arrives, nothing breaks: polling
carries on as before. The option adds a "HomeAssistant" entry to the
notification devices on your Kohler account. **Download diagnostics** shows
whether the feed is connected and how many messages it has received.

### Konnect presets (experimental)

Presets saved in the Konnect app (such as "Fill pasta pot") get a button
each, which dispenses the preset's amount using the same command as the
other dispense buttons. Kohler's preset format isn't documented, so the
buttons start **disabled**. Check that a button's `amount` attribute matches
the app, then enable it from the entity's settings. Presets added or removed
in the app appear or go unavailable within 5 minutes.

## Known limitations

- **Cloud only.** The faucet is controlled through Kohler's cloud, so it
  needs internet access and Kohler's service to be up.
- **Unofficial API.** Kohler can change it at any time.
- **Leak history.** Kohler keeps leak events in the faucet's history and
  doesn't document their format, so the integration can't tell when a leak is
  fixed. Press *Clear leak alert* once it is. If the faucet already had leak
  events when you set up the integration, *Leak* starts on until you clear
  it.
- **Raw status text.** *Status*, *Dispense progress* and *Handle* show
  Kohler's text as-is, since the full list of values isn't known. Only `On`
  counts as water running and only `…InProgress` counts as dispensing; other
  values show the *Water* switch as unknown. Diagnostics list every value
  seen, so new ones can be added.
- **Water usage** isn't available yet: Kohler's usage endpoint needs
  parameters nobody has worked out. `recon/faucet_probe.py` tries to find
  them.

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
  faucet, then **Download diagnostics**. Your email, password, device ID, serial number and location
  are redacted. Attach the file when you open an
  [issue](https://github.com/kedube/ha-kohler-sensate/issues).

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

## Upgrading from 0.1

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
  `faucet_probe.py` also hunts for the water-usage endpoint's parameters.
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
  ```

## Credits

- Forked from [jose-gonzalez-valdez/kohler-sensate-ha](https://github.com/jose-gonzalez-valdez/kohler-sensate-ha),
  which reverse-engineered the Sensate's cloud API.
- The instant-updates connection follows the field notes of
  [frozenmartini/kohler-anthem-plus](https://github.com/frozenmartini/kohler-anthem-plus)
  and [yon/kohler-anthem](https://github.com/yon/kohler-anthem) (both MIT),
  which work with the same Kohler cloud.

## License

MIT. See [LICENSE](LICENSE).
