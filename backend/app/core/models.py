"""Domain models.

Tickets are immutable: every change is an appended AuditEvent and the ticket is
a fold over its events (see repo layer). Fields not listed in MUTABLE_FIELDS
are frozen at creation; changing the action itself requires a superseding
ticket. API responses use camelCase aliases.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

TicketStatus = Literal[
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "DEPRECATED",
    "EXPIRED",
    "EXECUTING",
    "COMPLETED",
    "FAILED",
]

ALL_STATUSES: tuple[TicketStatus, ...] = (
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "DEPRECATED",
    "EXPIRED",
    "EXECUTING",
    "COMPLETED",
    "FAILED",
)

# No transition leaves these states; pollers stop here.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"REJECTED", "DEPRECATED", "EXPIRED", "COMPLETED", "FAILED"}
)

AuditEventType = Literal[
    "TICKET_CREATED",
    "APPROVAL_ADDED",
    "APPROVED",
    "REJECTED",
    "DEPRECATED",
    "EXPIRED",
    "EXECUTION_STARTED",
    "EXECUTION_COMPLETED",
    "EXECUTION_FAILED",
    "TAGS_UPDATED",
    "COMMENT_ADDED",
]


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ActionDetails(ApiModel):
    service: Literal["ec2"]
    operation: str  # AWS API name, e.g. "RunInstances", "StopInstances"
    region: str
    parameters: dict[str, Any]  # exact intended SDK params
    # sha256 of canonical JSON of `parameters`, computed by the gate — the agent
    # must echo it at execution/start.
    parameters_hash: str = ""
    # Specific ARNs/ids the action targets; empty only for pure-creation ops.
    # Approvers see exactly what is in scope; feeds IAM Resource scoping.
    resource_arns: list[str] = Field(default_factory=list)
    reason: str | None = None


class Approval(ApiModel):
    approved_by: str
    approved_at: datetime


class Execution(ApiModel):
    started_at: datetime
    finished_at: datetime | None = None
    outcome: Literal["success", "failure"] | None = None
    message: str | None = None
    aws_request_ids: list[str] = Field(default_factory=list)


class Actor(ApiModel):
    kind: Literal["agent", "human", "system"]
    id: str  # IAM ARN | email | "gate"


class Ticket(ApiModel):
    ticket_id: str  # ULID, sortable by creation time
    subject: str
    ticket_date: datetime  # set by the gate at creation
    status: TicketStatus
    planned_date: date
    planned_action: str  # human-readable summary
    action_details: ActionDetails
    # Management tags (team, project, cost-center, env); filterable in the UI
    # and propagated as AWS resource tags by the agent where supported.
    tags: dict[str, str] = Field(default_factory=dict)
    assignee: str  # VERIFIED agent IAM ARN from STS — never client-supplied
    proposed_by: str  # = assignee (agent-created) or human email (supersede)
    approvals: list[Approval] = Field(default_factory=list)
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    lineage_root_id: str  # first ticket in the chain (= own id if original)
    idempotency_key: str | None = None
    execution: Execution | None = None
    seq: int = 0  # event count; optimistic-concurrency token (CAS)


# The only ticket fields the event fold may change after creation. Everything
# else is frozen; the repo layer enforces this on replay and append.
MUTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        "approvals",
        "rejected_by",
        "rejected_at",
        "rejection_reason",
        "superseded_by",
        "execution",
        "seq",
        "tags",
    }
)


class AuditEvent(ApiModel):
    event_id: str
    ticket_id: str
    seq: int  # 1-based position in the ticket's event stream
    timestamp: datetime
    type: AuditEventType
    actor: Actor
    from_status: TicketStatus | None = None
    to_status: TicketStatus | None = None
    details: dict[str, Any] | None = None
