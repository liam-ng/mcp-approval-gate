from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.settings as settings_module
from app.api import auth as human_auth
from app.api.deps import get_repo
from app.api.errors import install_error_handlers
from app.api.tickets import router as tickets_router
from app.core import service
from app.core.schemas import TicketCreateRequest
from app.repo.jsonl_store import JsonlTicketRepository
from tests.conftest import AGENT_ARN
from tests.test_agent_api import create_payload


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    """Factory so tests can pick REQUIRED_APPROVALS before app creation."""

    def _make(required_approvals: int = 1, allow_self_approval: bool = False):
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("AUTH_MODE", "dev")
        monkeypatch.setenv("GATE_SERVER_ID", "approval-gate-test")
        monkeypatch.setenv("ALLOWED_AGENT_ARNS", "arn:aws:iam::123456789012:role/mcp-*")
        monkeypatch.setenv("REQUIRED_APPROVALS", str(required_approvals))
        monkeypatch.setenv("ALLOW_SELF_APPROVAL", str(allow_self_approval))
        settings_module._settings = None

        test_app = FastAPI()
        human_auth.install(test_app, settings_module.get_settings())
        test_app.include_router(tickets_router)
        install_error_handlers(test_app)
        repo = JsonlTicketRepository(str(tmp_path))
        test_app.dependency_overrides[get_repo] = lambda: repo
        client = TestClient(test_app)
        client.repo = repo  # type: ignore[attr-defined]
        return client

    yield _make
    settings_module._settings = None


def seed_agent_ticket(client) -> str:
    payload = TicketCreateRequest.model_validate(create_payload())

    async def _create():
        ticket, _ = await service.create_agent_ticket(client.repo, payload, AGENT_ARN, None)
        return ticket

    return anyio.run(_create).ticket_id


