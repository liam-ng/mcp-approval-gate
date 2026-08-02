"""Ticket status machine — the single source of truth for allowed transitions.

Structural rules (which status can move to which, and by which actor kind)
live here. Identity rules that need request context (approver != proposer,
caller ARN == assignee, ...) live in core/service.py, which always calls
assert_transition first.
"""

from __future__ import annotations

from app.core.models import TicketStatus

ActorKind = str  # "agent" | "human" | "system"

# (from, to) -> actor kinds allowed to make that transition.
# PENDING_APPROVAL -> PENDING_APPROVAL is an APPROVAL_ADDED event that has not
# yet reached the required approval count.
_ALLOWED: dict[tuple[TicketStatus, TicketStatus], frozenset[str]] = {
    ("PENDING_APPROVAL", "PENDING_APPROVAL"): frozenset({"human"}),
    ("PENDING_APPROVAL", "APPROVED"): frozenset({"human"}),
    ("PENDING_APPROVAL", "REJECTED"): frozenset({"human"}),
    ("PENDING_APPROVAL", "DEPRECATED"): frozenset({"human"}),
    ("PENDING_APPROVAL", "EXPIRED"): frozenset({"system"}),
    ("APPROVED", "DEPRECATED"): frozenset({"human"}),
    ("APPROVED", "EXPIRED"): frozenset({"system"}),
    ("APPROVED", "EXECUTING"): frozenset({"agent"}),
    ("EXECUTING", "COMPLETED"): frozenset({"agent"}),
    ("EXECUTING", "FAILED"): frozenset({"agent"}),
}


class TransitionError(Exception):
    def __init__(self, from_status: str, to_status: str, actor_kind: str):
        self.from_status = from_status
        self.to_status = to_status
        self.actor_kind = actor_kind
        super().__init__(
            f"transition {from_status} -> {to_status} not allowed for actor kind '{actor_kind}'"
        )


def can_transition(from_status: TicketStatus, to_status: TicketStatus, actor_kind: ActorKind) -> bool:
    return actor_kind in _ALLOWED.get((from_status, to_status), frozenset())


def assert_transition(from_status: TicketStatus, to_status: TicketStatus, actor_kind: ActorKind) -> None:
    if not can_transition(from_status, to_status, actor_kind):
        raise TransitionError(from_status, to_status, actor_kind)


def status_after_approval(approval_count: int, required_approvals: int) -> TicketStatus:
    """Status of a PENDING_APPROVAL ticket after `approval_count` approvals."""
    return "APPROVED" if approval_count >= required_approvals else "PENDING_APPROVAL"
