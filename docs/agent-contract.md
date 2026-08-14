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
- The gate validates them against botocore's service model before the ticket
  exists. A bad operation name, an unknown parameter, a wrong type or a missing
  required member is refused with **422 `INVALID_ACTION_PARAMETERS`** and a
  message naming the problem — fix and re-send; nothing was created, and no
  approver was disturbed.
  A second, hand-curated layer then checks the conditional requirements the
  model *cannot* express — "X is required unless Y". `RunInstances` marks only
  `MinCount`/`MaxCount` required, yet also needs `ImageId` unless you pass a
  `LaunchTemplate`; that is refused here too, with the same 422. Query both
  lists up front with `describe_operation_parameters`, whose `conditional` key
  returns them as `{oneOf, because}` entries.
  **Still a floor, not a guarantee.** Only the rules that are certain are
  encoded, because wrongly rejecting a legitimate change is worse than letting
  AWS report it. Send what the call genuinely needs to identify what it acts
  on, and ask the operator for values like AMI or subnet ids rather than
  inventing them.
- `resourceArns` must list every targeted resource; empty only for
  pure-creation operations (e.g. `RunInstances`).
- The gate sets `assignee` and `proposedBy` to your **verified role** ARN and
  computes `parametersHash` (sha256 of canonical JSON) — store both the
  returned `ticketId` and `parametersHash`.
- That is the `arn:aws:iam::<acct>:role/<Name>` form, **not** the
  `arn:aws:sts::<acct>:assumed-role/<Name>/<session>` form STS returns to you.
  The session suffix changes every time your process restarts, so normalizing is
  what keeps your tickets yours across one. Never compare `assignee` against your
  own raw `GetCallerIdentity` ARN — it will not match.
