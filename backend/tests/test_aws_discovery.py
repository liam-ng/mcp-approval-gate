"""Discovery endpoints: off by default, and never able to break the form.

The behaviour under test is mostly about failure. Discovery is a convenience —
if it 500s, or 404s, or raises, the create form it feeds becomes unusable, and
the ticket that form would have opened is the product. So the contract is: same
response shape whether it is disabled, broken, or working.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.settings as settings_module
from app.api import auth as human_auth
from app.api.aws_discovery import router as discovery_router
from app.api.errors import install_error_handlers
from app.aws import discovery


@pytest.fixture()
def make_client(monkeypatch):
    def _make(enabled: bool = False):
        # Every mandatory setting must be set here, including ones this module
        # never reads. `Settings` validates the whole object at construction, so
        # one missing field fails the fixture, not the feature. ALLOWED_AGENT_ARNS
        # is the trap: a developer's backend/.env.liam-dev supplies it, so
        # omitting it passes locally and fails in CI, which has no env file.
        monkeypatch.setenv("SESSION_SECRET", "test-secret")
        monkeypatch.setenv("AUTH_MODE", "dev")
        monkeypatch.setenv("GATE_SERVER_ID", "approval-gate-test")
        monkeypatch.setenv("ALLOWED_AGENT_ARNS", "arn:aws:iam::123456789012:role/mcp-*")
        settings_module._settings = None
        settings = settings_module.get_settings()
        settings.aws_discovery_enabled = enabled
        settings.aws_discovery_default_region = "ca-central-1"

        test_app = FastAPI()
        human_auth.install(test_app, settings)
        test_app.include_router(discovery_router)
        install_error_handlers(test_app)
        return TestClient(test_app)

    yield _make
    settings_module._settings = None
    discovery._cache._entries.clear()


def login(client):
    client.get("/api/auth/login?email=liam@example.com&role=approver", follow_redirects=False)


def test_requires_a_session(make_client):
    assert make_client().get("/api/aws/ec2/discover/subnets").status_code == 401


def test_disabled_is_a_200_saying_so_not_an_error(make_client):
    """The form reads `enabled` and falls back to a text input. A 404 or 500 here
    would make it show a broken picker instead."""
    client = make_client(enabled=False)
    login(client)
    r = client.get("/api/aws/ec2/discover/subnets")
    assert r.status_code == 200
    assert r.json() == {"items": [], "enabled": False, "error": r.json()["error"]}
    assert "not enabled" in r.json()["error"]


def test_no_aws_call_is_made_while_disabled(make_client, monkeypatch):
    """The credential-free default must stay genuinely credential-free."""

    def _boom(*args, **kwargs):
        raise AssertionError("built an AWS client while discovery was disabled")

    monkeypatch.setattr(discovery, "_client", _boom)
    client = make_client(enabled=False)
    login(client)
    assert client.get("/api/aws/ec2/discover/images").json()["enabled"] is False


def test_aws_failure_degrades_to_an_empty_list(make_client, monkeypatch):
    def _explode(*args, **kwargs):
        raise RuntimeError("AccessDenied: no ec2:DescribeSubnets")

    monkeypatch.setattr(discovery, "_client", _explode)
    client = make_client(enabled=True)
    login(client)
    body = client.get("/api/aws/ec2/discover/subnets").json()
    assert body["items"] == []
    assert body["enabled"] is True
    assert "AccessDenied" in body["error"]


def test_unknown_kind_does_not_raise(make_client):
    client = make_client(enabled=True)
    login(client)
    body = client.get("/api/aws/ec2/discover/unicorns").json()
    assert body["items"] == [] and body["enabled"] is False


def test_subnets_are_shaped_for_a_picker(make_client, monkeypatch):
    class _FakeEc2:
        def describe_subnets(self):
            return {
                "Subnets": [
                    {
                        "SubnetId": "subnet-0abc",
                        "AvailabilityZone": "ca-central-1a",
                        "CidrBlock": "10.0.1.0/24",
                        "VpcId": "vpc-0abc",
                        "Tags": [{"Key": "Name", "Value": "build"}],
                    }
                ]
            }

    monkeypatch.setattr(discovery, "_client", lambda *a, **k: _FakeEc2())
    client = make_client(enabled=True)
    login(client)
    (item,) = client.get("/api/aws/ec2/discover/subnets").json()["items"]
    assert item["id"] == "subnet-0abc"
    assert item["label"] == "build"  # Name tag, falling back to the id
    assert "ca-central-1a" in item["detail"]


def test_ami_alias_resolves_to_a_concrete_image(make_client, monkeypatch):
    """The parametersHash fix: the approver sees ami-…, not resolve:ssm:…"""

    class _FakeSsm:
        def get_parameters(self, Names):
            assert Names == ["/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"]
            return {"Parameters": [{"Value": "ami-0real"}]}

    monkeypatch.setattr(discovery, "_client", lambda *a, **k: _FakeSsm())
    client = make_client(enabled=True)
    login(client)
    body = client.get(
        "/api/aws/ec2/discover/ami-alias/resolve",
        params={"alias": "resolve:ssm:/aws/service/ami-amazon-linux-latest/"
                         "al2023-ami-kernel-default-x86_64"},
    ).json()
    assert body["items"][0]["id"] == "ami-0real"


def test_a_non_alias_is_rejected_without_calling_aws(make_client, monkeypatch):
    monkeypatch.setattr(
        discovery, "_client", lambda *a, **k: pytest.fail("called AWS for a non-alias")
    )
    client = make_client(enabled=True)
    login(client)
    body = client.get("/api/aws/ec2/discover/ami-alias/resolve", params={"alias": "ami-0abc"}).json()
    assert "not an SSM parameter alias" in body["error"]


def test_two_aliases_do_not_share_a_cache_entry(make_client, monkeypatch):
    """Keying only on (kind, region) would serve the first alias's image for both."""
    answers = {"/aws/service/a": "ami-aaa", "/aws/service/b": "ami-bbb"}

    class _FakeSsm:
        def get_parameters(self, Names):
            return {"Parameters": [{"Value": answers[Names[0]]}]}

    monkeypatch.setattr(discovery, "_client", lambda *a, **k: _FakeSsm())
    client = make_client(enabled=True)
    login(client)

    def _resolve(name: str) -> str:
        body = client.get(
            "/api/aws/ec2/discover/ami-alias/resolve", params={"alias": f"resolve:ssm:{name}"}
        ).json()
        return body["items"][0]["id"]

    assert _resolve("/aws/service/a") == "ami-aaa"
    assert _resolve("/aws/service/b") == "ami-bbb"


def test_failures_are_not_cached(make_client, monkeypatch):
    """A fixed IAM permission should show up on the next click, not after the TTL."""
    calls = {"n": 0}

    class _FakeEc2:
        def describe_vpcs(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("AccessDenied")
            return {"Vpcs": [{"VpcId": "vpc-0abc", "CidrBlock": "10.0.0.0/16"}]}

    monkeypatch.setattr(discovery, "_client", lambda *a, **k: _FakeEc2())
    client = make_client(enabled=True)
    login(client)
    assert client.get("/api/aws/ec2/discover/vpcs").json()["error"]
    assert client.get("/api/aws/ec2/discover/vpcs").json()["items"][0]["id"] == "vpc-0abc"
