#!/usr/bin/env python3
"""Kohler Konnect account recon — enumerate every device and dump raw state.

Goal of this script (Milestone 1 of the Sensate reverse-engineering):
  1. Log in to the user's Kohler Konnect account (READS only — uses the ROPC
     token, so only username + password are needed; no browser sign-in yet).
  2. Dump the raw JSON of EVERY device on the account. This reveals the
     Sensate faucet's device_id and its SKU / device-family code (the piece
     that replaces "gcs" in the API paths).
  3. Probe likely state endpoints for the faucet so we learn its state shape.

Nothing here writes to any device. It only reads.

Credentials are read from the environment (or a local .env file) and are NEVER
printed. Put them in recon/.env (git-ignored):

    KONNECT_EMAIL=you@example.com
    KONNECT_PASSWORD=your-password

Then:  ../.venv/bin/python discover.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from kohler_anthem import KohlerAnthemClient, KohlerConfig

# --- App-global credentials (baked into the Konnect mobile app; not secrets) ---
# Verified from the published kohler-anthem / kohler-konnect-ha projects.
DEFAULT_CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
DEFAULT_API_RESOURCE = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"
DEFAULT_APIM_KEY = "429ecb1d0b5e4258aa0a2bfadd82a493"

# The account-devices endpoint is generic (returns ALL devices, every family).
CUSTOMER_DEVICES = "/devices/api/v1/device-management/customer-device/{cid}"

# State endpoints we already know for the Anthem shower ("gcs"). We'll also try
# swapping the family segment to guess the faucet's, once we see its SKU.
KNOWN_STATE_EP = "/devices/api/v1/device-management/gcs-state/gcsadvancestate/{did}"

OUT_DIR = Path(__file__).parent / "captures"


def _load_dotenv() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _decode_oid(access_token: str) -> str | None:
    """Pull the tenant/customer id (oid claim) out of the B2C JWT."""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None
    return claims.get("oid") or claims.get("sub")


def _summarize_devices(raw: dict) -> list[dict]:
    """Best-effort walk of the customer payload to list (name, sku, id) tuples.

    Kohler nests devices under homes/rooms; the exact shape may vary, so we
    recurse and pick out anything that looks like a device record.
    """
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            # A device record tends to carry an id + a sku/model + a name.
            id_keys = [k for k in node if k.lower() in ("deviceid", "device_id", "id", "thingname", "serialnumber")]
            sku_keys = [k for k in node if k.lower() in ("sku", "model", "producttype", "devicetype", "family")]
            if id_keys and sku_keys:
                found.append({
                    "id": node.get(id_keys[0]),
                    "sku": node.get(sku_keys[0]),
                    "name": node.get("name") or node.get("deviceName") or node.get("nickname"),
                    "keys": sorted(node.keys()),
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(raw)
    return found


async def main() -> int:
    _load_dotenv()
    email = os.environ.get("KONNECT_EMAIL")
    password = os.environ.get("KONNECT_PASSWORD")
    # If not preset, ask interactively (password hidden, kept only in memory,
    # never written to disk or shell history).
    if not email:
        try:
            email = input("Kohler Konnect email: ").strip()
        except EOFError:
            email = ""
    if not password:
        import getpass
        try:
            password = getpass.getpass("Kohler Konnect password (hidden): ").strip()
        except EOFError:
            password = ""
    if not email or not password:
        print("ERROR: need a Konnect email and password to sign in.")
        return 2

    OUT_DIR.mkdir(exist_ok=True)

    config = KohlerConfig(
        username=email,
        password=password,
        client_id=DEFAULT_CLIENT_ID,
        apim_subscription_key=DEFAULT_APIM_KEY,
        api_resource=DEFAULT_API_RESOURCE,
    )
    client = KohlerAnthemClient(config)

    print("Signing in to Kohler Konnect (read-only)…")
    await client.connect()

    token = client._auth.token.access_token if client._auth.token else None
    tenant_id = _decode_oid(token) if token else None
    if not tenant_id:
        print("ERROR: signed in but could not read tenant id from token.")
        await client.close()
        return 3
    print(f"OK — tenant/customer id: {tenant_id}\n")

    # 1) Raw account-devices dump.
    print("Fetching all devices on the account…")
    raw = await client._request("GET", CUSTOMER_DEVICES.format(cid=tenant_id))
    (OUT_DIR / "customer_devices.json").write_text(json.dumps(raw, indent=2))
    print(f"  saved -> {OUT_DIR/'customer_devices.json'}")

    devices = _summarize_devices(raw)
    print(f"\nDevices detected: {len(devices)}")
    for d in devices:
        print(f"  • name={d['name']!r}  sku={d['sku']!r}  id={d['id']!r}")

    # 2) For every non-GCS device (candidate faucet), probe state endpoints.
    for d in devices:
        did = d["id"]
        sku = (d["sku"] or "").strip()
        if not did:
            continue
        print(f"\n--- Probing state for {d['name']!r} (sku={sku!r}) ---")
        candidates = [KNOWN_STATE_EP.format(did=did)]
        # Guess a family-specific state endpoint by lowercasing the SKU.
        fam = sku.lower()
        if fam and fam != "gcs":
            candidates += [
                f"/devices/api/v1/device-management/{fam}-state/{fam}advancestate/{did}",
                f"/devices/api/v1/device-management/{fam}-state/{fam}state/{did}",
                f"/devices/api/v1/device-management/{fam}-state/{did}",
                f"/devices/api/v1/device-management/device-state/{did}",
            ]
        for ep in candidates:
            try:
                state = await client._request("GET", ep)
            except Exception as exc:  # noqa: BLE001 - recon, surface everything
                print(f"    [miss] {ep}\n           {type(exc).__name__}: {str(exc)[:120]}")
                continue
            fname = OUT_DIR / f"state_{sku or 'unknown'}_{str(did)[:8]}.json"
            fname.write_text(json.dumps(state, indent=2))
            print(f"    [HIT ] {ep}\n           saved -> {fname}")
            break

    await client.close()
    print("\nDone. Share the files in recon/captures/ (they contain no password).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
