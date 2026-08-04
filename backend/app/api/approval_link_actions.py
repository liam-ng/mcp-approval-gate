"""Unauthenticated (no session, no SigV4) approve/reject via the signed link
mailed by notifications/ses.py. A fourth, narrow identity proof alongside
the human/agent/MCP paths (see auth/approval_links.py's docstring) — kept in
its own router/module for the same reason agent_tickets.py is separate from
tickets.py: a distinct auth mechanism never gets folded into a router built
around a different one.

GET returns a preview only — it never mutates anything, so it's safe against
email-client link prefetching/scanners (Outlook Safe Links and similar).
Only POST — which the landing page fires from an explicit user click —
calls into core/service.py's approve_ticket/reject_ticket, so every
invariant those enforce applies exactly as it would for a session approver.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_repo
from app.auth.approval_links import InvalidApprovalLink, verify_link_token
from app.core import service
from app.core.schemas import ApprovalLinkActionRequest, ApprovalLinkPreview
from app.repo.base import TicketRepository
from app.settings import get_settings

router = APIRouter(prefix="/api/tickets/by-link", tags=["approval-links"])

Repo = Annotated[TicketRepository, Depends(get_repo)]


class InvalidLink(service.ServiceError):
    http_status = 400
    code = "INVALID_LINK"


class MissingReason(service.ServiceError):
    http_status = 422
    code = "MISSING_REASON"


@router.get("/{token}", response_model=ApprovalLinkPreview, response_model_by_alias=True)
async def preview(token: str, repo: Repo):
    settings = get_settings()
    try:
        link = verify_link_token(settings, token)
    except InvalidApprovalLink as exc:
        raise InvalidLink(str(exc)) from exc

    ticket = await repo.get_ticket(link.ticket_id)
    if ticket is None:
        raise service.TicketNotFound(f"ticket {link.ticket_id} not found")

    actionable = ticket.status == "PENDING_APPROVAL" and not ticket.superseded_by
    return ApprovalLinkPreview(
        ticket_id=ticket.ticket_id,
        subject=ticket.subject,
        status=ticket.status,
        planned_date=ticket.planned_date,
        planned_action=ticket.planned_action,
        action_details=ticket.action_details,
        proposed_by=ticket.proposed_by,
        action=link.action,
        actionable=actionable,
    )


@router.post("/{token}", response_model=ApprovalLinkPreview, response_model_by_alias=True)
async def act(token: str, payload: ApprovalLinkActionRequest, repo: Repo):
    settings = get_settings()
    try:
        link = verify_link_token(settings, token)
    except InvalidApprovalLink as exc:
        raise InvalidLink(str(exc)) from exc
    # Defense in depth: the token proves this address was mailed a link
    # (i.e. was an approver when the ticket was created), but APPROVER_EMAILS
    # could have been edited since. Recheck against the live allowlist
    # rather than trusting the token's snapshot indefinitely.
    if link.email.lower() not in settings.approver_email_list:
        raise InvalidLink("this address is no longer an approver")

    if link.action == "approve":
        ticket = await service.approve_ticket(
            repo, link.ticket_id, link.email, settings.required_approvals
        )
    else:
        reason = (payload.reason or "").strip()
        if len(reason) < 5:
            raise MissingReason("a rejection reason of at least 5 characters is required")
        ticket = await service.reject_ticket(repo, link.ticket_id, link.email, reason)

    return ApprovalLinkPreview(
        ticket_id=ticket.ticket_id,
        subject=ticket.subject,
        status=ticket.status,
        planned_date=ticket.planned_date,
        planned_action=ticket.planned_action,
        action_details=ticket.action_details,
        proposed_by=ticket.proposed_by,
        action=link.action,
        actionable=False,
    )
