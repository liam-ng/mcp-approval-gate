"""Request/response schemas shared by the API (and mirrored by the frontend)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator

from app.core.models import ActionDetails, ApiModel, AuditEvent, Ticket, TicketStatus


def _validate_tag_limits(v: dict[str, str]) -> dict[str, str]:
    if len(v) > 20:
        raise ValueError("at most 20 tags")
    for key, value in v.items():
        if not key or len(key) > 64 or len(value) > 256:
            raise ValueError("tag keys must be 1-64 chars, values at most 256")
    return v


class ActionDetailsIn(ApiModel):
    """Client-supplied action details — the gate computes parametersHash."""

    service: Literal["ec2"]
    operation: str = Field(min_length=1)
    region: str = Field(min_length=1)
    parameters: dict[str, Any]
    resource_arns: list[str] = Field(default_factory=list)
    reason: str | None = None


class TicketCreateRequest(ApiModel):
    subject: str = Field(min_length=3, max_length=200)
    planned_date: date
    planned_action: str = Field(min_length=3, max_length=500)
    action_details: ActionDetailsIn
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("tags")
    @classmethod
    def _tag_limits(cls, v: dict[str, str]) -> dict[str, str]:
        return _validate_tag_limits(v)


class TagsUpdateRequest(ApiModel):
    """Change a ticket's tags without superseding it (recorded as a TAGS_UPDATED
    audit event, not a new ticket) — tags are metadata, not part of the
    hash-locked action, so this doesn't touch the approval-integrity surface."""

    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("tags")
    @classmethod
    def _tag_limits(cls, v: dict[str, str]) -> dict[str, str]:
        return _validate_tag_limits(v)


class AgentPollResponse(ApiModel):
    ticket_id: str
    status: TicketStatus
    approved_by: list[str]
    rejection_reason: str | None = None
    superseded_by: str | None = None
    action_details: ActionDetails


class ExecutionStartRequest(ApiModel):
    parameters_hash: str = Field(min_length=64, max_length=64)


class ExecutionStartResponse(ApiModel):
    ticket_id: str
    status: TicketStatus
    action_details: ActionDetails  # the agent must execute exactly this


class ExecutionResultRequest(ApiModel):
    outcome: Literal["success", "failure"]
    message: str | None = Field(default=None, max_length=2000)
    aws_request_ids: list[str] = Field(default_factory=list)


class RejectRequest(ApiModel):
    reason: str = Field(min_length=5, max_length=1000)


class CommentCreateRequest(ApiModel):
    text: str = Field(min_length=1, max_length=2000)


class CloseTicketRequest(ApiModel):
    reason: str | None = Field(default=None, max_length=1000)


class ApprovalLinkPreview(ApiModel):
    """What the unauthenticated /act landing page (api/approval_link_actions.py)
    shows before the approver confirms — enough to make an informed decision,
    same as the portal's ticket detail page, without exposing anything beyond
    what was already put in the notification email."""

    ticket_id: str
    subject: str
    status: TicketStatus
    planned_date: date
    planned_action: str
    action_details: ActionDetails
    proposed_by: str
    action: Literal["approve", "reject"]
    # False whenever clicking through would just fail server-side -- the
    # landing page uses this (and blocked_reason below) to show a generic
    # explanation instead of a confirm form that's destined to error out.
    actionable: bool
    blocked_reason: (
        Literal["already_actioned", "not_approver", "self_approval", "duplicate_approval"] | None
    ) = None


class ApprovalLinkActionRequest(ApiModel):
    # Only required (and only validated) when the link's action is "reject" —
    # mirrors RejectRequest's min length so the portal and the email link
    # hold approvers to the same bar for a rejection reason.
    reason: str | None = Field(default=None, max_length=1000)


class TicketDetailResponse(ApiModel):
    ticket: Ticket
    lineage: list[Ticket]
    audit_events: list[AuditEvent]


class TicketListResponse(ApiModel):
    items: list[Ticket]
    cursor: str | None = None


class MeResponse(ApiModel):
    email: str
    name: str | None = None
    role: Literal["approver", "viewer"]
    # Lets the frontend compute a ticket's "approval due" date itself
    # (createdAt/lastApproval + this), mirroring app/jobs/expiry.py's own
    # cutoff, without a separate config round trip.
    approval_ttl_hours: int
