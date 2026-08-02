"""TTL expiry sweep: stale PENDING_APPROVAL / APPROVED tickets -> EXPIRED.

Prevents an approval granted weeks ago from being executable forever. The
clock starts at ticket creation for pending tickets and at the last approval
for approved ones.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from ulid import ULID

from app.core.models import Actor, AuditEvent, Ticket
from app.repo.base import ConflictError, TicketRepository

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 600


def _expiry_start(ticket: Ticket) -> datetime:
    if ticket.status == "APPROVED" and ticket.approvals:
        return max(a.approved_at for a in ticket.approvals)
    return ticket.ticket_date


async def sweep_once(repo: TicketRepository, ttl_hours: int, now: datetime | None = None) -> int:
    """Expire stale tickets; returns how many were expired."""
    now = now or datetime.now(UTC)
    cutoff = timedelta(hours=ttl_hours)
    expired = 0
    for status in ("PENDING_APPROVAL", "APPROVED"):
        cursor: str | None = None
        while True:
            page = await repo.query_by_status(status, limit=100, cursor=cursor)  # type: ignore[arg-type]
            for ticket in page.items:
                if now - _expiry_start(ticket) < cutoff:
                    continue
                event = AuditEvent(
                    event_id=str(ULID()),
                    ticket_id=ticket.ticket_id,
                    seq=ticket.seq + 1,
                    timestamp=now,
                    type="EXPIRED",
                    actor=Actor(kind="system", id="gate"),
                    from_status=ticket.status,
                    to_status="EXPIRED",
                    details={"ttlHours": ttl_hours},
                )
                try:
                    await repo.append_event(ticket.ticket_id, ticket.seq, event)
                    expired += 1
                    logger.info("expired ticket %s (was %s)", ticket.ticket_id, ticket.status)
                except ConflictError:
                    pass  # ticket moved concurrently — next sweep re-evaluates
            if not page.cursor:
                break
            cursor = page.cursor
    return expired


async def run_expiry_loop(repo: TicketRepository, ttl_hours: int) -> None:
    while True:
        try:
            await sweep_once(repo, ttl_hours)
        except Exception:  # never let the loop die
            logger.exception("expiry sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
