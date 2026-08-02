"""Role resolution: OIDC groups claim first, email allowlist as fallback.

The fallback exists because IAM Identity Center's customer-managed OIDC apps
have limited group-claim support; APPROVER_EMAILS keeps the approver role
workable there. When migrating to Entra ID, set OIDC_GROUPS_CLAIM/
OIDC_APPROVER_GROUPS and clear the allowlist — no code change.
"""

from __future__ import annotations

from typing import Literal

from app.settings import Settings

Role = Literal["approver", "viewer"]


def resolve_role(email: str, groups: list[str], settings: Settings) -> Role:
    approver_groups = set(settings.approver_group_list)
    if approver_groups and approver_groups.intersection(groups):
        return "approver"
    if email.lower() in settings.approver_email_list:
        return "approver"
    return "viewer"
