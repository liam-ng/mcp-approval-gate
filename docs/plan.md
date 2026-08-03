# mcp-approval-gate — Implementation Plan (living document)

> This is the working copy of the project plan. It is updated at the end of every
> phase: checkboxes below track progress, and any decisions or deviations are
> recorded in the **Decision log** at the bottom.

## Progress

- [x] Phase 1 — Scaffold + domain core (models, status machine, canonical JSON)
- [x] Phase 2 — Repository layer (ABC + JSONL store + factory)
- [x] Phase 3 — Agent API (SigV4 auth + create/poll/start/result routes)
- [x] Phase 4 — Human auth + API (OIDC, RBAC, ticket routes, SES notifier)
- [x] Phase 5 — Frontend (React SPA mirroring gammon-powershell-portal UI)
- [x] Phase 6 — Packaging + docs (Dockerfile, k8s manifests, agent contract)
- [x] Phase 7 — Hardening (expiry sweep, structured logging, rate limiting)

## Todo

- [] Create a Github action pipeline for frontend and backend to scan code using sonarqube, hard gate for critical CWE, build docker image, scan image using Trivy with critical CVE hard gate, perform simple app start test and unit test, push to Azure contain registry using managed identity, output. 
- [x] Address open risk: ship the gate itself as an MCP tool (not a fork of the official AWS MCP server) so end users add it directly to Cursor/VS Code. Done: `/mcp` Streamable HTTP route (`backend/app/api/mcp_gateway.py`), OAuth2.1 Resource Server auth, Istio + SCP isolation of the upstream server. See `docs/mcp-gateway.md`.

## Context

An AI agent (AWS MCP server, running on the existing k8s cluster) can view/create/manage EC2. Today its changes lack auditability and human control. This project builds a **blocking approval gate**: before any mutating EC2 action, the agent must create a change-request ticket, wait for human approval in a web portal, execute only the approved parameters, and report the result. Tickets are immutable — edits create a superseding ticket and mark the old one Deprecated — giving a tamper-evident audit chain.

UI mirrors the conventions of the internal `gammon-powershell-portal` project (shadcn/ui + Tailwind HSL brand tokens, DataTable + approval-list patterns), while fixing its known anti-patterns.

## Confirmed decisions

| Decision | Choice |
|---|---|
| Tech stack | **React SPA (Vite) frontend + FastAPI (Python) backend**, single container |
| Enforcement | Blocking gate: create → poll → execute-on-Approved → report |
| Human auth | OIDC via Cognito (fronting IAM Identity Center over SAML) now; **provider-agnostic** so Azure AD/Entra ID is a config-only swap later |
| Agent auth | IAM SigV4 (presigned `sts:GetCallerIdentity` pattern); fully separate middleware path from human auth |
| Storage | MVP: local JSONL on PVC (single replica), behind a DynamoDB-shaped repository interface; production swap via `STORE_BACKEND` env to **DynamoDB** or **S3 with Object Lock** (WORM audit trail) |
| Ticket scoping | Tickets carry **management tags** (`tags`) and **resource-ARN scope** (`resourceArns`) so approvals are scoped to specific EC2 resources and taggable for cost/team management |
| Approvals required | Env-configurable `REQUIRED_APPROVALS=1\|2`; approvers distinct and never the proposer |
| Notifications | SES email in MVP — email approvers on ticket creation |
| Expiry | TTL-based `APPROVAL_TTL_HOURS` (default 72); stale Pending/Approved → EXPIRED |
| IDE distribution | End users add **the gate** to Cursor/VS Code as a remote MCP tool (`/mcp`, OAuth2.1 Resource Server), never the upstream AWS API MCP server directly — enforced in-cluster by Istio (`deploy/k8s/istio-authorizationpolicy.yaml`) and account-wide by an SCP (`deploy/scp/`). See `docs/mcp-gateway.md`. |

## Architecture

**Backend** — Python 3.12, FastAPI + uvicorn, Pydantic v2, pydantic-settings, Authlib (server-side OIDC Authorization Code flow, httpOnly session cookie), boto3 (SES now, DynamoDB/S3 later), httpx (forwarding presigned STS requests), python-ulid, pytest.

