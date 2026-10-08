# Changelog

<!-- Add new entries at the top under an "Unreleased" level-2 heading, grouped
by ### Added, Changed, Deprecated, Removed, Fixed, Security or Breaking
changes. Pushing to main releases them; see "Releasing" in the README. -->

## 0.10.0

### Removed

- The *Instant updates* option. Instant updates are always on, as in the
  Konnect app, and polling carries on beside them as a safety net. A faucet
  that had them off now connects, which adds a "HomeAssistant" entry to the
  Kohler account's notification devices.

### Added

- Konnect faucets with SKU `SET` (probably the Setra), which the Konnect app
  handles like the Sensate, are found during setup. Commands send each
  faucet's own SKU instead of always `SEN`.
- With instant updates, *Leak* turns on as soon as Kohler sends its real-time
  leak alert, instead of when the leak history is next read. *Leak* also has
  a `last_detected` attribute.
- *Firmware* asks Kohler's firmware check, the one the Konnect app uses,
  whether newer firmware is available, every hour and after an install.
- Clear errors when Kohler won't carry out a command: the faucet is offline,
  firmware is updating, water couldn't be dispensed, or commands are refused
  for this sign-in.

### Changed

- Dispenses and presets can be up to 3 gallons (11.36 L), the Konnect app's
  limit, instead of 4 L. Presets saved in the app above 4 L can now be
  dispensed. Larger dispenses count as dispensing for longer if the faucet
  never reports the water off.
- Like the Konnect app, turning the water on or dispensing is refused while
  the handle is closed or the faucet is downloading firmware. Turning the
  water off always works.
- *Dispense progress* is renamed *Firmware download*: Kohler's `progress`
  field follows firmware downloads, not the water.
- Presets come from the faucet's own preset list, as the app's faucet screen
  reads them, with the account-wide list as a fallback.
- Pressing *Clear leak alert* also clears a leak detected before then that
  Kohler only lists later. *Leak*'s `latest` attribute is the most recently
  detected event, whatever order Kohler lists them in.
- Faucets on one Kohler account share one instant-updates connection and
  one entry among the account's notification devices, instead of one each:
  Kohler's feed covers the whole account. Entries added by earlier versions
  for the other faucets are removed.
- Deleting the last faucet on an account removes its instant-updates entry
  from the account's notification devices.

### Fixed

- A firmware status in Kohler's `progress` field could make *Water* and
  *Dispensing* show the water running during a firmware update.
- A command Kohler answered with HTTP 200 but refused in the reply, such as
  "water could not be dispensed", was taken as carried out.
- Feed messages that don't name a device no longer count as proof that
  instant updates report this faucet.
- An app preset reported as anything but `OFF` counts as running, as in the
  Konnect app.
- The out-of-range message for the `dispense` action shows its limits
  without exponents, rounded so every amount it states is accepted.

## 0.9.0

### Changed

- Updated Home Assistant integration icons.

## 0.8.0

### Changed

- *Dispense amount* and *Preset* are configuration entities, listed under
  **Configuration** on the device page instead of among the controls, as
  settings for *Dispense set amount* and *Dispense preset*. Home Assistant's
  auto-generated dashboard no longer shows them; the README has a dashboard
  card that keeps each setting next to its button.

## 0.7.0

### Added

- The `kohler_sensate.dispense` action takes `preset:` to dispense a preset
  saved in the Konnect app by name, in one step from automations, scripts and
  voice.

## 0.6.0

### Breaking changes

- Konnect presets are chosen from a *Preset* dropdown and dispensed with one
  *Dispense preset* button, instead of a button per preset, so any number of
  presets fits in two entities. The old *Preset: name* buttons are removed;
  update dashboards, automations and scripts that pressed them.

## 0.5.0

### Added

- Latin American Spanish translation, which calls the faucet a *llave*.

### Fixed

- *Firmware* shows Home Assistant's update icon instead of the integration's
  logo.
- *Water used today* starts over at midnight, instead of showing the previous
  day's figure for up to 30 minutes.
- Spanish uses the words used in Spain, such as *grifo*, instead of the
  English word *faucet*.

## 0.4.0

### Breaking changes

- *Status* and *Handle* show translated states, so their values are now
  `off`/`on` and `open`/`closed` instead of Kohler's `Off`/`On` and
  `OPEN`/`CLOSED`. Update automations and templates that compare them with
  the old text. The *Water* switch is unchanged.

### Added

- Water usage from Kohler's own history: *Total water used*, which works as
  a water meter in the Energy dashboard, and *Water used today*.
- *Firmware* update entity: shows when Kohler has newer firmware for the
  faucet. Updates are still installed from the Konnect app.
- *Dispensing* also shows presets run from the Konnect app, with the
  preset's name, when instant updates are on.

### Fixed

- Konnect presets never appeared: the integration asked Kohler for them at
  an address that doesn't exist. Every preset saved in the app now gets a
  button, enabled from the start.
- *Dispensing* never turned on, because the Sensate doesn't report dispense
  progress. It now follows dispenses started from Home Assistant until the
  faucet reports the water off.

### Changed

- *Dispense progress* is disabled for new installs and moved to the
  diagnostic entities, since the Sensate always reports `NotStarted`.

## 0.3.0

### Changed

- Instant updates are on by default and no longer experimental: they're
  confirmed with a Sensate on firmware 16.0, where on/off and dispenses show
  up within a second or two. Faucets whose options you saved before keep
  their setting; turn it on under **Configure**.
