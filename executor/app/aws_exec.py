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


# Operation -> how to pull the ids of what it created out of the response.
#
# Deliberately a curated map and NOT a generic "any key ending in Id" scan:
# every response carries ResponseMetadata.RequestId, which such a scan would
# report as a created resource. Unknown operations yield nothing, which is the
# right answer for the mutate-existing ones — they create nothing.
#
# Only the PRIMARY resource per operation. A RunInstances also creates volumes
# and network interfaces, whose ids are in the same response; they are not
# captured here, so do not treat this list as exhaustive. The gateTicketId tag
# is what finds every resource a ticket produced.
_CREATED_RESOURCE_EXTRACTORS: dict[str, Any] = {
    "RunInstances": lambda r: [i["InstanceId"] for i in r.get("Instances", [])],
    "CreateVolume": lambda r: [r["VolumeId"]] if r.get("VolumeId") else [],
    "CreateSecurityGroup": lambda r: [r["GroupId"]] if r.get("GroupId") else [],
    "CreateSnapshot": lambda r: [r["SnapshotId"]] if r.get("SnapshotId") else [],
    "CreateKeyPair": lambda r: [r["KeyPairId"]] if r.get("KeyPairId") else [],
    "ImportKeyPair": lambda r: [r["KeyPairId"]] if r.get("KeyPairId") else [],
    "AllocateAddress": lambda r: [r["AllocationId"]] if r.get("AllocationId") else [],
}


def _created_resources(operation: str, response: dict[str, Any]) -> list[str]:
    """Never raises: a surprise response shape must not fail a call that worked.

    By the time this runs the AWS mutation has already happened. Losing the id
    of a resource that exists is an annoyance; turning a success into a
    reported failure would be a lie in the audit trail.
    """
    extractor = _CREATED_RESOURCE_EXTRACTORS.get(operation)
    if extractor is None:
        return []
    try:
        return [str(value) for value in extractor(response) if value]
    except Exception:  # noqa: BLE001
        log.warning("could not extract created resource ids from %s response", operation)
        return []


def execute(action_details: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Run the approved call.

    Returns (aws_request_ids, created_resource_ids) — the first is the
    CloudTrail join key for the audit trail, the second is what the call
    brought into existence, so an operator can ask the gate "what did my
    ticket create" instead of going hunting in the console.
    """
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
        return ["dry-run-no-request-id"], []

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
    return (
        [request_id] if request_id else [],
        _created_resources(operation, response),
    )