def login(client, email="peer@example.com", role="approver"):
    r = client.get(f"/api/auth/login?email={email}&role={role}", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_unauthenticated_gets_401(make_client):
    client = make_client()
    assert client.get("/api/tickets").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_me_returns_role(make_client):
    client = make_client()
    login(client, "viewer@example.com", "viewer")
    me = client.get("/api/me").json()
    assert me == {
        "email": "viewer@example.com",
        "name": "Dev User",
        "role": "viewer",
        "approvalTtlHours": 72,
        "allowSelfApproval": False,
    }


def test_viewer_cannot_approve(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client, "viewer@example.com", "viewer")
    r = client.post(f"/api/tickets/{tid}/approve")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "NOT_APPROVER"


def test_single_approval_flow(make_client):
    client = make_client(required_approvals=1)
    tid = seed_agent_ticket(client)
    login(client)
    r = client.post(f"/api/tickets/{tid}/approve")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPROVED"
    assert [a["approvedBy"] for a in body["approvals"]] == ["peer@example.com"]


def test_two_approvals_required(make_client):
    client = make_client(required_approvals=2)
    tid = seed_agent_ticket(client)

    login(client, "peer@example.com")
    first = client.post(f"/api/tickets/{tid}/approve").json()
    assert first["status"] == "PENDING_APPROVAL"
    assert len(first["approvals"]) == 1

    login(client, "manager@example.com")
    second = client.post(f"/api/tickets/{tid}/approve").json()
    assert second["status"] == "APPROVED"
    assert len(second["approvals"]) == 2


def test_duplicate_approver_rejected(make_client):
    client = make_client(required_approvals=2)
    tid = seed_agent_ticket(client)
    login(client, "peer@example.com")
    assert client.post(f"/api/tickets/{tid}/approve").status_code == 200
    r = client.post(f"/api/tickets/{tid}/approve")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "DUPLICATE_APPROVER"


def test_reject_requires_reason(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client)
    assert client.post(f"/api/tickets/{tid}/reject", json={"reason": "ok"}).status_code == 422
    r = client.post(f"/api/tickets/{tid}/reject", json={"reason": "wrong instance targeted"})
    assert r.status_code == 200
    assert r.json()["status"] == "REJECTED"
    assert r.json()["rejectionReason"] == "wrong instance targeted"


def test_approve_after_reject_is_conflict(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client)
    client.post(f"/api/tickets/{tid}/reject", json={"reason": "wrong instance targeted"})
    r = client.post(f"/api/tickets/{tid}/approve")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_supersede_links_and_deprecates(make_client):
    client = make_client()
    old_id = seed_agent_ticket(client)
    login(client, "editor@example.com")

    edited = create_payload(subject="Stop staging instance (rescheduled)")
    r = client.post(f"/api/tickets/{old_id}/supersede", json=edited)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["supersedes"] == old_id
    assert new["proposedBy"] == "editor@example.com"
    assert new["assignee"] == AGENT_ARN  # agent still executes

    detail = client.get(f"/api/tickets/{old_id}").json()
    assert detail["ticket"]["status"] == "DEPRECATED"
    assert detail["ticket"]["supersededBy"] == new["ticketId"]
    assert [t["ticketId"] for t in detail["lineage"]] == [old_id, new["ticketId"]]

    # Deprecated ticket is no longer actionable.
    r = client.post(f"/api/tickets/{old_id}/approve")
    assert r.status_code == 409

    # Double-supersede of the same ticket is rejected.
    r = client.post(f"/api/tickets/{old_id}/supersede", json=edited)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TICKET_SUPERSEDED"


def test_editor_cannot_approve_own_supersede(make_client):
    client = make_client()
    old_id = seed_agent_ticket(client)
    login(client, "editor@example.com")
    new = client.post(f"/api/tickets/{old_id}/supersede", json=create_payload()).json()

    r = client.post(f"/api/tickets/{new['ticketId']}/approve")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "APPROVER_IS_PROPOSER"

    # A different approver can.
    login(client, "someone-else@example.com")
    assert client.post(f"/api/tickets/{new['ticketId']}/approve").status_code == 200


def test_self_approval_allowed_when_toggled_on(make_client):
    client = make_client(allow_self_approval=True)
    old_id = seed_agent_ticket(client)
    login(client, "editor@example.com")
    new = client.post(f"/api/tickets/{old_id}/supersede", json=create_payload()).json()

    r = client.post(f"/api/tickets/{new['ticketId']}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED"


def test_update_tags_appends_audit_event_without_superseding(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client, "editor@example.com")

    r = client.post(f"/api/tickets/{tid}/tags", json={"tags": {"team": "gti", "env": "staging"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticketId"] == tid  # same ticket, not a new one
    assert body["tags"] == {
        "team": "gti",
        "env": "staging",
        "gateTicketId": tid,
        "owner": AGENT_ARN,  # set at creation; not user-editable
    }
    assert body["status"] == "PENDING_APPROVAL"  # unaffected

    detail = client.get(f"/api/tickets/{tid}").json()
    events = [e["type"] for e in detail["auditEvents"]]
    assert events == ["TICKET_CREATED", "TAGS_UPDATED"]


def test_update_tags_cannot_spoof_requestid_or_owner(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client, "editor@example.com")

    r = client.post(
        f"/api/tickets/{tid}/tags",
        json={"tags": {"gateTicketId": "fake-id", "owner": "someone-else@example.com"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == {"gateTicketId": tid, "owner": AGENT_ARN}


def test_update_tags_rejects_over_limit(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client)
    too_many = {f"k{i}": "v" for i in range(21)}
    assert client.post(f"/api/tickets/{tid}/tags", json={"tags": too_many}).status_code == 422


def test_update_tags_on_superseded_ticket_rejected(make_client):
    client = make_client()
    old_id = seed_agent_ticket(client)
    login(client, "editor@example.com")
    client.post(f"/api/tickets/{old_id}/supersede", json=create_payload())

    r = client.post(f"/api/tickets/{old_id}/tags", json={"tags": {"team": "gti"}})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TICKET_SUPERSEDED"


def test_viewer_can_comment(make_client):
    """Comments are open to the whole IT team, not gated to approvers like
    approve/reject are — a viewer role must be able to post one."""
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client, "viewer@example.com", "viewer")

    r = client.post(f"/api/tickets/{tid}/comments", json={"text": "Looks safe to me"})
    assert r.status_code == 200, r.text
    assert r.json()["ticketId"] == tid  # comment doesn't create a new ticket

    detail = client.get(f"/api/tickets/{tid}").json()
    comment_events = [e for e in detail["auditEvents"] if e["type"] == "COMMENT_ADDED"]
    assert len(comment_events) == 1
    assert comment_events[0]["details"]["text"] == "Looks safe to me"
    assert comment_events[0]["actor"] == {"kind": "human", "id": "viewer@example.com"}


def test_comment_allowed_regardless_of_ticket_status(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client)
    client.post(f"/api/tickets/{tid}/reject", json={"reason": "wrong instance targeted"})

    r = client.post(f"/api/tickets/{tid}/comments", json={"text": "Re-raised as TICK-456"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REJECTED"  # unaffected by the comment


def test_comment_requires_nonempty_text(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client)
    assert client.post(f"/api/tickets/{tid}/comments", json={"text": ""}).status_code == 422


def test_close_pending_ticket_appends_audit_event(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client, "viewer@example.com", "viewer")  # any session role, like supersede

    r = client.post(f"/api/tickets/{tid}/close", json={"reason": "no longer needed"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticketId"] == tid
    assert body["status"] == "CLOSED"

    detail = client.get(f"/api/tickets/{tid}").json()
    events = [e["type"] for e in detail["auditEvents"]]
    assert events == ["TICKET_CREATED", "CLOSED"]
    close_event = detail["auditEvents"][-1]
    assert close_event["actor"] == {"kind": "human", "id": "viewer@example.com"}
    assert close_event["details"]["reason"] == "no longer needed"


def test_close_approved_ticket(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client)
    client.post(f"/api/tickets/{tid}/approve")

    r = client.post(f"/api/tickets/{tid}/close", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CLOSED"


def test_close_terminal_ticket_rejected(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client)
    client.post(f"/api/tickets/{tid}/reject", json={"reason": "wrong instance targeted"})

    r = client.post(f"/api/tickets/{tid}/close", json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


def test_list_filters(make_client):
    client = make_client()
    tid1 = seed_agent_ticket(client)
    seed_agent_ticket(client)
    login(client)
    client.post(f"/api/tickets/{tid1}/approve")

    pending = client.get("/api/tickets", params={"status": "PENDING_APPROVAL"}).json()
    approved = client.get("/api/tickets", params={"status": "APPROVED"}).json()
    assert len(pending["items"]) == 1
    assert len(approved["items"]) == 1
    assert approved["items"][0]["ticketId"] == tid1

    tagged = client.get("/api/tickets", params={"tag": "owner=liam.ng"}).json()
    assert len(tagged["items"]) == 2
    assert client.get("/api/tickets", params={"tag": "team=other"}).json()["items"] == []


def test_audit_trail_content(make_client):
    client = make_client()
    tid = seed_agent_ticket(client)
    login(client)
    client.post(f"/api/tickets/{tid}/approve")

    detail = client.get(f"/api/tickets/{tid}").json()
    types = [e["type"] for e in detail["auditEvents"]]
    assert types == ["TICKET_CREATED", "APPROVED"]
    actors = [e["actor"] for e in detail["auditEvents"]]
    assert actors[0]["kind"] == "agent" and actors[1] == {"kind": "human", "id": "peer@example.com"}
