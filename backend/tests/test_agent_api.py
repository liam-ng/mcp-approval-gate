from __future__ import annotations

import base64
import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

import app.settings as settings_module
from app.api.agent_tickets import router as agent_router
from app.api.deps import get_repo
from app.api.errors import install_error_handlers
from app.repo.jsonl_store import JsonlTicketRepository

GATE_ID = "approval-gate-test"
ROLE_ARN = "arn:aws:sts::123456789012:assumed-role/mcp-agent/pod-1"
STS_JSON = {
    "GetCallerIdentityResponse": {
        "GetCallerIdentityResult": {
            "Arn": ROLE_ARN,
            "Account": "123456789012",
            "UserId": "AROAX:pod-1",
        }
    }
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("GATE_SERVER_ID", GATE_ID)
    monkeypatch.setenv("ALLOWED_AGENT_ARNS", "arn:aws:iam::123456789012:role/mcp-*")
    settings_module._settings = None

    test_app = FastAPI()
    test_app.include_router(agent_router)
    install_error_handlers(test_app)
    repo = JsonlTicketRepository(str(tmp_path))
    test_app.dependency_overrides[get_repo] = lambda: repo

    yield TestClient(test_app)
    settings_module._settings = None


def identity_header(
    *,
    server_id: str = GATE_ID,
    sign_server_id: bool = True,
    url: str = "https://sts.amazonaws.com/",
    body: str = "Action=GetCallerIdentity&Version=2011-06-15",
    amz_date: str | None = None,
    signature: str | None = None,
    method: str = "POST",
) -> str:
    signed = "host;x-amz-date" + (";x-gate-server-id" if sign_server_id else "")
    envelope = {
        "method": method,
        "url": url,
        "headers": {
            "Authorization": (
                "AWS4-HMAC-SHA256 Credential=AKIA/20260802/us-east-1/sts/aws4_request, "
                f"SignedHeaders={signed}, Signature={signature or secrets.token_hex(32)}"
            ),
            "X-Amz-Date": amz_date or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "X-Gate-Server-Id": server_id,
        },
        "body": body,
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def create_payload(**overrides):
    payload = {
        "subject": "Stop staging instance",
        "plannedDate": "2026-08-10",
        "plannedAction": "Stop EC2 instance i-0abc in staging",
        "actionDetails": {
            "service": "ec2",
            "operation": "StopInstances",
            "region": "ap-east-1",
            "parameters": {"InstanceIds": ["i-0abc"]},
            "resourceArns": ["arn:aws:ec2:ap-east-1:123456789012:instance/i-0abc"],
        },
        "tags": {"team": "gti"},
    }
    payload.update(overrides)
    return payload


def mock_sts(respx_mock, response=None):
    return respx_mock.post("https://sts.amazonaws.com/").mock(
        return_value=response or Response(200, json=STS_JSON)
    )


@respx.mock
def test_create_ticket_success(client, respx_mock):
    mock_sts(respx_mock)
    r = client.post(
        "/api/agent/tickets",
        json=create_payload(),
        headers={"X-Gate-Identity": identity_header()},
    )
    assert r.status_code == 201, r.text
    ticket = r.json()
    assert ticket["assignee"] == ROLE_ARN
    assert ticket["proposedBy"] == ROLE_ARN
    assert ticket["status"] == "PENDING_APPROVAL"
    assert len(ticket["actionDetails"]["parametersHash"]) == 64
    assert ticket["lineageRootId"] == ticket["ticketId"]


@respx.mock
def test_idempotent_create_returns_existing(client, respx_mock):
    mock_sts(respx_mock)
    h1 = {"X-Gate-Identity": identity_header(), "Idempotency-Key": "conv-1"}
    r1 = client.post("/api/agent/tickets", json=create_payload(), headers=h1)
    assert r1.status_code == 201
    h2 = {"X-Gate-Identity": identity_header(), "Idempotency-Key": "conv-1"}
    r2 = client.post("/api/agent/tickets", json=create_payload(), headers=h2)
    assert r2.status_code == 200
    assert r2.json()["ticketId"] == r1.json()["ticketId"]


@respx.mock
def test_wrong_sts_host_rejected(client, respx_mock):
    route = mock_sts(respx_mock)
    r = client.post(
        "/api/agent/tickets",
        json=create_payload(),
        headers={"X-Gate-Identity": identity_header(url="https://sts.evil.example.com/")},
    )
    assert r.status_code == 401
    assert not route.called  # rejected before any STS call


@respx.mock
def test_wrong_server_id_rejected(client, respx_mock):
    mock_sts(respx_mock)
    r = client.post(
        "/api/agent/tickets",
        json=create_payload(),
        headers={"X-Gate-Identity": identity_header(server_id="another-gate")},
    )
    assert r.status_code == 401


@respx.mock
def test_unsigned_server_id_rejected(client, respx_mock):
    mock_sts(respx_mock)
    r = client.post(
        "/api/agent/tickets",
        json=create_payload(),
        headers={"X-Gate-Identity": identity_header(sign_server_id=False)},
    )
    assert r.status_code == 401
    assert "not covered" in r.text


@respx.mock
def test_stale_date_rejected(client, respx_mock):
    mock_sts(respx_mock)
    stale = (datetime.now(UTC) - timedelta(minutes=10)).strftime("%Y%m%dT%H%M%SZ")
    r = client.post(
        "/api/agent/tickets",
        json=create_payload(),
        headers={"X-Gate-Identity": identity_header(amz_date=stale)},
    )
    assert r.status_code == 401
    assert "time window" in r.text


@respx.mock
def test_replayed_signature_rejected(client, respx_mock):
    mock_sts(respx_mock)
    header = identity_header(signature=secrets.token_hex(32))
    r1 = client.post(
        "/api/agent/tickets", json=create_payload(), headers={"X-Gate-Identity": header}
    )
    assert r1.status_code == 201
    r2 = client.post(
        "/api/agent/tickets", json=create_payload(), headers={"X-Gate-Identity": header}
    )
    assert r2.status_code == 401
    assert "replayed" in r2.text


@respx.mock
def test_non_allowlisted_arn_rejected(client, respx_mock):
    other = {
        "GetCallerIdentityResponse": {
            "GetCallerIdentityResult": {
                "Arn": "arn:aws:sts::999999999999:assumed-role/intruder/x",
                "Account": "999999999999",
            }
        }
    }
    mock_sts(respx_mock, Response(200, json=other))
    r = client.post(
        "/api/agent/tickets",
        json=create_payload(),
        headers={"X-Gate-Identity": identity_header()},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "AGENT_NOT_ALLOWED"


@respx.mock
def test_sts_rejection_propagates_as_401(client, respx_mock):
    mock_sts(respx_mock, Response(403, text="SignatureDoesNotMatch"))
    r = client.post(
        "/api/agent/tickets",
        json=create_payload(),
        headers={"X-Gate-Identity": identity_header()},
    )
    assert r.status_code == 401


@respx.mock
def test_poll_requires_assignee(client, respx_mock):
    mock_sts(respx_mock)
    created = client.post(
        "/api/agent/tickets", json=create_payload(), headers={"X-Gate-Identity": identity_header()}
    ).json()

    other = {
        "GetCallerIdentityResponse": {
            "GetCallerIdentityResult": {
                "Arn": "arn:aws:sts::123456789012:assumed-role/mcp-other/x",
                "Account": "123456789012",
            }
        }
    }
    respx_mock.post("https://sts.amazonaws.com/").mock(return_value=Response(200, json=other))
    r = client.get(
        f"/api/agent/tickets/{created['ticketId']}",
        headers={"X-Gate-Identity": identity_header()},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "NOT_ASSIGNEE"


@respx.mock
def test_execution_start_requires_approval_and_hash(client, respx_mock):
    mock_sts(respx_mock)
    created = client.post(
        "/api/agent/tickets", json=create_payload(), headers={"X-Gate-Identity": identity_header()}
    ).json()
    tid = created["ticketId"]
    good_hash = created["actionDetails"]["parametersHash"]

    # Not approved yet -> 409 INVALID_STATE
    r = client.post(
        f"/api/agent/tickets/{tid}/execution/start",
        json={"parametersHash": good_hash},
        headers={"X-Gate-Identity": identity_header()},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_STATE"


@respx.mock
def test_list_my_tickets_filters_by_assignee_and_status(client, respx_mock):
    mock_sts(respx_mock)
    mine = client.post(
        "/api/agent/tickets", json=create_payload(), headers={"X-Gate-Identity": identity_header()}
    ).json()

    other = {
        "GetCallerIdentityResponse": {
            "GetCallerIdentityResult": {
                "Arn": "arn:aws:sts::123456789012:assumed-role/mcp-other/x",
                "Account": "123456789012",
            }
        }
    }
    respx_mock.post("https://sts.amazonaws.com/").mock(return_value=Response(200, json=other))
    client.post(
        "/api/agent/tickets",
        json=create_payload(subject="Someone else's ticket"),
        headers={"X-Gate-Identity": identity_header()},
    )

    respx_mock.post("https://sts.amazonaws.com/").mock(return_value=Response(200, json=STS_JSON))
    r = client.get("/api/agent/tickets", headers={"X-Gate-Identity": identity_header()})
    assert r.status_code == 200
    ticket_ids = [t["ticketId"] for t in r.json()]
    assert ticket_ids == [mine["ticketId"]]

    r = client.get(
        "/api/agent/tickets", params={"status": "APPROVED"}, headers={"X-Gate-Identity": identity_header()}
    )
    assert r.json() == []


async def test_full_agent_flow_with_hash_mismatch(tmp_path):
    """create -> approve -> start (hash echo) -> mismatch rejected -> result,
    exercised at the service layer against one repo instance."""
    from app.core import service
    from app.core.schemas import ExecutionResultRequest, TicketCreateRequest

    repo = JsonlTicketRepository(str(tmp_path))
    payload = TicketCreateRequest.model_validate(create_payload())
    ticket, created = await service.create_agent_ticket(repo, payload, ROLE_ARN, "conv-9")
    assert created and ticket.status == "PENDING_APPROVAL"

    await service.approve_ticket(repo, ticket.ticket_id, "peer@example.com", 1)

    with pytest.raises(service.ParametersHashMismatch):
        await service.start_execution(repo, ticket.ticket_id, ROLE_ARN, "0" * 64)

    started = await service.start_execution(
        repo, ticket.ticket_id, ROLE_ARN, ticket.action_details.parameters_hash
    )
    assert started.status == "EXECUTING"

    done = await service.report_execution_result(
        repo,
        ticket.ticket_id,
        ROLE_ARN,
        ExecutionResultRequest(outcome="success", message="stopped", aws_request_ids=["req-1"]),
    )
    assert done.status == "COMPLETED"
    assert done.execution is not None and done.execution.outcome == "success"
