"""Structural validation of proposed AWS parameters (app/core/aws_schema.py)."""

from __future__ import annotations

import pytest

from app.core.aws_schema import (
    UnknownOperation,
    describe_operation,
    validate_parameters,
)

VALID_RUN = {"ImageId": "ami-0abc", "InstanceType": "t3.micro", "MinCount": 1, "MaxCount": 1}


def test_valid_call_passes():
    validate_parameters("ec2", "RunInstances", VALID_RUN)
    validate_parameters("ec2", "StopInstances", {"InstanceIds": ["i-0abc"]})


def test_unknown_parameter_is_rejected_and_names_the_typo():
    with pytest.raises(ValueError) as exc:
        validate_parameters("ec2", "RunInstances", {**VALID_RUN, "InstanceTypes": "t3.micro"})
    assert "InstanceTypes" in str(exc.value)


def test_wrong_type_is_rejected():
    with pytest.raises(ValueError, match="MinCount"):
        validate_parameters("ec2", "RunInstances", {**VALID_RUN, "MinCount": "one"})


def test_missing_required_parameter_is_rejected_with_the_required_list():
    with pytest.raises(ValueError) as exc:
        validate_parameters("ec2", "StopInstances", {})
    message = str(exc.value)
    assert "InstanceIds" in message
    assert "Required parameters for StopInstances" in message


def test_unknown_operation_is_rejected():
    with pytest.raises(UnknownOperation, match="RunInstance"):
        validate_parameters("ec2", "RunInstance", VALID_RUN)  # missing the plural


def test_conditionally_required_parameter_is_NOT_caught():
    """Documents the limit that motivated the DryRun pre-flight.

    This is the exact payload of ticket 01KZY48KXZRFT2VKKR6006MK3N, which AWS
    rejected with MissingParameter (ImageId). botocore's model cannot express
    "ImageId unless LaunchTemplate", so this passes here. If a future botocore
    starts catching it, delete this test and celebrate — do not "fix" it by
    hardcoding ImageId as required, which would break LaunchTemplate launches.
    """
    validate_parameters("ec2", "RunInstances", {"InstanceType": "t3.micro", "MinCount": 1, "MaxCount": 1})


def test_describe_operation_lists_required_and_accepted_without_values():
    described = describe_operation("ec2", "RunInstances")
    assert described["operation"] == "RunInstances"
    assert sorted(described["required"]) == ["MaxCount", "MinCount"]
    # ImageId is accepted-but-not-required — the whole point of the trap above.
    assert described["accepted"]["ImageId"] == "string"
    assert described["accepted"]["MinCount"] == "integer"
    assert "LaunchTemplate" in described["accepted"]
    # Names and types only; nothing that could be mistaken for a default value.
    assert all(isinstance(v, str) for v in described["accepted"].values())


def test_describe_operation_rejects_unknown_operation():
    with pytest.raises(UnknownOperation):
        describe_operation("ec2", "NotAnOperation")
