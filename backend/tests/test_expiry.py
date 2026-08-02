from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core import service
from app.core.schemas import TicketCreateRequest
from app.jobs.expiry import sweep_once
from app.repo.jsonl_store import JsonlTicketRepository
from tests.conftest import AGENT_ARN
from tests.test_agent_api import create_payload


@pytest.fixture
def repo(tmp_path):
    return JsonlTicketRepository(str(tmp_path))


async def seed(repo):
    payload = TicketCreateRequest.model_validate(create_payload())
    ticket, _ = await service.create_agent_ticket(repo, payload, AGENT_ARN, None)
    return ticket


async def test_fresh_tickets_not_expired(repo):
    await seed(repo)
    assert await sweep_once(repo, ttl_hours=72) == 0


async def test_stale_pending_expires(repo):
    ticket = await seed(repo)
    later = datetime.now(UTC) + timedelta(hours=73)
    assert await sweep_once(repo, ttl_hours=72, now=later) == 1
    got = await repo.get_ticket(ticket.ticket_id)
    assert got is not None and got.status == "EXPIRED"
    events = await repo.list_audit_events(ticket.ticket_id)
    assert events[-1].type == "EXPIRED" and events[-1].actor.kind == "system"


async def test_approved_ttl_counts_from_approval(repo):
    ticket = await seed(repo)
    # Approve "70 hours after creation" is not simulatable without clock
    # control, so approve now and check the boundary arithmetic instead:
    approved = await service.approve_ticket(repo, ticket.ticket_id, "peer@example.com", 1)
    approval_time = approved.approvals[0].approved_at

    just_inside = approval_time + timedelta(hours=71)
    assert await sweep_once(repo, ttl_hours=72, now=just_inside) == 0
    just_outside = approval_time + timedelta(hours=73)
    assert await sweep_once(repo, ttl_hours=72, now=just_outside) == 1
    got = await repo.get_ticket(ticket.ticket_id)
    assert got is not None and got.status == "EXPIRED"


async def test_terminal_and_executing_untouched(repo):
    ticket = await seed(repo)
    await service.approve_ticket(repo, ticket.ticket_id, "peer@example.com", 1)
    await service.start_execution(
        repo, ticket.ticket_id, AGENT_ARN, ticket.action_details.parameters_hash
    )
    later = datetime.now(UTC) + timedelta(hours=1000)
    assert await sweep_once(repo, ttl_hours=72, now=later) == 0
    got = await repo.get_ticket(ticket.ticket_id)
    assert got is not None and got.status == "EXECUTING"
