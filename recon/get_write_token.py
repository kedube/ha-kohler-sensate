#!/usr/bin/env python3
"""Seed a Kohler B2C 'write' token so we can send commands to the faucet.

Reads (state/usage) work with just your password. WRITES (dispense, on/off)
need a token from Kohler's `B2C_1A_signin` sign-in policy — the same sign-in the
mobile app does. Kohler only registered the app's own redirect URI (an
`msauth://…` link a desktop browser can't open), so the flow is manual:

  1. This script prints a Kohler sign-in URL.
  2. You open it in a browser and sign in with your Konnect account.
  3. The browser tries to jump to `msauth://com.kohler.hermoth/...?code=...` and
     shows a blank page / "can't open" — that's expected. Copy that WHOLE URL
     from the address bar.
  4. Paste it back here.
  5. The script exchanges it for a refresh token and saves it to recon/.env as
     KONNECT_B2C_REFRESH_TOKEN (git-ignored). Control scripts pick it up.

Nothing is sent to the faucet here. This only obtains the permission token.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import sys
import urllib.parse
from pathlib import Path

import aiohttp

CLIENT_ID = "8caf9530-1d13-48e6-867c-0f082878debc"
API_RESOURCE = "f5d87f3d-bdeb-4933-ab70-ef56cc343744"
B2C_TENANT = "konnectkohler.onmicrosoft.com"
B2C_AUTHORITY = f"https://konnectkohler.b2clogin.com/tfp/{B2C_TENANT}/B2C_1A_signin"
REDIRECT_URI = "msauth://com.kohler.hermoth/2DuDM2vGmcL4bKPn2xKzKpsy68k%3D"
SCOPE = f"openid offline_access https://{B2C_TENANT}/{API_RESOURCE}/apiaccess"

ENV_PATH = Path(__file__).parent / ".env"
ENV_KEY = "KONNECT_B2C_REFRESH_TOKEN"


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _authorize_url(challenge: str, state: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "nonce": secrets.token_urlsafe(16),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "response_mode": "query",
    }
    return f"{B2C_AUTHORITY}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(
        params, safe="/:"
    )


def _save_token(refresh_token: str) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = [
            ln for ln in ENV_PATH.read_text().splitlines()
            if not ln.strip().startswith(ENV_KEY + "=")
        ]
    lines.append(f"{ENV_KEY}={refresh_token}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(0o600)  # holds a long-lived account token


async def main() -> int:
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    url = _authorize_url(challenge, state)

    print("\n1) Open this URL in your browser and sign in to Kohler Konnect:\n")
    print(url)
    print(
        "\n2) After signing in, the browser will try to open a page starting with"
        "\n   'msauth://com.kohler.hermoth/...' and show a blank / 'cannot open'"
        "\n   page. That's expected. Copy the ENTIRE address from the address bar.\n"
    )
    redirect = input("3) Paste the full msauth://... URL here: ").strip()

    if not redirect.startswith("msauth://"):
        print("ERROR: that doesn't start with msauth:// — try again.")
        return 2
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(redirect).query)
    if "error" in qs:
        print("Sign-in error:", qs.get("error_description", qs["error"])[0])
        return 2
    code = qs.get("code", [""])[0]
    if not code:
        print("ERROR: no ?code= found in that URL.")
        return 2
    if qs.get("state", [""])[0] != state:
        print("ERROR: state mismatch — re-run and use a fresh URL.")
        return 2

    data = {
        "client_id": CLIENT_ID,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": urllib.parse.unquote(REDIRECT_URI),
        "code_verifier": verifier,
        "scope": SCOPE,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{B2C_AUTHORITY}/oauth2/v2.0/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            payload = await resp.json()
    if resp.status != 200 or "refresh_token" not in payload:
        print("Token exchange failed:",
              payload.get("error_description") or payload.get("error") or resp.status)
        return 3

    _save_token(payload["refresh_token"])
    print(f"\n✅ Success. Control token saved to {ENV_PATH} ({ENV_KEY}).")
    print("You can now run control_test.py to read state and (optionally) dispense.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
