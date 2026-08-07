"""Performs the one approved AWS call.

Deliberately boto3 and not a `call_aws` round-trip through the sibling
aws-api-mcp-server container. That server's tool surface takes an AWS *CLI
command string*, so driving it would mean rendering the approved SDK parameter
dict back into CLI flags (`{"InstanceIds": ["i-0abc"]}` -> `--instance-ids
i-0abc`) and hoping the round-trip is lossless. The whole point of
parametersHash is that what executes is byte-identical to what was approved,
and docs/agent-contract.md states `parameters` already *is* the exact SDK
shape — so passing it straight to botocore preserves the property that a
translation layer would quietly weaken.

The sibling container still matters: it is what an IDE agent explores AWS
with, and the SCP + NetworkPolicy exist so it cannot be run standalone to
bypass the gate. It is simply not in the execution path.
"""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore import xform_name
from botocore.exceptions import BotoCoreError, ClientError

from .settings import settings

log = logging.getLogger(__name__)


class ExecutionFailed(RuntimeError):
    """The AWS call itself failed. Reported to the gate as outcome=failure."""


def execute(action_details: dict[str, Any]) -> list[str]:
    """Run the approved call. Returns the AWS request ids for the audit trail."""
    service = action_details["service"]
    operation = action_details["operation"]
    region = action_details["region"]
    parameters = action_details.get("parameters") or {}

    # botocore's own PascalCase -> snake_case transform; do not hand-roll it,
    # it has real special cases (e.g. GetIPSetOperation -> get_ip_set).
    method_name = xform_name(operation)

    if settings.dry_run:
        log.warning(
            "DRY_RUN: would call %s.%s in %s with %s",
            service, method_name, region, parameters,
        )
        return ["dry-run-no-request-id"]

    client = boto3.client(service, region_name=region)
    method = getattr(client, method_name, None)
    if method is None:
        raise ExecutionFailed(
            f"{service} has no operation {operation} (tried {method_name})"
        )

    try:
        response = method(**parameters)
    except ClientError as exc:
        # A ClientError still carries a RequestId, and it is worth recording:
        # it is the join key to CloudTrail for the failed attempt too.
        request_id = exc.response.get("ResponseMetadata", {}).get("RequestId")
        raise ExecutionFailed(
            f"{exc.response.get('Error', {}).get('Code', 'ClientError')}: {exc}"
            + (f" (RequestId {request_id})" if request_id else "")
        ) from exc
    except BotoCoreError as exc:
        raise ExecutionFailed(str(exc)) from exc

    request_id = response.get("ResponseMetadata", {}).get("RequestId")
    return [request_id] if request_id else []
