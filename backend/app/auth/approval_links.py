"""Signed, single-purpose approve/reject links mailed by
notifications/ses.py, so an approver can act straight from the notification
email without a portal session.

This is a fourth, narrow identity proof alongside the three in CLAUDE.md's
"do not couple them" list (human session, agent SigV4, MCP bearer token):
possession of a link addressed to one specific approver for one specific
ticket. It never grows into a general credential — verify_link_token() only
ever feeds core/service.py's existing approve_ticket/reject_ticket, so every
invariant those enforce (approver != proposer, no duplicate approvals,
ticket must still be PENDING_APPROVAL) applies exactly as it would for a
session-authenticated approver clicking the same buttons in the portal.

Tokens are stateless (no server-side link table) — expiry is enforced by
itsdangerous' embedded timestamp, and re-use is naturally blocked by
service.py's own status/duplicate-approver guards, not by tracking token
IDs. That means a captured-but-unused approve link stays valid for its full
TTL; there's no revocation short of rotating SESSION_SECRET.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.settings import Settings

Action = Literal["approve", "reject"]

# Distinct salt keeps these tokens cryptographically separate from the
# session cookie (also signed with SESSION_SECRET, via starlette's
# SessionMiddleware in api/auth.py) even though both derive from the same
# secret -- one can never be replayed as the other.
_SALT = "mcp-approval-gate.approval-link.v1"


class InvalidApprovalLink(Exception):
    """Token is malformed, tampered with, or older than APPROVAL_TTL_HOURS."""


@dataclass(frozen=True)
class LinkPayload:
    ticket_id: str
    email: str
    action: Action


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=_SALT)


def generate_link_token(settings: Settings, ticket_id: str, email: str, action: Action) -> str:
    return _serializer(settings).dumps({"tid": ticket_id, "email": email, "action": action})


def verify_link_token(settings: Settings, token: str) -> LinkPayload:
    # Links are only ever useful while the ticket itself could still be
    # approved, so reuse the same TTL rather than adding a separate setting.
    max_age = settings.approval_ttl_hours * 3600
    try:
        data = _serializer(settings).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired) as exc:
        raise InvalidApprovalLink("this link is invalid or has expired") from exc
    try:
        return LinkPayload(ticket_id=data["tid"], email=data["email"], action=data["action"])
    except (KeyError, TypeError) as exc:
        raise InvalidApprovalLink("this link is invalid or has expired") from exc
