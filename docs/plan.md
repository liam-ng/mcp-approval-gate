# mcp-approval-gate — Implementation Plan (living document)

> This is the working copy of the project plan. It is updated at the end of every
> phase: checkboxes below track progress, and any decisions or deviations are
> recorded in the **Decision log** at the bottom.

## Progress

- [x] Phase 1 — Scaffold + domain core (models, status machine, canonical JSON)
- [x] Phase 2 — Repository layer (ABC + JSONL store + factory)
- [x] Phase 3 — Agent API (SigV4 auth + create/poll/start/result routes)
- [x] Phase 4 — Human auth + API (OIDC, RBAC, ticket routes, SES notifier)
- [ ] Phase 5 — Frontend (React SPA mirroring gammon-powershell-portal UI)
- [ ] Phase 6 — Packaging + docs (Dockerfile, k8s manifests, agent contract)
- [ ] Phase 7 — Hardening (expiry sweep, structured logging, rate limiting)

## Context

An AI agent (AWS MCP server, running on the existing k8s cluster) can view/create/manage EC2. Today its changes lack auditability and human control. This project builds a **blocking approval gate**: before any mutating EC2 action, the agent must create a change-request ticket, wait for human approval in a web portal, execute only the approved parameters, and report the result. Tickets are immutable — edits create a superseding ticket and mark the old one Deprecated — giving a tamper-evident audit chain.

UI mirrors the conventions of the internal `gammon-powershell-portal` project (shadcn/ui + Tailwind HSL brand tokens, DataTable + approval-list patterns), while fixing its known anti-patterns.

## Confirmed decisions

| Decision | Choice |
|---|---|
| Tech stack | **React SPA (Vite) frontend + FastAPI (Python) backend**, single container |
| Enforcement | Blocking gate: create → poll → execute-on-Approved → report |
| Human auth | OIDC vs IAM Identity Center now; **provider-agnostic** so Azure AD/Entra ID is a config-only swap later |
| Agent auth | IAM SigV4 (presigned `sts:GetCallerIdentity` pattern); fully separate middleware path from human auth |
| Storage | MVP: local JSONL on PVC (single replica), behind a DynamoDB-shaped repository interface; production swap via `STORE_BACKEND` env to **DynamoDB** or **S3 with Object Lock** (WORM audit trail) |
| Ticket scoping | Tickets carry **management tags** (`tags`) and **resource-ARN scope** (`resourceArns`) so approvals are scoped to specific EC2 resources and taggable for cost/team management |
| Approvals required | Env-configurable `REQUIRED_APPROVALS=1\|2`; approvers distinct and never the proposer |
| Notifications | SES email in MVP — email approvers on ticket creation |
| Expiry | TTL-based `APPROVAL_TTL_HOURS` (default 72); stale Pending/Approved → EXPIRED |

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

- **Human**: Authorization Code flow handled entirely by FastAPI (Authlib); provider built only from env (`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SCOPES`, `OIDC_GROUPS_CLAIM`). Entra ID swap = env change only. RBAC: groups claim → `approver|viewer` via `OIDC_APPROVER_GROUPS`, with `APPROVER_EMAILS` fallback (IAM Identity Center group claims are weak).
- **Agent**: presigned `sts:GetCallerIdentity` (Vault / aws-iam-authenticator pattern) in `X-Gate-Identity` header; must sign `X-Gate-Server-Id: $GATE_SERVER_ID`; ±5-min date window; replay nonce cache; gate forwards to STS via httpx (gate needs no STS permission); caller ARN checked against `ALLOWED_AGENT_ARNS` globs; verified ARN becomes `assignee`/`proposedBy`.

### API surface

Agent (SigV4): `POST /api/agent/tickets` (idempotent create) · `GET /api/agent/tickets/{id}` (poll; follows `supersededBy`) · `POST .../execution/start` (hash echo, returns approved actionDetails) · `POST .../execution/result`.

Human (session): `GET /api/tickets` · `GET /api/tickets/{id}` (`{ticket, lineage, auditEvents}`) · `POST .../approve` · `POST .../reject` (reason required) · `POST .../supersede` (atomic new+deprecate) · `GET /api/me` · `GET /api/healthz` (public).

### MCP-server contract (docs/agent-contract.md)

create (with `resourceArns` + `tags`) → poll 15–30 s → on APPROVED: `execution/start` echoing hash → execute the returned actionDetails, propagating `tags` + `gateTicketId` tag where supported → report result with AWS RequestIds. Strong enforcement at IAM: require `aws:RequestTag/gateTicketId` on mutating EC2 actions; scope the agent role by the approved `resourceArns`.

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
- IAM Identity Center: confirm a customer-managed OIDC app can be registered; group claims limited → `APPROVER_EMAILS` fallback.
- Not in v1: quorum >2, CSV export/retention, CloudTrail correlation job, time-zone preference.

## Decision log

- 2026-08-02 — Plan approved. Stack: React (Vite) + FastAPI (user preference; boto3/Pydantic fit, MCP ecosystem is Python). S3 Object Lock added as storage option; tags + resourceArns added to ticket model.
- 2026-08-02 — Phases 3–4 done. Agent auth rejects pre-STS on envelope problems (wrong host, unsigned server-id, stale date, replay) so STS is only called for plausible requests. Role is resolved at login and stored in the session (role changes need re-login). AUTH_MODE=dev provides local fake login; settings validation refuses it in production. `/api/me`, list filters (status/assignee/tag), audit trail verified by tests (53 passing).
- 2026-08-02 — Phases 1–2 done. Fold lives in `repo/base.py:apply_event` and only touches `MUTABLE_FIELDS`, making immutability structural. JSONL store repairs a torn supersede pair on boot by reverting the orphaned DEPRECATED (same rule the future S3 store needs). Approval events: `APPROVAL_ADDED` below threshold, `APPROVED` at threshold; both carry `details.approval`.
