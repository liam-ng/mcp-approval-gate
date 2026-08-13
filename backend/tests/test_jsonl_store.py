from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ulid import ULID

from app.core.models import Ticket
from app.repo.base import ConflictError, DuplicateError, NotFoundError
from app.repo.jsonl_store import JsonlTicketRepository
from tests.conftest import AGENT_ARN, created_event, make_event, make_ticket


@pytest.fixture
def repo(tmp_path):
    return JsonlTicketRepository(str(tmp_path))


def approval_details(email="peer@example.com"):
    return {"approval": {"approvedBy": email, "approvedAt": datetime.now(UTC).isoformat()}}


async def seed(repo, **overrides) -> Ticket:
    ticket = make_ticket(**overrides)
    await repo.create_ticket(ticket, created_event(ticket))
    return ticket


async def test_create_and_get(repo):
    ticket = await seed(repo)
    got = await repo.get_ticket(ticket.ticket_id)
    assert got is not None
    assert got.subject == ticket.subject
    assert got.seq == 1


async def test_duplicate_id_rejected(repo):
    ticket = await seed(repo)
    with pytest.raises(DuplicateError):
        await repo.create_ticket(ticket, created_event(ticket))


async def test_idempotency_lookup_and_duplicate(repo):
    ticket = await seed(repo, idempotency_key="conv-42")
    found = await repo.find_by_idempotency_key(AGENT_ARN, "conv-42")
    assert found is not None and found.ticket_id == ticket.ticket_id
    assert await repo.find_by_idempotency_key(AGENT_ARN, "other") is None
    dup = make_ticket(idempotency_key="conv-42")
    with pytest.raises(DuplicateError):
        await repo.create_ticket(dup, created_event(dup))


async def test_append_event_cas_conflict(repo):
    ticket = await seed(repo)
    ev = make_event(ticket, "APPROVAL_ADDED", to_status="APPROVED", details=approval_details())
    await repo.append_event(ticket.ticket_id, expected_seq=1, event=ev)
    stale = make_event(ticket, "APPROVAL_ADDED", to_status="APPROVED", details=approval_details("m@x.com"))
    with pytest.raises(ConflictError):
        await repo.append_event(ticket.ticket_id, expected_seq=1, event=stale)


async def test_append_to_missing_ticket(repo):
    ticket = make_ticket()
    ev = make_event(ticket, "EXPIRED", actor_kind="system", actor_id="gate", to_status="EXPIRED")
    with pytest.raises(NotFoundError):
        await repo.append_event(ticket.ticket_id, 1, ev)


async def test_fold_rebuild_after_restart(repo, tmp_path):
    ticket = await seed(repo)
    approved = make_event(ticket, "APPROVED", to_status="APPROVED", details=approval_details())
    await repo.append_event(ticket.ticket_id, 1, approved)

    reopened = JsonlTicketRepository(str(tmp_path))
    got = await reopened.get_ticket(ticket.ticket_id)
    assert got is not None
    assert got.status == "APPROVED"
    assert got.seq == 2
    assert got.approvals[0].approved_by == "peer@example.com"
    events = await reopened.list_audit_events(ticket.ticket_id)
    assert [e.type for e in events] == ["TICKET_CREATED", "APPROVED"]


async def test_torn_final_line_recovery(repo, tmp_path):
    ticket = await seed(repo)
    log = tmp_path / "tickets.jsonl"
    with open(log, "ab") as f:
        f.write(b'{"eventId":"torn","ticketId":"' + ticket.ticket_id.encode())  # no newline, cut off

    reopened = JsonlTicketRepository(str(tmp_path))
    got = await reopened.get_ticket(ticket.ticket_id)
    assert got is not None and got.seq == 1  # torn line skipped, state intact


async def test_corrupt_middle_line_aborts(repo, tmp_path):
    await seed(repo)
    log = tmp_path / "tickets.jsonl"
    content = log.read_bytes()
    log.write_bytes(b'{"garbage": tru\n' + content)
    with pytest.raises(RuntimeError, match="corrupt"):
        JsonlTicketRepository(str(tmp_path))


async def test_transact_supersede_atomic(repo, tmp_path):
    old = await seed(repo)
    new = make_ticket(supersedes=old.ticket_id, lineage_root_id=old.lineage_root_id,
                      proposed_by="editor@example.com")
    dep = make_event(old, "DEPRECATED", actor_id="editor@example.com",
                     to_status="DEPRECATED", details={"supersededBy": new.ticket_id})
    await repo.transact_supersede(old.ticket_id, 1, dep, new, created_event(new))

    old_after = await repo.get_ticket(old.ticket_id)
    new_after = await repo.get_ticket(new.ticket_id)
    assert old_after.status == "DEPRECATED" and old_after.superseded_by == new.ticket_id
    assert new_after.status == "PENDING_APPROVAL" and new_after.supersedes == old.ticket_id

    lineage = await repo.query_lineage(old.lineage_root_id)
    assert [t.ticket_id for t in lineage] == sorted([old.ticket_id, new.ticket_id])

    # Both lines land in one write; a reopened store sees the same state.
    reopened = JsonlTicketRepository(str(tmp_path))
    assert (await reopened.get_ticket(new.ticket_id)) is not None


