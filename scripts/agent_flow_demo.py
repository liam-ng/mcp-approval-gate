#!/usr/bin/env python3
"""End-to-end demo of the agent flow against a running gate.

Usage:
    GATE_URL=http://localhost:8000 GATE_SERVER_ID=mcp-approval-gate-dev \
        python scripts/agent_flow_demo.py

Requires AWS credentials in the environment (any principal matched by the
gate's ALLOWED_AGENT_ARNS). The script creates a ticket, polls until a human
approves it in the UI, starts execution with the hash echo, and reports a
fake success — exercising every agent endpoint.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid

import botocore.session
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

GATE_URL = os.environ.get("GATE_URL", "http://localhost:8000").rstrip("/")
GATE_SERVER_ID = os.environ.get("GATE_SERVER_ID", "mcp-approval-gate-dev")
STS_BODY = "Action=GetCallerIdentity&Version=2011-06-15"


def gate_identity_header() -> str:
    session = botocore.session.get_session()
    credentials = session.get_credentials()
    if credentials is None:
        sys.exit("No AWS credentials found in the environment")
    request = AWSRequest(
        method="POST",
        url="https://sts.amazonaws.com/",
        data=STS_BODY,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "X-Gate-Server-Id": GATE_SERVER_ID,
        },
    )
    SigV4Auth(credentials, "sts", "us-east-1").add_auth(request)
    envelope = {
        "method": "POST",
        "url": "https://sts.amazonaws.com/",
        "headers": dict(request.headers),
        "body": STS_BODY,
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def call(method: str, path: str, **kwargs) -> httpx.Response:
    headers = kwargs.pop("headers", {})
    headers["X-Gate-Identity"] = gate_identity_header()  # fresh signature per call
    response = httpx.request(method, f"{GATE_URL}{path}", headers=headers, timeout=15, **kwargs)
    if response.status_code >= 400:
        print(f"  ! {method} {path} -> {response.status_code}: {response.text}")
    return response


def main() -> None:
    payload = {
        "subject": "Demo: stop staging instance i-0demo",
        "plannedDate": time.strftime("%Y-%m-%d"),
        "plannedAction": "Stop EC2 instance i-0demo (agent-flow demo)",
        "actionDetails": {
            "service": "ec2",
            "operation": "StopInstances",
            "region": "ap-east-1",
            "parameters": {"InstanceIds": ["i-0demo"]},
            "resourceArns": ["arn:aws:ec2:ap-east-1:123456789012:instance/i-0demo"],
            "reason": "agent flow demo",
        },
        "tags": {"env": "demo"},
    }

    print("1) Creating ticket ...")
    r = call("POST", "/api/agent/tickets", json=payload,
             headers={"Idempotency-Key": f"demo-{uuid.uuid4()}"})
    r.raise_for_status()
    ticket = r.json()
    ticket_id, params_hash = ticket["ticketId"], ticket["actionDetails"]["parametersHash"]
    print(f"   ticket {ticket_id} PENDING_APPROVAL")
    print(f"   approve it in the UI: {GATE_URL}/tickets/{ticket_id}")

    print("2) Polling until approved ...")
    while True:
        status = call("GET", f"/api/agent/tickets/{ticket_id}").json()
        s = status["status"]
        print(f"   status={s}")
        if s == "APPROVED":
            break
        if s in ("REJECTED", "EXPIRED"):
            sys.exit(f"   stopped: {s} ({status.get('rejectionReason')})")
        if s == "DEPRECATED":
            ticket_id = status["supersededBy"]
            print(f"   superseded -> following {ticket_id} (re-confirm in real usage!)")
            continue
        time.sleep(10)

    print("3) Starting execution (hash echo) ...")
    r = call("POST", f"/api/agent/tickets/{ticket_id}/execution/start",
             json={"parametersHash": params_hash})
    r.raise_for_status()
    print(f"   EXECUTING; approved params: {r.json()['actionDetails']['parameters']}")

    print("4) Reporting result ...")
    r = call("POST", f"/api/agent/tickets/{ticket_id}/execution/result",
             json={"outcome": "success", "message": "demo only — no AWS call made",
                   "awsRequestIds": []})
    r.raise_for_status()
    print(f"   final status: {r.json()['status']}")


if __name__ == "__main__":
    main()