- The gate also adds two tags to every ticket, overwriting any you send under
  the same keys: `gateTicketId` (= this ticket's own id) and `owner`
  (= `proposedBy`). Don't try to set either yourself; they're not
  caller-controlled. For resource-creating operations the gate additionally
  merges those tags into `parameters.TagSpecifications` — so the `parameters`
  on the ticket you get back will not be byte-identical to the ones you sent.
  That is expected, and it is the version the hash covers. See step 4.
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
- `FAILED` / `CLOSED` **with** `supersededBy` set → a human opened a follow-up
  to a ticket that already ran (e.g. a fixed retry after a failure). Same rule
  as `DEPRECATED`: follow the link, treat the successor as a new instruction.
  Note the old ticket keeps its `FAILED`/`CLOSED` status — `supersededBy`, not
  the status, is what tells you a follow-up exists. Stop polling the old one
  either way; it is terminal.

### 3. Start — `POST /api/agent/tickets/{id}/execution/start`

Body: `{"parametersHash": "<hash from create>"}`.

- `409 HASH_MISMATCH` means the ticket you remember is not the ticket that was
  approved — abort and re-create.
- On 200, the response echoes the approved `actionDetails`. **Execute from
  this response, not from your local memory.**

### 4. Execute

One AWS call, **exactly** the approved parameters — send `actionDetails.parameters`
from the `execution/start` response verbatim, adding nothing. Capture the SDK
response's `ResponseMetadata.RequestId`.

That includes `TagSpecifications`, which the gate now writes into `parameters`
itself at creation for resource-creating operations (`RunInstances`,
`CreateSecurityGroup`, `CreateVolume`, `CreateSnapshot`, `CreateKeyPair`,
`ImportKeyPair`). Earlier revisions of this document asked the executor to
propagate the ticket's `tags` onto created resources; that is no longer your
job, and doing it yourself now would send parameters that differ from the ones
whose hash was approved. The tags are already in what you were handed.

Why the gate does it: `gateTicketId` is the ticket's own id, so nothing can
supply it before the ticket exists — and the IAM policy and SCP both *deny*
resource creation when `aws:RequestTag/gateTicketId` is absent. Injecting at
creation rather than at execution means `parametersHash` covers the tags and
the approver saw them.

### 5. Report — `POST /api/agent/tickets/{id}/execution/result`

```json
{
  "outcome": "success",
  "message": "instance stopped",
  "awsRequestIds": ["..."],
  "createdResources": ["i-0abc"]
}
```

`createdResources` is optional and defaults to empty — send the ids of
resources the call brought into existence, read off the SDK response
(`RunInstances` → `Instances[].InstanceId`, `CreateVolume` → `VolumeId`, and so
on). It is what lets an operator ask the gate "what did my ticket create"
instead of hunting in the console, and it is surfaced by the
`get_change_ticket_details` MCP tool. Capped at 100 ids of 128 chars; anything
longer belongs in the audit trail by tag, not here. Never fail a call that
already succeeded because you could not parse its response — an id you failed
to report is recoverable from the `gateTicketId` tag, a success reported as a
failure is not.

`outcome: "failure"` marks the ticket FAILED; retrying requires a new
(superseding) ticket — never re-execute a FAILED ticket. A human creates that
retry by superseding the failed ticket (portal, or the `supersede_change_ticket`
MCP tool), which links the two and starts a fresh approval cycle. The failed
ticket stays `FAILED`, so the record that AWS was already touched survives the
retry.

## Strong enforcement (for the agent-role owner)

The gate verifies *intent* (hash echo) but cannot observe the actual AWS API
call. Close the gap at IAM and, for account-wide bypass resistance, at the
AWS Organizations SCP layer (`deploy/scp/deny-ec2-mutations-except-gate.json`):

1. Restrict who can call mutating EC2 actions **at all** to this role — an
   SCP `Deny` keyed on `StringNotEquals: {aws:PrincipalArn: <this role>}` is
   the actual bypass fix, since it blocks every other caller (a developer's
   own credentials, the public MCP server run standalone, the CLI, the
   console) regardless of tool.
2. Additionally require `aws:RequestTag/gateTicketId` (value = the ticket's
   own id, set automatically at creation — see step 1) — but **only** on
   resource-*creating* calls that accept a `TagSpecifications` parameter
   (`RunInstances`, `CreateSecurityGroup`, `CreateVolume`, `CreateSnapshot`,
   `CreateKeyPair`). Actions on an *existing* resource (`StopInstances`,
   `TerminateInstances`, `AuthorizeSecurityGroupIngress`, ...) carry no
   `TagSpecifications` at all, so `aws:RequestTag` is never present on them —
   a deny-if-absent condition on those would block this role from ever
   performing them, not just unapproved callers. There is no AWS-native
   condition key that binds an arbitrary existing-resource call to "went
   through an approved ticket"; that check is the gate's own
   `parametersHash` + status-machine logic, which this role is trusted to
   follow because (1) above, plus network isolation (see
   `deploy/k8s/istio-authorizationpolicy.yaml`), make it the only code path
   holding this role's credentials.
3. Scope the role's `Resource` element (or ABAC tag conditions) using the
   approved `resourceArns`.
4. Optionally correlate nightly: gate `awsRequestIds` ↔ CloudTrail events.

## Discovering tickets without a known id — `GET /api/agent/tickets`

Tickets aren't only created by this agent's own `POST` calls. A human can
also open one conversationally through the gate's `/mcp` endpoint
(`docs/mcp-gateway.md`) — those tickets share this role's `assignee`, but
the agent process was never told the id. Poll the list endpoint (same
`X-Gate-Identity` auth, results filtered server-side to tickets whose
`assignee` is your own verified **role** ARN — an MCP-proposed ticket carries
the gate's configured `MCP_EXECUTOR_ARN`, which is always in role form):

```
GET /api/agent/tickets?status=APPROVED
```

Response: an array of the same object `GET /api/agent/tickets/{id}` returns.
Treat every item exactly like a ticket you created yourself — poll/execute
via `execution/start` → `execution/result` as usual.

## Error envelope

Non-2xx responses: `{"error": {"code": "...", "message": "..."}}` (or
FastAPI's `{"detail": ...}` for auth failures). Notable codes:
`AGENT_AUTH_FAILED` (401), `AGENT_NOT_ALLOWED` (403), `NOT_ASSIGNEE` (403),
`INVALID_STATE` (409), `HASH_MISMATCH` (409), `NOT_FOUND` (404).
