"""Account lookups for the create form's pickers (session-cookie auth).

Split from `api/aws_meta.py` on purpose: that module describes the AWS *API*
from local files and needs no credentials, this one reads the *account* and
does. Keeping them apart means "which endpoints require AWS trust" is answerable
by looking at the imports.

Every route returns 200 with `{items, enabled, error}` even when the lookup
failed. The form treats an empty list as "type the id yourself", which is the
behaviour that keeps a missing IAM permission or a blocked egress rule from
taking the whole page down. See app/aws/discovery.py's DEGRADE, NEVER BLOCK.
"""

from __future__ import annotations

from typing import Annotated, Callable

from fastapi import APIRouter, Depends, Query

from app.api.auth import SessionUser, require_session
from app.aws import discovery
from app.settings import get_settings

router = APIRouter(prefix="/api/aws/ec2/discover", tags=["aws"])

User = Annotated[SessionUser, Depends(require_session)]

# Region is a form field, so lookups follow it rather than a fixed setting —
# a subnet in ca-central-1 is not a choice for a launch in eu-west-1.
Region = Annotated[str | None, Query(description="defaults to AWS_DISCOVERY_DEFAULT_REGION")]

_LOOKUPS: dict[str, Callable[[str], discovery.Discovery]] = {
    "vpcs": discovery.list_vpcs,
    "subnets": discovery.list_subnets,
    "security-groups": discovery.list_security_groups,
    "key-pairs": discovery.list_key_pairs,
    "instances": discovery.list_instances,
    "volumes": discovery.list_volumes,
    "images": discovery.list_images,
}


def _region(region: str | None) -> str:
    return region or get_settings().aws_discovery_default_region


@router.get("/{kind}")
async def discover(kind: str, _: User, region: Region = None) -> dict:
    lookup = _LOOKUPS.get(kind)
    if lookup is None:
        return discovery.Discovery(
            enabled=False, error=f"unknown resource kind {kind!r}"
        ).as_dict()
    return lookup(_region(region)).as_dict()


@router.get("/ami-alias/resolve")
async def resolve_ami_alias(alias: str, _: User, region: Region = None) -> dict:
    """`resolve:ssm:/aws/service/...` -> the concrete ami- it currently points at.

    The form calls this so the approver reviews a real image id instead of an
    alias that resolves at execution time — see the function's docstring for
    why that matters to parametersHash.
    """
    return discovery.resolve_ami_alias(_region(region), alias).as_dict()
