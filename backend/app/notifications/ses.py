"""SES email notifications — fire-and-forget, never blocks the ticket flow."""

from __future__ import annotations

import asyncio
import logging

from app.auth.approval_links import generate_link_token
from app.core.models import Ticket
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def notify_ticket_created(ticket: Ticket) -> None:
    """Schedule one email per approver — not one email to all recipients —
    since each gets its own personalized approve/reject links
    (api/approval_link_actions.py). Failures are logged, not raised."""
    settings = get_settings()
    if not settings.notify_on_create:
        return
    recipients = settings.approver_email_list
    if not recipients:
        logger.warning("NOTIFY_ON_CREATE is set but APPROVER_EMAILS is empty")
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - no loop in some test contexts
        return
    for recipient in recipients:
        loop.create_task(_send(ticket, recipient, settings))


async def _send(ticket: Ticket, recipient: str, settings: Settings) -> None:
    base = settings.public_base_url.rstrip("/")
    portal_url = f"{base}/tickets/{ticket.ticket_id}"
    approve_url = (
        f"{base}/act?token="
        f"{generate_link_token(settings, ticket.ticket_id, recipient, 'approve')}"
    )
    reject_url = (
        f"{base}/act?token="
        f"{generate_link_token(settings, ticket.ticket_id, recipient, 'reject')}"
    )
    subject = f"[Approval Gate] New change request: {ticket.subject}"
    body = (
        f"A new change request is waiting for approval.\n\n"
        f"Subject:        {ticket.subject}\n"
        f"Planned action: {ticket.planned_action}\n"
        f"Operation:      {ticket.action_details.service}:{ticket.action_details.operation} "
        f"({ticket.action_details.region})\n"
        f"Resources:      {', '.join(ticket.action_details.resource_arns) or '(new resources)'}\n"
        f"Proposed by:    {ticket.proposed_by}\n"
        f"Planned date:   {ticket.planned_date.isoformat()}\n\n"
        f"Approve: {approve_url}\n"
        f"Reject:  {reject_url}\n"
        f"(Both links open a confirmation page first — nothing happens until "
        f"you click through — and expire in {settings.approval_ttl_hours}h "
        f"along with the ticket itself.)\n\n"
        f"Full details in the portal: {portal_url}\n"
    )
    try:
        import boto3

        client = await asyncio.to_thread(boto3.client, "sesv2", region_name=settings.ses_region)
        await asyncio.to_thread(
            client.send_email,
            FromEmailAddress=settings.ses_from_address,
            Destination={"ToAddresses": [recipient]},
            Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
        )
        logger.info("notified %s for ticket %s", recipient, ticket.ticket_id)
    except Exception:
        logger.exception("failed to send SES notification for ticket %s", ticket.ticket_id)
