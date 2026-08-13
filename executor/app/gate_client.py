"""Client for the gate's SigV4-authenticated agent API (docs/agent-contract.md).

Every call carries a *freshly signed* X-Gate-Identity envelope: the gate
replay-protects each one, so a cached header fails the second time it is used.

Re-signing alone is NOT enough to make two envelopes differ -- see identity_header.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
from typing import Any

import botocore.session
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from .settings import settings

log = logging.getLogger(__name__)

_STS_URL = "https://sts.amazonaws.com/"
_STS_BODY = "Action=GetCallerIdentity&Version=2011-06-15"

_session = botocore.session.get_session()


class GateError(RuntimeError):
    """Non-2xx from the gate, carrying its error envelope code where present."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code


def identity_header() -> str:
    """Base64 envelope of a presigned sts:GetCallerIdentity request.

    X-GATE-NONCE IS LOAD-BEARING, NOT DECORATION. SigV4 is deterministic: same credentials, same
    canonical request, same X-Amz-Date produces a byte-identical signature -- and X-Amz-Date has
    only 1-second resolution. The gate's replay cache keys on sha256(Signature), so without the
    nonce ANY TWO CALLS IN THE SAME CLOCK SECOND collide and the second is rejected 401
    "replayed identity request". poll -> execution/start -> execution/result runs in ~8ms, so it
    hit this on the first ticket that ever reached the executor (2026-08-13); before that the poll
    always returned [] and only one call per 20s cycle was ever signed, which hid it completely.
    Signed, not merely sent -- an unsigned header changes nothing about the signature.
    """
    credentials = _session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            "no AWS credentials — the pod needs the IRSA ServiceAccount "
            "(deploy/k8s/agent-serviceaccount.yaml)"
        )
    request = AWSRequest(
        method="POST",
        url=_STS_URL,
        data=_STS_BODY,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            # Must be signed, not merely sent: the gate checks it appears in
            # SignedHeaders before it trusts the envelope.
            "X-Gate-Server-Id": settings.gate_server_id,
            # STS ignores unknown headers but still validates the signature over them, so this is
            # safe to add -- X-Gate-Server-Id above already proves the pattern round-trips.
            "X-Gate-Nonce": secrets.token_hex(16),
        },
    )
    SigV4Auth(credentials, "sts", settings.sts_region).add_auth(request)
    envelope = {
        "method": "POST",
        "url": _STS_URL,
        "headers": dict(request.headers),
        "body": _STS_BODY,
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    code, message = "UNKNOWN", response.text
    try:
        body = response.json()
        if isinstance(body.get("error"), dict):
            code = body["error"].get("code", code)
            message = body["error"].get("message", message)
        elif "detail" in body:  # FastAPI auth failures
            code, message = "AUTH", str(body["detail"])
    except (ValueError, AttributeError):
        pass
    raise GateError(response.status_code, code, message)


class GateClient:
    def __init__(self) -> None:
        self._http = httpx.Client(
            base_url=settings.gate_base_url.rstrip("/"),
            timeout=settings.request_timeout_seconds,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(
            method, path, headers={"X-Gate-Identity": identity_header()}, **kwargs
        )
        _raise_for_status(response)
        return response.json()

    def list_approved(self) -> list[dict[str, Any]]:
        """Tickets assigned to this verified ARN and awaiting execution.

        Covers both creation paths: ones this process opened itself and ones a
        human opened conversationally via /mcp, which this process was never
        told the id of.
        """
        return self._request("GET", "/api/agent/tickets", params={"status": "APPROVED"})

    def start_execution(self, ticket_id: str, parameters_hash: str) -> dict[str, Any]:
        """APPROVED -> EXECUTING. Returns the ticket, whose actionDetails is
        the authoritative thing to execute -- never the caller's own copy."""
        return self._request(
            "POST",
            f"/api/agent/tickets/{ticket_id}/execution/start",
            json={"parametersHash": parameters_hash},
        )

    def report_result(
        self,
        ticket_id: str,
        outcome: str,
        message: str,
        aws_request_ids: list[str],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/agent/tickets/{ticket_id}/execution/result",
            json={
                "outcome": outcome,
                "message": message[:1000],
                "awsRequestIds": aws_request_ids,
            },
        )
