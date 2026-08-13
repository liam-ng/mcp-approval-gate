"""Read-only EC2 metadata for the portal's create form (session-cookie auth).

This is the human counterpart of the `describe_operation_parameters` MCP tool —
same payload, same source, so the form and the agent are never told different
things about an operation.

NO AWS CREDENTIALS AND NO NETWORK. Everything here reads botocore's bundled
JSON via `core/aws_schema.py`. The endpoints that *do* talk to AWS (to list
real subnets, images and so on) are deliberately a separate module,
`api/aws_discovery.py`, so that the boundary between "describes the API" and
"reads the account" is visible in the import graph rather than being a comment.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import SessionUser, require_session
from app.core.aws_conditional import SUGGESTED_OPERATIONS, describe_operation_full
from app.core.aws_schema import UnknownOperation
from app.core.service import InvalidActionParameters

router = APIRouter(prefix="/api/aws/ec2", tags=["aws"])

User = Annotated[SessionUser, Depends(require_session)]


@router.get("/operations")
async def list_operations(_: User) -> dict[str, list[str]]:
    """The form's picker list. See SUGGESTED_OPERATIONS — not a whitelist."""
    return {"operations": list(SUGGESTED_OPERATIONS)}


@router.get("/operations/{operation}")
async def get_operation(operation: str, _: User) -> dict:
    """Parameter shape for one operation: required, accepted, conditional, gateTags.

    Typed-in operations reach here too, so an unknown name is a normal 422
    rather than a 404 — it is the same class of mistake as a bad parameter, and
    mapping it onto the existing INVALID_ACTION_PARAMETERS code means the form
    renders it with the machinery it already has.
    """
    try:
        return describe_operation_full("ec2", operation)
    except UnknownOperation as exc:
        raise InvalidActionParameters(str(exc)) from exc