async def test_partial_supersede_repaired_on_boot(repo, tmp_path):
    old = await seed(repo)
    new = make_ticket(supersedes=old.ticket_id, lineage_root_id=old.lineage_root_id)
    dep = make_event(old, "DEPRECATED", to_status="DEPRECATED",
                     details={"supersededBy": new.ticket_id})
    await repo.transact_supersede(old.ticket_id, 1, dep, new, created_event(new))

    # Simulate a crash that persisted the DEPRECATED line but tore the
    # successor's TICKET_CREATED line: drop the last line from the log.
    log = tmp_path / "tickets.jsonl"
    lines = log.read_bytes().splitlines(keepends=True)
    log.write_bytes(b"".join(lines[:-1]))

    reopened = JsonlTicketRepository(str(tmp_path))
    repaired = await reopened.get_ticket(old.ticket_id)
    assert repaired.status == "PENDING_APPROVAL"  # deprecation reverted
    assert repaired.superseded_by is None
    assert await reopened.get_ticket(new.ticket_id) is None


async def test_partial_supersede_of_terminal_ticket_repaired_on_boot(repo, tmp_path):
    """Same torn-write repair, on the SUPERSEDED path. The old ticket keeps its
    FAILED status here, so a repair keyed on status == DEPRECATED would miss it
    and leave superseded_by pointing at a ticket that was never written."""
    old = await seed(repo, status="FAILED")
    new = make_ticket(supersedes=old.ticket_id, lineage_root_id=old.lineage_root_id)
    link = make_event(old, "SUPERSEDED", to_status="FAILED",
                      details={"supersededBy": new.ticket_id})
    await repo.transact_supersede(old.ticket_id, 1, link, new, created_event(new))
    assert (await repo.get_ticket(old.ticket_id)).superseded_by == new.ticket_id

    log = tmp_path / "tickets.jsonl"
    lines = log.read_bytes().splitlines(keepends=True)
    log.write_bytes(b"".join(lines[:-1]))

    reopened = JsonlTicketRepository(str(tmp_path))
    repaired = await reopened.get_ticket(old.ticket_id)
    assert repaired.superseded_by is None  # dangling link reverted
    assert repaired.status == "FAILED"  # ...without disturbing the outcome
    assert await reopened.get_ticket(new.ticket_id) is None


async def test_superseded_event_does_not_touch_status_or_execution(repo):
    """The fold's SUPERSEDED branch sets exactly one field."""
    old = await seed(repo, status="CLOSED")
    before = await repo.get_ticket(old.ticket_id)
    link = make_event(old, "SUPERSEDED", to_status="CLOSED", details={"supersededBy": "T-NEW"})
    after = await repo.append_event(old.ticket_id, before.seq, link)

    assert after.superseded_by == "T-NEW"
    assert after.status == "CLOSED"
    assert after.execution == before.execution
    assert after.model_dump(exclude={"superseded_by", "seq"}) == before.model_dump(
        exclude={"superseded_by", "seq"}
    )


async def test_immutability_frozen_fields_survive_fold(repo, tmp_path):
    """No event payload can rewrite frozen fields — apply_event only touches
    MUTABLE_FIELDS, even if details smuggles ticket-shaped data."""
    ticket = await seed(repo)
    ev = make_event(
        ticket, "APPROVAL_ADDED", to_status="APPROVED",
        details={**approval_details(), "subject": "HACKED", "ticket": {"subject": "HACKED"}},
    )
    updated = await repo.append_event(ticket.ticket_id, 1, ev)
    assert updated.subject == ticket.subject
    assert updated.action_details == ticket.action_details
    assert updated.assignee == ticket.assignee


async def test_query_by_status_pagination(repo):
    for _ in range(5):
        await seed(repo)
    page1 = await repo.query_by_status("PENDING_APPROVAL", limit=2)
    assert len(page1.items) == 2 and page1.cursor is not None
    page2 = await repo.query_by_status("PENDING_APPROVAL", limit=2, cursor=page1.cursor)
    assert len(page2.items) == 2
    page3 = await repo.query_by_status("PENDING_APPROVAL", limit=2, cursor=page2.cursor)
    assert len(page3.items) == 1 and page3.cursor is None
    ids = {t.ticket_id for t in page1.items + page2.items + page3.items}
    assert len(ids) == 5
