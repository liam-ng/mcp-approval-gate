"""Agent-facing API: SigV4-authenticated, entirely separate from human auth.

Contract (docs/agent-contract.md): create -> poll -> execution/start (hash
echo) -> execute the returned actionDetails -> execution/result.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from app.auth.agent_auth import AgentIdentity, verify_agent
from app.core import service
from app.core.models import Ticket
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
        repo, payload, agent.caller_arn, idempotency_key
    )
    if not created:
        response.status_code = 200  # replayed create returns the existing ticket
    else:
        notify_ticket_created(ticket)
    return ticket


@router.get("/{ticket_id}", response_model=AgentPollResponse, response_model_by_alias=True)
async def poll_ticket(ticket_id: str, agent: Agent, repo: Repo):
    ticket = await service.get_agent_ticket(repo, ticket_id, agent.caller_arn)
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
        repo, ticket_id, agent.caller_arn, payload.parameters_hash
    )
    return ExecutionStartResponse(
        ticket_id=ticket.ticket_id, status=ticket.status, action_details=ticket.action_details
    )


@router.post("/{ticket_id}/execution/result", response_model=Ticket, response_model_by_alias=True)
async def report_result(
    ticket_id: str, payload: ExecutionResultRequest, agent: Agent, repo: Repo
):
    return await service.report_execution_result(repo, ticket_id, agent.caller_arn, payload)
