"""Email-link approve/reject (auth/approval_links.py, api/approval_link_actions.py).

Deliberately its own auth path (see approval_links.py's docstring): a signed
token instead of a session, feeding the exact same core/service.py entry
points a session-authenticated approver reaches from the portal. These
tests exercise the token itself (roundtrip, tamper, expiry) and the router
built on top of it (preview never mutates, POST enforces the same
invariants as tickets.py, links are effectively single-use).
"""

from __future__ import annotations

import time

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.settings as settings_module
from app.api.approval_link_actions import router as approval_link_router
from app.api.deps import get_repo
from app.api.errors import install_error_handlers
from app.auth.approval_links import (
    InvalidApprovalLink,
    generate_link_token,
    verify_link_token,
)
from app.core import service
from app.core.schemas import TicketCreateRequest
from app.repo.jsonl_store import JsonlTicketRepository
from app.settings import Settings
from tests.conftest import AGENT_ARN
from tests.test_agent_api import create_payload

APPROVER = "peer@example.com"


def _tamper(token: str) -> str:
    # Flip a character in the middle of the signature segment, not the
    # final character of the whole token -- base64's last character can
    # have padding-insignificant bits, so several values decode to the same
    # bytes there and flipping only that one is flaky.
    head, sig = token.rsplit(".", 1)
    mid = len(sig) // 2
    flipped = "a" if sig[mid] != "a" else "b"
    return f"{head}.{sig[:mid]}{flipped}{sig[mid + 1:]}"


# --- token unit tests (no HTTP, no repo) -----------------------------------


def _settings(**overrides) -> Settings:
    fields = dict(
        session_secret="test-secret",
        gate_server_id="approval-gate-test",
        allowed_agent_arns="arn:aws:iam::123456789012:role/mcp-*",
        auth_mode="dev",
    )
    fields.update(overrides)
    return Settings(**fields)


def test_token_roundtrip():
    settings = _settings()
    token = generate_link_token(settings, "tkt-1", APPROVER, "approve")
    payload = verify_link_token(settings, token)
    assert payload.ticket_id == "tkt-1"
    assert payload.email == APPROVER
    assert payload.action == "approve"


def test_token_tamper_rejected():
    settings = _settings()
    token = generate_link_token(settings, "tkt-1", APPROVER, "approve")
    with pytest.raises(InvalidApprovalLink):
        verify_link_token(settings, _tamper(token))


def test_token_wrong_secret_rejected():
    token = generate_link_token(_settings(session_secret="secret-a"), "tkt-1", APPROVER, "approve")
    with pytest.raises(InvalidApprovalLink):
        verify_link_token(_settings(session_secret="secret-b"), token)


def test_token_expiry():
    settings = _settings(approval_ttl_hours=0)
    token = generate_link_token(settings, "tkt-1", APPROVER, "approve")
    time.sleep(1.1)  # itsdangerous timestamps have 1s resolution
    with pytest.raises(InvalidApprovalLink):
        verify_link_token(settings, token)


def test_session_cookie_secret_cannot_be_replayed_as_a_link_token():
    # Same SESSION_SECRET, but the session cookie is signed with a different
    # salt (starlette's SessionMiddleware) -- itsdangerous.URLSafeTimedSerializer
    # ties the salt into the signature, so this must fail closed.
    from itsdangerous import URLSafeTimedSerializer

    settings = _settings()
    other_salt_token = URLSafeTimedSerializer(settings.session_secret, salt="unrelated-salt").dumps(
        {"tid": "tkt-1", "email": APPROVER, "action": "approve"}
    )
    with pytest.raises(InvalidApprovalLink):
        verify_link_token(settings, other_salt_token)


# --- router tests ------------------------------------------------------------


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    def _make(*, approver_emails: str = APPROVER, approval_ttl_hours: int = 72):
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("AUTH_MODE", "dev")
        monkeypatch.setenv("GATE_SERVER_ID", "approval-gate-test")
        monkeypatch.setenv("ALLOWED_AGENT_ARNS", "arn:aws:iam::123456789012:role/mcp-*")
        monkeypatch.setenv("APPROVER_EMAILS", approver_emails)
        monkeypatch.setenv("APPROVAL_TTL_HOURS", str(approval_ttl_hours))
        settings_module._settings = None

        test_app = FastAPI()
        test_app.include_router(approval_link_router)
        install_error_handlers(test_app)
        repo = JsonlTicketRepository(str(tmp_path))
        test_app.dependency_overrides[get_repo] = lambda: repo
        client = TestClient(test_app)
        client.repo = repo  # type: ignore[attr-defined]
        return client

    yield _make
    settings_module._settings = None


def seed_ticket(client, **overrides) -> str:
    payload = TicketCreateRequest.model_validate(create_payload(**overrides))

    async def _create():
        ticket, _ = await service.create_agent_ticket(client.repo, payload, AGENT_ARN, None)
        return ticket

    return anyio.run(_create).ticket_id


def token_for(action: str, ticket_id: str, email: str = APPROVER) -> str:
    return generate_link_token(settings_module.get_settings(), ticket_id, email, action)


