# Changelog

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