**Frontend** — React + TypeScript strict + Vite, shadcn/ui + Tailwind (brand tokens copied from the portal), react-router, TanStack Query + TanStack Table, react-hook-form + zod.

**Single deployable**: FastAPI mounts the built SPA (`StaticFiles` + index.html fallback) and owns `/api/*`. One image, one k8s Deployment (`strategy: Recreate`, RWO PVC).

### Ticket lifecycle

```
PENDING_APPROVAL ──approve (n>=required)──> APPROVED ──start (hash echo)──> EXECUTING ──> COMPLETED | FAILED
      │  │  │                                 │  │
      │  │  └─reject──> REJECTED              │  └─supersede──> DEPRECATED
      │  └─supersede──> DEPRECATED            └─TTL──> EXPIRED
      └─TTL──> EXPIRED
```

- Approver must hold the `approver` role, must not be the proposer, and must not have already approved the same ticket. Status flips to APPROVED when approvals reach `REQUIRED_APPROVALS`.
- Every change appends an immutable `AuditEvent`; the ticket is a fold over its events. Frozen fields (subject, actionDetails, plannedDate, …) never change — edits require a superseding ticket (`supersedes`/`supersededBy` links, shared `lineageRootId`).
- `parametersHash` = sha256 of canonical JSON of the intended SDK parameters, computed by the gate; the agent must echo it at `execution/start` and execute the actionDetails returned by that response.

### Storage backends (`STORE_BACKEND`)

- `jsonl` (MVP): one AuditEvent per line in `$DATA_DIR/tickets.jsonl`; boot-time fold into memory; single `asyncio.Lock` writer; fsync on approval/execution events; torn final line skipped on replay.
- `dynamodb` (production): single table `PK=TICKET#id / SK=META|EVENT#seq`; GSI1 `STATUS#status/ticketDate`, GSI2 `LINEAGE#rootId`, GSI3 `IDEM#arn#key`; `append_event` → `TransactWriteItems` with `ConditionExpression seq = :expected`.
- `s3` (compliance): each AuditEvent an immutable object `tickets/{id}/events/{seq:06d}.json` in a versioned bucket with **Object Lock** (WORM); CAS via conditional `PutObject` (`If-None-Match: *`); same fold code as jsonl. Middle ground: DynamoDB operational store + dual-write audit mirror to the Object Lock bucket (`AUDIT_MIRROR_S3_BUCKET`).

### Auth

- **Human**: Authorization Code flow handled entirely by FastAPI (Authlib); provider built only from env (`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SCOPES`, `OIDC_GROUPS_CLAIM`). Entra ID swap = env change only. RBAC: groups claim → `approver|viewer` via `OIDC_APPROVER_GROUPS`, with `APPROVER_EMAILS` fallback (SAML-sourced group claims can be weak/absent).
- **Agent**: presigned `sts:GetCallerIdentity` (Vault / aws-iam-authenticator pattern) in `X-Gate-Identity` header; must sign `X-Gate-Server-Id: $GATE_SERVER_ID`; ±5-min date window; replay nonce cache; gate forwards to STS via httpx (gate needs no STS permission); caller ARN checked against `ALLOWED_AGENT_ARNS` globs; verified ARN becomes `assignee`/`proposedBy`.
- **IDE (MCP)**: `/mcp` is an OAuth2.1 Resource Server — Cursor/VS Code run the auth-code+PKCE flow directly against the same OIDC IdP (the gate is never in that path); the gate only validates the bearer token's signature/claims against the IdP's JWKS (`MCP_OAUTH_ISSUER`/`MCP_OAUTH_AUDIENCE`). Details: `docs/mcp-gateway.md`.

### API surface

