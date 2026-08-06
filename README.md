# Kohler Sensate — Home Assistant integration

Unofficial Home Assistant integration for the **Kohler Sensate** touchless
kitchen faucet (Konnect / SKU `SEN`). Its headline feature is **dispensing
measured amounts of water** from Home Assistant — plus on/off, one-tap quick
amounts, water status, and leak detection.

Kohler dropped HomeKit support for the Sensate and never shipped a Home
Assistant integration, so this talks to the Kohler Konnect cloud directly. It
signs in with your normal Konnect email + password (no app tokens to extract).

> ⚠️ Unofficial and cloud-dependent. Not affiliated with or endorsed by Kohler.
> The cloud API is undocumented and may change at any time.

## Features

- **Switch** — turn the water on/off.
- **Quick-dispense buttons** — one tap for 50 mL, 250, 500, 750 mL, 1 L, 2 L, 3 L.
- **Free-amount dispense** — a number field + button for any amount, and a
  `kohler_sensate.dispense` service (`amount_ml` or `amount_l`) for automations
  and voice assistants ("dispense half a liter").
- **Sensors** — status, dispense progress, handle position, last amount dispensed.
- **Binary sensors** — leak detected, and currently-dispensing.

## Installation

### HACS (recommended)
1. HACS → ⋮ → *Custom repositories* → add this repo, category **Integration**.
2. Install **Kohler Sensate Faucet**, then restart Home Assistant.

### Manual
Copy `custom_components/kohler_sensate/` into your HA `config/custom_components/`
folder and restart Home Assistant.

## Setup
Settings → Devices & Services → **Add Integration** → *Kohler Sensate* → sign in
with your Konnect email and password.

## The `dispense` service
```yaml
service: kohler_sensate.dispense
data:
  amount_ml: 250   # or:  amount_l: 0.25
```
Amounts are sent to Kohler in liters (the app's native unit); the integration
converts for you.

## How it works
Reverse-engineered from the Konnect mobile app. See
[`PROTOCOL.md`](PROTOCOL.md) for the full cloud-API write-up.

## Caveats
- Cloud-dependent (not local); requires internet and Kohler's cloud being up.
- Unofficial — could break if Kohler changes their API.

## License
MIT
