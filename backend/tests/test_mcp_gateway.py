"""Tests for the /mcp OAuth2.1 Resource Server route (api/mcp_gateway.py).

Cursor/VS Code never touch this repo's code — they run OAuth against the
real IdP. Here we stand in for the IdP with a locally generated RSA key and
a respx-mocked discovery/JWKS endpoint, then drive the MCP Streamable HTTP
JSON-RPC protocol directly, the way any spec-compliant client would.
"""

from __future__ import annotations

import time

import pytest
import respx
from authlib.jose import JsonWebKey, jwt
from httpx import Response
from starlette.testclient import TestClient

import app.settings as settings_module
from app.api.mcp_gateway import build_mcp_app
from app.repo.jsonl_store import JsonlTicketRepository

ISSUER = "https://idp.example.com"
EXECUTOR_ARN = "arn:aws:iam::123456789012:role/mcp-executor"
KEY = JsonWebKey.generate_key("RSA", 2048, options={"kid": "test-key"}, is_private=True)


def issue_token(
    *, email="alice@example.com", aud="gate-mcp", client_id=None, scope="mcp:invoke", exp_delta=3600
) -> str:
    now = int(time.time())
    payload = {"iss": ISSUER, "iat": now, "exp": now + exp_delta}
    if aud is not None:
        payload["aud"] = aud
    if client_id is not None:
        payload["client_id"] = client_id
    if email is not None:
        payload["email"] = email
    if scope:
        payload["scope"] = scope
    return jwt.encode({"alg": "RS256", "kid": "test-key"}, payload, KEY).decode()


def mock_idp(respx_mock, *, userinfo_email=None):
    respx_mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=Response(
            200, json={"jwks_uri": f"{ISSUER}/jwks.json", "userinfo_endpoint": f"{ISSUER}/userinfo"}
        )
    )
    respx_mock.get(f"{ISSUER}/jwks.json").mock(
        return_value=Response(200, json={"keys": [KEY.as_dict(is_private=False)]})
    )
    if userinfo_email is not None:
        respx_mock.get(f"{ISSUER}/userinfo").mock(return_value=Response(200, json={"email": userinfo_email}))


@pytest.fixture()
def mcp_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("GATE_SERVER_ID", "gate-test")
    monkeypatch.setenv("ALLOWED_AGENT_ARNS", EXECUTOR_ARN)
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("MCP_OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_OAUTH_AUDIENCE", "gate-mcp")
    monkeypatch.setenv("MCP_EXECUTOR_ARN", EXECUTOR_ARN)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gate.example.com")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings_module._settings = None

    from app.repo import factory

    factory.get_repository.cache_clear()  # mcp_gateway.py calls get_repo() -> get_repository() directly

    settings = settings_module.get_settings()
    mcp_app = build_mcp_app(settings)
    repo = factory.get_repository()
    assert isinstance(repo, JsonlTicketRepository)

    with TestClient(mcp_app, base_url="http://127.0.0.1") as client:
        client.repo = repo  # type: ignore[attr-defined]
        yield client

    factory.get_repository.cache_clear()
    settings_module._settings = None


def rpc(client, method, params=None, *, id_=1, headers=None, session_id=None):
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    hdrs = {"Accept": "application/json, text/event-stream", **(headers or {})}
    if session_id:
        hdrs["Mcp-Session-Id"] = session_id
    return client.post("/mcp", json=body, headers=hdrs)


def authed_headers(**token_kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(**token_kwargs)}"}


def initialize(client, headers):
    return rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "cursor", "version": "1"}},
        headers=headers,
    )


def test_no_bearer_token_rejected(mcp_client):
    r = initialize(mcp_client, headers={})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


def test_wrong_audience_rejected(mcp_client):
    with respx.mock:
        mock_idp(respx.mock)
        r = initialize(mcp_client, headers=authed_headers(aud="someone-else"))
        assert r.status_code == 401


def test_client_id_claim_satisfies_audience_when_aud_missing(mcp_client):
    """Cognito access tokens carry `client_id`, never `aud` — the verifier must
    accept a match on either claim (app/auth/mcp_token_verifier.py)."""
    with respx.mock:
        mock_idp(respx.mock)
        headers = authed_headers(aud=None, client_id="gate-mcp")
        assert initialize(mcp_client, headers).status_code == 200