- While the feed is working, polling during running water, dispenses and
  commands slows from every 5 seconds to every 30, since the feed reports
  those changes as they happen.

### Added

- The integration checks that instant updates keep up. If a poll finds a
  change the feed never announced, polling goes back to its usual pace until
  the feed delivers again. Diagnostics count the missed changes.

### Fixed

- When the instant-updates connection drops, the faucet is re-read at once
  and polling returns to every 30 seconds, instead of waiting up to 5 minutes
  for the next poll.

## 0.2.1

### Security

- Diagnostics redact the device ID, account email and account ID wherever
  they appear, not just in known fields. Kohler's configuration reply repeats
  the device ID under `id`, which diagnostics used to show.

## 0.2.0

### Added

- Metric or imperial units, per faucet, under **Configure**. Controls the
  quick-dispense buttons (mL/L or cups/quarts/gallons), the *Dispense amount*
  number (mL or fl oz), the *Last dispensed* sensor, and the `dispense`
  action's default unit. New faucets follow Home Assistant's unit system.
- `dispense` action: `amount` + `unit` (`ml`, `l`, `fl_oz`, `cup`, `qt`,
  `gal`), and `device_id` to choose a faucet.
- Re-authentication flow when Kohler rejects the saved password.
- Accounts with several faucets: pick which one to add.
- *Clear leak alert* button. *Leak* now turns on for leak events you haven't
  cleared, instead of staying on for good once Kohler records any leak.
  Cleared events are remembered across restarts.
- *Connected* diagnostic sensor. When Kohler reports the faucet offline, its
  live entities go unavailable instead of showing stale values, and commands
  fail with a clear "faucet is offline" error.
- Water safety limit option (default 10 minutes, 0 = off): water turned on
  from Home Assistant is turned off after that long unless it was turned off
  first. The deadline survives reloads and restarts, and a failed attempt is
  retried every minute.
- Instant updates (experimental, off by default): listens to Kohler's Azure
  IoT Hub feed like the Konnect app and re-reads the faucet when it changes.
  Idle polling relaxes to 5 minutes once the feed has proven it delivers.
- Konnect preset buttons (experimental, disabled by default), dispensing each
  preset's amount with the verified dispense command.
- **Reconfigure** to change the account email or password.
- Repair notices when the faucet is no longer on the Kohler account, or when
  Kohler keeps rejecting requests (likely an API change).
- Kohler's `Retry-After` is honored when it throttles requests (30 s to
  15 min), and commands report how long to wait.
- Diagnostics download, with personal data redacted. It also lists every
  status/progress/handle value seen, presets, and the instant-updates state.
- `recon/faucet_probe.py` prints Kohler's full error replies and tries
  common parameters for the undocumented water-usage endpoint.
- Translated entity names, errors and icons, in Dutch, French, German,
  Italian, Polish, Brazilian Portuguese and Swedish (new) and Spanish
  (updated). A test checks that every language has the same keys and
  placeholders as the English strings.
- A test suite that runs against the latest Home Assistant and 2026.3, plus a Ruff
  lint workflow.
- Automatic releases: once CI passes on `main`, the changelog's new entries
  are published as a GitHub release, with the version bumped to match.

### Fixed

- A network error or Kohler outage while renewing the session no longer
  triggers a re-login and stops polling. Only a rejected password does.
- Re-login no longer fails: 0.1 asked Home Assistant to re-authenticate but
  had no re-authentication step.
- The session is renewed before it expires, and an HTTP 401 renews it and
  retries once.
- Concurrent requests share one sign-in instead of racing.
- With several faucets, `dispense` no longer runs water at all of them.
- The `dispense` action exists even before a faucet finishes loading, and
  gives a clear error instead of "unknown action".
- *Last dispensed* was almost always unknown (Kohler only reports the amount
  while dispensing). It now records every dispense from Home Assistant and
  survives restarts.
- *Leak* shows unknown instead of "dry" until the leak history has been read.
- Unrecognized faucet statuses (an error or offline state, say) no longer
  show the water as on or trigger fast polling; the switch shows unknown.
- Refreshes requested by commands now run within 1 s instead of up to 10 s.
- A failed firmware/leak-history request no longer makes every entity
  unavailable.
- Setup no longer crashes if Kohler reports firmware as an object.
- The *Dispense amount* number restores correctly after a restart or a unit
  change.
- The password field is masked in the setup form and never re-filled after an
  error.

### Changed

- Adaptive polling: every 30 s when idle and every 5 s while water runs or
  just after a command (was a fixed 10 s). That's about a third of the
  requests when idle, and faster updates while dispensing.
- Repository moved to [kedube/ha-kohler-sensate](https://github.com/kedube/ha-kohler-sensate);
  code owner is now @kedube.
- Removed the `kohler-anthem` dependency. 0.1 used private internals of that
  shower library; the integration now has its own small client and uses Home
  Assistant's shared HTTP session.
- The config entry stores the faucet's device ID. 0.1 entries keep working.
- Minimum Home Assistant version is 2026.3.0 (was 2024.8.0), the first
  release that shows the integration's own icon and logo.
- New requirement `paho-mqtt` (already bundled with Home Assistant), used only
  when instant updates are on.

### Security

- Debug logs no longer include request payloads with the account ID. Kohler
  account responses (home address, coordinates, Wi-Fi name) are never logged.
- Error messages never include credentials or tokens.
- `recon/` tools: the saved refresh token is now readable only by you, and
  the tools warn that captures contain personal data.

## 0.1.0

- First release.
