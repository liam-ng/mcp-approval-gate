# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **blocking approval gate**: an AI agent (AWS MCP server) must create a change-request ticket, wait for human approval in a web portal, execute exactly the approved parameters, and report the result, before it's allowed to mutate EC2. Tickets are immutable — editing a submitted ticket creates a *superseding* ticket and marks the original `DEPRECATED`, preserving full lineage for audit.

Two deployables: `backend/Dockerfile` (FastAPI, serves `/api/*`, `/mcp`, `/.well-known`) and `frontend/Dockerfile` (Vite build served by nginx). An **HTTPRoute** (Gateway API on F5 NGINX Gateway Fabric — ingress-nginx is retired) splits traffic by path — both pods sit behind one host, so the browser sees a single origin and the session cookie works with no CORS setup. `backend/app/main.py:_mount_spa` still exists and early-returns when `app/static` is absent (which it is in the backend image), so it's now only a local-dev convenience: `npm run build` then `uvicorn` still serves the SPA from one process without needing two containers.

`docs/plan.md` is the living design doc — checkboxes and a dated decision log are updated at the end of every phase/change. Read it for the full rationale behind anything below. (There is also a stale root-level `plan.md` from the very first commit — `docs/plan.md` is the one that's kept current.)

## Commands

Backend (Python 3.12, from `backend/`):
```bash
pip install -e ".[dev]"
cp ../.env.example .env
uvicorn app.main:app --reload              # http://localhost:8000

python -m pytest                           # full suite — MUST run from backend/
python -m pytest tests/test_status_machine.py            # one file
python -m pytest tests/test_agent_api.py -k hash_mismatch # one test by name
```
`asyncio_mode = "auto"` is configured in `backend/pyproject.toml`; running pytest from the repo root instead of `backend/` makes async tests fail.

Frontend (from `frontend/`):
```bash
npm install
npm run dev          # http://localhost:5173, proxies /api -> :8000 (vite.config.ts)
npm run build         # tsc --noEmit && vite build — this is the frontend "check"
```

Dev-mode login (no IdP needed, `AUTH_MODE=dev`, refused when `ENV=production`):
`GET /api/auth/login?email=you@x.com&role=approver`

Docker / k8s:
```bash
docker build -t REGISTRY/mcp-approval-gate-backend:TAG ./backend
docker build -t REGISTRY/mcp-approval-gate-frontend:TAG ./frontend
kubectl apply --dry-run=client -f deploy/k8s/
```

Environment note: in this WSL sandbox, Node is only on PATH after `export PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH"`, and `pip install` needs `--break-system-packages`.

## Architecture

### Domain core (`backend/app/core/`)
- `models.py` — Pydantic v2 models. `Ticket`, `ActionDetails`, `Approval`, `Execution`, `AuditEvent`. All API-facing models use `alias_generator=to_camel` so wire format is camelCase while Python stays snake_case. `MUTABLE_FIELDS` is the frozenset of the only fields an event is allowed to change (`status`, `approvals`, `rejected_*`, `superseded_by`, `execution`, `seq`, `tags`) — subject/actionDetails/plannedDate etc. are frozen forever after creation. `tags` is deliberately mutable (via a `TAGS_UPDATED` event, `service.update_tags`) because tags are metadata, not part of `ActionDetails`/`parametersHash` — changing them never requires a superseding ticket or fresh approval.
- `status_machine.py` — single source of truth for legal `(from_status, to_status)` transitions and which actor kind may perform them.
- `canonical_json.py` — deterministic JSON serialization (sorted keys, no NaN) used to compute `parametersHash`; the agent must echo this hash back at `execution/start`, so any parameter drift between ticket-approval-time and execution-time is rejected with 409.
- `service.py` — business rules layered over the repo + status machine (approver ≠ proposer, no duplicate approvals, hash-mismatch handling, idempotent agent-side create, supersede semantics).

**Tickets are event-sourced.** A ticket is a fold over its `AuditEvent` log. `apply_event()` in `backend/app/repo/base.py` is the *one* fold implementation shared by every storage backend, and it only ever touches `MUTABLE_FIELDS` — that's what makes immutability structural rather than convention. Any new mutation must go through `repo.append_event`, never direct field assignment.

### Repository layer (`backend/app/repo/`)
`base.py` defines `TicketRepository` (ABC) deliberately shaped like DynamoDB single-table access patterns (get-by-id, query-by-status, lineage query, CAS append via expected `seq`) so swapping backends is a config change, not a rewrite. `factory.py` picks the implementation from `STORE_BACKEND` (`lru_cache`d singleton):
- `jsonl` (MVP default) — append-only log on the PVC, folded into memory at boot, single `asyncio.Lock` writer, fsync on approval/execution events, torn-final-line recovery.
- `dynamodb` — `dynamodb_store.py`, currently a documented stub (`PK=TICKET#id / SK=META|EVENT#seq`, GSI1 status, GSI2 lineage, GSI3 idempotency; `append_event` via `TransactWriteItems` conditional on `seq`).
- `s3` — `s3_store.py`, currently a documented stub (Object Lock / WORM, one immutable object per event, CAS via conditional `PutObject`).

### Three independent auth paths — do not couple them
- **Human** (`backend/app/api/auth.py`, `backend/app/auth/rbac.py`): server-side OIDC Authorization Code flow via Authlib, httpOnly session cookie. The provider is built entirely from env (`OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET`/`OIDC_SCOPES`/`OIDC_GROUPS_CLAIM`) so swapping providers is a config-only change — never hardcode a provider-specific assumption here. On AWS, `OIDC_ISSUER` points at a **Cognito User Pool**, not IAM Identity Center directly — Identity Center's own OIDC service only issues public clients (no `client_secret`), so Identity Center is instead added as a SAML 2.0 application behind Cognito, which re-issues standard OIDC JWTs (see `.env.example`'s AWS scenario block and `docs/plan.md`'s 2026-08-03 decision log entry). Azure AD/Entra ID is a direct, native OIDC swap with no such indirection. `APPROVER_EMAILS` is a fallback allowlist because SAML-sourced group claims can be weak/absent.
- **Agent** (`backend/app/auth/agent_auth.py`, `replay_cache.py`): verifies a presigned `sts:GetCallerIdentity` request (Vault/aws-iam-authenticator pattern) carried in the `X-Gate-Identity` header — validates host/body/date window, checks `X-Gate-Server-Id` is in `SignedHeaders`, checks a replay cache, forwards to STS, and matches the resulting ARN against `ALLOWED_AGENT_ARNS` globs. The gate itself needs **no** AWS permissions to do this verification. This path must stay untouched by any future human-IdP migration.
- **IDE/MCP** (`backend/app/api/mcp_gateway.py`, `backend/app/auth/mcp_token_verifier.py`): OAuth 2.1 Resource Server for Cursor/VS Code, gated by `MCP_ENABLED`. The gate is never in the browser-redirect/token-exchange path — it only verifies the bearer token's signature against the OIDC IdP's JWKS. See `docs/mcp-gateway.md`.

### API surface
- `backend/app/api/agent_tickets.py` — SigV4-only routes: create (idempotent), list (caller's own tickets, e.g. to discover ones an MCP tool call created), poll, `execution/start` (hash echo), `execution/result`.
- `backend/app/api/tickets.py` — session-only routes: list/filter, detail (ticket + lineage + audit events), approve, reject, supersede.
- `backend/app/api/mcp_gateway.py` — `build_mcp_app()`: the `/mcp` Streamable HTTP route (official `mcp` SDK's `MCPServer`), tools `create_change_ticket` / `check_ticket_status` / `supersede_change_ticket` / `close_ticket`. Mounted at the ASGI level in `main.py`, not via `app.include_router` — see the comment there for why (the SDK's own auth middleware and `main.py`'s SPA catch-all route can't cleanly nest inside the same FastAPI router).
- `backend/app/api/errors.py` — maps the `ServiceError`/`RepoError` hierarchy to `{"error": {"code","message"}}`.
- `backend/app/api/middleware.py` — access logging + a dependency-free sliding-window rate limiter on `/api/agent/*`.
- `backend/app/jobs/expiry.py` — background sweep (started from `main.py`'s lifespan) that expires stale `PENDING_APPROVAL`/`APPROVED` tickets per `APPROVAL_TTL_HOURS`.
- `backend/app/notifications/ses.py` — fire-and-forget SES email on ticket creation; never raises into the request path.

### Frontend (`frontend/src/`)
React 19 + Vite + TypeScript strict, shadcn/ui + Tailwind, brand tokens mirrored from the internal `gammon-powershell-portal` project. `lib/api.ts` is a typed fetch wrapper that redirects to `/login` on 401. TanStack Query drives polling on ticket lists/detail (`refetchInterval`), which stops once a ticket reaches a terminal status. `routes/` are the pages (dashboard, tickets list, ticket detail, login); `components/tickets/` holds the ticket-specific UI (status badge, lineage chain, audit timeline, approve/reject with confirm dialogs, supersede dialog via react-hook-form + zod); `components/ui/` are the shadcn primitives.

### Settings (`backend/app/settings.py`)
Everything is `pydantic-settings`, validated at import time — invalid/missing env crashes at boot rather than on first request. Cross-field checks enforce e.g. `AUTH_MODE=dev` never in `ENV=production`, `STORE_BACKEND=dynamodb` requires `DYNAMODB_TABLE`, `STORE_BACKEND=s3` requires `S3_BUCKET`. `.env.example` documents every scenario (dev vs oidc, each OIDC provider, each store backend) as commented-out alternatives — keep it in sync when adding new env vars.

### Deployment (`deploy/k8s/`)
Split into `backend-deployment.yaml` / `backend-service.yaml` and `frontend-deployment.yaml` / `frontend-service.yaml`, with `httproute.yaml` routing `/api`, `/mcp`, `/.well-known` to the backend and everything else to the frontend (that prefix list mirrors `frontend/vite.config.ts`'s dev proxy — keep them in sync). Backend: single replica, `strategy: Recreate` (RWO PVC + single-writer JSONL store), IRSA ServiceAccount, `/api/healthz` probes. Frontend: stateless, `RollingUpdate`, no ServiceAccount and no secrets. The route attaches to a **shared, platform-owned Gateway** — never add a Gateway here: NGF provisions one NGINX data plane Deployment + Service per Gateway resource, so a per-app Gateway means a second data plane and a second load balancer. Attach only to a TLS listener (SigV4 identity headers must never traverse plaintext); the HTTP→HTTPS redirect belongs to whoever owns the Gateway. Locally (`deploy/k8s/liam-dev/`) that shared Gateway is `localwsl` in the `nginx-gateway` namespace, Helm-managed by the `ngf` release — its `http` listener sets no `hostname` and allows routes `from: All`, so adding a host needs no Gateway edit (which is just as well, since `helm upgrade` would clobber one). The backend Deployment's selector is deliberately the bare `app: mcp-approval-gate` label — selectors are immutable, and `istio-authorizationpolicy.yaml`'s NetworkPolicy grants upstream-MCP access on exactly that label, so the frontend's distinct label correctly excludes it. `istio-authorizationpolicy.yaml` + `deploy/scp/` isolate the upstream AWS API MCP server so this gate is the only legitimate caller — see `docs/mcp-gateway.md`.

## Key invariants to preserve

- Never mutate a `Ticket` field outside `apply_event`/`MUTABLE_FIELDS` — that's the whole immutability guarantee.
- Never let the human-auth, agent-auth, and MCP/OAuth code paths depend on each other.
- `parametersHash` must be computed by the gate (not trusted from the agent) and echoed back at execution start.
- `aws:RequestTag/gateTicketId` only exists on resource-*creating* EC2 calls (those with a `TagSpecifications` param) — never apply it as a deny-if-absent condition to actions on existing resources (`StopInstances`, `TerminateInstances`, ...), or it silently blocks the legitimate executor too. See `docs/agent-contract.md`'s "Strong enforcement" section.
- Keep `.env.example` and `docs/plan.md`'s decision log updated when settings or architecture change.
