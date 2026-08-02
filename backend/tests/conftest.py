from __future__ import annotations

from datetime import UTC, date, datetime

from ulid import ULID

from app.core.canonical_json import parameters_hash
from app.core.models import ActionDetails, Actor, AuditEvent, Ticket

AGENT_ARN = "arn:aws:sts::123456789012:assumed-role/mcp-agent/session"


def make_ticket(**overrides) -> Ticket:
    ticket_id = overrides.pop("ticket_id", str(ULID()))
    params = overrides.pop("parameters", {"InstanceIds": ["i-0abc"]})
    fields: dict = {
        "ticket_id": ticket_id,
        "subject": "Stop staging instance",
        "ticket_date": datetime.now(UTC),
        "status": "PENDING_APPROVAL",
        "planned_date": date(2026, 8, 10),
        "planned_action": "Stop EC2 instance i-0abc in staging",
        "action_details": ActionDetails(
            service="ec2",
            operation="StopInstances",
            region="ap-east-1",
            parameters=params,
            parameters_hash=parameters_hash(params),
            resource_arns=["arn:aws:ec2:ap-east-1:123456789012:instance/i-0abc"],
        ),
        "tags": {"team": "gti"},
        "assignee": AGENT_ARN,
        "proposed_by": AGENT_ARN,
        "lineage_root_id": ticket_id,
        "seq": 1,
    }
    fields.update(overrides)
    return Ticket(**fields)


def created_event(ticket: Ticket) -> AuditEvent:
    return AuditEvent(
        event_id=str(ULID()),
        ticket_id=ticket.ticket_id,
        seq=1,
        timestamp=ticket.ticket_date,
        type="TICKET_CREATED",
        actor=Actor(kind="agent", id=ticket.assignee),
        to_status="PENDING_APPROVAL",
        details={"ticket": ticket.model_dump(mode="json", by_alias=True)},
    )


def make_event(ticket: Ticket, type_: str, *, actor_kind="human", actor_id="peer@example.com", to_status=None, details=None) -> AuditEvent:
    return AuditEvent(
        event_id=str(ULID()),
        ticket_id=ticket.ticket_id,
        seq=ticket.seq + 1,
        timestamp=datetime.now(UTC),
        type=type_,  # type: ignore[arg-type]
        actor=Actor(kind=actor_kind, id=actor_id),
        from_status=ticket.status,
        to_status=to_status,
        details=details,
    )
