"""Business rules on top of the status machine and repository.

The status machine answers "may this status move there, by this actor kind";
this module adds the identity rules that need request context: approver is
never the proposer, approvers are distinct, only the latest ticket in a
lineage is actionable, the executing caller must be the assignee, and the
executed parameters must be exactly the approved ones (hash echo).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ulid import ULID

from app.core.canonical_json import parameters_hash
from app.core.models import (
    ActionDetails,
    Actor,
    Approval,
    AuditEvent,
    Ticket,
)
from app.core.schemas import ExecutionResultRequest, TicketCreateRequest
from app.core.status_machine import assert_transition, status_after_approval
from app.repo.base import TicketRepository


class ServiceError(Exception):
    """Base; carries an HTTP-ish code for the routes to map."""

    http_status = 400
    code = "BAD_REQUEST"


class TicketNotFound(ServiceError):
    http_status = 404
    code = "NOT_FOUND"


class NotTicketAssignee(ServiceError):
    http_status = 403
    code = "NOT_ASSIGNEE"


class InvalidTicketState(ServiceError):
    http_status = 409
    code = "INVALID_STATE"


class ParametersHashMismatch(ServiceError):
    http_status = 409
    code = "HASH_MISMATCH"


class ApproverIsProposer(ServiceError):
    http_status = 403
    code = "APPROVER_IS_PROPOSER"


class DuplicateApprover(ServiceError):
    http_status = 409
    code = "DUPLICATE_APPROVER"


class TicketSuperseded(ServiceError):
    http_status = 409
    code = "TICKET_SUPERSEDED"


def _now() -> datetime:
    return datetime.now(UTC)


def _event(ticket_or_id, seq: int, type_: str, actor: Actor, *, from_status=None, to_status=None, details=None) -> AuditEvent:
    ticket_id = ticket_or_id if isinstance(ticket_or_id, str) else ticket_or_id.ticket_id
    return AuditEvent(
        event_id=str(ULID()),
        ticket_id=ticket_id,
        seq=seq,
        timestamp=_now(),
        type=type_,
        actor=actor,
        from_status=from_status,
        to_status=to_status,
        details=details,
    )


def build_ticket(payload: TicketCreateRequest, *, assignee: str, proposed_by: str,
                 idempotency_key: str | None = None,
                 supersedes: Ticket | None = None) -> tuple[Ticket, AuditEvent]:
    """Materialize a new PENDING_APPROVAL ticket + its TICKET_CREATED event."""
    ticket_id = str(ULID())
    details = ActionDetails(
        service=payload.action_details.service,
        operation=payload.action_details.operation,
        region=payload.action_details.region,
        parameters=payload.action_details.parameters,
        parameters_hash=parameters_hash(payload.action_details.parameters),
        resource_arns=payload.action_details.resource_arns,
        reason=payload.action_details.reason,
    )
    # gateTicketId/owner are default tags the gate itself sets on every
    # ticket, always overwritten last so a caller can't spoof who the
    # requester was or which ticket a resource traces back to. gateTicketId
    # doubles as the tag IAM enforcement keys on (aws:RequestTag/gateTicketId,
    # deploy/scp/deny-ec2-mutations-except-gate.json) — setting it here means
    # an agent that just propagates `tags` verbatim (docs/agent-contract.md
    # step 4) gets it for free, no separate manual tag to remember.
    tags = {**payload.tags, "gateTicketId": ticket_id, "owner": proposed_by}
    ticket = Ticket(
        ticket_id=ticket_id,
        subject=payload.subject,
        ticket_date=_now(),
        status="PENDING_APPROVAL",
        planned_date=payload.planned_date,
        planned_action=payload.planned_action,
        action_details=details,
        tags=tags,
        assignee=assignee,
        proposed_by=proposed_by,
        supersedes=supersedes.ticket_id if supersedes else None,
        lineage_root_id=supersedes.lineage_root_id if supersedes else ticket_id,
        idempotency_key=idempotency_key,
        seq=1,
    )
    actor_kind = "agent" if proposed_by == assignee else "human"
    created = _event(
        ticket, 1, "TICKET_CREATED", Actor(kind=actor_kind, id=proposed_by),
        to_status="PENDING_APPROVAL",
        details={"ticket": ticket.model_dump(mode="json", by_alias=True)},
    )
    return ticket, created


# --- agent operations -------------------------------------------------------


async def create_agent_ticket(
    repo: TicketRepository, payload: TicketCreateRequest, caller_arn: str,
    idempotency_key: str | None,
) -> tuple[Ticket, bool]:
    """Returns (ticket, created). Idempotent per (caller_arn, key)."""
    if idempotency_key:
        existing = await repo.find_by_idempotency_key(caller_arn, idempotency_key)
        if existing:
            return existing, False
    ticket, created = build_ticket(
        payload, assignee=caller_arn, proposed_by=caller_arn, idempotency_key=idempotency_key
    )
    await repo.create_ticket(ticket, created)
    return ticket, True


async def create_mcp_ticket(
    repo: TicketRepository, payload: TicketCreateRequest, proposer_email: str,
    executor_arn: str, idempotency_key: str | None,
) -> tuple[Ticket, bool]:
    """Ticket creation from the /mcp route (see api/mcp_gateway.py).

    The proposer is a human, identified by the OAuth bearer token the IDE
    presented — never an AI agent. The assignee is the fixed, trusted
    executor identity (MCP_EXECUTOR_ARN) that polls and executes via the
    existing SigV4 agent contract; the human's own credentials never touch
    AWS. Because proposed_by != assignee, approve_ticket's
    proposer-cannot-approve-own-ticket rule still applies normally, and
    because proposed_by is the human (not the executor), that same human
    cannot approve their own MCP-created ticket either.
    """
    if idempotency_key:
        existing = await repo.find_by_idempotency_key(executor_arn, idempotency_key)
        if existing:
            return existing, False
    ticket, created = build_ticket(
        payload, assignee=executor_arn, proposed_by=proposer_email, idempotency_key=idempotency_key
    )
    await repo.create_ticket(ticket, created)
    return ticket, True


async def get_agent_ticket(repo: TicketRepository, ticket_id: str, caller_arn: str) -> Ticket:
    ticket = await repo.get_ticket(ticket_id)
    if ticket is None:
        raise TicketNotFound(f"ticket {ticket_id} not found")
    if ticket.assignee != caller_arn:
        raise NotTicketAssignee("caller is not the ticket assignee")
    return ticket


async def start_execution(
    repo: TicketRepository, ticket_id: str, caller_arn: str, echoed_hash: str
) -> Ticket:
    ticket = await get_agent_ticket(repo, ticket_id, caller_arn)
    if ticket.status != "APPROVED":
        raise InvalidTicketState(f"ticket is {ticket.status}, not APPROVED")
    if echoed_hash != ticket.action_details.parameters_hash:
        raise ParametersHashMismatch(
            "echoed parametersHash does not match the approved ticket — the plan drifted; abort"
        )
    assert_transition(ticket.status, "EXECUTING", "agent")
    event = _event(
        ticket, ticket.seq + 1, "EXECUTION_STARTED", Actor(kind="agent", id=caller_arn),
        from_status=ticket.status, to_status="EXECUTING",
    )
    return await repo.append_event(ticket.ticket_id, ticket.seq, event)


async def report_execution_result(
    repo: TicketRepository, ticket_id: str, caller_arn: str, result: ExecutionResultRequest
) -> Ticket:
    ticket = await get_agent_ticket(repo, ticket_id, caller_arn)
    if ticket.status != "EXECUTING":
        raise InvalidTicketState(f"ticket is {ticket.status}, not EXECUTING")
    to_status = "COMPLETED" if result.outcome == "success" else "FAILED"
    assert_transition(ticket.status, to_status, "agent")
    event = _event(
        ticket, ticket.seq + 1,
        "EXECUTION_COMPLETED" if result.outcome == "success" else "EXECUTION_FAILED",
        Actor(kind="agent", id=caller_arn),
        from_status=ticket.status, to_status=to_status,
        details={
            "outcome": result.outcome,
            "message": result.message,
            "awsRequestIds": result.aws_request_ids,
        },
    )
    return await repo.append_event(ticket.ticket_id, ticket.seq, event)


# --- human operations -------------------------------------------------------


def _assert_actionable_by(ticket: Ticket, email: str) -> None:
    if ticket.superseded_by:
        raise TicketSuperseded(f"ticket superseded by {ticket.superseded_by}")
    if ticket.proposed_by.lower() == email.lower():
        raise ApproverIsProposer("the proposer cannot approve or reject their own ticket")


async def approve_ticket(
    repo: TicketRepository, ticket_id: str, approver_email: str, required_approvals: int
) -> Ticket:
    ticket = await repo.get_ticket(ticket_id)
    if ticket is None:
        raise TicketNotFound(f"ticket {ticket_id} not found")
    if ticket.status != "PENDING_APPROVAL":
        raise InvalidTicketState(f"ticket is {ticket.status}, not PENDING_APPROVAL")
    _assert_actionable_by(ticket, approver_email)
    if any(a.approved_by.lower() == approver_email.lower() for a in ticket.approvals):
        raise DuplicateApprover("this approver has already approved the ticket")

    new_count = len(ticket.approvals) + 1
    to_status = status_after_approval(new_count, required_approvals)
    assert_transition(ticket.status, to_status, "human")
    approval = Approval(approved_by=approver_email, approved_at=_now())
    event = _event(
        ticket, ticket.seq + 1,
        "APPROVED" if to_status == "APPROVED" else "APPROVAL_ADDED",
        Actor(kind="human", id=approver_email),
        from_status=ticket.status, to_status=to_status,
        details={"approval": approval.model_dump(mode="json", by_alias=True)},
    )
    return await repo.append_event(ticket.ticket_id, ticket.seq, event)


async def reject_ticket(
    repo: TicketRepository, ticket_id: str, approver_email: str, reason: str
) -> Ticket:
    ticket = await repo.get_ticket(ticket_id)
    if ticket is None:
        raise TicketNotFound(f"ticket {ticket_id} not found")
    if ticket.status != "PENDING_APPROVAL":
        raise InvalidTicketState(f"ticket is {ticket.status}, not PENDING_APPROVAL")
    _assert_actionable_by(ticket, approver_email)
    assert_transition(ticket.status, "REJECTED", "human")
    event = _event(
        ticket, ticket.seq + 1, "REJECTED", Actor(kind="human", id=approver_email),
        from_status=ticket.status, to_status="REJECTED", details={"reason": reason},
    )
    return await repo.append_event(ticket.ticket_id, ticket.seq, event)


async def supersede_ticket(
    repo: TicketRepository, old_ticket_id: str, editor_email: str, payload: TicketCreateRequest
) -> Ticket:
    old = await repo.get_ticket(old_ticket_id)
    if old is None:
        raise TicketNotFound(f"ticket {old_ticket_id} not found")
    if old.superseded_by:
        raise TicketSuperseded(f"ticket already superseded by {old.superseded_by}")
    if old.status not in ("PENDING_APPROVAL", "APPROVED"):
        raise InvalidTicketState(f"cannot supersede a {old.status} ticket")
    assert_transition(old.status, "DEPRECATED", "human")

    # assignee carries over: the AI agent still executes; the human editor
    # becomes the proposer, so they cannot approve their own edit.
    new_ticket, created = build_ticket(
        payload, assignee=old.assignee, proposed_by=editor_email, supersedes=old
    )
    deprecated = _event(
        old, old.seq + 1, "DEPRECATED", Actor(kind="human", id=editor_email),
        from_status=old.status, to_status="DEPRECATED",
        details={"supersededBy": new_ticket.ticket_id},
    )
    await repo.transact_supersede(old.ticket_id, old.seq, deprecated, new_ticket, created)
    return new_ticket


async def add_comment(repo: TicketRepository, ticket_id: str, actor_email: str, text: str) -> Ticket:
    """Discussion only — open to any authenticated IT-team session user
    regardless of role or ticket status, unlike approve/reject/supersede.
    No field on Ticket changes; the comment lives only in the audit trail."""
    ticket = await repo.get_ticket(ticket_id)
    if ticket is None:
        raise TicketNotFound(f"ticket {ticket_id} not found")
    event = _event(
        ticket, ticket.seq + 1, "COMMENT_ADDED", Actor(kind="human", id=actor_email),
        from_status=ticket.status, to_status=ticket.status,
        details={"text": text},
    )
    return await repo.append_event(ticket.ticket_id, ticket.seq, event)


async def update_tags(
    repo: TicketRepository, ticket_id: str, actor_email: str, new_tags: dict[str, str]
) -> Ticket:
    """Change a ticket's tags in place via a TAGS_UPDATED audit event — no new
    ticket, no fresh approval. Safe to do without superseding because tags are
    metadata: they're not part of ActionDetails, so parametersHash and every
    approver-facing fact about the action are unaffected."""
    ticket = await repo.get_ticket(ticket_id)
    if ticket is None:
        raise TicketNotFound(f"ticket {ticket_id} not found")
    if ticket.superseded_by:
        raise TicketSuperseded(f"ticket already superseded by {ticket.superseded_by}")
    # gateTicketId/owner are set once at creation (build_ticket) and are never
    # user-editable afterward — reassert them regardless of what the caller sent.
    tags = {**new_tags, "gateTicketId": ticket.ticket_id, "owner": ticket.proposed_by}
    event = _event(
        ticket, ticket.seq + 1, "TAGS_UPDATED", Actor(kind="human", id=actor_email),
        from_status=ticket.status, to_status=ticket.status,
        details={"oldTags": ticket.tags, "tags": tags},
    )
    return await repo.append_event(ticket.ticket_id, ticket.seq, event)
