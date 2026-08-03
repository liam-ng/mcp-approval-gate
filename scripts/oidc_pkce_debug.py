#!/usr/bin/env python3
"""Manually drive an Authorization Code + PKCE flow against the public
MCP/IDE app client, to see the IdP's *raw* token-endpoint response.

Cursor (and other MCP clients) often truncate or swallow the actual error
text from a failed token exchange. This script reproduces the same request
Cursor makes — same client_id, redirect_uri, scope, and resource indicator —
so a failure here shows Cognito's real error/error_description directly.

Usage:
    OIDC_ISSUER=https://cognito-idp.<region>.amazonaws.com/<pool-id> \
    CLIENT_ID=<public app client id> \
        python scripts/oidc_pkce_debug.py

Optional env: REDIRECT_URI (default http://localhost:8787/callback),
RESOURCE (default http://localhost:8001/mcp), SCOPE (default "openid profile email").
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import urllib.parse

import httpx

ISSUER = os.environ["OIDC_ISSUER"].rstrip("/")
CLIENT_ID = os.environ["CLIENT_ID"]
REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://localhost:8787/callback")
RESOURCE = os.environ.get("RESOURCE")  # unset = omit the `resource` param entirely
SCOPE = os.environ.get("SCOPE", "openid profile email")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> None:
    discovery = httpx.get(f"{ISSUER}/.well-known/openid-configuration", timeout=15).json()
    authorize_endpoint = discovery["authorization_endpoint"]
    token_endpoint = discovery["token_endpoint"]

    code_verifier = b64url(secrets.token_bytes(32))
    code_challenge = b64url(hashlib.sha256(code_verifier.encode()).digest())
    state = secrets.token_urlsafe(8)

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if RESOURCE:
        params["resource"] = RESOURCE
    print("1) Open this URL, log in, then paste the FULL URL you land on\n"
          "   (it's fine that the page itself fails to load — just need the address bar):\n")
    print(f"{authorize_endpoint}?{urllib.parse.urlencode(params)}\n")

    pasted = input("2) Paste the resulting redirect URL here: ").strip()
    query = urllib.parse.urlparse(pasted).query
    parsed = urllib.parse.parse_qs(query)

    if "error" in parsed:
        sys.exit(f"   IdP returned an error at the authorize step: {parsed}")
    code = parsed.get("code", [None])[0]
    if not code:
        sys.exit(f"   No 'code' param found in pasted URL: {pasted}")
    if parsed.get("state", [None])[0] != state:
        sys.exit("   state mismatch — did you paste the right URL?")

    print("\n3) Exchanging code for tokens ...")
    token_data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    if RESOURCE:
        token_data["resource"] = RESOURCE
    response = httpx.post(
        token_endpoint,
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    print(f"   HTTP {response.status_code}")
    print(f"   {response.text}")


if __name__ == "__main__":
    main()
