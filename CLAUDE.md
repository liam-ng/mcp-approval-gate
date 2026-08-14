# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **blocking approval gate**: an AI agent (AWS MCP server) must create a change-request ticket, wait for human approval in a web portal, execute exactly the approved parameters, and report the result, before it's allowed to mutate EC2. Tickets are immutable — editing a submitted ticket creates a *superseding* ticket and marks the original `DEPRECATED`, preserving full lineage for audit.

Three deployables: `backend/Dockerfile` (FastAPI, serves `/api/*`, `/mcp`, `/.well-known`), `frontend/Dockerfile` (Vite build served by nginx), and `executor/Dockerfile` (headless poller that performs approved tickets — serves no port, ships alongside the upstream `aws-api-mcp-server` in one pod). Each has its own GitHub Actions workflow and `pipelines/<component>/ci.sh`. Only the first two are behind the HTTPRoute. An **HTTPRoute** (Gateway API on F5 NGINX Gateway Fabric — ingress-nginx is retired) splits traffic by path — both pods sit behind one host, so the browser sees a single origin and the session cookie works with no CORS setup. `backend/app/main.py:_mount_spa` still exists and early-returns when `app/static` is absent (which it is in the backend image), so it's now only a local-dev convenience: `npm run build` then `uvicorn` still serves the SPA from one process without needing two containers.

