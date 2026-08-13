"""MCP Streamable HTTP endpoint for IDE clients (Cursor, VS Code).

This is the ONLY MCP endpoint end users should ever point their IDE at — see
docs/mcp-gateway.md for the full rationale and IDE setup steps. It never
talks to AWS itself: `create_change_ticket` opens a ticket exactly like the
existing agent contract (docs/agent-contract.md) and hands execution off to
the same trusted, SigV4-authenticated executor identity (MCP_EXECUTOR_ARN)
that already polls /api/agent/tickets. The real upstream AWS MCP server
(aws-api-mcp-server) that identity talks to is network-isolated from
everyone else — including this gate's own other routes — by the Istio
AuthorizationPolicy in deploy/k8s/istio-authorizationpolicy.yaml.

Auth: OAuth 2.1 Resource Server (RFC 9728 protected-resource metadata is
published automatically by the SDK at
/.well-known/oauth-protected-resource/mcp). Cursor/VS Code run the full
Authorization Code + PKCE flow directly against the OIDC IdP — the gate is
never in that browser-redirect or token-exchange path — and present the
resulting bearer token on every call; OidcTokenVerifier below only checks
its signature and claims against the IdP's JWKS.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette

from app.api.deps import get_repo
from app.auth.mcp_token_verifier import OidcTokenVerifier
from app.core import service
from app.core.aws_conditional import describe_operation_full
from app.core.aws_schema import UnknownOperation
from app.core.canonical_json import parameters_hash
from app.core.schemas import ActionDetailsIn, TicketCreateRequest
from app.settings import Settings


def build_mcp_app(settings: Settings) -> Starlette:
    server = MCPServer(
        name="mcp-approval-gate",
        version="0.1.0",
        instructions=(
            "Propose AWS EC2 changes for human approval. create_change_ticket never "
            "executes anything itself: it opens a ticket that a human approver (never "
            "you) must approve in the web portal before the trusted executor runs it. "
            "Always surface ticketUrl to the user. Use check_ticket_status to follow up. "
            "`parameters` must be the exact boto3/SDK parameter dict for the operation — "
            "if you are not certain of the names, types or required set, call "
            "describe_operation_parameters FIRST rather than guessing. A ticket whose "
            "parameters AWS rejects wastes a human's approval. Include whatever identifies "
            "what the call acts on (e.g. RunInstances needs an ImageId unless you pass a "
            "LaunchTemplate, even though the model does not list it as required) — and ask "
            "the operator for such values, never invent them. "
            "If a ticket needs different parameters, or a failed one needs retrying with "
            "a fix, use supersede_change_ticket rather than opening a duplicate — it links "
            "the new ticket to the old one and requires fresh approval. Use close_ticket to withdraw a ticket "
            "that's no longer needed instead of leaving it pending forever."
        ),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.mcp_issuer),  # type: ignore[arg-type]
            resource_server_url=AnyHttpUrl(settings.mcp_resource_server_url),
            required_scopes=settings.mcp_required_scope_list or None,
        ),
        token_verifier=OidcTokenVerifier(settings),
    )

    @server.tool(structured_output=True)
    async def create_change_ticket(
        subject: str,
        planned_date: str,
        planned_action: str,
        operation: str,
        region: str,
        parameters: dict[str, Any],
        resource_arns: list[str] | None = None,
        reason: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Open a change-request ticket for a mutating EC2 action (service is
        always ec2 in v1). Returns immediately with status PENDING_APPROVAL —
        this does NOT wait for a human. Surface ticketUrl to the user so they
        (or a peer/manager — never the proposer) can approve it in the portal,
        then call check_ticket_status to follow up before assuming anything
        executed. Read-only Describe*/List*/Get* calls do not need a ticket."""
        access_token = get_access_token()
        if access_token is None or not access_token.subject:
            raise ValueError("no authenticated user on this request")

        payload = TicketCreateRequest(
            subject=subject,
            planned_date=planned_date,  # type: ignore[arg-type]
            planned_action=planned_action,
            action_details=ActionDetailsIn(
                service="ec2",
                operation=operation,
                region=region,
                parameters=parameters,
                resource_arns=resource_arns or [],
                reason=reason,
            ),
            tags=tags or {},
        )
        # Same call with the same params from the same user maps to the same
        # ticket rather than opening a duplicate on every retry/re-ask.
        idempotency_key = f"mcp:{access_token.subject}:{operation}:{parameters_hash(parameters)}"

        repo = get_repo()
        ticket, created = await service.create_mcp_ticket(
            repo, payload, access_token.subject, settings.mcp_executor_arn, idempotency_key  # type: ignore[arg-type]
        )
        if created:
            from app.notifications.ses import notify_ticket_created

            notify_ticket_created(ticket)

        return {
            "ticketId": ticket.ticket_id,
            "status": ticket.status,
            "ticketUrl": f"{settings.public_base_url.rstrip('/')}/tickets/{ticket.ticket_id}",
            "created": created,
        }

    @server.tool(structured_output=True)
    async def describe_operation_parameters(operation: str) -> dict[str, Any]:
        """List the parameters an EC2 operation takes, before proposing it.

        Call this when you are not certain of the exact parameter names, types
        or required set for `operation` — it is cheaper than having a ticket
        rejected, and far cheaper than having a human approve a call that AWS
        then refuses. Returns `required` (names AWS's model marks mandatory)
        and `accepted` (every valid name mapped to its type).

        IMPORTANT — `required` is necessary but not always sufficient. AWS has
        conditional requirements botocore's model cannot express, so they come
        back separately in `conditional`: a list of `{oneOf, because}` entries,
        each meaning "at least one of these names must be supplied". For
        RunInstances that is `ImageId` or `LaunchTemplate`. The gate enforces
        both lists at creation, so a ticket missing either is rejected before
        any human sees it. Treat `required` + `conditional` as a floor, not a
        complete checklist, and think about what the specific call needs to
        identify what it acts on.

        `gateTags` lists the resource types the gate will tag with the ticket
        id on your behalf — it injects TagSpecifications itself, so do not
        construct them for those types.

        Returns no example values by design. Never invent an ImageId, SubnetId
        or similar — ask the operator which one they mean.
        """
        try:
            return describe_operation_full("ec2", operation)
        except UnknownOperation as exc:
            raise ValueError(str(exc)) from exc

    @server.tool(structured_output=True)
    async def check_ticket_status(ticket_id: str) -> dict[str, Any]:
        """Check the status of a ticket previously opened with
        create_change_ticket. Only APPROVED tickets will eventually execute."""
        access_token = get_access_token()
        repo = get_repo()
        ticket = await repo.get_ticket(ticket_id)
        if ticket is None:
            raise ValueError(f"no such ticket: {ticket_id}")
        if (
            access_token
            and access_token.subject
            and ticket.proposed_by.lower() != access_token.subject.lower()
        ):
            raise ValueError("you may only check tickets you proposed")
        return {
            "ticketId": ticket.ticket_id,
            "status": ticket.status,
            "approvedBy": [a.approved_by for a in ticket.approvals],
            "rejectionReason": ticket.rejection_reason,
            "supersededBy": ticket.superseded_by,
        }

    @server.tool(structured_output=True)
    async def supersede_change_ticket(
        ticket_id: str,
        subject: str,
        planned_date: str,
        planned_action: str,
        operation: str,
        region: str,
        parameters: dict[str, Any],
        resource_arns: list[str] | None = None,
        reason: str | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Edit a previously-created ticket, or follow up on one that already
        ran. Tickets are immutable — this creates a new PENDING_APPROVAL ticket
        linked to `ticket_id`, requiring fresh approval.

        Two cases, both valid:
        - `ticket_id` is PENDING_APPROVAL or APPROVED (it never ran): it is
          marked DEPRECATED and replaced.
        - `ticket_id` is FAILED or CLOSED (it ran): use this to retry a failed
          change with a fix, or to make a further change to something that
          succeeded. The old ticket KEEPS its status — a follow-up never
          rewrites the record of what already happened to AWS.

        REJECTED and EXPIRED tickets cannot be superseded; open a fresh ticket
        with create_change_ticket instead.

        You (the authenticated user) become the new proposer, so you cannot also
        approve this superseding ticket. Supply the FULL new details, not just
        what changed — this replaces the ticket's action entirely, it does not
        patch individual fields."""
        access_token = get_access_token()
        if access_token is None or not access_token.subject:
            raise ValueError("no authenticated user on this request")

        payload = TicketCreateRequest(
            subject=subject,
            planned_date=planned_date,  # type: ignore[arg-type]
            planned_action=planned_action,
            action_details=ActionDetailsIn(
                service="ec2",
                operation=operation,
                region=region,
                parameters=parameters,
                resource_arns=resource_arns or [],
                reason=reason,
            ),
            tags=tags or {},
        )
        repo = get_repo()
        try:
            new_ticket = await service.supersede_ticket(repo, ticket_id, access_token.subject, payload)
        except service.ServiceError as e:
            raise ValueError(str(e)) from e

        return {
            "ticketId": new_ticket.ticket_id,
            "status": new_ticket.status,
            "ticketUrl": f"{settings.public_base_url.rstrip('/')}/tickets/{new_ticket.ticket_id}",
            "supersedes": ticket_id,
        }

    @server.tool(structured_output=True)
    async def close_ticket(ticket_id: str, reason: str | None = None) -> dict[str, Any]:
        """Withdraw a ticket that's no longer needed, without executing it —
        an alternative to leaving it pending or asking someone to reject it.
        Only PENDING_APPROVAL/APPROVED tickets can be closed this way; once
        an agent has started executing, it must run to completion."""
        access_token = get_access_token()
        if access_token is None or not access_token.subject:
            raise ValueError("no authenticated user on this request")

        repo = get_repo()
        try:
            ticket = await service.close_ticket(repo, ticket_id, access_token.subject, reason)
        except service.ServiceError as e:
            raise ValueError(str(e)) from e

        return {"ticketId": ticket.ticket_id, "status": ticket.status}

    return server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        # DNS-rebinding protection targets browser clients hitting a
        # localhost dev server; irrelevant behind our Ingress (TLS + a real
        # hostname) and redundant with the Istio mTLS + OAuth bearer layers
        # already gating this route.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