def test_email_resolved_via_userinfo_when_missing_from_token(mcp_client):
    """Cognito access tokens never carry profile claims like `email` — the
    verifier must fall back to a /userinfo lookup."""
    with respx.mock:
        mock_idp(respx.mock, userinfo_email="alice@example.com")
        headers = authed_headers(email=None)
        assert initialize(mcp_client, headers).status_code == 200
        rpc(mcp_client, "notifications/initialized", headers=headers)

        result = rpc(
            mcp_client,
            "tools/call",
            {
                "name": "create_change_ticket",
                "arguments": {
                    "subject": "Stop staging instance",
                    "planned_date": "2026-08-10",
                    "planned_action": "Stop EC2 instance i-0abc",
                    "operation": "StopInstances",
                    "region": "ap-east-1",
                    "parameters": {"InstanceIds": ["i-0abc"]},
                },
            },
            headers=headers,
        ).json()["result"]["structuredContent"]
        assert result["created"] is True


def test_protected_resource_metadata_advertises_the_idp(mcp_client):
    r = mcp_client.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_servers"] == [f"{ISSUER}/"]
    assert body["resource"] == "https://gate.example.com/mcp"


def test_create_change_ticket_creates_pending_ticket(mcp_client):
    with respx.mock:
        mock_idp(respx.mock)
        headers = authed_headers(email="alice@example.com")
        assert initialize(mcp_client, headers).status_code == 200
        assert rpc(mcp_client, "notifications/initialized", headers=headers).status_code < 300

        result = rpc(
            mcp_client,
            "tools/call",
            {
                "name": "create_change_ticket",
                "arguments": {
                    "subject": "Stop staging instance",
                    "planned_date": "2026-08-10",
                    "planned_action": "Stop EC2 instance i-0abc",
                    "operation": "StopInstances",
                    "region": "ap-east-1",
                    "parameters": {"InstanceIds": ["i-0abc"]},
                    "resource_arns": ["arn:aws:ec2:ap-east-1:123456789012:instance/i-0abc"],
                },
            },
            headers=headers,
        )
        assert result.status_code == 200
        body = result.json()["result"]
        assert body["isError"] is False
        content = body["structuredContent"]
        assert content["status"] == "PENDING_APPROVAL"
        assert content["created"] is True


def test_create_change_ticket_is_idempotent_per_user_and_params(mcp_client):
    import anyio

    with respx.mock:
        mock_idp(respx.mock)
        headers = authed_headers(email="alice@example.com")
        initialize(mcp_client, headers)
        rpc(mcp_client, "notifications/initialized", headers=headers)
        args = {
            "name": "create_change_ticket",
            "arguments": {
                "subject": "Stop staging instance",
                "planned_date": "2026-08-10",
                "planned_action": "Stop EC2 instance i-0abc",
                "operation": "StopInstances",
                "region": "ap-east-1",
                "parameters": {"InstanceIds": ["i-0abc"]},
            },
        }
        first = rpc(mcp_client, "tools/call", args, headers=headers).json()["result"]["structuredContent"]
        second = rpc(mcp_client, "tools/call", args, headers=headers).json()["result"]["structuredContent"]
        assert first["ticketId"] == second["ticketId"]
        assert second["created"] is False

        tickets = anyio.run(mcp_client.repo.query_all, 50, None)
        assert len(tickets.items) == 1
        assert tickets.items[0].proposed_by == "alice@example.com"
        assert tickets.items[0].assignee == EXECUTOR_ARN


def test_check_ticket_status_rejects_other_users(mcp_client):
    with respx.mock:
        mock_idp(respx.mock)
        alice = authed_headers(email="alice@example.com")
        initialize(mcp_client, alice)
        rpc(mcp_client, "notifications/initialized", headers=alice)
        created = rpc(
            mcp_client,
            "tools/call",
            {
                "name": "create_change_ticket",
                "arguments": {
                    "subject": "Stop staging instance",
                    "planned_date": "2026-08-10",
                    "planned_action": "Stop EC2 instance i-0abc",
                    "operation": "StopInstances",
                    "region": "ap-east-1",
                    "parameters": {"InstanceIds": ["i-0abc"]},
                },
            },
            headers=alice,
        ).json()["result"]["structuredContent"]

        bob = authed_headers(email="bob@example.com")
        initialize(mcp_client, bob)
        rpc(mcp_client, "notifications/initialized", headers=bob)
        result = rpc(
            mcp_client,
            "tools/call",
            {"name": "check_ticket_status", "arguments": {"ticket_id": created["ticketId"]}},
            headers=bob,
        ).json()["result"]
        assert result["isError"] is True