`docs/plan.md` is the living design doc — checkboxes and a dated decision log are updated at the end of every phase/change. Read it for the full rationale behind anything below. (There is also a stale root-level `plan.md` from the very first commit — `docs/plan.md` is the one that's kept current.)

## Commands

Backend (Python 3.14, from `backend/`):
```bash
pip install -e ".[dev]"
cp ../.env.example .env
uvicorn app.main:app --reload              # http://localhost:8000

python -m pytest                           # full suite — MUST run from backend/
python -m pytest tests/test_status_machine.py            # one file
python -m pytest tests/test_agent_api.py -k hash_mismatch # one test by name
```
`asyncio_mode = "auto"` is configured in `backend/pyproject.toml`; running pytest from the repo root instead of `backend/` makes async tests fail.

`backend/tests/conftest.py` sets `ENV_FILE` to a nonexistent path before anything imports `app.settings`, so **every test must declare the settings it needs itself**. That is deliberate: `Settings.model_config` resolves its env file once at class-definition time, so a local `backend/.env.liam-dev` would otherwise supply mandatory values (`ALLOWED_AGENT_ARNS` is the usual one) that CI — which checks out no env file — does not have. Without this, a test that forgets a setting passes locally and fails in CI.

Frontend (from `frontend/`):
```bash
npm install
npm run dev          # http://localhost:5173, proxies /api -> :8000 (vite.config.ts)
npm run build         # tsc --noEmit && vite build — this is the frontend "check"
```

Dev-mode login (no IdP needed, `AUTH_MODE=dev`, refused when `ENV=production`):
`GET /api/auth/login?email=you@x.com&role=approver`

Docker:
```bash
docker build -t REGISTRY/mcp-approval-gate-backend:TAG ./backend
docker build -t REGISTRY/mcp-approval-gate-frontend:TAG ./frontend
docker build -t REGISTRY/mcp-approval-gate-executor:TAG ./executor
```

k8s manifests are **not in this repo** — see the Deployment section below. From a
clone of the manifest repo:
```bash
kubectl kustomize apps/mcp-approval-gate/overlays/liam-dev | kubectl diff -f -   # drift check
kubectl kustomize apps/mcp-approval-gate/overlays/template | kubectl apply --dry-run=client -f -
```

Environment note: in this WSL sandbox, Node is only on PATH after `export PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH"`, and `pip install` needs `--break-system-packages`.

## Architecture

### Domain core (`backend/app/core/`)
- `models.py` — Pydantic v2 models. `Ticket`, `ActionDetails`, `Approval`, `Execution`, `AuditEvent`. All API-facing models use `alias_generator=to_camel` so wire format is camelCase while Python stays snake_case. `MUTABLE_FIELDS` is the frozenset of the only fields an event is allowed to change (`status`, `approvals`, `rejected_*`, `superseded_by`, `execution`, `seq`, `tags`) — subject/actionDetails/plannedDate etc. are frozen forever after creation. `tags` is deliberately mutable (via a `TAGS_UPDATED` event, `service.update_tags`) because tags are metadata, not part of `ActionDetails`/`parametersHash` — changing them never requires a superseding ticket or fresh approval.
- `status_machine.py` — single source of truth for legal `(from_status, to_status)` transitions and which actor kind may perform them.
- `canonical_json.py` — deterministic JSON serialization (sorted keys, no NaN) used to compute `parametersHash`; the agent must echo this hash back at `execution/start`, so any parameter drift between ticket-approval-time and execution-time is rejected with 409.
- `aws_schema.py` — validates a proposed call against **botocore's own service models** at creation time (`build_ticket`, so all creation paths get it), rejecting unknown operations/parameters, wrong types and missing required members with 422 `INVALID_ACTION_PARAMETERS` before an approver is ever asked. Reads botocore's local JSON only — **never construct a boto3 client here**, that starts credential resolution; the one place in the backend that may build a client is `app/aws/`. Known limit: the models can't express conditional requirements, which is what `aws_conditional.py` exists for.
- `aws_conditional.py` — the hand-curated second layer for the requirements botocore's flat `required` list cannot state (`RunInstances` needs `ImageId` *unless* `LaunchTemplate`). Rules are **data, not `if`s**, because three consumers read the same table: `build_ticket` (422), the `describe_operation_parameters` MCP tool, and the portal form's field rendering. Keep it short — a false *reject* blocks a legitimate change with no operator override, while a false *accept* costs one approval and gets a clear error from AWS; the docstring lists the rules deliberately left out and why. It also owns `TAGGABLE_ON_CREATE`/`with_gate_tags`, which inject `TagSpecifications` carrying `gateTicketId` into `parameters` at creation — **before** `parameters_hash`, so the tag IAM and the SCP both demand is covered by the approved hash, visible to the approver, and the executor stays byte-verbatim. That map mirrors the inline policy exactly: an op missing from it is denied at AWS for lacking the tag, an op wrongly added to it is denied for tagging outside `TagOnCreateOnly`'s `ec2:CreateAction` list.
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
- `backend/app/api/tickets.py` — session-only routes: create (the portal form), list/filter, detail (ticket + lineage + audit events), approve, reject, supersede. Portal-created tickets go through `service.create_human_ticket`, the same function the `/mcp` path uses — identical trust shape (human proposes, `MCP_EXECUTOR_ARN` is the assignee), differing only in how the person authenticated. The route is **not** gated on `MCP_ENABLED`, which governs IDE access and has nothing to do with a signed-in human using the portal; it does require an executor ARN, or it 503s rather than opening a ticket nothing would execute.
- `backend/app/api/aws_meta.py` — credential-free EC2 parameter metadata for the create form (same payload as the MCP describe tool, so the form and an agent are never told different things). `backend/app/api/aws_discovery.py` + `backend/app/aws/` — the *optional* read-only account lookups behind the form's subnet/AMI/security-group pickers, off unless `AWS_DISCOVERY_ENABLED`. Keep the two apart: one describes the AWS API from local files, the other reads the account and needs credentials.
- `backend/app/api/mcp_gateway.py` — `build_mcp_app()`: the `/mcp` Streamable HTTP route (official `mcp` SDK's `MCPServer`), tools `create_change_ticket` / `check_ticket_status` / `supersede_change_ticket` / `close_ticket` / `describe_operation_parameters`. Mounted at the ASGI level in `main.py`, not via `app.include_router` — see the comment there for why (the SDK's own auth middleware and `main.py`'s SPA catch-all route can't cleanly nest inside the same FastAPI router).
- `backend/app/api/errors.py` — maps the `ServiceError`/`RepoError` hierarchy to `{"error": {"code","message"}}`.
- `backend/app/api/middleware.py` — access logging + a dependency-free sliding-window rate limiter on `/api/agent/*`.
- `backend/app/jobs/expiry.py` — background sweep (started from `main.py`'s lifespan) that expires stale `PENDING_APPROVAL`/`APPROVED` tickets per `APPROVAL_TTL_HOURS`.
- `backend/app/notifications/ses.py` — fire-and-forget SES email on ticket creation; never raises into the request path.

### Frontend (`frontend/src/`)
React 19 + Vite + TypeScript strict, shadcn/ui + Tailwind, brand tokens mirrored from the internal `gammon-powershell-portal` project. `lib/api.ts` is a typed fetch wrapper that redirects to `/login` on 401. TanStack Query drives polling on ticket lists/detail (`refetchInterval`), which stops once a ticket reaches a terminal status. `routes/` are the pages (dashboard, tickets list, ticket detail, login); `components/tickets/` holds the ticket-specific UI (status badge, lineage chain, audit timeline, approve/reject with confirm dialogs, supersede dialog via react-hook-form + zod); `components/ui/` are the shadcn primitives.

### Settings (`backend/app/settings.py`)
Everything is `pydantic-settings`, validated at import time — invalid/missing env crashes at boot rather than on first request. Cross-field checks enforce e.g. `AUTH_MODE=dev` never in `ENV=production`, `STORE_BACKEND=dynamodb` requires `DYNAMODB_TABLE`, `STORE_BACKEND=s3` requires `S3_BUCKET`. `.env.example` documents every scenario (dev vs oidc, each OIDC provider, each store backend) as commented-out alternatives — keep it in sync when adding new env vars.

### Deployment — manifests live in another repo
K8s manifests are **not here**. They live in [`liam-ng/liam-dev-k8s-argoCD`](https://github.com/liam-ng/liam-dev-k8s-argoCD) (private, cluster-wide config for the WSL cluster) under `apps/mcp-approval-gate/`, as kustomize `base/` + `overlays/liam-dev` (the real deployed config) + `overlays/template` (production-shaped placeholders, deployed nowhere, no Argo Application). Argo CD reconciles the liam-dev overlay; `kubectl apply` is no longer the deploy path. Only `deploy/iam/` and `deploy/scp/` (AWS JSON policy documents, which nothing reconciles) stayed in this repo.

The split, and why each piece is where it is: `base/` holds the two Deployments + Services, the executor pod, the PVC, the ServiceAccount, the HTTPRoute's *rules*, the AuthorizationPolicy, and the eight environment-neutral NetworkPolicies. Each overlay owns only what genuinely differs — config values, secret source (ESO vs a template Secret), mTLS mode, the HTTPRoute's *attachment* (parentRefs/hostnames), and the gateway-ingress/kubelet-probe NetworkPolicies. Images come from the `images:` transformer, which is what CI rewrites per-sha.

Invariants that survived the move: the backend Deployment's selector is deliberately the bare `app: mcp-approval-gate` label — selectors are immutable, and `authorizationpolicy.yaml`'s NetworkPolicy grants upstream-MCP access on exactly that label, so the frontend's distinct label correctly excludes it. The route attaches to a **shared, platform-owned Gateway** — never add a Gateway: NGF provisions one NGINX data plane Deployment + Service per Gateway resource, so a per-app Gateway means a second data plane and a second load balancer. On liam-dev that Gateway is `localwsl` in `nginx-gateway`, Helm-managed by the `ngf` release, and it runs **hostNetwork** — which is why that overlay's gateway-ingress policies must use `ipBlock` (a hostNetwork pod has no pod IP, so no selector matches it) while the template's use selectors. Neither is a simplification of the other. `httproute.yaml`'s three backend prefixes mirror `frontend/vite.config.ts`'s dev proxy — that coupling now spans two repos, so change both.

NetworkPolicy traps the files document: it matches the **pod** port (the frontend is Service 80 → pod 8080, so a rule saying `80` denies everything), a named port no container declares resolves to nothing and denies everything the same way, and default-deny Egress silently breaks DNS, sidecar→istiod, and kubelet probes unless each is allowed by name. **The cluster now runs Calico, not flannel** — these policies are enforced. They were written when flannel made them inert documentation; do not reason from that.

## Key invariants to preserve

- Never mutate a `Ticket` field outside `apply_event`/`MUTABLE_FIELDS` — that's the whole immutability guarantee.
- Never let the human-auth, agent-auth, and MCP/OAuth code paths depend on each other.
- **The gate needs no AWS permissions** — with one scoped, opt-in exception. `app/aws/` is the only package allowed to build a boto3 client; it is read-only, off by default, and runs under a *separate* Describe-only role that must never be the executor's. If `boto3.client(` appears anywhere else in `backend/app/`, that's a bug. The invariant's real content is that the approval and verification path holds no AWS trust: SigV4 agent verification still forwards to STS with no credentials of its own, and parameter validation still reads local JSON. Neither may change.
- `parametersHash` must be computed by the gate (not trusted from the agent) and echoed back at execution start.
- `aws:RequestTag/gateTicketId` only exists on resource-*creating* EC2 calls (those with a `TagSpecifications` param) — never apply it as a deny-if-absent condition to actions on existing resources (`StopInstances`, `TerminateInstances`, ...), or it silently blocks the legitimate executor too. See `docs/agent-contract.md`'s "Strong enforcement" section.
- Keep `.env.example` and `docs/plan.md`'s decision log updated when settings or architecture change.
