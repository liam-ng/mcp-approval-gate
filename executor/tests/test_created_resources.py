"""Pulling created resources out of an AWS response, as {type, id, arn}.

Most of the value is in what this does NOT produce. A generic "any key ending
in Id" scan would report ResponseMetadata.RequestId as a created resource, and
an ARN assembled from an assumed account would be a plausible-looking wrong
answer written into an immutable audit trail. Both are tested against here.
"""

from __future__ import annotations

from app.aws_exec import _created_resources

REGION = "ca-central-1"
ACCOUNT = "035614256871"


def test_run_instances_yields_instance_ids_and_full_arns():
    """The headline case: AWS returns OwnerId, so the ARN is real, not inferred."""
    response = {
        "OwnerId": ACCOUNT,
        "Instances": [{"InstanceId": "i-0aaa"}, {"InstanceId": "i-0bbb"}],
        "ResponseMetadata": {"RequestId": "req-1"},
    }
    assert _created_resources("RunInstances", REGION, response) == [
        {"type": "instance", "id": "i-0aaa",
         "arn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0aaa"},
        {"type": "instance", "id": "i-0bbb",
         "arn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0bbb"},
    ]


def test_no_owner_id_means_no_arn_rather_than_a_guessed_one():
    """A null is honest. A wrong ARN in a traceability record is worse than none."""
    response = {"Instances": [{"InstanceId": "i-0aaa"}]}
    (entry,) = _created_resources("RunInstances", REGION, response)
    assert entry == {"type": "instance", "id": "i-0aaa", "arn": None}


def test_security_group_prefers_the_arn_aws_returned():
    response = {"GroupId": "sg-0abc", "SecurityGroupArn": "arn:aws:ec2:x:1:security-group/sg-0abc"}
    (entry,) = _created_resources("CreateSecurityGroup", REGION, response)
    assert entry["arn"] == "arn:aws:ec2:x:1:security-group/sg-0abc"


def test_snapshot_arn_comes_from_owner_id():
    response = {"SnapshotId": "snap-0abc", "OwnerId": ACCOUNT}
    (entry,) = _created_resources("CreateSnapshot", REGION, response)
    assert entry["arn"] == f"arn:aws:ec2:{REGION}:{ACCOUNT}:snapshot/snap-0abc"


def test_operations_aws_tells_us_nothing_about_report_id_only():
    """CreateVolume/CreateKeyPair/AllocateAddress return no account and no ARN.

    Key pairs are the trap this pins down: a key pair's ARN path is its KeyName,
    not the KeyPairId recorded as `id`, so `key-pair/{id}` would be wrong in a
    way that looks right. None is the correct answer.
    """
    assert _created_resources("CreateVolume", REGION, {"VolumeId": "vol-0abc"}) == [
        {"type": "volume", "id": "vol-0abc", "arn": None}
    ]
    assert _created_resources("CreateKeyPair", REGION, {"KeyPairId": "key-0abc"}) == [
        {"type": "key-pair", "id": "key-0abc", "arn": None}
    ]
    assert _created_resources("AllocateAddress", REGION, {"AllocationId": "eipalloc-0abc"}) == [
        {"type": "elastic-ip", "id": "eipalloc-0abc", "arn": None}
    ]


def test_the_request_id_is_never_reported_as_a_resource():
    for operation in ("RunInstances", "CreateVolume", "StopInstances"):
        entries = _created_resources(operation, REGION, {"ResponseMetadata": {"RequestId": "req-1"}})
        assert all(e["id"] != "req-1" for e in entries)


def test_mutating_operations_create_nothing():
    assert _created_resources(
        "StopInstances", REGION, {"StoppingInstances": [{"InstanceId": "i-0abc"}]}
    ) == []
    assert _created_resources(
        "TerminateInstances", REGION, {"TerminatingInstances": [{"InstanceId": "i-0abc"}]}
    ) == []


def test_a_surprise_response_shape_does_not_fail_the_call():
    """The AWS mutation already happened by this point. Losing an id is an
    annoyance; raising here would report a successful change as a failure."""
    assert _created_resources("RunInstances", REGION, {"Instances": "not-a-list"}) == []
    assert _created_resources("RunInstances", REGION, {}) == []
    assert _created_resources("CreateVolume", REGION, {"VolumeId": None}) == []
