"""Conditional requirements botocore's models cannot express.

`aws_schema.py` is deliberately model-only: it asks botocore whether a call is
structurally valid and nothing more. That leaves a real gap, documented as its
KNOWN LIMIT — AWS has requirements of the form "X is required *unless* you pass
Y", and the models represent `required` as a flat list, so they cannot say it.
RunInstances is the case that cost a real approval (ticket
01KZY48KXZRFT2VKKR6006MK3N): the model marks only MinCount/MaxCount required,
so a launch with no ImageId validated cleanly and failed at AWS.

This module is the hand-curated second layer. It stays separate from
`aws_schema` on purpose — that module derives everything from botocore and must
keep doing so, while everything here is a human assertion about AWS behaviour
that no local file states.

THE GOVERNING TRADEOFF, and the reason this table is short. A false *accept*
costs one wasted approval and produces a clear error from AWS. A false *reject*
blocks a legitimate change with no override available to the operator at all.
So only rules that are certain belong here. Two that were considered and
deliberately left out:

  * AuthorizeSecurityGroupIngress / RevokeSecurityGroupIngress "needs
    IpPermissions" — false. The flat legacy form (CidrIp/FromPort/ToPort/
    IpProtocol) is still in the current model and still works, so requiring
    IpPermissions would reject valid calls.
  * ModifyInstanceAttribute "exactly one attribute" — a real AWS rule, but it
    ranges over ~20 candidate members, and getting it subtly wrong rejects
    legitimate changes. Low value, high false-reject risk.

Three consumers read the same table, which is why the rules are data rather
than inline `if` statements: `build_ticket` (rejects with 422), the
`describe_operation` payload the MCP agent and the portal form both read, and
the form's own required-field marking. A private validator helper would have
been re-typed in the frontend and drifted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .aws_schema import (
    InvalidParameters,
    describe_operation,
    operation_members,
)


@dataclass(frozen=True)
class ConditionalRule:
    """At least one of `names` must be supplied.

    A single-element `names` is a plain "required" that the model omits; two or
    more is an either/or. That is the only shape AWS's conditional requirements
    take in the operations this gate permits, so there is deliberately no
    second rule kind to reason about.
    """

    names: tuple[str, ...]
    because: str

    def satisfied_by(self, parameters: dict[str, Any]) -> bool:
        return any(_present(parameters, name) for name in self.names)

    @property
    def message(self) -> str:
        if len(self.names) == 1:
            return f"{self.names[0]} is required — {self.because}"
        joined = " or ".join(self.names)
        return f"one of {joined} is required — {self.because}"

    def as_dict(self) -> dict[str, Any]:
        """Wire form. Consumed by the MCP describe tool and the portal form."""
        return {"oneOf": list(self.names), "because": self.because}


def _present(parameters: dict[str, Any], name: str) -> bool:
    """Absent, null and empty all count as not supplied.

    `{"ImageId": ""}` is not a launch anyone meant, and letting it through
    would defeat the whole check while looking like it passed.
    """
    if name not in parameters:
        return False
    value = parameters[name]
    return value is not None and value != "" and value != [] and value != {}


# Keyed by operation. Only EC2 — the gate is EC2-only (schemas.py's
# `service: Literal["ec2"]`), and widening that is an IAM/SCP decision, not a
# validation one. Every name here is asserted to be a real member of its
# operation's input shape by test_aws_conditional.py, so a typo cannot become a
# rule that can never be satisfied.
CONDITIONAL_RULES: dict[str, tuple[ConditionalRule, ...]] = {
    "RunInstances": (
        ConditionalRule(
            ("ImageId", "LaunchTemplate"),
            "an instance needs an image, either directly or via a launch template",
        ),
    ),
    "CreateVolume": (
        ConditionalRule(
            ("AvailabilityZone",),
            "a volume is created in one AZ and cannot be moved",
        ),
        ConditionalRule(
            ("Size", "SnapshotId"),
            "a volume's size comes either from Size or from the snapshot it restores",
        ),
    ),
    "DeleteSecurityGroup": (
        ConditionalRule(
            ("GroupId", "GroupName"),
            "the group to delete must be identified",
        ),
    ),
    "DeleteKeyPair": (
        ConditionalRule(
            ("KeyName", "KeyPairId"),
            "the key pair to delete must be identified",
        ),
    ),
    "ReleaseAddress": (
        ConditionalRule(
            ("AllocationId", "PublicIp"),
            "the address to release must be identified",
        ),
    ),
    "AssociateAddress": (
        ConditionalRule(
            ("AllocationId",),
            "the elastic IP to associate must be identified",
        ),
        ConditionalRule(
            ("InstanceId", "NetworkInterfaceId"),
            "an address is associated with an instance or a network interface",
        ),
    ),
    "DisassociateAddress": (
        ConditionalRule(
            ("AssociationId", "PublicIp"),
            "the association to break must be identified",
        ),
    ),
}


def rules_for(service: str, operation: str) -> tuple[ConditionalRule, ...]:
    """The rules that apply, or an empty tuple. Never raises on unknown input."""
    if service != "ec2":
        return ()
    return CONDITIONAL_RULES.get(operation, ())


def validate_conditional(service: str, operation: str, parameters: dict[str, Any]) -> None:
    """Raise InvalidParameters naming every unmet rule, not just the first.

    Reuses `aws_schema`'s exception rather than introducing a parallel type:
    `build_ticket` already maps it to INVALID_ACTION_PARAMETERS/422, and an
    agent correcting itself should not have to distinguish "botocore rejected
    this" from "the overlay rejected this" — both mean the same thing to it.
    """
    unmet = [rule for rule in rules_for(service, operation) if not rule.satisfied_by(parameters)]
    if not unmet:
        return
    detail = "; ".join(rule.message for rule in unmet)
    raise InvalidParameters(
        f"{operation} is missing parameters AWS requires conditionally: {detail}. "
        f"botocore's model does not list these as required, but the call fails without them."
    )


# The operations the portal form offers in its picker. Mirrors the action list
# in deploy/iam/iam-policy-agent-ec2-mutation.json, because an operation the
# executor's role cannot perform is one a human should not be led into
# proposing.
#
# NOT A WHITELIST, and nothing enforces it. The gate deliberately accepts a
# ticket for any valid EC2 operation — restricting which mutations exist is
# IAM's and the SCP's job, and duplicating that decision here would create a
# second, weaker copy of it that drifts. This list only decides what the form
# suggests; the form also accepts a typed-in operation name.
SUGGESTED_OPERATIONS: tuple[str, ...] = (
    "RunInstances",
    "StartInstances",
    "StopInstances",
    "RebootInstances",
    "TerminateInstances",
    "ModifyInstanceAttribute",
    "ModifyInstanceMetadataOptions",
    "CreateTags",
    "DeleteTags",
    "CreateSecurityGroup",
    "DeleteSecurityGroup",
    "AuthorizeSecurityGroupIngress",
    "AuthorizeSecurityGroupEgress",
    "RevokeSecurityGroupIngress",
    "RevokeSecurityGroupEgress",
    "CreateVolume",
    "DeleteVolume",
    "AttachVolume",
    "DetachVolume",
    "CreateSnapshot",
    "DeleteSnapshot",
    "AllocateAddress",
    "ReleaseAddress",
    "AssociateAddress",
    "DisassociateAddress",
    "CreateKeyPair",
    "DeleteKeyPair",
    "ImportKeyPair",
)


def describe_operation_full(service: str, operation: str) -> dict[str, Any]:
    """`describe_operation` plus this module's rules, as one payload.

    Additive on purpose: `aws_schema.describe_operation`'s shape is asserted in
    tests and quoted in the MCP tool contract, so it stays exactly as it was
    and the conditional layer arrives under its own key. The MCP describe tool
    and the portal form both read this, which is what keeps the rules from
    being re-typed in TypeScript and drifting.
    """
    described = describe_operation(service, operation)
    described["conditional"] = [rule.as_dict() for rule in rules_for(service, operation)]
    described["gateTags"] = list(TAGGABLE_ON_CREATE.get(operation, ()))
    return described


# --- gateTicketId propagation ----------------------------------------------

# Operation -> the resource types its created resources are tagged under.
#
# MIRRORS deploy/iam/iam-policy-agent-ec2-mutation.json EXACTLY, and must keep doing
# so. Two separate statements there depend on this map being neither too small
# nor too large:
#
#   * too small — `RunInstancesCreatedResourcesMustBeTagged` and SCP statement
#     `DenyGateExecutorUntaggedResourceCreation` both deny the call outright
#     when `aws:RequestTag/gateTicketId` is absent, so a create-operation
#     missing from this map is approved and then refused at AWS.
#   * too large — tagging on create is itself authorized by `TagOnCreateOnly`,
#     which is conditioned on `ec2:CreateAction` being one of a fixed list.
#     Adding TagSpecifications to an operation outside that list turns a call
#     that would have succeeded into an AccessDenied on ec2:CreateTags.
#     AuthorizeSecurityGroupIngress is the trap: it accepts the parameter (to
#     tag the rules it creates) but its ec2:CreateAction is not in that list.
#     Its Revoke counterpart does not even accept TagSpecifications, so "has
#     the member" is not a usable test either way — hence a curated map.
#
# RunInstances lists three types because the policy's Resource block does: one
# launch creates an instance, its volumes and its primary network interface,
# and the tag condition is evaluated against each of those ARNs.
TAGGABLE_ON_CREATE: dict[str, tuple[str, ...]] = {
    "RunInstances": ("instance", "volume", "network-interface"),
    "CreateSecurityGroup": ("security-group",),
    "CreateVolume": ("volume",),
    "CreateSnapshot": ("snapshot",),
    "CreateKeyPair": ("key-pair",),
    "ImportKeyPair": ("key-pair",),
}


def with_gate_tags(
    service: str, operation: str, parameters: dict[str, Any], tags: dict[str, str]
) -> dict[str, Any]:
    """Return `parameters` with the ticket's tags merged into TagSpecifications.

    WHY THE GATE DOES THIS AND NOT THE EXECUTOR. `gateTicketId` is the ticket's
    own id, so no caller can supply it — it does not exist until the gate mints
    it. That leaves two places it can be added: here, at creation, or in the
    executor just before the call. Here is strictly better on three counts:
    `parametersHash` covers the tags, so they are part of what was approved;
    the approver sees in the ticket exactly what will be sent; and the executor
    stays byte-verbatim, which is the property the hash exists to protect.

    Caller-supplied TagSpecifications are preserved — gate tags are merged into
    an existing entry for the same resource type rather than replacing it, and
    win on key collision for the same reason `build_ticket` overwrites
    gateTicketId/owner last: neither may be spoofed.

    A no-op for every operation not in TAGGABLE_ON_CREATE, including all of the
    mutate-existing-resource ones, which carry no TagSpecifications parameter
    at all (CLAUDE.md's invariant about aws:RequestTag).
    """
    if service != "ec2":
        return parameters
    resource_types = TAGGABLE_ON_CREATE.get(operation)
    if not resource_types or not tags:
        return parameters
    # Defensive: the map is asserted against the models in tests, but a future
    # botocore could drop the member and this must not synthesize a parameter
    # the operation does not accept.
    if "TagSpecifications" not in operation_members(service, operation):
        return parameters

    gate_tags = [{"Key": key, "Value": value} for key, value in sorted(tags.items())]
    existing = parameters.get("TagSpecifications") or []
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for spec in existing:
        if not isinstance(spec, dict):  # let botocore's validator report it
            merged.append(spec)
            continue
        resource_type = spec.get("ResourceType")
        if resource_type not in resource_types:
            merged.append(spec)
            continue
        seen.add(resource_type)
        caller_tags = [t for t in spec.get("Tags") or [] if isinstance(t, dict)]
        kept = [t for t in caller_tags if t.get("Key") not in tags]
        merged.append({**spec, "ResourceType": resource_type, "Tags": [*kept, *gate_tags]})

    for resource_type in resource_types:
        if resource_type not in seen:
            merged.append({"ResourceType": resource_type, "Tags": gate_tags})

    return {**parameters, "TagSpecifications": merged}
