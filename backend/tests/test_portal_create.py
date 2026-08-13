"""The portal's own create path: POST /api/tickets and the form's schema lookup.

Distinct from test_human_api.py because these need MCP_EXECUTOR_ARN set — the
route refuses without it, which is itself one of the tests here.
"""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.settings as settings_module
from app.core import service
from app.api import auth as human_auth
from app.api.aws_meta import router as aws_meta_router
from app.api.deps import get_repo
from app.api.errors import install_error_handlers
from app.api.tickets import router as tickets_router
from app.repo.jsonl_store import JsonlTicketRepository

EXECUTOR_ARN = "arn:aws:iam::123456789012:role/mcp-approval-gate-executor"


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    def _make(executor_arn: str | None = EXECUTOR_ARN):
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("AUTH_MODE", "dev")
        monkeypatch.setenv("GATE_SERVER_ID", "approval-gate-test")
        monkeypatch.setenv("ALLOWED_AGENT_ARNS", "arn:aws:iam::123456789012:role/mcp-*")
        settings_module._settings = None

        test_app = FastAPI()
        settings = settings_module.get_settings()
        # Assigned rather than set through the environment on purpose:
        # `Settings.model_config`'s env_file is resolved once at class-definition
        # time, so a developer's backend/.env.liam-dev is already baked in and
        # `monkeypatch.delenv` cannot take MCP_EXECUTOR_ARN away again. Mutating
        # the cached singleton is what makes "unconfigured" actually testable.
        settings.mcp_executor_arn = executor_arn
        human_auth.install(test_app, settings)
        test_app.include_router(tickets_router)
        test_app.include_router(aws_meta_router)
        install_error_handlers(test_app)
        repo = JsonlTicketRepository(str(tmp_path))
        test_app.dependency_overrides[get_repo] = lambda: repo
        client = TestClient(test_app)
        client.repo = repo  # type: ignore[attr-defined]
        return client

    yield _make
    settings_module._settings = None


def login(client, email="liam@example.com", role="approver"):
    r = client.get(f"/api/auth/login?email={email}&role={role}", follow_redirects=False)
    assert r.status_code in (302, 307)


def payload(**overrides):
    body = {
        "subject": "Launch a build box",
        "plannedDate": "2026-09-01",
        "plannedAction": "Launch one t3.micro",
        "actionDetails": {
            "service": "ec2",
            "operation": "RunInstances",
            "region": "ca-central-1",
            "parameters": {
                "ImageId": "ami-0abc",
                "InstanceType": "t3.micro",
                "MinCount": 1,
                "MaxCount": 1,
            },
        },
        "tags": {"team": "gti"},
    }
    body.update(overrides)
    return body


# --- create -----------------------------------------------------------------


def test_requires_a_session(make_client):
    assert make_client().post("/api/tickets", json=payload()).status_code == 401


def test_creates_a_pending_ticket_assigned_to_the_executor(make_client):
    client = make_client()
    login(client)
    r = client.post("/api/tickets", json=payload())
    assert r.status_code == 201, r.text
    ticket = r.json()
    assert ticket["status"] == "PENDING_APPROVAL"
    assert ticket["proposedBy"] == "liam@example.com"
    # The failure this guards is 304b78d's: a ticket nobody is assigned to gets
    # approved and then sits there, because the poller filters on assignee.
    assert ticket["assignee"] == EXECUTOR_ARN


def test_gate_tags_are_injected_into_the_call_and_covered_by_the_hash(make_client):
    """The approver must see the TagSpecifications that IAM requires."""
    client = make_client()
    login(client)
    ticket = client.post("/api/tickets", json=payload()).json()
    specs = ticket["actionDetails"]["parameters"]["TagSpecifications"]
    by_type = {spec["ResourceType"]: spec["Tags"] for spec in specs}
    assert set(by_type) == {"instance", "volume", "network-interface"}
    assert {"Key": "gateTicketId", "Value": ticket["ticketId"]} in by_type["instance"]
    assert {"Key": "team", "Value": "gti"} in by_type["instance"]


def test_injected_tags_survive_the_executor_hash_echo(make_client):
    """End to end on the property the injection could plausibly break.

    `build_ticket` hashes the merged parameters and `start_execution` compares
    the executor's echo against that stored hash. `canonical_json` sorts keys
    but NOT list elements, so if `with_gate_tags` ever built its
    TagSpecifications/Tags lists in a non-deterministic order, every launch
    would fail HASH_MISMATCH and refuse to run. No other test covers this: the
    shared fixture payload is StopInstances, which gets no injection at all.
    """
    client = make_client()
    login(client)
    ticket = client.post("/api/tickets", json=payload()).json()

    async def _drive():
        await service.approve_ticket(client.repo, ticket["ticketId"], "peer@example.com", 1)
        return await service.start_execution(
            client.repo,
            ticket["ticketId"],
            EXECUTOR_ARN,
            ticket["actionDetails"]["parametersHash"],
        )

    started = anyio.run(_drive)
    assert started.status == "EXECUTING"
    assert "TagSpecifications" in started.action_details.parameters


def test_double_submit_returns_the_same_ticket(make_client):
    client = make_client()
    login(client)
    first = client.post("/api/tickets", json=payload()).json()
    second = client.post("/api/tickets", json=payload()).json()
    assert first["ticketId"] == second["ticketId"]


def test_conditional_rule_is_enforced_on_this_path_too(make_client):
    """RunInstances with no ImageId — the call that wasted a real approval."""
    client = make_client()
    login(client)
    body = payload()
    body["actionDetails"]["parameters"] = {"InstanceType": "t3.micro", "MinCount": 1, "MaxCount": 1}
    r = client.post("/api/tickets", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_ACTION_PARAMETERS"
    assert "ImageId" in r.json()["error"]["message"]


def test_refuses_when_no_executor_is_configured(make_client):
    """Better a 503 now than an approved ticket nothing will ever pick up."""
    client = make_client(executor_arn=None)
    login(client)
    r = client.post("/api/tickets", json=payload())
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "EXECUTOR_NOT_CONFIGURED"


# --- the form's schema lookup ----------------------------------------------


def test_operations_list_requires_a_session(make_client):
    assert make_client().get("/api/aws/ec2/operations").status_code == 401


def test_operations_list_is_offered(make_client):
    client = make_client()
    login(client)
    operations = client.get("/api/aws/ec2/operations").json()["operations"]
    assert "RunInstances" in operations
    assert "TerminateInstances" in operations


def test_operation_schema_carries_what_the_form_needs(make_client):
    client = make_client()
    login(client)
    described = client.get("/api/aws/ec2/operations/RunInstances").json()
    assert described["required"] == ["MaxCount", "MinCount"]
    assert described["conditional"][0]["oneOf"] == ["ImageId", "LaunchTemplate"]
    assert described["accepted"]["ImageId"] == "string"
    # The form must not render TagSpecifications inputs the gate will overwrite.
    assert described["gateTags"] == ["instance", "volume", "network-interface"]


def test_unknown_operation_is_a_422_not_a_404(make_client):
    client = make_client()
    login(client)
    r = client.get("/api/aws/ec2/operations/RunInstancez")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_ACTION_PARAMETERS"
