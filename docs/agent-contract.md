# MCP Server ↔ Approval Gate Integration Contract

The gate is a **blocking** control: the MCP server must not execute any
mutating EC2 action until a human approves the corresponding ticket. Read-only
calls (`Describe*`, `Get*`) bypass the gate.

## Flow

```
build actionDetails ──> POST /api/agent/tickets ──> poll GET /api/agent/tickets/{id}
        (exact SDK params)     (Idempotency-Key)          every 15–30 s + jitter
                                                              │
              REJECTED / EXPIRED: surface reason, stop  <─────┤
              DEPRECATED: follow supersededBy, re-confirm <───┤
                                                              ▼ APPROVED
                    POST .../execution/start  { parametersHash }
                                                              │ 409 = plan drifted, abort
                                                              ▼ 200
                    execute EXACTLY the actionDetails returned by /start
                                                              │
                    POST .../execution/result { outcome, message, awsRequestIds }
```

## Authentication (IAM SigV4, no shared secrets)

Every request carries an `X-Gate-Identity` header: a base64-encoded JSON
envelope of a **presigned `sts:GetCallerIdentity` request** signed with the
agent's own credentials (IRSA on the cluster). The gate forwards it to STS and
trusts the returned ARN. Requirements:

- The signed request must include header `X-Gate-Server-Id: <value>` matching
  the gate's `GATE_SERVER_ID`, and that header must be in `SignedHeaders`.
- `X-Amz-Date` must be within ±5 minutes of the gate clock (NTP assumed).
- Each signed envelope is single-use (replay-protected); sign a fresh one per
  request.
- The verified caller ARN must match the gate's `ALLOWED_AGENT_ARNS` globs.

### Building the header (Python / botocore)

```python
import base64, json
import botocore.session
from botocore.awsrequest import AWSRequest
from botocore.auth import SigV4Auth

GATE_SERVER_ID = "mcp-approval-gate-prod"   # must match the gate's env

def gate_identity_header() -> str:
    session = botocore.session.get_session()
    credentials = session.get_credentials()
    request = AWSRequest(
        method="POST",
        url="https://sts.amazonaws.com/",
        data="Action=GetCallerIdentity&Version=2011-06-15",
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
        "body": "Action=GetCallerIdentity&Version=2011-06-15",
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()
```

## Endpoints

### 1. Create — `POST /api/agent/tickets`

Headers: `X-Gate-Identity`, `Idempotency-Key` (recommended: stable hash of
conversation id + operation, so retries return the same ticket — 200 instead
of 201).

```json
{
  "subject": "Stop staging instance i-0abc",
  "plannedDate": "2026-08-10",
  "plannedAction": "Stop EC2 instance i-0abc in staging to reduce cost",
  "actionDetails": {
    "service": "ec2",
    "operation": "StopInstances",
    "region": "ap-east-1",
    "parameters": {"InstanceIds": ["i-0abc"]},
    "resourceArns": ["arn:aws:ec2:ap-east-1:123456789012:instance/i-0abc"],
    "reason": "requested by cost review"
  },
  "tags": {"team": "gti", "env": "staging"}
}
```

Rules:
- `parameters` must be the **exact** SDK parameters you intend to send.
- `resourceArns` must list every targeted resource; empty only for
  pure-creation operations (e.g. `RunInstances`).
- The gate sets `assignee` and `proposedBy` to your **verified** ARN and
  computes `parametersHash` (sha256 of canonical JSON) — store both the
  returned `ticketId` and `parametersHash`.
- Surface the ticket URL (`{PUBLIC_BASE_URL}/tickets/{ticketId}`) to the human
  operator in the conversation.

### 2. Poll — `GET /api/agent/tickets/{id}`

Every 15–30 s with jitter, up to your own deadline. Response includes
`status`, `approvedBy`, `rejectionReason`, `supersededBy`, `actionDetails`.

- `APPROVED` → proceed to /start.
- `REJECTED` / `EXPIRED` → report the reason to the operator and stop.
- `DEPRECATED` → a human edited the request. Follow `supersededBy`, fetch the
  new ticket, and **treat it as a new instruction**: re-confirm with the
  operator before continuing to poll it (its parameters differ from what you
  proposed).

### 3. Start — `POST /api/agent/tickets/{id}/execution/start`

Body: `{"parametersHash": "<hash from create>"}`.

- `409 HASH_MISMATCH` means the ticket you remember is not the ticket that was
  approved — abort and re-create.
- On 200, the response echoes the approved `actionDetails`. **Execute from
  this response, not from your local memory.**

### 4. Execute

One AWS call, exactly the approved parameters. Where the operation supports
tagging (e.g. `RunInstances` `TagSpecifications`), propagate the ticket's
`tags` **plus `gateTicketId=<ticketId>`** onto created resources. Capture the
SDK response's `ResponseMetadata.RequestId`.

### 5. Report — `POST /api/agent/tickets/{id}/execution/result`

```json
{"outcome": "success", "message": "instance stopped", "awsRequestIds": ["..."]}
```

`outcome: "failure"` marks the ticket FAILED; retrying requires a new
(superseding) ticket — never re-execute a FAILED ticket.

## Strong enforcement (for the agent-role owner)

The gate verifies *intent* (hash echo) but cannot observe the actual AWS API
call. Close the gap at IAM:

1. Add a condition to the agent role requiring `aws:RequestTag/gateTicketId`
   on mutating EC2 actions, so any call that didn't come through the gate
   fails at IAM.
2. Scope the role's `Resource` element (or ABAC tag conditions) using the
   approved `resourceArns`.
3. Optionally correlate nightly: gate `awsRequestIds` ↔ CloudTrail events.

## Error envelope

Non-2xx responses: `{"error": {"code": "...", "message": "..."}}` (or
FastAPI's `{"detail": ...}` for auth failures). Notable codes:
`AGENT_AUTH_FAILED` (401), `AGENT_NOT_ALLOWED` (403), `NOT_ASSIGNEE` (403),
`INVALID_STATE` (409), `HASH_MISMATCH` (409), `NOT_FOUND` (404).
