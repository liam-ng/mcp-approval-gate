from __future__ import annotations

import os

# app.settings validates at import and GATE_SERVER_ID has no default, so this must run before any
# `import app.*` below. Same reason the CI import check sets it inline.
os.environ.setdefault("GATE_SERVER_ID", "gate-test")
os.environ.setdefault("STS_REGION", "us-east-1")

import pytest  # noqa: E402
from botocore.credentials import Credentials  # noqa: E402

from app import gate_client  # noqa: E402


@pytest.fixture()
def signing_credentials(monkeypatch):
    """Static credentials, so any difference between two envelopes comes from the code under test
    rather than from a credential refresh."""

    class _Session:
        def get_credentials(self):
            return Credentials("AKIAEXAMPLE", "secret", None)

    monkeypatch.setattr(gate_client, "_session", _Session())
