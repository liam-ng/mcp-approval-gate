"""Agent-facing API: SigV4-authenticated, entirely separate from human auth.

Contract (docs/agent-contract.md): create -> poll -> execution/start (hash
echo) -> execute the returned actionDetails -> execution/result.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from app.auth.agent_auth import AgentIdentity, verify_agent
from app.core import service
from app.core.models import Ticket, TicketStatus
from app.core.schemas import (
    AgentPollResponse,
    ExecutionResultRequest,
    ExecutionStartRequest,
    ExecutionStartResponse,
    TicketCreateRequest,
)
from app.api.deps import get_repo
from app.notifications.ses import notify_ticket_created
from app.repo.base import TicketRepository

router = APIRouter(prefix="/api/agent/tickets", tags=["agent"])

Agent = Annotated[AgentIdentity, Depends(verify_agent)]
Repo = Annotated[TicketRepository, Depends(get_repo)]


@router.post("", response_model=Ticket, response_model_by_alias=True, status_code=201)
async def create_ticket(
    payload: TicketCreateRequest,
    agent: Agent,
    repo: Repo,
    response: Response,
    idempotency_key: str | None = Header(default=None),
):
    ticket, created = await service.create_agent_ticket(
        repo, payload, agent.principal_arn, idempotency_key, actor_arn=agent.caller_arn
    )
    if not created:
        response.status_code = 200  # replayed create returns the existing ticket
    else:
        notify_ticket_created(ticket)
    return ticket


@router.get("", response_model=list[AgentPollResponse], response_model_by_alias=True)
async def list_my_tickets(agent: Agent, repo: Repo, status: TicketStatus | None = None, limit: int = 50):
    """Discover tickets assigned to the caller that it did not itself create —
    e.g. ones a human proposed via /mcp (api/mcp_gateway.py). The executor
    polls this (typically `?status=APPROVED`) instead of tracking ticket ids
    it never received."""
    page = await repo.query_by_status(status, limit=limit) if status else await repo.query_all(limit=limit)
    # principal_arn, NOT caller_arn -- an MCP-proposed ticket carries MCP_EXECUTOR_ARN in role form,
    # which the session-suffixed caller_arn can never equal. That mismatch made this endpoint, whose
    # whole purpose is the /mcp case above, return [] for every such ticket until it expired.
    mine = [t for t in page.items if t.assignee == agent.principal_arn]
    return [
        AgentPollResponse(
            ticket_id=t.ticket_id,
            status=t.status,
            approved_by=[a.approved_by for a in t.approvals],
            rejection_reason=t.rejection_reason,
            superseded_by=t.superseded_by,
            action_details=t.action_details,
        )
        for t in mine
    ]


@router.get("/{ticket_id}", response_model=AgentPollResponse, response_model_by_alias=True)
async def poll_ticket(ticket_id: str, agent: Agent, repo: Repo):
    ticket = await service.get_agent_ticket(repo, ticket_id, agent.principal_arn)
    return AgentPollResponse(
        ticket_id=ticket.ticket_id,
        status=ticket.status,
        approved_by=[a.approved_by for a in ticket.approvals],
        rejection_reason=ticket.rejection_reason,
        superseded_by=ticket.superseded_by,
        action_details=ticket.action_details,
    )


@router.post(
    "/{ticket_id}/execution/start",
    response_model=ExecutionStartResponse,
    response_model_by_alias=True,
)
async def start_execution(
    ticket_id: str, payload: ExecutionStartRequest, agent: Agent, repo: Repo
):
    ticket = await service.start_execution(
        repo, ticket_id, agent.principal_arn, payload.parameters_hash, actor_arn=agent.caller_arn
    )
    return ExecutionStartResponse(
        ticket_id=ticket.ticket_id, status=ticket.status, action_details=ticket.action_details
    )


@router.post("/{ticket_id}/execution/result", response_model=Ticket, response_model_by_alias=True)
async def report_result(
    ticket_id: str, payload: ExecutionResultRequest, agent: Agent, repo: Repo
):
    return await service.report_execution_result(
        repo, ticket_id, agent.principal_arn, payload, actor_arn=agent.caller_arn
    )
