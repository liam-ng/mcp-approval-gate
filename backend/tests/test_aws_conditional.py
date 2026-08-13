"""The hand-curated conditional layer, and the tag injection that feeds IAM.

Two of these tests guard against the failure mode the module's docstring calls
the governing tradeoff: a rule naming a member that does not exist, or a
resource type the IAM policy does not authorize, would reject or break
legitimate changes permanently. Both are asserted against botocore's own models
rather than against a copy of them.
"""

from __future__ import annotations

import pytest

from app.core.aws_conditional import (
    CONDITIONAL_RULES,
    TAGGABLE_ON_CREATE,
    describe_operation_full,
    validate_conditional,
    with_gate_tags,
)
from app.core.aws_schema import InvalidParameters, operation_members, validate_parameters


# --- the rules themselves ---------------------------------------------------


def test_run_instances_without_image_is_rejected():
    """The exact call that wasted an approval on ticket 01KZY48KXZRFT2VKKR6006MK3N."""
    params = {"InstanceType": "t3.micro", "MinCount": 1, "MaxCount": 1}
    # botocore is happy with it — that is the whole reason this layer exists.
    validate_parameters("ec2", "RunInstances", params)
    with pytest.raises(InvalidParameters) as exc:
        validate_conditional("ec2", "RunInstances", params)
    assert "ImageId" in str(exc.value)
    assert "LaunchTemplate" in str(exc.value)


def test_run_instances_with_launch_template_is_accepted():
    """The either/or half. Requiring ImageId outright would break this launch."""
    validate_conditional(
        "ec2",
        "RunInstances",
        {"LaunchTemplate": {"LaunchTemplateId": "lt-0abc"}, "MinCount": 1, "MaxCount": 1},
    )


def test_empty_string_does_not_satisfy_a_rule():
    with pytest.raises(InvalidParameters):
        validate_conditional("ec2", "RunInstances", {"ImageId": "", "MinCount": 1, "MaxCount": 1})


def test_every_unmet_rule_is_reported_at_once():
    """Two rules, one message — an agent correcting itself shouldn't need two round trips."""
    with pytest.raises(InvalidParameters) as exc:
        validate_conditional("ec2", "CreateVolume", {})
    message = str(exc.value)
    assert "AvailabilityZone" in message
    assert "Size" in message and "SnapshotId" in message


def test_security_group_rules_are_not_over_constrained():
    """The flat legacy form is still valid; a rule requiring IpPermissions would reject it.

    Guards the decision recorded in the module docstring. If botocore ever drops
    CidrIp/FromPort/ToPort, this is where to notice.
    """
    flat = {"GroupId": "sg-0abc", "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
            "CidrIp": "10.0.0.0/8"}
    validate_parameters("ec2", "AuthorizeSecurityGroupIngress", flat)
    validate_conditional("ec2", "AuthorizeSecurityGroupIngress", flat)


def test_operations_without_rules_pass_through():
    validate_conditional("ec2", "StopInstances", {"InstanceIds": ["i-0abc"]})
    validate_conditional("ec2", "NotAnOperation", {})  # must not raise — validation is elsewhere


def test_non_ec2_service_has_no_rules():
    """Widening beyond EC2 is an IAM/SCP decision; this layer must not pre-empt it."""
    validate_conditional("s3", "CreateBucket", {})


def test_every_rule_names_a_real_member():
    """A typo'd name is a rule that can never be satisfied — a permanent false reject."""
    for operation, rules in CONDITIONAL_RULES.items():
        members = operation_members("ec2", operation)
        for rule in rules:
            for name in rule.names:
                assert name in members, f"{operation}: {name} is not a member of the input shape"


# --- gateTicketId injection -------------------------------------------------

TAGS = {"gateTicketId": "01TEST", "owner": "liam@example.com"}


