#!/usr/bin/env python3
"""Read state and (optionally) send commands to the Sensate faucet.

Requires a control token first:  python get_write_token.py

Usage:
  python control_test.py                 # just read + print faucet state
  python control_test.py --on            # turn water ON  (asks to confirm)
  python control_test.py --off           # turn water OFF (asks to confirm)
  python control_test.py --dispense-ml 250   # dispense 250 mL (asks to confirm)

Commands that run water ask for a typed "YES" first — run them while you're at
the sink. quantity is sent to Kohler in LITERS (the app's canonical unit).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from kohler_anthem import KohlerAnthemClient, KohlerConfig

CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
API_RESOURCE = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"
APIM_KEY = "429ecb1d0b5e4258aa0a2bfadd82a493"

CUSTOMER_DEVICES = "/devices/api/v1/device-management/customer-device/{cid}"
FAUCET_STATE = "/devices/api/v1/device-management/faucet-state/{did}"
CMD_DISPENSE = "/platform/api/v1/commands/faucet/dispense"
CMD_ONOFF = "/platform/api/v1/commands/faucet/onoff"

ENV_PATH = Path(__file__).parent / ".env"


def _load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _decode_oid(token: str) -> str | None:
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("oid")
    except Exception:
        return None


def _confirm(msg: str) -> bool:
    return input(f"{msg}\nType yes to proceed: ").strip().lower() in ("yes", "y", "si", "sí")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", action="store_true", help="turn water ON")
    ap.add_argument("--off", action="store_true", help="turn water OFF")
    ap.add_argument("--dispense-ml", type=float, metavar="ML",
                    help="dispense this many milliliters")
    args = ap.parse_args()

    _load_dotenv()
    email = os.environ.get("KONNECT_EMAIL") or input("Konnect email: ").strip()
    password = os.environ.get("KONNECT_PASSWORD")
    if not password:
        import getpass
        password = getpass.getpass("Konnect password (hidden): ").strip()
    refresh = os.environ.get("KONNECT_B2C_REFRESH_TOKEN")

    writing = args.on or args.off or args.dispense_ml is not None
    if writing and not refresh:
        # No B2C write token yet. Instead of bailing, ATTEMPT the write with the
        # ROPC (username/password) token — community notes say gcs writes reject
        # ROPC with 403, but that was never tested on a faucet (SEN). If it works
        # we skip the whole browser-token dance. A 403 just tells us we do need it.
        print("NOTE: no B2C control token — testing whether the faucet accepts a "
              "command with your normal login (ROPC). A 403 just means we need "
              "the browser token after all; it won't hurt anything.\n")

    client = KohlerAnthemClient(KohlerConfig(
        username=email, password=password, client_id=CLIENT_ID,
        apim_subscription_key=APIM_KEY, api_resource=API_RESOURCE,
        b2c_refresh_token=refresh,
    ))
    await client.connect()
    token = client._auth.token.access_token if client._auth.token else None
    tenant = _decode_oid(token) if token else None

    raw = await client._request("GET", CUSTOMER_DEVICES.format(cid=tenant))
    did = None
    for home in raw.get("customerHome") or []:
        for dev in home.get("devices") or []:
            if (dev.get("sku") or "").upper() in ("SEN", "FAUCET"):
                did = dev["deviceId"]
    if not did:
        print("No faucet found on the account."); await client.close(); return 3

    def print_state(tag: str) -> None:
        pass

    state = await client._request("GET", FAUCET_STATE.format(did=did))
    print(f"Faucet {did} state: {json.dumps(state.get('state'), indent=2)}")

    async def send(label: str, endpoint: str, payload: dict) -> None:
        print(f"\n{label}\nPayload: {json.dumps(payload)}")
        try:
            resp = await client._request("POST", endpoint, json=payload)
            print("✅ ACCEPTED by Kohler. Response:", json.dumps(resp))
        except Exception as exc:  # noqa: BLE001 - report cleanly
            msg = str(exc)
            if "403" in msg or "Forbidden" in msg:
                print("⛔ 403 Forbidden — this command needs the B2C browser token "
                      "after all (ROPC not allowed for writes).")
            print("Error:", type(exc).__name__, "-", msg[:200])

    if args.on or args.off:
        action = "ON" if args.on else "OFF"
        payload = {"action": action, "deviceId": did, "sku": "SEN", "tenantId": tenant}
        if _confirm(f"About to turn the water {action}."):
            await send(f"Turning water {action}", CMD_ONOFF, payload)
        else:
            print("Cancelled.")

    if args.dispense_ml is not None:
        liters = round(args.dispense_ml / 1000.0, 4)
        payload = {"deviceId": did, "quantity": liters, "sku": "SEN", "tenantId": tenant}
        if _confirm(f"About to DISPENSE {args.dispense_ml:g} mL (quantity={liters} L) — "
                    "this runs water at your faucet."):
            await send(f"Dispensing {args.dispense_ml:g} mL", CMD_DISPENSE, payload)
        else:
            print("Cancelled.")

    await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
