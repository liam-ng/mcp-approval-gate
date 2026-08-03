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
from app.core.models import Ticket, TicketStatus
from app.core.schemas import (
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
    return await service.approve_ticket(
        repo, ticket_id, user.email, get_settings().required_approvals
    )


@router.post("/{ticket_id}/reject", response_model=Ticket, response_model_by_alias=True)
async def reject(ticket_id: str, payload: RejectRequest, user: Approver, repo: Repo):
    return await service.reject_ticket(repo, ticket_id, user.email, payload.reason)


@router.post("/{ticket_id}/supersede", response_model=Ticket, response_model_by_alias=True)
async def supersede(ticket_id: str, payload: TicketCreateRequest, user: User, repo: Repo):
    return await service.supersede_ticket(repo, ticket_id, user.email, payload)


@router.post("/{ticket_id}/tags", response_model=Ticket, response_model_by_alias=True)
async def update_tags(ticket_id: str, payload: TagsUpdateRequest, user: User, repo: Repo):
    return await service.update_tags(repo, ticket_id, user.email, payload.tags)


@router.post("/{ticket_id}/comments", response_model=Ticket, response_model_by_alias=True)
async def add_comment(ticket_id: str, payload: CommentCreateRequest, user: User, repo: Repo):
    return await service.add_comment(repo, ticket_id, user.email, payload.text)