def test_run_instances_gets_all_three_resource_types():
    """The IAM policy's Resource block names instance, volume and network-interface."""
    out = with_gate_tags("ec2", "RunInstances", {"ImageId": "ami-0abc"}, TAGS)
    types = {spec["ResourceType"] for spec in out["TagSpecifications"]}
    assert types == {"instance", "volume", "network-interface"}
    for spec in out["TagSpecifications"]:
        assert {"Key": "gateTicketId", "Value": "01TEST"} in spec["Tags"]


def test_injected_parameters_still_validate():
    """A synthesized parameter that botocore rejects would break every create call."""
    out = with_gate_tags("ec2", "RunInstances", {"ImageId": "ami-0abc", "MinCount": 1,
                                                 "MaxCount": 1}, TAGS)
    validate_parameters("ec2", "RunInstances", out)


def test_caller_tags_are_preserved_and_gate_tags_win():
    out = with_gate_tags(
        "ec2",
        "CreateVolume",
        {
            "AvailabilityZone": "ca-central-1a",
            "Size": 8,
            "TagSpecifications": [
                {
                    "ResourceType": "volume",
                    "Tags": [
                        {"Key": "Name", "Value": "data"},
                        {"Key": "gateTicketId", "Value": "spoofed"},
                    ],
                }
            ],
        },
        TAGS,
    )
    (spec,) = out["TagSpecifications"]
    assert {"Key": "Name", "Value": "data"} in spec["Tags"]
    assert {"Key": "gateTicketId", "Value": "01TEST"} in spec["Tags"]
    assert {"Key": "gateTicketId", "Value": "spoofed"} not in spec["Tags"]


def test_unrelated_resource_type_specs_are_left_alone():
    out = with_gate_tags(
        "ec2",
        "RunInstances",
        {"ImageId": "ami-0abc",
         "TagSpecifications": [{"ResourceType": "elastic-gpu", "Tags": [{"Key": "a", "Value": "b"}]}]},
        TAGS,
    )
    kept = [s for s in out["TagSpecifications"] if s["ResourceType"] == "elastic-gpu"]
    assert kept == [{"ResourceType": "elastic-gpu", "Tags": [{"Key": "a", "Value": "b"}]}]


def test_mutating_operations_are_untouched():
    """CLAUDE.md's invariant: aws:RequestTag only exists on resource-creating calls."""
    params = {"InstanceIds": ["i-0abc"]}
    assert with_gate_tags("ec2", "StopInstances", params, TAGS) == params
    assert with_gate_tags("ec2", "TerminateInstances", params, TAGS) == params


def test_authorize_security_group_ingress_is_not_tagged():
    """It accepts TagSpecifications, but ec2:CreateAction for it is not in TagOnCreateOnly.

    Injecting there converts a working call into an AccessDenied on ec2:CreateTags,
    which is why the map is curated rather than derived from "has the member".
    """
    params = {"GroupId": "sg-0abc", "IpPermissions": []}
    assert with_gate_tags("ec2", "AuthorizeSecurityGroupIngress", params, TAGS) == params


def test_input_is_not_mutated_in_place():
    params = {"ImageId": "ami-0abc"}
    with_gate_tags("ec2", "RunInstances", params, TAGS)
    assert params == {"ImageId": "ami-0abc"}


def test_every_taggable_operation_accepts_tag_specifications():
    for operation in TAGGABLE_ON_CREATE:
        assert "TagSpecifications" in operation_members("ec2", operation)


# --- the composed describe payload ------------------------------------------


def test_describe_operation_full_carries_rules_and_tag_types():
    described = describe_operation_full("ec2", "RunInstances")
    assert described["required"] == ["MaxCount", "MinCount"]  # unchanged base payload
    assert described["conditional"][0]["oneOf"] == ["ImageId", "LaunchTemplate"]
    assert described["conditional"][0]["because"]
    assert described["gateTags"] == ["instance", "volume", "network-interface"]


def test_describe_operation_full_is_empty_for_unruled_operations():
    described = describe_operation_full("ec2", "StopInstances")
    assert described["conditional"] == []
    assert described["gateTags"] == []
