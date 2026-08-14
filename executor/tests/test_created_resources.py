"""Pulling created-resource ids out of an AWS response.

The value of this is entirely in what it does NOT return: a generic "any key
ending in Id" scan would report ResponseMetadata.RequestId as a created
resource, which would then be written into the audit trail as a resource that
does not exist.
"""

from __future__ import annotations

from app.aws_exec import _created_resources


def test_run_instances_yields_every_instance_id():
    response = {
        "Instances": [{"InstanceId": "i-0aaa"}, {"InstanceId": "i-0bbb"}],
        "ResponseMetadata": {"RequestId": "req-1"},
    }
    assert _created_resources("RunInstances", response) == ["i-0aaa", "i-0bbb"]


def test_the_request_id_is_never_reported_as_a_resource():
    for operation in ("RunInstances", "CreateVolume", "StopInstances"):
        assert "req-1" not in _created_resources(
            operation, {"ResponseMetadata": {"RequestId": "req-1"}}
        )


def test_single_resource_operations():
    assert _created_resources("CreateVolume", {"VolumeId": "vol-0abc"}) == ["vol-0abc"]
    assert _created_resources("CreateSecurityGroup", {"GroupId": "sg-0abc"}) == ["sg-0abc"]
    assert _created_resources("CreateSnapshot", {"SnapshotId": "snap-0abc"}) == ["snap-0abc"]
    assert _created_resources("AllocateAddress", {"AllocationId": "eipalloc-0abc"}) == [
        "eipalloc-0abc"
    ]


def test_mutating_operations_create_nothing():
    assert _created_resources("StopInstances", {"StoppingInstances": [{"InstanceId": "i-0abc"}]}) == []
    assert _created_resources("TerminateInstances", {"TerminatingInstances": [{"InstanceId": "i-0abc"}]}) == []


def test_a_surprise_response_shape_does_not_fail_the_call():
    """The AWS mutation already happened by this point. Losing an id is an
    annoyance; raising here would report a successful change as a failure."""
    assert _created_resources("RunInstances", {"Instances": "not-a-list"}) == []
    assert _created_resources("RunInstances", {}) == []
    assert _created_resources("CreateVolume", {"VolumeId": None}) == []
