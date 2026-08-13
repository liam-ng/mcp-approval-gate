"""Read-only account lookups so the portal form can offer real choices.

Without this, a human filling in the create form has to know a subnet id by
heart and type it correctly. That is exactly the situation that produces the
two failure modes this gate is meant to avoid: a wrong-but-valid id that gets
approved and does the wrong thing, and an invented id that wastes an approval.

WHAT THIS COSTS, STATED PLAINLY. CLAUDE.md's invariant is that the gate needs
no AWS permissions. This module is a deliberate, scoped amendment to that: a
separate, Describe-only identity, off by default, never used to mutate
anything. The invariant's real content is that the *approval and verification*
path holds no AWS trust — SigV4 agent verification still forwards to STS
without any credentials of its own, and `core/aws_schema.py` still validates
parameters from local JSON. Neither is touched by anything here. What would
break the invariant is this module ever gaining a write call, or the discovery
role ever being the executor role.

DEGRADE, NEVER BLOCK. Every function returns a `Discovery` result carrying
either items or a reason, and raises nothing into the request path. A form that
falls back to a free-text box is mildly annoying; a form that 500s because a
dropdown could not load is unusable, and the ticket it would have opened is the
point of the product.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.settings import Settings, get_settings

log = logging.getLogger(__name__)

# Long enough that clicking around the form is one round trip per resource
# kind, short enough that a subnet created five minutes ago shows up.
_DEFAULT_TTL_SECONDS = 300


@dataclass
class Discovery:
    """Items, or the reason there are none. Never an exception."""

    items: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"items": self.items, "enabled": self.enabled, "error": self.error}


_DISABLED = Discovery(enabled=False, error="AWS discovery is not enabled on this gate")


class _Cache:
    """Tiny TTL cache. A dict plus a lock beats a dependency for one use."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, ...], tuple[float, Discovery]] = {}
        self._lock = threading.Lock()

    def get_or_call(self, key: tuple[str, ...], ttl: int, fn: Callable[[], Discovery]) -> Discovery:
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        result = fn()
        # Failures are not cached: the usual cause is a missing permission or a
        # blocked egress rule, and once that is fixed the form should recover on
        # the next click rather than after a TTL nobody can see.
        if result.error is None:
            with self._lock:
                self._entries[key] = (now, result)
        return result


_cache = _Cache()


def _client(settings: Settings, service: str, region: str):
    """Build a read-only client. The ONLY boto3 client construction in the app.

    Two credential shapes are supported because the two environments differ.
    On EKS, IRSA gives the pod the discovery role directly and no assume is
    needed. Locally there is no OIDC provider (see docs/plan.md 2026-08-07), so
    credentials arrive as a mounted profile and AWS_DISCOVERY_ROLE_ARN names the
    role to step into.
    """
    import boto3  # imported here so an AWS-less deployment never loads it

    if not settings.aws_discovery_role_arn:
        return boto3.client(service, region_name=region)

    sts = boto3.client("sts", region_name=region)
    assumed = sts.assume_role(
        RoleArn=settings.aws_discovery_role_arn,
        RoleSessionName="mcp-approval-gate-discovery",
        DurationSeconds=900,
    )["Credentials"]
    return boto3.client(
        service,
        region_name=region,
        aws_access_key_id=assumed["AccessKeyId"],
        aws_secret_access_key=assumed["SecretAccessKey"],
        aws_session_token=assumed["SessionToken"],
    )


def _run(
    kind: str,
    region: str,
    fn: Callable[[Any], list[dict[str, Any]]],
    *,
    service: str = "ec2",
    key_extra: str | None = None,
) -> Discovery:
    """Cached, credential-guarded, never-raising wrapper around one lookup.

    `key_extra` exists because one kind can have several answers — an AMI alias
    lookup is keyed by the parameter name too, or two different aliases would
    share a cache entry and the second caller would get the first one's image.
    """
    settings = get_settings()
    if not settings.aws_discovery_enabled:
        return _DISABLED
    ttl = settings.aws_discovery_cache_seconds or _DEFAULT_TTL_SECONDS

    def _call() -> Discovery:
        try:
            client = _client(settings, service, region)
            return Discovery(items=fn(client))
        except Exception as exc:  # noqa: BLE001 - see DEGRADE, NEVER BLOCK above
            log.warning("discovery %s in %s failed: %s", kind, region, exc)
            return Discovery(error=f"{type(exc).__name__}: {exc}")

    key = (kind, region, key_extra) if key_extra else (kind, region)
    return _cache.get_or_call(key, ttl, _call)


def _name_tag(resource: dict[str, Any]) -> str | None:
    for tag in resource.get("Tags") or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


# --- the lookups ------------------------------------------------------------