def test_preview_does_not_mutate(make_client):
    client = make_client()
    tid = seed_ticket(client)
    token = token_for("approve", tid)

    r = client.get(f"/api/tickets/by-link/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticketId"] == tid
    assert body["action"] == "approve"
    assert body["actionable"] is True
    assert body["blockedReason"] is None

    ticket = anyio.run(client.repo.get_ticket, tid)
    assert ticket.status == "PENDING_APPROVAL"
    assert ticket.approvals == []


def test_preview_explains_revoked_approver(make_client):
    client = make_client(approver_emails="someone-else@example.com")
    tid = seed_ticket(client)
    token = token_for("approve", tid, APPROVER)

    body = client.get(f"/api/tickets/by-link/{token}").json()
    assert body["actionable"] is False
    assert body["blockedReason"] == "not_approver"


def test_preview_explains_self_approval(make_client):
    client = make_client(approver_emails=f"{APPROVER},{AGENT_ARN}")
    tid = seed_ticket(client)  # proposed_by == AGENT_ARN
    token = token_for("approve", tid, AGENT_ARN)

    body = client.get(f"/api/tickets/by-link/{token}").json()
    assert body["actionable"] is False
    assert body["blockedReason"] == "self_approval"


def test_preview_explains_duplicate_approval(make_client, monkeypatch):
    monkeypatch.setenv("REQUIRED_APPROVALS", "2")
    client = make_client(approver_emails=f"{APPROVER},manager@example.com")
    settings_module._settings = None
    tid = seed_ticket(client)
    approve_token = token_for("approve", tid, APPROVER)
    assert client.post(f"/api/tickets/by-link/{approve_token}", json={}).status_code == 200

    body = client.get(f"/api/tickets/by-link/{approve_token}").json()
    assert body["actionable"] is False
    assert body["blockedReason"] == "duplicate_approval"


def test_preview_explains_already_actioned(make_client):
    client = make_client()
    tid = seed_ticket(client)
    approve_token = token_for("approve", tid)
    assert client.post(f"/api/tickets/by-link/{approve_token}", json={}).status_code == 200

    reject_token = token_for("reject", tid)
    body = client.get(f"/api/tickets/by-link/{reject_token}").json()
    assert body["actionable"] is False
    assert body["blockedReason"] == "already_actioned"


def test_approve_via_link(make_client):
    client = make_client()
    tid = seed_ticket(client)
    token = token_for("approve", tid)

    r = client.post(f"/api/tickets/by-link/{token}", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED"

    ticket = anyio.run(client.repo.get_ticket, tid)
    assert [a.approved_by for a in ticket.approvals] == [APPROVER]


def test_reject_via_link_requires_reason(make_client):
    client = make_client()
    tid = seed_ticket(client)
    token = token_for("reject", tid)

    r = client.post(f"/api/tickets/by-link/{token}", json={})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MISSING_REASON"

    r = client.post(f"/api/tickets/by-link/{token}", json={"reason": "wrong instance targeted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REJECTED"


def test_link_is_effectively_single_use(make_client):
    client = make_client()
    tid = seed_ticket(client)
    token = token_for("approve", tid)

    assert client.post(f"/api/tickets/by-link/{token}", json={}).status_code == 200
    r = client.post(f"/api/tickets/by-link/{token}", json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_second_distinct_approver_link_still_works_until_threshold(make_client, monkeypatch):
    monkeypatch.setenv("REQUIRED_APPROVALS", "2")
    client = make_client(approver_emails=f"{APPROVER},manager@example.com")
    settings_module._settings = None
    tid = seed_ticket(client)

    r1 = client.post(f"/api/tickets/by-link/{token_for('approve', tid, APPROVER)}", json={})
    assert r1.status_code == 200
    assert r1.json()["status"] == "PENDING_APPROVAL"

    r2 = client.post(
        f"/api/tickets/by-link/{token_for('approve', tid, 'manager@example.com')}", json={}
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "APPROVED"


def test_proposer_cannot_approve_own_ticket_via_link(make_client):
    client = make_client(approver_emails=f"{APPROVER},{AGENT_ARN}")
    tid = seed_ticket(client)  # proposed_by == AGENT_ARN
    token = token_for("approve", tid, AGENT_ARN)

    r = client.post(f"/api/tickets/by-link/{token}", json={})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APPROVER_IS_PROPOSER"


def test_revoked_approver_link_rejected(make_client):
    # Token was minted while the recipient was an approver, but APPROVER_EMAILS
    # no longer includes them by the time they click -- must fail closed.
    client = make_client(approver_emails="someone-else@example.com")
    tid = seed_ticket(client)
    token = token_for("approve", tid, APPROVER)

    r = client.post(f"/api/tickets/by-link/{token}", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_LINK"


def test_tampered_token_rejected_by_router(make_client):
    client = make_client()
    tid = seed_ticket(client)
    tampered = _tamper(token_for("approve", tid))

    assert client.get(f"/api/tickets/by-link/{tampered}").status_code == 400
    assert client.post(f"/api/tickets/by-link/{tampered}", json={}).status_code == 400
