"""Offline validation of a ticket's AWS parameters against botocore's models.

Catches an agent proposing a call that cannot possibly succeed — a typo'd key,
a wrong type, a missing required member, an operation that doesn't exist — at
ticket-creation time, so a human is never asked to approve something AWS will
reject. Before this, the first sign of trouble was an EXECUTION_FAILED event
after the approval had already been spent.

NO AWS CREDENTIALS AND NO NETWORK. botocore ships its service definitions as
local JSON, and everything here reads those files. That matters: "the gate
itself needs no AWS permissions" is a stated invariant (CLAUDE.md), and this
module must not become the thing that quietly breaks it. Do not add a call
that constructs a boto3 *client* here — building one is where credential
resolution starts.

KNOWN LIMIT, and the reason this is only half the story. The models express
"required" as a flat per-operation list, so they cannot represent AWS's
conditional requirements. RunInstances lists only MinCount/MaxCount as
required, because ImageId is required *unless* you pass LaunchTemplate — so
{"InstanceType": ..., "MinCount": 1, "MaxCount": 1} validates cleanly here and
still fails at AWS with MissingParameter (ticket 01KZY48KXZRFT2VKKR6006MK3N,
2026-08-13). Only EC2 itself knows those rules; catching them needs a DryRun
pre-flight from the executor, which is where the credentials live. See
docs/plan.md's 2026-08-13 entry.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import botocore.session
from botocore.exceptions import UnknownServiceError
from botocore.model import OperationModel, OperationNotFoundError
from botocore.validate import ParamValidator


@lru_cache(maxsize=None)
def _operation_model(service: str, operation: str) -> OperationModel:
    """Cached: loading a service model parses a multi-MB JSON file.

    `maxsize=None` is safe even though `operation` arrives from an agent-authored
    ticket: lru_cache does not store exceptions, so a miss that raises leaves
    nothing behind, and only real operation names (a closed set per service) can
    ever occupy an entry. Verified — 50 bogus names leave currsize at 0.
    """
    session = botocore.session.get_session()
    return session.get_service_model(service).operation_model(operation)


class UnknownOperation(ValueError):
    """The service has no such operation — almost always an agent typo."""


class InvalidParameters(ValueError):
    """Parameters that botocore's model rejects.

    A dedicated type rather than a bare ValueError so `build_ticket` can catch
    exactly what this module means to signal. Catching plain ValueError there
    would report an unexpected botocore internal failure to the caller as
    "your parameters are wrong", which sends them chasing the wrong bug.
    """


def _resolve(service: str, operation: str) -> OperationModel:
    try:
        return _operation_model(service, operation)
    except (OperationNotFoundError, UnknownServiceError) as exc:
        raise UnknownOperation(f"{service} has no operation {operation!r}") from exc


def describe_operation(service: str, operation: str) -> dict[str, Any]:
    """The parameter shape of an operation: what's required, what's accepted.

    Feeds both the MCP describe tool and the hint appended to a validation
    error, so an agent that guessed wrong can correct itself without a human
    round trip. Deliberately returns names and types only — never example
    values. A plausible-looking default (an ImageId, a SubnetId) is worse than
    an error, because it would launch something nobody chose.
    """
    shape = _resolve(service, operation).input_shape
    if shape is None:  # operations that take no parameters at all
        return {"operation": operation, "required": [], "accepted": {}}
    required = sorted(shape.required_members)
    return {
        "operation": operation,
        "required": required,
        # name -> type, so an agent can tell a list from a string without
        # having to guess from the name.
        "accepted": {name: member.type_name for name, member in sorted(shape.members.items())},
    }


def validate_parameters(service: str, operation: str, parameters: dict[str, Any]) -> None:
    """Raise InvalidParameters/UnknownOperation with an agent-readable message.

    Non-raising for anything botocore accepts — this is a structural check, not
    a policy one. Whether the caller is *allowed* to run the operation is IAM's
    job, and whether the values make sense is AWS's.
    """
    shape = _resolve(service, operation).input_shape
    if shape is None:
        if parameters:
            raise InvalidParameters(
                f"{operation} takes no parameters, got {sorted(parameters)}"
            )
        return

    report = ParamValidator().validate(parameters, shape)
    if not report.has_errors():
        return

    # botocore's report is already specific ("Unknown parameter in input:
    # "InstanceTypes", must be one of: ..."). Append the required list, which
    # it omits when the failure was a typo rather than an omission.
    required = sorted(shape.required_members)
    hint = f" Required parameters for {operation}: {', '.join(required)}." if required else ""
    raise InvalidParameters(f"{report.generate_report()}{hint}")
