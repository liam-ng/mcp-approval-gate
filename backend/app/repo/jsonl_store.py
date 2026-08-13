"""JSONL append-log store (MVP backend).

One AuditEvent per line in $DATA_DIR/tickets.jsonl; TICKET_CREATED events
embed the full ticket under details["ticket"]. On boot the log is folded into
memory. Crash-safety properties:

- Appends never rewrite existing bytes, so history cannot be silently mutated.
- A torn final line (crash mid-append) is detected as invalid JSON and
  skipped on replay; corruption anywhere else aborts startup loudly.
- transact_supersede writes both lines in a single os.write so the pair is
  torn together or not at all; a superseded_by pointing at a successor whose
  TICKET_CREATED line is missing is repaired on replay by reverting the link
  event. Keyed on the dangling link rather than on status == DEPRECATED, since
  a follow-up to a FAILED/CLOSED ticket leaves its status untouched.

Requires a single replica (k8s strategy: Recreate) — writes are serialized by
one in-process asyncio.Lock and fsync'd.
"""

from __future__ import annotations

import json
import logging
import os
from asyncio import Lock
from pathlib import Path

from app.core.models import AuditEvent, Ticket, TicketStatus
from app.repo.base import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    Page,
    TicketRepository,
    apply_event,
)

logger = logging.getLogger(__name__)


def _dump(event: AuditEvent) -> bytes:
    return (json.dumps(event.model_dump(mode="json", by_alias=True), ensure_ascii=False) + "\n").encode()


