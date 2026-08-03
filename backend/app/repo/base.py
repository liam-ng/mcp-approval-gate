"""Repository contract and the shared event fold.

The interface is deliberately shaped like DynamoDB single-table access
patterns (get by id, query by status, lineage chain, conditional append) so
the production migration is a config swap, not a rewrite:

- dynamodb: PK=TICKET#id / SK=META|EVENT#seq; GSI1 STATUS#status/ticketDate;
  GSI2 LINEAGE#rootId; GSI3 IDEM#arn#key. append_event -> TransactWriteItems
  with ConditionExpression seq = :expected.
- s3 (Object Lock / WORM): one immutable object per event
  tickets/{id}/events/{seq:06d}.json; CAS via conditional PutObject
  (If-None-Match: *). Same fold as jsonl.

Every store materializes tickets by folding AuditEvents through apply_event —
the fold below is the single implementation all backends share, and it only
touches MUTABLE_FIELDS, which is what makes tickets immutable by construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.models import AuditEvent, Execution, Ticket, TicketStatus


class RepoError(Exception):
    pass


class NotFoundError(RepoError):
    pass


class DuplicateError(RepoError):
    pass


class ConflictError(RepoError):
    """CAS failure: the ticket changed since the caller read it (HTTP 409)."""


@dataclass
class Page:
    items: list[Ticket]
    cursor: str | None = None


def apply_event(ticket: Ticket, event: AuditEvent) -> Ticket:
    """Fold one event into a ticket, returning a new Ticket.

    Only fields in MUTABLE_FIELDS change; anything else in the event payload
    is ignored, so no event can rewrite frozen fields.
    """
    if event.seq != ticket.seq + 1:
        raise ConflictError(
            f"event seq {event.seq} does not follow ticket seq {ticket.seq} for {ticket.ticket_id}"
        )
    updates: dict = {"seq": event.seq}
    details = event.details or {}

    if event.type in ("APPROVAL_ADDED", "APPROVED"):
        from app.core.models import Approval

        approval = Approval.model_validate(details["approval"])
        updates["approvals"] = [*ticket.approvals, approval]
        updates["status"] = event.to_status
    elif event.type == "REJECTED":
        updates["status"] = "REJECTED"
        updates["rejected_by"] = event.actor.id
        updates["rejected_at"] = event.timestamp
        updates["rejection_reason"] = details.get("reason")
    elif event.type == "DEPRECATED":
        updates["status"] = "DEPRECATED"
        updates["superseded_by"] = details["supersededBy"]
    elif event.type == "EXPIRED":
        updates["status"] = "EXPIRED"
    elif event.type == "CLOSED":
        updates["status"] = "CLOSED"
    elif event.type == "EXECUTION_STARTED":
        updates["status"] = "EXECUTING"
        updates["execution"] = Execution(started_at=event.timestamp)
    elif event.type in ("EXECUTION_COMPLETED", "EXECUTION_FAILED"):
        if ticket.execution is None:
            raise ConflictError(f"{event.type} without EXECUTION_STARTED for {ticket.ticket_id}")
        updates["status"] = "CLOSED" if event.type == "EXECUTION_COMPLETED" else "FAILED"
        updates["execution"] = ticket.execution.model_copy(
            update={
                "finished_at": event.timestamp,
                "outcome": details.get("outcome"),
                "message": details.get("message"),
                "aws_request_ids": details.get("awsRequestIds", []),
            }
        )
    elif event.type == "TAGS_UPDATED":
        updates["tags"] = details["tags"]
    elif event.type == "COMMENT_ADDED":
        pass  # discussion only; no Ticket field changes, just the seq bump above
    elif event.type == "TICKET_CREATED":
        raise ConflictError(f"duplicate TICKET_CREATED for {ticket.ticket_id}")
    else:  # pragma: no cover - exhaustive over AuditEventType
        raise RepoError(f"unknown event type {event.type}")

    return ticket.model_copy(update=updates)


class TicketRepository(ABC):
    @abstractmethod
    async def create_ticket(self, ticket: Ticket, created: AuditEvent) -> None:
        """Persist a new ticket. Raises DuplicateError on id or (assignee,
        idempotency key) collision."""

    @abstractmethod
    async def get_ticket(self, ticket_id: str) -> Ticket | None: ...

    @abstractmethod
    async def find_by_idempotency_key(self, assignee_arn: str, key: str) -> Ticket | None: ...

    @abstractmethod
    async def query_by_status(
        self, status: TicketStatus, limit: int = 50, cursor: str | None = None
    ) -> Page: ...

    @abstractmethod
    async def query_all(self, limit: int = 50, cursor: str | None = None) -> Page: ...

    @abstractmethod
    async def query_lineage(self, lineage_root_id: str) -> list[Ticket]:
        """All tickets in a chain, oldest first."""

    @abstractmethod
    async def list_audit_events(self, ticket_id: str) -> list[AuditEvent]: ...

    @abstractmethod
    async def append_event(self, ticket_id: str, expected_seq: int, event: AuditEvent) -> Ticket:
        """The only mutation primitive. CAS-guarded by expected_seq; raises
        ConflictError if the ticket moved."""

    @abstractmethod
    async def transact_supersede(
        self,
        old_ticket_id: str,
        expected_seq: int,
        deprecated_event: AuditEvent,
        new_ticket: Ticket,
        created_event: AuditEvent,
    ) -> None:
        """Atomically deprecate the old ticket and create its successor."""