def list_vpcs(region: str) -> Discovery:
    return _run("vpcs", region, lambda c: [
        {
            "id": vpc["VpcId"],
            "label": _name_tag(vpc) or vpc["VpcId"],
            "detail": vpc.get("CidrBlock", ""),
        }
        for vpc in c.describe_vpcs()["Vpcs"]
    ])


def list_subnets(region: str) -> Discovery:
    return _run("subnets", region, lambda c: [
        {
            "id": subnet["SubnetId"],
            "label": _name_tag(subnet) or subnet["SubnetId"],
            "detail": f"{subnet.get('AvailabilityZone', '')} · {subnet.get('CidrBlock', '')}",
            "vpcId": subnet.get("VpcId"),
        }
        for subnet in c.describe_subnets()["Subnets"]
    ])


def list_security_groups(region: str) -> Discovery:
    return _run("security-groups", region, lambda c: [
        {
            "id": group["GroupId"],
            "label": group.get("GroupName") or group["GroupId"],
            "detail": group.get("Description", ""),
            "vpcId": group.get("VpcId"),
        }
        for group in c.describe_security_groups()["SecurityGroups"]
    ])


def list_key_pairs(region: str) -> Discovery:
    return _run("key-pairs", region, lambda c: [
        {"id": key["KeyName"], "label": key["KeyName"], "detail": key.get("KeyType", "")}
        for key in c.describe_key_pairs()["KeyPairs"]
    ])


def list_instances(region: str) -> Discovery:
    def _fetch(client) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        paginator = client.get_paginator("describe_instances")
        for page in paginator.paginate():
            for reservation in page["Reservations"]:
                for instance in reservation["Instances"]:
                    state = instance.get("State", {}).get("Name", "")
                    if state == "terminated":
                        continue
                    items.append({
                        "id": instance["InstanceId"],
                        "label": _name_tag(instance) or instance["InstanceId"],
                        "detail": f"{instance.get('InstanceType', '')} · {state}",
                    })
        return items

    return _run("instances", region, _fetch)


def list_volumes(region: str) -> Discovery:
    return _run("volumes", region, lambda c: [
        {
            "id": volume["VolumeId"],
            "label": _name_tag(volume) or volume["VolumeId"],
            "detail": f"{volume.get('Size', '')} GiB · {volume.get('State', '')}",
        }
        for volume in c.describe_volumes()["Volumes"]
    ])


def list_images(region: str) -> Discovery:
    """Scoped hard, because unscoped DescribeImages returns tens of thousands.

    Self-owned images plus the current Amazon Linux 2023 and Ubuntu LTS lines
    is what someone filling in this form actually picks from. Anything else can
    be typed into the ImageId field directly.
    """

    def _fetch(client) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        for owners, filters in (
            (["self"], []),
            (
                ["amazon"],
                [{"Name": "name", "Values": ["al2023-ami-2023.*-x86_64", "al2023-ami-2023.*-arm64"]}],
            ),
            (
                ["099720109477"],  # Canonical
                [{"Name": "name", "Values": ["ubuntu/images/hvm-ssd*/ubuntu-*-24.04-*-server-*"]}],
            ),
        ):
            response = client.describe_images(
                Owners=owners,
                Filters=[*filters, {"Name": "state", "Values": ["available"]}],
                IncludeDeprecated=False,
            )
            images.extend(response["Images"])
        # Newest first, then capped — a picker is not a catalogue.
        images.sort(key=lambda i: i.get("CreationDate", ""), reverse=True)
        return [
            {
                "id": image["ImageId"],
                "label": image.get("Name") or image["ImageId"],
                "detail": f"{image.get('Architecture', '')} · {image.get('CreationDate', '')[:10]}",
            }
            for image in images[:50]
        ]

    return _run("images", region, _fetch)


def resolve_ami_alias(region: str, alias: str) -> Discovery:
    """Turn `resolve:ssm:/aws/service/...` into the concrete ami- it points at.

    This is the fix for the parametersHash gap recorded in docs/plan.md on
    2026-08-13: an alias is resolved by RunInstances at *execution* time, so the
    hash only ever proved the approver saw the same alias string, not the same
    image. Resolving it here means the approver reviews the actual AMI id, and
    what executes is what they read.
    """
    parameter = alias.removeprefix("resolve:ssm:")
    if not parameter.startswith("/"):
        return Discovery(error=f"not an SSM parameter alias: {alias!r}")

    def _fetch(client) -> list[dict[str, Any]]:
        value = client.get_parameters(Names=[parameter])["Parameters"]
        return [{"id": p["Value"], "label": p["Value"], "detail": parameter} for p in value]

    return _run("ami-alias", region, _fetch, service="ssm", key_extra=parameter)
