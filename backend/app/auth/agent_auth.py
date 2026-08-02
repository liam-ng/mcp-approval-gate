"""Agent authentication: IAM SigV4 via presigned sts:GetCallerIdentity.

The agent signs an sts:GetCallerIdentity request with its own credentials
(IRSA on the cluster) and sends it base64-encoded in the X-Gate-Identity
header. The gate validates the envelope, forwards it verbatim to STS, and
trusts the ARN STS returns — the Vault / aws-iam-authenticator pattern. The
gate itself needs no AWS permissions for this.

The signed request MUST include the X-Gate-Server-Id header (value =
GATE_SERVER_ID) inside SignedHeaders, binding the signature to this gate so a
token captured for another service cannot be replayed here.

This path is fully independent of the human OIDC flow: migrating the human
IdP (IAM Identity Center -> Entra ID) never touches this module.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import Header, HTTPException

from app.auth.replay_cache import ReplayCache
from app.settings import get_settings

STS_HOST_RE = re.compile(r"^sts(\.[a-z0-9-]+)?\.amazonaws\.com$")
EXPECTED_BODY = "Action=GetCallerIdentity&Version=2011-06-15"
MAX_CLOCK_SKEW_SECONDS = 300

_replay_cache = ReplayCache()


@dataclass
class AgentIdentity:
    caller_arn: str      # as returned by STS (assumed-role form for roles)
    role_arn: str | None  # normalized arn:aws:iam::acct:role/Name, if derivable
    account: str


def _unauthorized(reason: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "AGENT_AUTH_FAILED", "message": reason})


def _header(headers: dict[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def normalize_role_arn(caller_arn: str) -> str | None:
    # arn:aws:sts::123:assumed-role/RoleName/session -> arn:aws:iam::123:role/RoleName
    m = re.match(r"^arn:aws:sts::(\d+):assumed-role/([^/]+)/.+$", caller_arn)
    if m:
        return f"arn:aws:iam::{m.group(1)}:role/{m.group(2)}"
    if re.match(r"^arn:aws:iam::\d+:(role|user)/", caller_arn):
        return caller_arn
    return None


def _arn_allowed(caller_arn: str, role_arn: str | None, globs: list[str]) -> bool:
    candidates = [caller_arn] + ([role_arn] if role_arn else [])
    return any(fnmatch.fnmatch(arn, g) for arn in candidates for g in globs)


async def verify_agent(x_gate_identity: str = Header(...)) -> AgentIdentity:
    settings = get_settings()

    try:
        envelope = json.loads(base64.b64decode(x_gate_identity))
        method = envelope["method"]
        url = httpx.URL(envelope["url"])
        headers: dict[str, str] = envelope["headers"]
        body: str = envelope.get("body", "")
    except (ValueError, KeyError, TypeError):
        raise _unauthorized("malformed X-Gate-Identity envelope")

    # 1. The request must be exactly a GetCallerIdentity POST to real STS.
    if method.upper() != "POST" or url.scheme != "https" or not STS_HOST_RE.match(url.host):
        raise _unauthorized("identity request must be an https POST to STS")
    if url.path not in ("", "/") or body != EXPECTED_BODY:
        raise _unauthorized("identity request must be GetCallerIdentity")

    authorization = _header(headers, "authorization") or ""
    if not authorization.startswith("AWS4-HMAC-SHA256"):
        raise _unauthorized("missing SigV4 authorization header")

    # 2. Clock window on the signed date.
    amz_date = _header(headers, "x-amz-date")
    if not amz_date:
        raise _unauthorized("missing X-Amz-Date")
    try:
        signed_at = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise _unauthorized("invalid X-Amz-Date")
    if abs((datetime.now(UTC) - signed_at).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
        raise _unauthorized("identity request outside the allowed time window")

    # 3. Server-id binding: header present, correct, and actually signed.
    server_id = _header(headers, "x-gate-server-id")
    if server_id != settings.gate_server_id:
        raise _unauthorized("missing or wrong X-Gate-Server-Id")
    signed_headers_m = re.search(r"SignedHeaders=([^,\s]+)", authorization)
    signed_headers = (signed_headers_m.group(1).lower().split(";") if signed_headers_m else [])
    if "x-gate-server-id" not in signed_headers:
        raise _unauthorized("X-Gate-Server-Id is not covered by the signature")

    # 4. Replay protection on the signature itself.
    sig_m = re.search(r"Signature=([0-9a-f]+)", authorization)
    if not sig_m:
        raise _unauthorized("malformed authorization header")
    nonce = hashlib.sha256(sig_m.group(1).encode()).hexdigest()
    if not _replay_cache.check_and_add(nonce):
        raise _unauthorized("replayed identity request")

    # 5. Let STS be the judge: forward the presigned request verbatim.
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            url, content=body, headers={**headers, "accept": "application/json"}
        )
    if response.status_code != 200:
        raise _unauthorized("STS rejected the identity request")
    try:
        result = response.json()["GetCallerIdentityResponse"]["GetCallerIdentityResult"]
        caller_arn, account = result["Arn"], result["Account"]
    except (ValueError, KeyError):
        raise _unauthorized("unexpected STS response")

    # 6. Allowlist.
    role_arn = normalize_role_arn(caller_arn)
    if not _arn_allowed(caller_arn, role_arn, settings.allowed_agent_arn_globs):
        raise HTTPException(
            status_code=403,
            detail={"code": "AGENT_NOT_ALLOWED", "message": f"{caller_arn} is not an allowed agent"},
        )
    return AgentIdentity(caller_arn=caller_arn, role_arn=role_arn, account=account)
