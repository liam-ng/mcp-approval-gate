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


def _entry(kind: str, resource_id: str, arn: str | None = None) -> dict[str, Any]:
    return {"type": kind, "id": resource_id, "arn": arn}


def _ec2_arn(region: str, account: str | None, kind: str, path_id: str) -> str | None:
    """An ARN only when the account came from AWS. None is a valid answer.

    NEVER construct an ARN from an assumed account. A null tells a reader "we
    did not learn this"; a plausible-looking wrong ARN in an audit trail is
    worse than nothing, because the whole purpose of the record is that someone
    can follow it back to a real resource.
    """
    if not account:
        return None
    return f"arn:aws:ec2:{region}:{account}:{kind}/{path_id}"


# Operation -> what it created, as {type, id, arn} entries.
#
# Deliberately a curated map and NOT a generic "any key ending in Id" scan:
# every response carries ResponseMetadata.RequestId, which such a scan would
# report as a created resource.
#
# ARNs are built ONLY from data the response itself carried. AWS hands back the
# account in different ways per operation and sometimes not at all:
#   * RunInstances / CreateSnapshot return OwnerId, so the ARN is derivable.
#   * CreateSecurityGroup returns SecurityGroupArn outright — prefer it over
#     assembling one.
#   * CreateVolume, CreateKeyPair, ImportKeyPair and AllocateAddress return
#     neither, so those entries carry arn=None. (OutpostArn is the Outpost, not
#     the resource — not a substitute.)
# The executor could learn its account from sts:GetCallerIdentity, but that
# would make it *assert* the resource's owner rather than report what AWS said,
# and cross-account creation would then be silently mislabelled.
#
# Key pairs are the specific trap: a key pair's ARN path is its KeyName, not
# the KeyPairId this reports as `id`, so `key-pair/{id}` would be a wrong ARN
# that looks right.
#
# Only the PRIMARY resource per operation. A RunInstances also creates volumes
# and network interfaces, whose ids are in the same response; they are not
# captured here, so this is not exhaustive — the gateTicketId tag is what finds
# everything a ticket produced.
_CREATED_RESOURCE_EXTRACTORS: dict[str, Any] = {
    "RunInstances": lambda r, region: [
        _entry("instance", i["InstanceId"],
               _ec2_arn(region, r.get("OwnerId"), "instance", i["InstanceId"]))
        for i in r.get("Instances", [])
        if i.get("InstanceId")
    ],
    "CreateSecurityGroup": lambda r, region: (
        [_entry("security-group", r["GroupId"], r.get("SecurityGroupArn"))]
        if r.get("GroupId") else []
    ),
    "CreateSnapshot": lambda r, region: (
        [_entry("snapshot", r["SnapshotId"],
                _ec2_arn(region, r.get("OwnerId"), "snapshot", r["SnapshotId"]))]
        if r.get("SnapshotId") else []
    ),
    "CreateVolume": lambda r, region: (
        [_entry("volume", r["VolumeId"])] if r.get("VolumeId") else []
    ),
    "CreateKeyPair": lambda r, region: (
        [_entry("key-pair", r["KeyPairId"])] if r.get("KeyPairId") else []
    ),
    "ImportKeyPair": lambda r, region: (
        [_entry("key-pair", r["KeyPairId"])] if r.get("KeyPairId") else []
    ),
    "AllocateAddress": lambda r, region: (
        [_entry("elastic-ip", r["AllocationId"])] if r.get("AllocationId") else []
    ),
}


def _created_resources(
    operation: str, region: str, response: dict[str, Any]
) -> list[dict[str, Any]]:
    """Never raises: a surprise response shape must not fail a call that worked.

    By the time this runs the AWS mutation has already happened. Losing the id
    of a resource that exists is an annoyance; turning a success into a
    reported failure would be a lie in the audit trail.
    """
    extractor = _CREATED_RESOURCE_EXTRACTORS.get(operation)
    if extractor is None:  # every mutate-existing operation lands here
        return []
    try:
        return extractor(response, region)
    except Exception:  # noqa: BLE001
        log.warning("could not extract created resources from %s response", operation)
        return []


def execute(action_details: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Run the approved call.

    Returns (aws_request_ids, created_resources) — the first is the CloudTrail
    join key for the audit trail, the second is what the call brought into
    existence as {type, id, arn} entries, so an operator can ask the gate "what
    did my ticket create" instead of going hunting in the console. `arn` is
    None wherever AWS's response did not tell us the account.
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
        _created_resources(operation, region, response),
    )
