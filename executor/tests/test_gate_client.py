"""Regression tests for the 2026-08-13 same-second signature collision.

poll -> execution/start -> execution/result runs in ~8ms. Before the nonce, all three signed
envelopes in that window were byte-identical and the gate's replay cache 401'd the 2nd and 3rd.
"""

from __future__ import annotations

import base64
import json
import re

from app.gate_client import identity_header


def envelope(header: str) -> dict:
    return json.loads(base64.b64decode(header))


def authorization(header: str) -> str:
    headers = envelope(header)["headers"]
    return next(v for k, v in headers.items() if k.lower() == "authorization")


def signature(header: str) -> str:
    m = re.search(r"Signature=([0-9a-f]+)", authorization(header))
    assert m, "no SigV4 signature in the envelope"
    return m.group(1)


def test_two_envelopes_signed_in_the_same_second_differ(signing_credentials):
    """THE BUG. Same credentials, same request, same X-Amz-Date -- SigV4 is deterministic, so
    without added entropy these two signatures were equal and the gate rejected the second."""
    first, second = identity_header(), identity_header()

    # Guard the premise: if these ran in different seconds the test proves nothing.
    assert envelope(first)["headers"]["X-Amz-Date"] == envelope(second)["headers"]["X-Amz-Date"]
    assert signature(first) != signature(second)


def test_nonce_is_covered_by_the_signature(signing_credentials):
    """An unsigned header would still leave the signature identical -- the whole point is that the
    nonce is part of the canonical request."""
    header = identity_header()
    signed = re.search(r"SignedHeaders=([^,\s]+)", authorization(header))
    assert signed and "x-gate-nonce" in signed.group(1).lower().split(";")


def test_nonce_does_not_displace_the_server_id_binding(signing_credentials):
    """The server-id binding is what stops an envelope captured by one gate being replayed at
    another. The gate rejects any envelope where it is not signed."""
    header = identity_header()
    signed = re.search(r"SignedHeaders=([^,\s]+)", authorization(header))
    assert signed and "x-gate-server-id" in signed.group(1).lower().split(";")
    assert envelope(header)["headers"]["X-Gate-Server-Id"] == "gate-test"


def test_envelope_still_describes_a_getcalleridentity_post(signing_credentials):
    """The gate validates all of this before it forwards to STS; the nonce must not disturb it."""
    body = envelope(identity_header())
    assert body["method"] == "POST"
    assert body["url"] == "https://sts.amazonaws.com/"
    assert body["body"] == "Action=GetCallerIdentity&Version=2011-06-15"
