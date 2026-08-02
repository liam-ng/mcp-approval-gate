"""Regression test for the top-level ASGI dispatcher in main.py.

The MCP sub-app is dispatched to directly at the ASGI level rather than
mounted as a FastAPI sub-route (see the comment in main.py), which means its
own Starlette lifespan never receives uvicorn's "lifespan" scope — that has
to be nested inside create_app()'s lifespan instead. Without that, every
/mcp request fails with "Task group is not initialized" because the
Streamable HTTP session manager was never started. This test would have
caught it: it drives requests through the actual top-level `app` object,
the same one uvicorn serves, not a hand-built test app.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from starlette.testclient import TestClient

MCP_ENV = {
    "SESSION_SECRET": "test-secret",
    "AUTH_MODE": "dev",
    "GATE_SERVER_ID": "gate-test",
    "ALLOWED_AGENT_ARNS": "arn:aws:iam::123456789012:role/x",
    "MCP_ENABLED": "true",
    "MCP_OAUTH_ISSUER": "https://idp.example.com",
    "MCP_EXECUTOR_ARN": "arn:aws:iam::123456789012:role/x",
}


@pytest.fixture()
def dispatcher_app(tmp_path, monkeypatch):
    for key, value in MCP_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import app.settings as settings_module
    from app.repo import factory

    settings_module._settings = None
    factory.get_repository.cache_clear()

    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")

    yield main.app

    sys.modules.pop("app.main", None)
    factory.get_repository.cache_clear()
    settings_module._settings = None


def test_healthz_and_mcp_both_reachable_through_one_dispatcher(dispatcher_app):
    with TestClient(dispatcher_app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/healthz").json() == {"status": "ok"}

        metadata = client.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == 200
        assert metadata.json()["authorization_servers"] == ["https://idp.example.com/"]

        # No token: the /mcp path must reach the MCP sub-app's own auth
        # enforcement (401), not 404 (would mean the dispatcher routed it to
        # the FastAPI app's SPA fallback instead) and not a 500 from a
        # session manager that was never started.
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}},
            },
        )
        assert r.status_code == 401
        assert "resource_metadata" in r.headers.get("www-authenticate", "")
