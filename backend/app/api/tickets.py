"""Human-facing ticket API (session-cookie auth).

Enforcement lives here and in core/service.py, not in the SPA: approver role
required to approve/reject, approver is never the proposer, only the latest
ticket in a lineage is actionable, CAS on seq turns approval races into 409s.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.auth import SessionUser, require_approver, require_session
from app.api.deps import get_repo
from app.core import service
from app.core.canonical_json import parameters_hash
from app.core.models import Ticket, TicketStatus
from app.notifications.ses import notify_ticket_created
from app.core.schemas import (
    CloseTicketRequest,
    CommentCreateRequest,
    RejectRequest,
    TagsUpdateRequest,
    TicketCreateRequest,
    TicketDetailResponse,
    TicketListResponse,
)
from app.repo.base import TicketRepository
from app.settings import get_settings

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

Repo = Annotated[TicketRepository, Depends(get_repo)]
User = Annotated[SessionUser, Depends(require_session)]
Approver = Annotated[SessionUser, Depends(require_approver)]


@router.get("", response_model=TicketListResponse, response_model_by_alias=True)
async def list_tickets(
    _: User,
    repo: Repo,
    status: TicketStatus | None = None,
    assignee: str | None = None,
    tag: str | None = Query(default=None, description="key=value"),
    limit: int = Query(default=50, le=100),
    cursor: str | None = None,
):
    page = (
        await repo.query_by_status(status, limit=limit, cursor=cursor)
        if status
        else await repo.query_all(limit=limit, cursor=cursor)
    )
    items = page.items
    if assignee:
        items = [t for t in items if t.assignee == assignee]
    if tag and "=" in tag:
        key, value = tag.split("=", 1)
        items = [t for t in items if t.tags.get(key) == value]
    return TicketListResponse(items=items, cursor=page.cursor)


@router.post("", response_model=Ticket, response_model_by_alias=True, status_code=201)
async def create_ticket(payload: TicketCreateRequest, user: User, repo: Repo):
    """Open a ticket from the portal form.

    Identical in trust shape to the /mcp path — a human proposes, the trusted
    executor identity is the assignee — so it goes through the same service
    function. It is NOT gated on MCP_ENABLED: that flag governs whether IDEs
    may reach the gateway, which has nothing to do with whether a signed-in
    human may open a ticket in the portal they are already looking at. What it
    does need is the executor ARN, without which the ticket would be approved
    and then never picked up.
    """
    settings = get_settings()
    if not settings.executor_arn:
        raise service.ExecutorNotConfigured(
            "no executor identity is configured (MCP_EXECUTOR_ARN), so a ticket "
            "created here could never be executed"
        )
    # Same shape as the MCP path's key, for the same reason: a double-submitted
    # form (or a retried request) maps to the one ticket instead of opening a
    # duplicate that a human then has to close.
    idempotency_key = (
        f"portal:{user.email}:{payload.action_details.operation}:"
        f"{parameters_hash(payload.action_details.parameters)}"
    )
    ticket, created = await service.create_human_ticket(
        repo, payload, user.email, settings.executor_arn, idempotency_key
    )
    if created:
        notify_ticket_created(ticket)
    return ticket


@router.get("/{ticket_id}", response_model=TicketDetailResponse, response_model_by_alias=True)
async def get_ticket(ticket_id: str, _: User, repo: Repo):
    ticket = await repo.get_ticket(ticket_id)
    if ticket is None:
        raise service.TicketNotFound(f"ticket {ticket_id} not found")
    lineage = await repo.query_lineage(ticket.lineage_root_id)
    events = await repo.list_audit_events(ticket_id)
    return TicketDetailResponse(ticket=ticket, lineage=lineage, audit_events=events)


@router.post("/{ticket_id}/approve", response_model=Ticket, response_model_by_alias=True)
async def approve(ticket_id: str, user: Approver, repo: Repo):
    settings = get_settings()
    return await service.approve_ticket(
        repo,
        ticket_id,
        user.email,
        settings.required_approvals,
        allow_self_approval=settings.allow_self_approval,
    )


@router.post("/{ticket_id}/reject", response_model=Ticket, response_model_by_alias=True)
async def reject(ticket_id: str, payload: RejectRequest, user: Approver, repo: Repo):
    settings = get_settings()
    return await service.reject_ticket(
        repo,
        ticket_id,
        user.email,
        payload.reason,
        allow_self_approval=settings.allow_self_approval,
    )


@router.post("/{ticket_id}/supersede", response_model=Ticket, response_model_by_alias=True)
async def supersede(ticket_id: str, payload: TicketCreateRequest, user: User, repo: Repo):
    return await service.supersede_ticket(repo, ticket_id, user.email, payload)


@router.post("/{ticket_id}/tags", response_model=Ticket, response_model_by_alias=True)
async def update_tags(ticket_id: str, payload: TagsUpdateRequest, user: User, repo: Repo):
    return await service.update_tags(repo, ticket_id, user.email, payload.tags)


@router.post("/{ticket_id}/comments", response_model=Ticket, response_model_by_alias=True)
async def add_comment(ticket_id: str, payload: CommentCreateRequest, user: User, repo: Repo):
    return await service.add_comment(repo, ticket_id, user.email, payload.text)


@router.post("/{ticket_id}/close", response_model=Ticket, response_model_by_alias=True)
async def close_ticket(ticket_id: str, payload: CloseTicketRequest, user: User, repo: Repo):
    return await service.close_ticket(repo, ticket_id, user.email, payload.reason)