Agent (SigV4): `POST /api/agent/tickets` (idempotent create) · `GET /api/agent/tickets` (list the caller's own tickets, e.g. `?status=APPROVED`, to discover ones a human proposed via `/mcp`) · `GET /api/agent/tickets/{id}` (poll; follows `supersededBy`) · `POST .../execution/start` (hash echo, returns approved actionDetails) · `POST .../execution/result`.

Human (session): `GET /api/tickets` · `GET /api/tickets/{id}` (`{ticket, lineage, auditEvents}`) · `POST .../approve` · `POST .../reject` (reason required) · `POST .../supersede` (atomic new+deprecate) · `GET /api/me` · `GET /api/healthz` (public).

IDE (OAuth2.1 bearer, `docs/mcp-gateway.md`): `POST /mcp` — MCP Streamable HTTP JSON-RPC; tools `create_change_ticket` (proposedBy=human, assignee=`MCP_EXECUTOR_ARN`) and `check_ticket_status`.

### MCP-server contract (docs/agent-contract.md)

create (with `resourceArns` + `tags`) → poll 15–30 s → on APPROVED: `execution/start` echoing hash → execute the returned actionDetails, propagating `tags` + `gateTicketId` tag where supported → report result with AWS RequestIds. Strong enforcement: an SCP (`deploy/scp/`) plus IAM restrict mutating EC2 actions to this role alone, everywhere — not just in-cluster; scope the role by the approved `resourceArns`. See `docs/agent-contract.md`'s "Strong enforcement" section for why the `aws:RequestTag/gateTicketId` condition only applies to resource-creating calls, not `StopInstances`-style actions on existing resources.

## Build order

1. **Scaffold + domain core** — repo layout, pyproject, Vite app, brand tokens; settings, models, status machine, canonical JSON. Tests: transition matrix, canonical-JSON stability, approval threshold.
2. **Repository** — ABC, JSONL store, factory + DynamoDB/S3 stubs. Tests: fold/rebuild, torn-line recovery, CAS conflict, supersede atomicity, idempotency, immutability.
3. **Agent API** — agent auth + replay cache + 4 routes. Tests with mocked STS.
4. **Human auth + API** — Authlib OIDC, rbac, 6 routes, service rules, SES notifier.
5. **Frontend** — shell, dashboard, list, detail, dialogs.
6. **Packaging + docs** — Dockerfile, k8s manifests, agent-contract.md, README, .env.example.
7. **Hardening** — expiry sweep, structlog, slowapi rate limit, DynamoDB/S3 stores behind flag.

## Open risks

- JSONL on PVC: single-AZ EBS, no PITR — schedule snapshots; DynamoDB/S3 is the durability fix. Single replica: gate down ⇒ agent blocked (accepted for MVP).
- Replay window: nonce cache resets on pod restart; bounded by ±5-min SigV4 window + mandatory TLS + server-id binding.
- Fidelity gap: hash echo proves intent, not the actual AWS call — IAM `gateTicketId` request-tag condition is the strong enforcement.
- IAM Identity Center: **resolved 2026-08-03, not usable directly.** Its own OIDC service (`sso-oidc`) only registers public clients (PKCE/device-code) — no `client_secret`, so Authlib's confidential Authorization Code flow can't use it as `OIDC_ISSUER`. Its "trusted token issuer" feature is a token-*exchange* mechanism for an app that already authenticated a user elsewhere, not a login flow. The working path is Identity Center as a SAML 2.0 application → Amazon Cognito User Pool (SAML IdP + hosted-UI app client with a secret) → standard OIDC from there; see `.env.example`'s AWS scenario block. Group claims still limited (SAML attribute mapping) → `APPROVER_EMAILS` fallback, or use Cognito's own `cognito:groups` instead.
- Not in v1: quorum >2, CSV export/retention, CloudTrail correlation job, time-zone preference.

## Decision log

- 2026-08-03 — AWS human-auth path corrected: IAM Identity Center cannot be `OIDC_ISSUER` directly (its OIDC service only supports public clients; the "trusted token issuer" feature is token-exchange for an already-authenticated app, not a login flow — confirmed against AWS docs after a live setup attempt hit a dead end trying to source an `OIDC_CLIENT_ID`/secret from a trusted-token-issuer config). Working AWS path: Identity Center SAML 2.0 application → Cognito User Pool (SAML IdP + hosted-UI app client with a generated secret) → this gate via the same generic `OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET` config, zero code changes (`backend/app/api/auth.py` already does plain discovery-URL OIDC). `.env.example`'s AWS scenario block rewritten accordingly; `OIDC_GROUPS_CLAIM` now suggested as `cognito:groups`. Azure AD/Entra scenario unaffected — it was already a direct, working confidential client the whole time. No backend code changed.
- 2026-08-02 — MCP gateway added. The gate now exposes `/mcp` (Streamable HTTP, via the official `mcp` Python SDK's `MCPServer`) as an OAuth2.1 Resource Server, so end users add the gate — not the upstream AWS API MCP server — to Cursor/VS Code. New `core/service.py:create_mcp_ticket` mirrors `create_agent_ticket` but with proposedBy=human (from the validated bearer token) and assignee=a fixed `MCP_EXECUTOR_ARN`; a new `GET /api/agent/tickets` (SigV4, filtered to the caller's own assignee) lets that executor discover tickets it didn't itself create. Mounted via a small top-level ASGI dispatcher in `main.py` rather than FastAPI sub-routing, because the SDK's auth middleware (bearer verification + the contextvar `get_access_token()` relies on) needs to own its own Starlette app end to end — nesting it under the SPA's catch-all fallback route would either lose that middleware or make the fallback unreachable (see the comment in `main.py`). Network isolation of the real upstream server: an Istio `AuthorizationPolicy` (`deploy/k8s/istio-authorizationpolicy.yaml`, in-cluster only) plus an SCP (`deploy/scp/deny-ec2-mutations-except-gate.json`, account/OU-wide, tool-agnostic) both allow only the gate's trusted executor identity. Corrected a latent bug while writing the SCP: `aws:RequestTag/gateTicketId` only exists on resource-*creating* EC2 calls (those with a `TagSpecifications` param) — extending it to existing-resource actions like `StopInstances` would silently block the executor too, not just bypass attempts; `docs/agent-contract.md`'s "Strong enforcement" section and the SCP file both reflect the fix. Full design and the OAuth topology (gate is a pure Resource Server; Cursor/VS Code run the auth-code+PKCE flow directly against the IdP, never through the gate) are in `docs/mcp-gateway.md`. 64 backend tests green.
- 2026-08-02 — Plan approved. Stack: React (Vite) + FastAPI (user preference; boto3/Pydantic fit, MCP ecosystem is Python). S3 Object Lock added as storage option; tags + resourceArns added to ticket model.
- 2026-08-02 — Phase 7 done. Expiry sweep runs in-app via FastAPI lifespan every 10 min; APPROVED TTL counts from the last approval, PENDING from creation; EXECUTING and terminal tickets are never expired. Deviation from plan: stdlib logging + a dependency-free sliding-window rate limiter (60 req/min per client on /api/agent/*) instead of structlog/slowapi, to keep the image lean — revisit when scaling out. 57 backend tests green.
- 2026-08-02 — Phase 6 done. Dockerfile (node build → python:3.12-slim, non-root 1001); k8s manifests validate with `kubectl apply --dry-run=client`; agent contract doc includes botocore snippet for the presigned identity header; `scripts/agent_flow_demo.py` exercises the full agent flow against a running gate.
- 2026-08-02 — Phase 5 done. Pages: dashboard (status cards + recent), tickets (Active/History tabs, tag filter, 10 s polling on active), detail (lineage chain, hash-locked parameters view, audit timeline, approve/reject with confirm dialogs + interlock, supersede via react-hook-form+zod). TanStack Query `refetchInterval` stops on terminal statuses. Approve/reject buttons self-hide for proposer/duplicate-approver/viewer (server still enforces). SPA served by FastAPI with index.html fallback (verified).
- 2026-08-02 — Phases 3–4 done. Agent auth rejects pre-STS on envelope problems (wrong host, unsigned server-id, stale date, replay) so STS is only called for plausible requests. Role is resolved at login and stored in the session (role changes need re-login). AUTH_MODE=dev provides local fake login; settings validation refuses it in production. `/api/me`, list filters (status/assignee/tag), audit trail verified by tests (53 passing).
- 2026-08-02 — Phases 1–2 done. Fold lives in `repo/base.py:apply_event` and only touches `MUTABLE_FIELDS`, making immutability structural. JSONL store repairs a torn supersede pair on boot by reverting the orphaned DEPRECATED (same rule the future S3 store needs). Approval events: `APPROVAL_ADDED` below threshold, `APPROVED` at threshold; both carry `details.approval`.

## Detailed Design

> As-built reference. This section supersedes the original Next.js-era `plan.md`
> at the repo root (kept only for history) — everything below reflects the
> actual React + FastAPI implementation.

### Directory structure

```
backend/
  app/
    main.py                 # create_app(): routers, lifespan (expiry sweep), SPA mount, /api/healthz
    settings.py              # pydantic-settings; validated at import, crashes at boot on bad env
    api/
      auth.py                 # Authlib OIDC login/callback/logout, dev_login, /api/me, require_session/require_approver
      agent_tickets.py        # POST /api/agent/tickets, GET /{id}, POST .../execution/start|result
      tickets.py               # GET /api/tickets, GET /{id}, POST .../approve|reject|supersede
      deps.py                  # get_repo() dependency
      errors.py                # ServiceError/RepoError -> {"error": {"code","message"}}
      middleware.py            # access logging + sliding-window rate limit on /api/agent/*
    core/
      models.py                # Ticket, ActionDetails, Approval, Execution, Actor, AuditEvent, MUTABLE_FIELDS
      schemas.py                # request/response models (TicketCreateRequest, AgentPollResponse, ...)
      status_machine.py         # _ALLOWED transition table, can_transition/assert_transition
      canonical_json.py          # canonicalize() + parameters_hash()
      service.py                 # business rules; every mutation goes through here
    auth/
      agent_auth.py              # verify_agent(): presigned-STS SigV4 verification
      replay_cache.py             # in-memory TTL nonce cache
      rbac.py                     # groups claim / APPROVER_EMAILS -> approver|viewer
    repo/
      base.py                     # TicketRepository ABC + shared apply_event() fold
      jsonl_store.py                # MVP backend
      dynamodb_store.py             # documented stub
      s3_store.py                    # documented stub
      factory.py                     # get_repository() switches on STORE_BACKEND
    notifications/ses.py           # fire-and-forget SES email on TICKET_CREATED
    jobs/expiry.py                 # sweep_once()/run_expiry_loop(): TTL -> EXPIRED
  tests/                            # pytest, asyncio_mode=auto (backend/pyproject.toml)
frontend/
  src/
    App.tsx, main.tsx                # react-router shell, QueryClientProvider, Toaster
    routes/                          # login, dashboard, tickets, ticket-detail
    components/layout/                # sidebar, header
    components/tickets/                # ticket-table, ticket-columns, ticket-status-badge,
                                        # lineage-chain, audit-timeline, approve-reject-actions, supersede-dialog
    components/ui/                     # shadcn primitives
    lib/api.ts, lib/types.ts            # fetch wrapper (401 -> /login), TS mirrors of the Pydantic models
    index.css                           # portal HSL brand vars + success/warning/info
docs/agent-contract.md                # MCP-server integration contract
deploy/k8s/                           # namespace, deployment, service, ingress, serviceaccount, pvc, configmap, secret
scripts/agent_flow_demo.py             # live E2E demo: create -> poll -> start -> result
Dockerfile                             # node:20-alpine build stage -> python:3.12-slim runtime
```

### Data model (`backend/app/core/models.py`)

All API models inherit `ApiModel` (`alias_generator=to_camel, populate_by_name=True`), so the wire format is camelCase while Python stays snake_case.

```python
TicketStatus = Literal["PENDING_APPROVAL","APPROVED","REJECTED","DEPRECATED",
                        "EXPIRED","EXECUTING","COMPLETED","FAILED"]
TERMINAL_STATUSES = {"REJECTED","DEPRECATED","EXPIRED","COMPLETED","FAILED"}

class ActionDetails(ApiModel):
    service: Literal["ec2"]              # v1 scope
    operation: str                       # AWS API name, e.g. "RunInstances"
    region: str
    parameters: dict[str, Any]           # exact intended SDK params
    parameters_hash: str = ""            # sha256 of canonical JSON, computed BY THE GATE
    resource_arns: list[str] = []        # specific ARNs/ids targeted; empty only for pure-creation ops
    reason: str | None = None

class Approval(ApiModel):
    approved_by: str
    approved_at: datetime

class Execution(ApiModel):
    started_at: datetime
    finished_at: datetime | None = None
    outcome: Literal["success","failure"] | None = None
    message: str | None = None
    aws_request_ids: list[str] = []

class Actor(ApiModel):
    kind: Literal["agent","human","system"]
    id: str                              # IAM ARN | email | "gate"

class Ticket(ApiModel):
    ticket_id: str                       # ULID
    subject: str
    ticket_date: datetime                # set by the gate at creation
    status: TicketStatus
    planned_date: date
    planned_action: str                  # human-readable summary
    action_details: ActionDetails
    tags: dict[str, str] = {}            # management tags; filterable, propagated to AWS resources
    assignee: str                        # VERIFIED agent IAM ARN from STS — never client-supplied
    proposed_by: str                     # = assignee (agent-created) or human email (supersede)
    approvals: list[Approval] = []       # status -> APPROVED at REQUIRED_APPROVALS
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    lineage_root_id: str                 # first ticket in the chain (= own id if original)
    idempotency_key: str | None = None
    execution: Execution | None = None
    seq: int = 0                         # event count; optimistic-concurrency token (CAS)

# The only fields an AuditEvent fold may change. Everything else is frozen.
MUTABLE_FIELDS = frozenset({"status","approvals","rejected_by","rejected_at",
                             "rejection_reason","superseded_by","execution","seq"})

class AuditEvent(ApiModel):
    event_id: str; ticket_id: str; seq: int; timestamp: datetime
    type: Literal["TICKET_CREATED","APPROVAL_ADDED","APPROVED","REJECTED","DEPRECATED",
                  "EXPIRED","EXECUTION_STARTED","EXECUTION_COMPLETED","EXECUTION_FAILED"]
    actor: Actor
    from_status: TicketStatus | None = None
    to_status: TicketStatus | None = None
    details: dict[str, Any] | None = None
```

**Immutability**: every change is an appended `AuditEvent`; a ticket is a fold over its events (`repo/base.py:apply_event`). The fold only ever assigns `MUTABLE_FIELDS` — frozen fields (subject, `actionDetails`, `plannedDate`, tags, …) structurally cannot change; editing requires a superseding ticket.

### Transitions (`backend/app/core/status_machine.py:_ALLOWED`)

| From | To | Actor kind / guard |
|---|---|---|
| PENDING_APPROVAL | PENDING_APPROVAL (`APPROVAL_ADDED`) | human; approver ≠ proposer, not already approved by them, count < required |
| PENDING_APPROVAL | APPROVED (`APPROVED`) | human; same guard, approvals reach `REQUIRED_APPROVALS` |
| PENDING_APPROVAL | REJECTED | human; approver ≠ proposer, reason required (min 5 chars) |
| PENDING_APPROVAL / APPROVED | DEPRECATED | human, via supersede |
| PENDING_APPROVAL / APPROVED | EXPIRED | system sweep (`APPROVAL_TTL_HOURS`) |
| APPROVED | EXECUTING | agent; caller ARN == assignee, `parametersHash` echo matches |
| EXECUTING | COMPLETED / FAILED | agent; caller ARN == assignee |

Structural (from, to, actor-kind) rules live in `status_machine.py`; identity rules that need request context (approver ≠ proposer, caller ARN == assignee, etc.) live in `core/service.py`, which always calls `assert_transition` first.

### Repository (`backend/app/repo/base.py`)

```python
class TicketRepository(ABC):
    async def create_ticket(ticket: Ticket, created: AuditEvent) -> None            # DuplicateError on id/idempotency collision
    async def get_ticket(ticket_id: str) -> Ticket | None
    async def find_by_idempotency_key(assignee_arn: str, key: str) -> Ticket | None
    async def query_by_status(status: TicketStatus, limit=50, cursor=None) -> Page
    async def query_all(limit=50, cursor=None) -> Page
    async def query_lineage(lineage_root_id: str) -> list[Ticket]                    # oldest first
    async def list_audit_events(ticket_id: str) -> list[AuditEvent]
    async def append_event(ticket_id: str, expected_seq: int, event: AuditEvent) -> Ticket  # CAS -> ConflictError (409)
    async def transact_supersede(old_ticket_id, expected_seq, deprecated_event,
                                  new_ticket, created_event) -> None                  # atomic
```

Shaped like DynamoDB single-table access patterns so a backend swap is config-only:
- **jsonl** (MVP, `factory.py` default): one `AuditEvent` per line in `$DATA_DIR/tickets.jsonl`; boot-time fold into an in-memory dict + indexes (by-status, by-lineage-root, by-idempotency-key); single `asyncio.Lock` writer; fsync on approval/execution events; a torn final line is detected and skipped on replay; a torn `transact_supersede` pair (crash between the two writes) is repaired on boot by reverting the orphaned `DEPRECATED`.
- **dynamodb**: single table `PK=TICKET#id / SK=META|EVENT#seq`; GSI1 `STATUS#status/ticketDate`; GSI2 `LINEAGE#rootId`; GSI3 `IDEM#arn#key`. `append_event` → `TransactWriteItems` with `ConditionExpression seq = :expected`.
- **s3** (compliance/WORM): each `AuditEvent` is an immutable object `tickets/{id}/events/{seq:06d}.json` in a versioned, **Object Lock**-enabled bucket; CAS via conditional `PutObject` (`If-None-Match: *`); same fold code as jsonl. Optional middle ground: DynamoDB as the operational store, dual-write audit events to the Object Lock bucket (`AUDIT_MIRROR_S3_BUCKET`) for compliance-grade retention without S3's query limitations.

### Auth

**Human** (`backend/app/api/auth.py`, `backend/app/auth/rbac.py`) — Authlib server-side Authorization Code flow; the SPA never touches tokens. Provider built entirely from env (`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SCOPES`, `OIDC_GROUPS_CLAIM`) — migrating Cognito → Entra ID is an env-only change. `rbac.py` maps the groups claim → `approver|viewer` via `OIDC_APPROVER_GROUPS`, with an `APPROVER_EMAILS` allowlist fallback (SAML-sourced group claims can be weak/absent). Role is resolved at login and cached in the session cookie (role changes require re-login). `AUTH_MODE=dev` provides `GET /api/auth/login?email=&role=` for local dev; refused by `settings.py` when `ENV=production`.

**Agent** (`backend/app/auth/agent_auth.py:verify_agent`) — presigned `sts:GetCallerIdentity` (Vault / aws-iam-authenticator pattern), carried base64-encoded in `X-Gate-Identity`:
1. Decode the envelope; reject unless method/scheme/host match `POST https://sts(.<region>)?.amazonaws.com` and the body is exactly `Action=GetCallerIdentity&Version=2011-06-15`.
2. Reject unless `X-Amz-Date` is within ±300s of now.
3. Reject unless `X-Gate-Server-Id` header equals `GATE_SERVER_ID` **and** is present in the signature's `SignedHeaders` (binds the signature to this gate — a token captured for another service can't be replayed here).
4. Reject if `sha256(Signature)` is already in the in-memory replay cache (else add it).
5. Forward the request verbatim to STS via httpx (the gate needs no STS permission of its own); parse `Arn`/`Account` from the response.
6. Normalize an assumed-role ARN to its IAM role ARN and check both forms against `ALLOWED_AGENT_ARNS` glob patterns (`fnmatch`); 403 if neither matches.

The verified ARN becomes `assignee`/`proposedBy` on created tickets and must match on every subsequent agent call for that ticket.

### API surface

Agent (`X-Gate-Identity`, prefix `/api/agent/tickets`): `POST ""` (idempotent create via `Idempotency-Key` header; 201, or 200 on replay) · `GET /{id}` (poll; assignee-only; response includes `supersededBy` so a deprecated ticket's poller can follow the chain) · `POST /{id}/execution/start` (body `{parametersHash}`; 409 `HASH_MISMATCH` on drift; response echoes the approved `actionDetails` — the agent must execute exactly this, not local memory) · `POST /{id}/execution/result` (`{outcome, message?, awsRequestIds?}`).

Human (session cookie, prefix `/api/tickets`): `GET ""` (filter by `status`/`assignee`/`tag=key=value`, cursor pagination) · `GET /{id}` (`{ticket, lineage[], auditEvents[]}`) · `POST /{id}/approve` (approver role; rejects proposer/duplicate approver; CAS on `seq` → 409 on a concurrent-approval race) · `POST /{id}/reject` (`{reason}`, min 5 chars) · `POST /{id}/supersede` (body = a new `TicketCreateRequest`; atomically deprecates the old ticket and creates the successor with `supersedes`/`lineageRootId` set and `proposedBy` = the editing human — who therefore can't also approve their own superseding ticket). Plus `GET /api/me` and public `GET /api/healthz`.

Every mutation is Pydantic-validated and appends its `AuditEvent` through `service.py` → `repo.append_event`/`transact_supersede`; errors surface as `{"error": {"code", "message"}}` via `api/errors.py`.

### Notifications & expiry

`notifications/ses.py:notify_ticket_created` — fire-and-forget (`asyncio.create_task`, boto3 SESv2 via `to_thread`), never raises into the request path; gated by `NOTIFY_ON_CREATE`/`SES_FROM_ADDRESS`/`SES_REGION`.

`jobs/expiry.py` — runs every 10 minutes from the FastAPI `lifespan`. `APPROVED` tickets expire `APPROVAL_TTL_HOURS` after their **last approval**; `PENDING_APPROVAL` tickets expire that many hours after `ticketDate`. `EXECUTING` and terminal tickets are never swept. A `ConflictError` from a concurrent transition is swallowed (the ticket moved on its own before the sweep reached it).

### UI (mirrors `gammon-powershell-portal`)

Brand tokens copied into `frontend/src/index.css` (navy `--primary: 217 49% 36%`, red `--secondary: 0 85% 49%`, `.section-title`) plus added `--success/--warning/--info`. Pages: dashboard (status-count cards + recent tickets), tickets (Active/History tabs, tag filter, 10s polling on active), ticket detail (lineage chain, hash-locked parameters view, audit timeline, approve/reject with confirm dialogs, supersede via react-hook-form + zod). TanStack Query `refetchInterval` stops once a ticket/list only contains terminal statuses. Approve/reject controls self-hide for the proposer/a duplicate approver/a viewer as a UX nicety — the server enforces all of it regardless (`tickets.py`, `service.py`).

### MCP-server contract (`docs/agent-contract.md`)

1. Build exact `actionDetails` (including `resourceArns` for every targeted resource and management `tags`) → `POST /api/agent/tickets` with an `Idempotency-Key`; surface the ticket URL to the operator. Read-only `Describe*` calls bypass the gate entirely.
2. Poll every 15–30s (jittered) until `APPROVED` (proceed), `REJECTED`/`EXPIRED` (stop, surface the reason), or `DEPRECATED` (follow `supersededBy` and re-confirm).
3. `execution/start` echoing `parametersHash`; execute using the `actionDetails` from *that* response; propagate `tags` + a `gateTicketId=<ticketId>` tag onto the AWS resources where the operation supports tagging.
4. Report the result with AWS RequestIds via `execution/result`.
5. Recommended hard enforcement (owned by the agent-role admin, not this gate): an IAM condition requiring `aws:RequestTag/gateTicketId` on mutating EC2 actions so an ungated call fails at IAM regardless of what the gate says; scope the agent role's `Resource`/ABAC condition to the ticket's `resourceArns`.

### Deployment specifics

`Dockerfile` — stage 1 `node:20-alpine` (`npm ci && vite build`); stage 2 `python:3.12-slim`, deps installed from `backend/requirements.txt` (cached layer, see Decision log), non-root `1001:1001`, `uvicorn app.main:app` on `:8000`.

`deploy/k8s/deployment.yaml` — `replicas: 1` with `strategy: Recreate` is **required** while `STORE_BACKEND=jsonl` (RWO PVC, single-writer store, in-memory replay cache); scale out only after moving to DynamoDB + a shared replay cache. `runAsNonRoot`/`fsGroup: 1001`, readiness/liveness on `/api/healthz`, env from a ConfigMap + Secret. Other manifests: `namespace.yaml`, `pvc.yaml` (1Gi RWO), `serviceaccount.yaml` (IRSA annotation), `service.yaml` (ClusterIP), `ingress.yaml` (ALB, TLS mandatory — SigV4 identity headers must never traverse plaintext), `secret.yaml` (template only; recommend External/Sealed Secrets for `SESSION_SECRET`/`OIDC_CLIENT_SECRET`).

