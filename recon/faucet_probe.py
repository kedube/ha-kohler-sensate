#!/usr/bin/env python3
"""Read the Sensate faucet's REAL state via the faucet-* endpoints.

M1's first pass hit the generic gcs-state shell (all nulls). Static analysis of
the Konnect APK revealed the faucet has its own endpoint family:

  READS  (GET):
    /devices/api/v1/device-management/faucet-state/{device_id}
    /devices/api/v1/device-management/faucet-usage/{device_id}
    /devices/api/v1/device-management/faucet-configuration/{device_id}
    /devices/api/v1/device-management/faucet-experience/{device_id}

  WRITES (POST /platform/api/v1/commands/faucet/{cmd}) — NOT sent here:
    dispense, onoff, presetexperience, experience, factoryreset

This script only READS. It confirms the SEN->faucet mapping and captures the
real state / water-usage / preset shapes we need to model the integration.
It also tries a few GUESSED date-range parameters for faucet-usage, which
answers HTTP 400 without parameters, and prints Kohler's full error replies.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import os
import sys
from pathlib import Path

from kohler_anthem import KohlerAnthemClient, KohlerConfig

DEFAULT_CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
DEFAULT_API_RESOURCE = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"
DEFAULT_APIM_KEY = "429ecb1d0b5e4258aa0a2bfadd82a493"

CUSTOMER_DEVICES = "/devices/api/v1/device-management/customer-device/{cid}"
FAUCET_READS = {
    "faucet_state": "/devices/api/v1/device-management/faucet-state/{did}",
    "faucet_usage": "/devices/api/v1/device-management/faucet-usage/{did}",
    "faucet_configuration": "/devices/api/v1/device-management/faucet-configuration/{did}",
    "faucet_experience": "/devices/api/v1/device-management/faucet-experience/{did}",
}

OUT_DIR = Path(__file__).parent / "captures"


def _load_dotenv() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _decode_oid(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    return claims.get("oid") or claims.get("sub")


def _find_faucet_devices(raw: dict) -> list[str]:
    ids: list[str] = []
    for home in raw.get("customerHome") or []:
        for dev in home.get("devices") or []:
            sku = (dev.get("sku") or "").upper()
            did = dev.get("deviceId")
            if did and sku in ("SEN", "FAUCET"):
                ids.append(did)
    return ids


def _error_body(exc: Exception) -> str:
    raw = getattr(exc, "raw_response", None)
    return json.dumps(raw) if raw is not None else str(exc)[:600]


def _usage_param_guesses() -> list[dict[str, str]]:
    """Common date-range parameter shapes. These are GUESSES, not known values."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    day, iso, epoch = "%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", lambda d: str(int(d.timestamp()))
    return [
        {"startDate": week_ago.strftime(day), "endDate": now.strftime(day)},
        {"fromDate": week_ago.strftime(day), "toDate": now.strftime(day)},
        {"startDate": week_ago.strftime(iso), "endDate": now.strftime(iso)},
        {"from": week_ago.strftime(iso), "to": now.strftime(iso)},
        {"startTime": epoch(week_ago), "endTime": epoch(now)},
        {"period": "week"},
        {"type": "weekly"},
        {"days": "7"},
    ]


async def _probe_usage(client: KohlerAnthemClient, did: str) -> None:
    """Find the parameters faucet-usage wants (it answers 400 without any).

    Read-only GETs. Look first at the bare request's error body: it may name
    the required parameters outright, making the guesses unnecessary.
    """
    ep = FAUCET_READS["faucet_usage"].format(did=did)
    print(f"\n--- faucet-usage parameter probe for {did[:12]} ---")
    for params in _usage_param_guesses():
        try:
            data = await client._request("GET", ep, params=params)
        except Exception as exc:  # noqa: BLE001
            print(f"  [miss] {params}\n         {_error_body(exc)[:300]}")
            continue
        fname = OUT_DIR / f"faucet_usage_{did[:12]}.json"
        fname.write_text(json.dumps({"params": params, "response": data}, indent=2))
        print(f"  [HIT ] {params} -> {fname.name}")
        return
    print("  No guess worked. The error bodies above are the best lead.")


async def main() -> int:
    _load_dotenv()
    email = os.environ.get("KONNECT_EMAIL") or input("Kohler Konnect email: ").strip()
    password = os.environ.get("KONNECT_PASSWORD")
    if not password:
        import getpass
        password = getpass.getpass("Kohler Konnect password (hidden): ").strip()
    if not email or not password:
        print("ERROR: need email and password.")
        return 2

    OUT_DIR.mkdir(exist_ok=True)
    client = KohlerAnthemClient(KohlerConfig(
        username=email, password=password,
        client_id=DEFAULT_CLIENT_ID, apim_subscription_key=DEFAULT_APIM_KEY,
        api_resource=DEFAULT_API_RESOURCE,
    ))
    print("Signing in (read-only)…")
    await client.connect()
    token = client._auth.token.access_token if client._auth.token else None
    tenant = _decode_oid(token) if token else None
    if not tenant:
        print("ERROR: no tenant id from token."); await client.close(); return 3

    raw = await client._request("GET", CUSTOMER_DEVICES.format(cid=tenant))
    faucets = _find_faucet_devices(raw)
    print(f"Faucet devices: {faucets or '(none found)'}\n")

    for did in faucets:
        for label, tmpl in FAUCET_READS.items():
            ep = tmpl.format(did=did)
            try:
                data = await client._request("GET", ep)
            except Exception as exc:  # noqa: BLE001
                print(f"  [miss] {label:22} {type(exc).__name__}")
                # A 400 often says which parameters are missing; show all of it.
                print(f"         {_error_body(exc)}")
                continue
            fname = OUT_DIR / f"{label}_{did[:12]}.json"
            fname.write_text(json.dumps(data, indent=2))
            keys = list(data.keys()) if isinstance(data, dict) else f"({type(data).__name__})"
            print(f"  [HIT ] {label:22} -> {fname.name}")
            print(f"         top-level keys: {keys}")

    for did in faucets:
        await _probe_usage(client, did)

    await client.close()
    print("\nDone. Files in recon/captures/ contain no password, but they DO contain "
          "your account id, home address/coordinates, Wi-Fi name and device "
          "serials. Redact them before sharing.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