class JsonlTicketRepository(TicketRepository):
    def __init__(self, data_dir: str):
        self._path = Path(data_dir) / "tickets.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._tickets: dict[str, Ticket] = {}
        self._events: dict[str, list[AuditEvent]] = {}
        self._by_idem: dict[tuple[str, str], str] = {}
        self._replay()

    # --- replay -----------------------------------------------------------

    def _replay(self) -> None:
        if not self._path.exists():
            return
        raw_lines = self._path.read_bytes().split(b"\n")
        # Trailing element after a final newline is empty; a non-empty last
        # element means the final append was torn.
        for i, raw in enumerate(raw_lines):
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                if i == len(raw_lines) - 1:
                    logger.warning("skipping torn final line in %s", self._path)
                    continue
                raise RuntimeError(f"corrupt event log {self._path} at line {i + 1}")
            self._apply_to_state(AuditEvent.model_validate(data))
        self._repair_partial_supersede()

    def _apply_to_state(self, event: AuditEvent) -> None:
        if event.type == "TICKET_CREATED":
            ticket = Ticket.model_validate((event.details or {})["ticket"])
            self._tickets[ticket.ticket_id] = ticket
            self._events.setdefault(ticket.ticket_id, []).append(event)
            if ticket.idempotency_key:
                self._by_idem[(ticket.assignee, ticket.idempotency_key)] = ticket.ticket_id
        else:
            current = self._tickets[event.ticket_id]
            self._tickets[event.ticket_id] = apply_event(current, event)
            self._events[event.ticket_id].append(event)

    def _repair_partial_supersede(self) -> None:
        for ticket in list(self._tickets.values()):
            # Keyed on superseded_by, NOT on status == "DEPRECATED": a follow-up
            # to a FAILED/CLOSED ticket sets the link via a SUPERSEDED event and
            # leaves the status alone, so a status check would sail straight past
            # a torn write on exactly that path and leave a dangling link.
            if ticket.superseded_by is not None and ticket.superseded_by not in self._tickets:
                logger.warning(
                    "reverting partial supersede on %s (missing successor %s)",
                    ticket.ticket_id,
                    ticket.superseded_by,
                )
                events = self._events[ticket.ticket_id]
                events.pop()
                rebuilt = Ticket.model_validate((events[0].details or {})["ticket"])
                for ev in events[1:]:
                    rebuilt = apply_event(rebuilt, ev)
                self._tickets[ticket.ticket_id] = rebuilt

    # --- write path -------------------------------------------------------

    def _append_lines(self, payload: bytes) -> None:
        with open(self._path, "ab") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

    async def create_ticket(self, ticket: Ticket, created: AuditEvent) -> None:
        async with self._lock:
            if ticket.ticket_id in self._tickets:
                raise DuplicateError(f"ticket {ticket.ticket_id} already exists")
            if ticket.idempotency_key and (ticket.assignee, ticket.idempotency_key) in self._by_idem:
                raise DuplicateError(
                    f"idempotency key {ticket.idempotency_key!r} already used by {ticket.assignee}"
                )
            self._append_lines(_dump(created))
            self._apply_to_state(created)

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        return self._tickets.get(ticket_id)

    async def find_by_idempotency_key(self, assignee_arn: str, key: str) -> Ticket | None:
        ticket_id = self._by_idem.get((assignee_arn, key))
        return self._tickets.get(ticket_id) if ticket_id else None

    async def query_by_status(
        self, status: TicketStatus, limit: int = 50, cursor: str | None = None
    ) -> Page:
        items = [t for t in self._tickets.values() if t.status == status]
        return self._paginate(items, limit, cursor)

    async def query_all(self, limit: int = 50, cursor: str | None = None) -> Page:
        return self._paginate(list(self._tickets.values()), limit, cursor)

    def _paginate(self, items: list[Ticket], limit: int, cursor: str | None) -> Page:
        items.sort(key=lambda t: t.ticket_id, reverse=True)  # ULIDs sort by creation time
        offset = int(cursor) if cursor else 0
        window = items[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(items) else None
        return Page(items=window, cursor=next_cursor)

    async def query_lineage(self, lineage_root_id: str) -> list[Ticket]:
        chain = [t for t in self._tickets.values() if t.lineage_root_id == lineage_root_id]
        chain.sort(key=lambda t: t.ticket_id)
        return chain

    async def list_audit_events(self, ticket_id: str) -> list[AuditEvent]:
        if ticket_id not in self._events:
            raise NotFoundError(f"ticket {ticket_id} not found")
        return list(self._events[ticket_id])

    async def append_event(self, ticket_id: str, expected_seq: int, event: AuditEvent) -> Ticket:
        async with self._lock:
            current = self._tickets.get(ticket_id)
            if current is None:
                raise NotFoundError(f"ticket {ticket_id} not found")
            if current.seq != expected_seq:
                raise ConflictError(
                    f"ticket {ticket_id} is at seq {current.seq}, expected {expected_seq}"
                )
            updated = apply_event(current, event)  # validates seq continuity
            self._append_lines(_dump(event))
            self._tickets[ticket_id] = updated
            self._events[ticket_id].append(event)
            return updated

    async def transact_supersede(
        self,
        old_ticket_id: str,
        expected_seq: int,
        supersede_event: AuditEvent,
        new_ticket: Ticket,
        created_event: AuditEvent,
    ) -> None:
        async with self._lock:
            old = self._tickets.get(old_ticket_id)
            if old is None:
                raise NotFoundError(f"ticket {old_ticket_id} not found")
            if old.seq != expected_seq:
                raise ConflictError(
                    f"ticket {old_ticket_id} is at seq {old.seq}, expected {expected_seq}"
                )
            if new_ticket.ticket_id in self._tickets:
                raise DuplicateError(f"ticket {new_ticket.ticket_id} already exists")
            # DEPRECATED or SUPERSEDED — the fold decides what the event changes.
            linked = apply_event(old, supersede_event)
            # Single write so the pair cannot be half-persisted (see module doc).
            self._append_lines(_dump(supersede_event) + _dump(created_event))
            self._tickets[old_ticket_id] = linked
            self._events[old_ticket_id].append(supersede_event)
            self._apply_to_state(created_event)
