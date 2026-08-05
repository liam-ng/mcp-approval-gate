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

- [x] Create a Github action pipeline for frontend and backend to scan code using sonarqube, hard gate for critical CWE, build docker image, scan image using Trivy with critical CVE hard gate, perform simple app start test and unit test, push to Azure contain registry using managed identity, output. Done: `pipelines/{lib.sh,backend/ci.sh,frontend/ci.sh}` hold the logic; `.github/workflows/{backend,frontend}.yml` are thin wrappers with path filters so each component builds independently. Two deviations from the wording, both forced: (1) **workflows cannot live in `pipelines/`** — GitHub Actions only executes from `.github/workflows/`, so the scripts live in `pipelines/` and the YAML calls them; (2) **"managed identity" is not available to GitHub-hosted runners** — managed identity is fetched from Azure IMDS and only exists on Azure-hosted compute, so this uses **workload identity federation** (`azure/login` + `id-token: write`), which is the secretless equivalent and avoids a stored service-principal credential.
- [ ] Fix the 10 pre-existing backend test failures so the CI test gate can go green. Confirmed *not* environmental (reproduced on a clean `main` via `git stash`): 9 in `tests/test_mcp_gateway.py` (`KeyError: 'result'` / `403 != 200`) plus `test_human_api.py::test_list_filters` (asserts `owner=liam.ng` but `build_ticket` sets the `owner` default tag from `proposed_by`, which is the agent ARN for agent-seeded tickets). `requirements.txt` pins `mcp>=2.0.0,<3` and 2.0.0 is installed locally, so CI resolves the same or newer and will hit the same failures — **`.github/workflows/backend.yml` will be red on its first run until this is fixed.**
- [x] Address open risk: ship the gate itself as an MCP tool (not a fork of the official AWS MCP server) so end users add it directly to Cursor/VS Code. Done: `/mcp` Streamable HTTP route (`backend/app/api/mcp_gateway.py`), OAuth2.1 Resource Server auth, Istio + SCP isolation of the upstream server. See `docs/mcp-gateway.md`.
- [x] Ticket detail page: show an "Approval due" date. Done: derived client-side (`ticket-detail.tsx`) from `ticketDate`/last approval + `approvalTtlHours`, mirroring `backend/app/jobs/expiry.py:_expiry_start`'s own cutoff; `approval_ttl_hours` added to `MeResponse`/`/api/me` so no new endpoint was needed.
- [x] Allow changing a ticket's tags without superseding. Done: `tags` added to `MUTABLE_FIELDS`, new `TAGS_UPDATED` event type + `apply_event` branch (`backend/app/repo/base.py`), `service.update_tags`, `POST /api/tickets/{id}/tags` (`TagsUpdateRequest`), and a `TagsEditor` dialog on the detail page. Deliberately bypasses `status_machine` entirely (not a status transition); the only guard is rejecting an already-superseded ticket, same as `supersede_ticket`.
- [x] Reorder ticket-detail and supersede-dialog fields to: Reason for changes → Planned action → Resources in scope → Action parameters (JSON). Done in both files; "Reason" relabeled "Reason for changes" consistently.
- [x] Change "Reason for change" from `<Input>` to `<Textarea>` in `supersede-dialog.tsx`. Done.
- [x] "Resources in scope" now renders a Terraform-plan-style summary (`frontend/src/lib/resource-scope.ts`: `N to add (N x EC2), N to change, N to destroy`) above the raw ARN list, classifying `operation` by prefix (`Run`/`Create`/`Launch` = add, `Terminate`/`Delete` = destroy, else change). A ticket is always exactly one EC2 call, so only one bucket is ever non-zero — still shown Terraform-style since that's the vocabulary approvers already read. The supersede dialog shows the same summary live, recomputed from the edited parameters JSON.
- [x] Comments: any IT-team session user (viewer or approver, any ticket status) can leave a discussion comment. Done: `COMMENT_ADDED` event type + no-op `apply_event` branch (comments don't change any `Ticket` field, so no `MUTABLE_FIELDS` change needed), `service.add_comment`, `POST /api/tickets/{id}/comments` (`CommentCreateRequest`), rendered inline in the existing audit trail (`audit-timeline.tsx`) with a `CommentForm` at the bottom of the timeline. Deliberately open to viewers too, unlike approve/reject/tags — visibility and discussion were explicitly requested for the whole team, not just approvers. Scoped to the human/session path only for now; agent-authored comments (e.g. automatic execution notes via the SigV4 or MCP paths) would be a separate follow-on, not implemented here.
- [x] Email-based approve/reject links, so `NOTIFY_ON_CREATE` notifications aren't read-only. Done: `auth/approval_links.py` (signed, single-purpose tokens), `api/approval_link_actions.py` (unauthenticated preview/act router), `/act` frontend route. See the 2026-08-03 decision log entry for the design (why stateless tokens, why GET/POST are split, the group-derived-approver limitation carried over from the existing notification feature).
- [x] Explain (not just hide) approve/reject buttons when they're unavailable, and a settings-gated self-approval toggle. Done: `/act` page's `blockedReason` (already_actioned/not_approver/self_approval/duplicate_approval), and `ALLOW_SELF_APPROVAL` threaded through `service.py`/`api/tickets.py`/`api/approval_link_actions.py`/`/api/me`. See the newest 2026-08-03 decision log entry.
- [ ] (Low priority, post-MVP) Notify approvers derived from `OIDC_APPROVER_GROUPS`, not just the `APPROVER_EMAILS` allowlist. Today `ses.py` only ever emails `APPROVER_EMAILS`, because a token tells you *this user's* groups while enumerating *members of a group* is a directory query that OIDC specifies nothing for — so this cannot be done provider-agnostically, which is the property `CLAUDE.md` protects hardest. Three routes evaluated 2026-08-04:
  - **Query the IdP directory.** Cognito-only is small (`cognito-idp:ListUsersInGroup`, paginate, TTL cache) but needs the user pool ID (parse from `OIDC_ISSUER` or a new setting) and a new IAM permission — spending the "gate needs *no* AWS permissions to verify agents" property. Also under-reports under Identity Center→Cognito SAML federation, where users only exist in the pool *after first login*. Provider-agnostic means a `DirectoryClient` protocol + factory (mirroring `repo/base.py`), and the Entra implementation is a different integration entirely: separate app registration, `GroupMember.Read.All` + admin consent, client-credentials flow, group name→object ID resolution.
  - **Harvest at login (preferred if picked up).** `auth.py`'s `callback` already holds both the email *and* the groups claim; persist "saw `email` in an approver group at `T`" and use that as the notification list. Provider-agnostic, no new permissions, no directory API. Tradeoff: only knows people who have logged in at least once, and goes stale until their next login — which is cosmetic here, since `approve_ticket` re-checks authorization server-side regardless of who got emailed.
  - **Status quo** — `APPROVER_EMAILS` only. Fine for the MVP.
- [ ] (Low priority, post-MVP) Admin settings page in the portal, overriding env. Mechanism is ~1–2 days; the risk is scope, so **"web settings take precedence over `.env`" must not be a blanket rule** — settings fall into three tiers and only the third is safe:
  - **Never runtime-editable** — `AUTH_MODE`, `ENV`, `OIDC_*`, `SESSION_SECRET`, `ALLOWED_AGENT_ARNS`, `GATE_SERVER_ID`, `ALLOW_SELF_APPROVAL`, `APPROVER_EMAILS`. These *are* the security boundary: a web-editable `ALLOWED_AGENT_ARNS` turns one compromised admin session into EC2 execution rights, and editable `APPROVER_EMAILS`/`ALLOW_SELF_APPROVAL` is one-click self-escalation. Rotating `SESSION_SECRET` would also silently invalidate every outstanding email approval link (`auth/approval_links.py`). Keeping these env/GitOps-managed also keeps them reviewable in git rather than mutable from a browser.
  - **Impossible without a restart** — `MCP_ENABLED`, `STORE_BACKEND`, `DATA_DIR`. `main.py:86` reads settings at *import* time to pick the ASGI topology, and `repo/factory.py:get_repository` is `lru_cache(maxsize=1)`.
  - **Safe to make editable** — `REQUIRED_APPROVALS`, `APPROVAL_TTL_HOURS`, `NOTIFY_ON_CREATE`, `SES_FROM_ADDRESS`, notification recipients. Workflow/ops policy with no identity implications. Prefer scoping the page to exactly these, with env as the *default* and the store as an override for those keys only — not a general env-override layer.
  - Three prerequisites before any of it: (1) **there is no admin role** — `rbac.py`'s `Role` is `approver|viewer`, and a third must come from env/IdP groups *only*, or an admin can grant themselves admin; (2) **settings changes must be audited** — "`REQUIRED_APPROVALS` was 2 when this was approved, then someone set it to 1" is exactly what an auditor needs, and it fits the existing event log, though `AuditEvent` is keyed to `ticket_id` so it needs a separate stream or a synthetic id; (3) **boot-time validation is a stated guarantee** ("invalid env crashes at boot rather than on first request"), so runtime writes need the same cross-field validators plus a rollback path. Note this partly subsumes the group-derived-approver item above: once recipients are a managed list, login-harvested group members become *suggestions* an admin curates.
- [ ] (Low priority, on hold) Attachment/media support on tickets. Deliberately deferred: the JSONL store folds its entire event log into memory at boot, so embedding binary/base64 attachments inline would bloat both the on-disk log and startup replay time linearly with attachment size; the S3/DynamoDB backends are still documented stubs (no real object-storage wiring yet); and file upload adds a real security surface (malware scanning, content-type validation, access control) to a tool whose value proposition is a narrow, hash-locked, text-auditable record of one exact AWS API call. If/when revisited, prefer a **link field on a comment** (a URL to an external system — screenshot host, runbook, dashboard) over actual file storage — gets most of the value without the storage/security rework.

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
PENDING_APPROVAL ──approve (n>=required)──> APPROVED ──start (hash echo)──> EXECUTING ──> CLOSED | FAILED
      │  │  │  │                              │  │  │
      │  │  │  └─close───────> CLOSED         │  │  └─close───────> CLOSED
      │  │  └─reject────────> REJECTED        │  └─supersede────> DEPRECATED
      │  └─supersede───────> DEPRECATED       └─TTL────────────> EXPIRED
      └─TTL───────────> EXPIRED
```

`close` withdraws a PENDING_APPROVAL/APPROVED ticket without executing it — any session user, no approver gate (unlike approve/reject), since nothing gets executed either way. Distinct from the same `CLOSED` status reached via successful execution: the audit trail tells them apart by event type (`CLOSED` vs `EXECUTION_COMPLETED`).

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

Human (session): `GET /api/tickets` · `GET /api/tickets/{id}` (`{ticket, lineage, auditEvents}`) · `POST .../approve` · `POST .../reject` (reason required) · `POST .../supersede` (atomic new+deprecate) · `POST .../close` (withdraw without executing, any session user) · `POST .../tags` · `POST .../comments` · `GET /api/me` · `GET /api/healthz` (public).

IDE (OAuth2.1 bearer, `docs/mcp-gateway.md`): `POST /mcp` — MCP Streamable HTTP JSON-RPC; tools `create_change_ticket` (proposedBy=human, assignee=`MCP_EXECUTOR_ARN`), `check_ticket_status`, `supersede_change_ticket`, and `close_ticket`.

### MCP-server contract (docs/agent-contract.md)

create (with `resourceArns` + `tags`; the gate adds default tags `gateTicketId`/`owner`) → poll 15–30 s → on APPROVED: `execution/start` echoing hash → execute the returned actionDetails, propagating `tags` verbatim where supported (`gateTicketId` is already in there) → report result with AWS RequestIds. Strong enforcement: an SCP (`deploy/scp/`) plus IAM restrict mutating EC2 actions to this role alone, everywhere — not just in-cluster; scope the role by the approved `resourceArns`. See `docs/agent-contract.md`'s "Strong enforcement" section for why the `aws:RequestTag/gateTicketId` condition only applies to resource-creating calls, not `StopInstances`-style actions on existing resources.

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
- Cognito access tokens are not shaped like a generic OIDC ID token: **resolved 2026-08-03.** No `aud` claim (audience lives in `client_id` instead) and no profile claims like `email` (only the ID token gets those, but OAuth Bearer calls — including every MCP call from Cursor — always carry the *access* token, never the ID token). `OidcTokenVerifier` (`backend/app/auth/mcp_token_verifier.py`) now checks `client_id` as a fallback for audience, and resolves email via a cached `/userinfo` call when it's absent from the token. Lesson: when adding a new IdP-facing check, don't assume standard-OIDC-shaped claims on an *access* token — verify against that specific IdP's actual token, not just its discovery doc. `MCP_REQUIRED_SCOPES` must include `email` or Cognito never grants it to request against `/userinfo` in the first place.
- Not in v1: quorum >2, CSV export/retention, CloudTrail correlation job, time-zone preference.

## Decision log

- 2026-08-05 — **CI: registry moved to a GitHub environment secret; sonar becomes a skippable job; build + Trivy moved to first-party actions.** (1) The registry hostname was hardcoded as a workflow-level `env: REGISTRY:` in both workflows; it now comes from the `liam-dev` **environment** secret `REGISTRY_ENDPOINT`. Environment secrets only resolve in a job that declares `environment:`, so `environment: liam-dev` + a job-level `env: REGISTRY:` sit on the `image` job only — `test`/`sonar` don't touch the registry and shouldn't inherit an environment's protection rules. A guard fails the job loudly if the secret is empty, because `lib.sh`'s `REGISTRY="${REGISTRY:-<hardcoded ACR>}"` would otherwise silently fall back to the old hostname for the push while the natively-built tag had no registry prefix at all. (2) The first job (`verify-configs`) now checks every secret the run depends on and fails with one `::error::` per missing name, so a misconfigured repo dies in ~5 seconds instead of at the push step, ten minutes of build+scan+smoke later. `REGISTRY_ENDPOINT` is required on every run (the image tag is built from it); `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID` are required only when the run actually publishes (push to `main`), since PRs never log in to ACR; `SONAR_TOKEN`/`SONAR_HOST_URL` are advisory. It declares `environment: liam-dev` for the same reason `image` does — an environment secret reads as empty outside its environment, which would make the check report a false failure. Secrets are passed in via `env:` rather than interpolated into the script body, so a value containing a quote can't break the shell. `image` lists `verify-configs` in its own `needs` even though `sonar` already depends on it: a failed `verify-configs` skips `sonar`, and `skipped` is an allowed `sonar` result, so without the direct edge a missing secret would have let the image job run anyway. Cross-job references use `needs['verify-configs']` — a hyphen inside an expression parses as subtraction. (3) SonarQube previously ran as a job that *always* started and no-op'd its steps when `SONAR_TOKEN` was unset; `secrets` cannot be referenced from a job-level `if:`, so a tiny `config` job now resolves `SONAR_TOKEN != ''` into an output and `sonar` gates on it — the job is genuinely skipped, not a green job full of skipped steps. That forces `image`'s condition to become `always() && test == 'success' && (sonar == 'success' || sonar == 'skipped')`, since a plain `needs:` treats a skipped dependency as a reason to skip. (4) `ci.sh build`/`ci.sh scan` in CI replaced by `docker/build-push-action@v7` (with `load: true` so scan and smoke still find the image in the local daemon, plus a per-component `type=gha` layer cache the plain `docker build` couldn't have) and `aquasecurity/trivy-action@v0.36.0` at identical settings (`CRITICAL`, `--ignore-unfixed`, `--exit-code 1`). The script's `build`/`scan`/`all` commands are kept, unchanged, for local runs — the workflow just no longer calls those two. `smoke` and `push` still shell out to `ci.sh` as before. The tag is bridged by exporting `IMAGE_TAG` (short SHA — the same derivation `lib.sh` does) and `IMAGE_REF` to `$GITHUB_ENV` in one step, so the natively built tag and the one `smoke`/`push` compute cannot drift.
- 2026-08-04 — **Ingress replaced by Gateway API on F5 NGINX Gateway Fabric (NGF)**, because ingress-nginx is retired in the target clusters. `deploy/k8s/ingress.yaml` and `deploy/k8s/liam-dev/ingress.yaml` are deleted, replaced by `httproute.yaml` in each. The routing intent is byte-for-byte the same — the three API prefixes to the backend, `/` to the frontend, one hostname — so nothing in the previous entry's single-origin/cookie reasoning changes. Three things did change. (1) **No Gateway is defined in this repo.** NGF provisions one NGINX data plane Deployment + Service per `Gateway` resource, so a per-app Gateway means a second data plane and a second load balancer; locally it would also mean two NGINX pods contending for the node's `:80`. The HTTPRoute therefore attaches by `parentRefs` to a shared, platform-owned Gateway — `localwsl` in the `nginx-gateway` namespace (Helm release `ngf`) for `liam-dev`, a placeholder to adjust for AWS. That Gateway needed no edits to accept `mcp.localwsl`: its `http` listener sets no `hostname` (so the route's own `hostnames` disambiguates) and already allows routes `from: All`, which matters because hand-editing it would be clobbered by the next `helm upgrade`. (2) **Rule ordering stopped being load-bearing.** The old comment relied on ingress-nginx giving longer prefixes precedence; Gateway API *mandates* longest-PathPrefix-wins independent of document order, and `PathPrefix` is segment-aware, so `/api` can no longer match a hypothetical `/apifoo`. (3) **TLS moved off the app's manifest onto the Gateway listener** — Gateway API has no equivalent of the ALB's ACM-certificate-ARN annotation (listener TLS takes a `kubernetes.io/tls` Secret), so the cert and the HTTP→HTTPS redirect both become the Gateway owner's concern; this repo's contribution to the TLS-mandatory invariant is now simply that its `parentRefs` names an HTTPS `sectionName`. Staying on ALB would mean the AWS Gateway API controller rather than NGF. One trap recorded for `/mcp`: NGF does **not** implement `HTTPRoute.rules[].timeouts` — it accepts the field and silently ignores it — so if NGF's default `proxy_read_timeout` truncates Streamable-HTTP/SSE MCP sessions, the fix is an NGF `SnippetsFilter`, not a Gateway API timeout.
- 2026-08-04 — **Monolith split into two pods**, and CI moved to GitHub Actions. The root `Dockerfile` (node build → copied into the Python image as `static/`) is gone, replaced by `backend/Dockerfile` and `frontend/Dockerfile` with contexts `./backend` and `./frontend`, so neither half can invalidate the other's layer cache. `ingress.yaml` now does the API-vs-static split that `main.py:_mount_spa` used to do in-process, routing `/api`, `/mcp` and `/.well-known` to the backend — that prefix list is taken from `frontend/vite.config.ts`'s dev proxy and must stay in sync with it; missing `/.well-known` in particular breaks MCP OAuth discovery as an opaque nginx 404 rather than an auth error. Both pods deliberately sit behind **one host**: the browser still sees a single origin, so the httpOnly session cookie keeps working and no CORS config is needed anywhere — a separate SPA hostname would have forced `SameSite=None` plus a CORS allowlist, a real security regression for no benefit. `_mount_spa` was left in place rather than deleted: it early-returns when `app/static` is absent (which it is in the backend image), so it's now purely a local-dev convenience — `npm run build` + `uvicorn` still serves the SPA from one process. The backend Deployment keeps its original name and bare `app: mcp-approval-gate` selector because Deployment selectors are immutable *and* `istio-authorizationpolicy.yaml`'s NetworkPolicy grants upstream-`aws-api-mcp-server` access on exactly that label; the frontend's distinct label therefore excludes it from that access automatically, which is the correct outcome. Frontend runs `nginxinc/nginx-unprivileged` on :8080 (UID 101) so both pods keep `runAsNonRoot`. Verified by building both images and asserting the split holds: backend `/` now returns a JSON 404 (proving no `static/` leaked in) while the frontend serves `index.html` for `/`, `/login`, `/tickets/:id` and `/act`.
- 2026-08-03 — Two follow-ons to the approve/reject UI. (1) The `/act` email-link landing page (previous entry below) now explains *why* a confirm form isn't shown instead of letting the visitor click through to a guaranteed failure: `ApprovalLinkPreview` gained `blockedReason` (`already_actioned` / `not_approver` / `self_approval` / `duplicate_approval`), computed server-side in `approval_link_actions.py`'s new `_blocked_reason()` and rendered as one of four fixed messages in `approve-by-link.tsx`'s `BLOCKED_MESSAGES` map — mirroring the courtesy the portal already gave session approvers (`approve-reject-actions.tsx`'s role/proposer/duplicate messages, which predate this repo and were the reference point). `_blocked_reason` is advisory only; POST re-derives everything from the same `service.py` calls, so it can't be raced/bypassed. (2) `ALLOW_SELF_APPROVAL` setting added (default `false`) so a human can approve/reject their own proposal when explicitly turned on — `_assert_actionable_by` (`core/service.py`) takes an `allow_self_approval` kwarg threaded through `approve_ticket`/`reject_ticket` from both call sites (`api/tickets.py`, `api/approval_link_actions.py`), and `/api/me` exposes it (`MeResponse.allow_self_approval`) so `approve-reject-actions.tsx` can skip its proposer block (with a visible "self-approval enabled" notice, not a silent bypass) instead of showing buttons that would just 403. Scoped narrowly on purpose: agent-created tickets always have `proposed_by` = the agent's own ARN, never a human email, so this only ever matters for MCP-created or supersede/edited tickets where a human is the proposer — it doesn't touch the four-eyes guarantee for agent-originated changes at all. Intended for small/solo deployments (e.g. a single approver in `liam-dev`) where a second human approver genuinely doesn't exist, not as a routine bypass — left off by default everywhere, including `liam-dev`, pending an explicit decision to turn it on there.
- 2026-08-03 — Email-based approve/reject links added (`auth/approval_links.py`, `api/approval_link_actions.py`, `/act` frontend route), so an approver can act straight from the SES notification once `NOTIFY_ON_CREATE=true` without a portal session. Treated as a fourth, narrow identity proof alongside the three CLAUDE.md says never to couple (human session, agent SigV4, MCP bearer) — kept in its own router/module for the same reason `agent_tickets.py` is split from `tickets.py`. Tokens are `itsdangerous.URLSafeTimedSerializer(SESSION_SECRET, salt=...)`, a distinct salt from the session cookie's own signer so a leaked one can't be replayed as the other despite sharing a secret; no new setting needed. TTL reuses `APPROVAL_TTL_HOURS` rather than adding a second one — a link outliving its ticket has no purpose. Deliberately stateless (no server-side link/nonce table): re-use is blocked by the same guards a session approver already hits in `service.py` (ticket must still be `PENDING_APPROVAL`, same approver can't approve twice), not by tracking token IDs, so effective single-use falls out of existing invariants for free. Split GET (preview only, never mutates — safe against email-client link-prefetching bots like Outlook Safe Links) from POST (the actual `approve_ticket`/`reject_ticket` call, gated behind an explicit confirm click on the `/act` landing page) rather than a one-click GET-triggers-approval link, which is a known real-world foot-gun for this exact feature shape. Added a live recheck against `APPROVER_EMAILS` at click time (not just trusting the token's snapshot) as defense in depth against the allowlist being edited between send and click — a real gap already, since `resolve_role`'s OIDC-group path has no equivalent check outside a live session, meaning **group-derived approvers still don't receive these emails at all**, same pre-existing limitation as the notification feature itself (`ses.py` only ever emailed `APPROVER_EMAILS`). Switched `notify_ticket_created` from one multi-recipient email to one email per recipient, since links must be personalized per approver address for the audit trail to attribute the right human. `/api/tickets/by-link/*` added to the existing sliding-window rate limiter alongside `/api/agent/*`.
- 2026-08-03 — Tags made mutable in place (`TAGS_UPDATED` event) while everything else stays frozen: the immutability guarantee (`MUTABLE_FIELDS`, `CLAUDE.md`'s "key invariants") is specifically about the *approved action* — `ActionDetails`/`parametersHash` must never drift after approval. Tags are cost-center/team/env metadata that never feed `parametersHash` or IAM scoping, so editing them carries none of the risk a supersede exists to guard against; requiring a fresh approval cycle just to relabel a ticket would be approval fatigue with no security benefit. Bypasses `status_machine` entirely rather than adding self-loop transitions there, since a tags edit isn't a status transition (status is untouched) — the only check is the same "not already superseded" guard `supersede_ticket` uses. Also added a Terraform-plan-style "Resources in scope" summary and an "Approval due" date (computed client-side from the existing `expiry.py` cutoff, no new stored field) to the ticket detail page, and reordered/relabeled the Reason/Planned action/Resources/Parameters fields consistently between the detail page and the supersede dialog. Action-parameters editability itself was **not** changed — it remains supersede-only, which was already correct: supersede computes a fresh gate-side `parametersHash` and forces a new approval cycle, so it was never a bypass of the hash-lock invariant.
- 2026-08-03 — `gateTicketId` (= the ticket's own id) and `owner` (= `proposed_by`) added as default tags every ticket gets at creation (`service.build_ticket`), so AWS resources the agent tags on the caller's behalf trace back to the originating ticket and requester for cost attribution. `gateTicketId` reuses the tag key that was already load-bearing for IAM enforcement (`aws:RequestTag/gateTicketId`, `deploy/scp/deny-ec2-mutations-except-gate.json`) — it was previously something each agent implementation had to remember to add manually at execution time (`docs/agent-contract.md` step 4); setting it as a gate-side default tag closes that gap, since an agent that just propagates `tags` verbatim now gets it for free. Considered introducing a separate `requestID` tag instead, but that would've meant two keys carrying the same value (only one of them IAM-enforced) — consolidated onto the existing name instead. Both tags are gate-assigned, not caller-supplied: `build_ticket` overwrites them last regardless of what's in the request payload, and `service.update_tags` reasserts them from the ticket's own frozen `ticket_id`/`proposed_by` on every edit — so the tag-editor UI can't be used to spoof either value. The frontend `TagsEditor` renders both as a fixed, non-editable block rather than ordinary rows.
- 2026-08-03 — Comments added (`COMMENT_ADDED` event), deliberately open to any session user, not approver-gated: this tool is IT-team-internal only, so unlike approve/reject/tags there's no reason to restrict who can weigh in on a ticket, and comments carry zero risk to the approval-integrity invariants since they don't touch any `Ticket` field (the `apply_event` branch is a no-op past the seq bump — no `MUTABLE_FIELDS` change needed). Also allowed on any ticket status, including terminal ones, since post-hoc discussion ("why was this rejected", "re-raised as TICK-456") is a legitimate use case comments shouldn't block. Rendered inline in the existing audit trail rather than a separate panel, so "everyone has visibility" falls out of the same view/RBAC that already gates ticket-detail access — no new visibility rule needed. Attachment/media support was considered alongside this and explicitly deferred (see Todo) — it fights the JSONL store's in-memory-replay design and the S3/DynamoDB backends aren't wired up yet, unlike comments which fit the existing event-sourced model directly.
- 2026-08-03 — `COMPLETED` status renamed to `CLOSED`, and a new manual "close" action added as a fourth outcome alongside approve/reject/supersede: any session user can withdraw a PENDING_APPROVAL/APPROVED ticket without executing it (`service.close_ticket`, `POST /api/tickets/{id}/close`, `CloseTicketDialog`), appending a `CLOSED` audit event. Reused the `CLOSED` status name for both outcomes (successful execution *and* manual withdrawal) rather than inventing a third terminal status — same pattern already used for `REJECTED`/`DEPRECATED`/`EXPIRED`, which are each both a `TicketStatus` and an identically-named `AuditEventType`. The two paths stay distinguishable in the audit trail by event type (`EXECUTION_COMPLETED` vs `CLOSED`), which is what actually matters for audit purposes — the ticket-level status only needs to answer "is this over and did anything execute," not "how did it end down to the sub-reason" (`REJECTED`/`EXPIRED`/`DEPRECATED` already answer that where it matters). Close is open to any session user rather than approver-gated like approve/reject: withdrawing your own request isn't a self-approval risk since nothing executes either way, so it follows supersede's precedent, not approve/reject's. Also added `supersede_change_ticket` and `close_ticket` MCP tools (`mcp_gateway.py`), mirroring `create_change_ticket`'s pattern of using the OAuth-validated `access_token.subject` as the identity — closing a real gap where an IDE agent previously had no sanctioned way to edit or withdraw a ticket it had opened via MCP, and would otherwise have had to script direct calls to the session-cookie API with an identity the gate can't verify came from that specific human. IAM Identity Center cannot be `OIDC_ISSUER` directly (its OIDC service only supports public clients; the "trusted token issuer" feature is token-exchange for an already-authenticated app, not a login flow — confirmed against AWS docs after a live setup attempt hit a dead end trying to source an `OIDC_CLIENT_ID`/secret from a trusted-token-issuer config). Working AWS path: Identity Center SAML 2.0 application → Cognito User Pool (SAML IdP + hosted-UI app client with a generated secret) → this gate via the same generic `OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET` config, zero code changes (`backend/app/api/auth.py` already does plain discovery-URL OIDC). `.env.example`'s AWS scenario block rewritten accordingly; `OIDC_GROUPS_CLAIM` now suggested as `cognito:groups`. Azure AD/Entra scenario unaffected — it was already a direct, working confidential client the whole time. No backend code changed.
- 2026-08-03 — MCP/Cursor OAuth against Cognito: **end-to-end connection confirmed working, Cursor shows tools available.** Getting there surfaced two separate, unrelated Cognito-specific gaps, easy to conflate because both manifested as opaque auth failures with no useful client-side error text:
  - **Symptom 1 — token exchange itself failing (`invalid_grant`).** Initially suspected to be Cognito's token endpoint rejecting the RFC 8707 `resource` parameter that MCP clients send unconditionally on authorization/token requests — **ruled out** via a standalone PKCE debug script (`scripts/oidc_pkce_debug.py`) that replays Cursor's exact request shape directly against Cognito outside of Cursor: a request with `resource=http://localhost:5173/mcp` set returned the same `invalid_grant`, while an otherwise-identical request with `resource` omitted returned `200` with a valid token — but only until the Cognito **resource server** (`aws-mcp-approval-gate`, the Cognito-side object that declares a resource/identifier and its scopes) was deleted and recreated pointing at the current `http://localhost:5173/mcp`, after which the *with-`resource`* run also passed. Actual root cause: a stale Cognito resource server still registered against an earlier URL (from before the `:8001`→`:5173` proxy consolidation) — Cognito validates the `resource` parameter against its registered resource servers and returns the generic `invalid_grant` rather than a specific "unknown resource" error, which is what made this look like a spec-level RFC 8707 rejection rather than a stale-registration mismatch. Lesson: `invalid_grant` from Cognito during an MCP OAuth exchange is not proof of a protocol-level incompatibility — check the resource server's registered identifier matches `PUBLIC_BASE_URL`/mcp exactly before assuming a gate-side or spec-level fix is needed. (Entra ID v2.0's `AADSTS9010010` gap for the same `resource` parameter is a separate, still-real issue — not evaluated further here since Cognito is the provider in use.)
  - **Symptom 2 — token obtained, but every `/mcp` call still 401'd.** Root cause: Cognito access tokens don't carry `aud` or `email` claims at all (see the matching "Open risks" entry above) — the previous `OidcTokenVerifier` implementation assumed a generic OIDC-shaped token that had both. Fixed by adding a `client_id`-claim fallback for audience and a cached `/userinfo` lookup for email, plus setting `MCP_REQUIRED_SCOPES=openid email profile` and pointing `MCP_OAUTH_AUDIENCE` at the IDE app client's client ID rather than the `/mcp` resource URL. See `docs/mcp-gateway.md`'s "Cognito-specific caveat" and `backend/tests/test_mcp_gateway.py`'s `test_client_id_claim_satisfies_audience_when_aud_missing` / `test_email_resolved_via_userinfo_when_missing_from_token`.
  - **Lesson learned**: don't assume a "generic OIDC" IdP integration is actually provider-agnostic in practice — the human-login path (`backend/app/api/auth.py`) never hit either gap because it reads the *ID token*, which is standard-shaped on every provider tried so far; the MCP path broke because OAuth Bearer/Resource-Server calls always use the *access* token, and access-token shape (which claims exist, which spec extensions are honored) varies by IdP far more than ID-token shape does. Any future IdP swap for the MCP path needs its access token inspected directly, not just its `/.well-known/openid-configuration`.
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
deploy/k8s/                           # namespace, deployment, service, httproute, serviceaccount, pvc, configmap, secret
scripts/agent_flow_demo.py             # live E2E demo: create -> poll -> start -> result
Dockerfile                             # node:20-alpine build stage -> python:3.12-slim runtime
```

### Data model (`backend/app/core/models.py`)

All API models inherit `ApiModel` (`alias_generator=to_camel, populate_by_name=True`), so the wire format is camelCase while Python stays snake_case.

```python
TicketStatus = Literal["PENDING_APPROVAL","APPROVED","REJECTED","DEPRECATED",
                        "EXPIRED","EXECUTING","CLOSED","FAILED"]
TERMINAL_STATUSES = {"REJECTED","DEPRECATED","EXPIRED","CLOSED","FAILED"}

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
    tags: dict[str, str] = {}            # management tags; filterable, propagated to AWS resources.
                                          # gateTicketId/owner are gate-assigned defaults, set at
                                          # creation (build_ticket) and reasserted on every edit
                                          # (update_tags) — never caller-controlled.
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
# `tags` is deliberately mutable (TAGS_UPDATED) — tags are metadata, not part
# of ActionDetails/parametersHash, so editing them never needs a supersede.
MUTABLE_FIELDS = frozenset({"status","approvals","rejected_by","rejected_at",
                             "rejection_reason","superseded_by","execution","seq","tags"})

class AuditEvent(ApiModel):
    event_id: str; ticket_id: str; seq: int; timestamp: datetime
    type: Literal["TICKET_CREATED","APPROVAL_ADDED","APPROVED","REJECTED","DEPRECATED",
                  "EXPIRED","EXECUTION_STARTED","EXECUTION_COMPLETED","EXECUTION_FAILED",
                  "TAGS_UPDATED","COMMENT_ADDED","CLOSED"]
    actor: Actor
    from_status: TicketStatus | None = None
    to_status: TicketStatus | None = None
    details: dict[str, Any] | None = None
```

**Immutability**: every change is an appended `AuditEvent`; a ticket is a fold over its events (`repo/base.py:apply_event`). The fold only ever assigns `MUTABLE_FIELDS` — frozen fields (subject, `actionDetails`, `plannedDate`, …) structurally cannot change; editing the action itself requires a superseding ticket. `TAGS_UPDATED` and `COMMENT_ADDED` are the two exceptions to "every event is a status transition": neither goes through `status_machine` (tags aren't a status transition; a comment changes no `Ticket` field at all, not even via `MUTABLE_FIELDS` — the fold is a no-op past the `seq` bump).

### Transitions (`backend/app/core/status_machine.py:_ALLOWED`)

| From | To | Actor kind / guard |
|---|---|---|
| PENDING_APPROVAL | PENDING_APPROVAL (`APPROVAL_ADDED`) | human; approver ≠ proposer, not already approved by them, count < required |
| PENDING_APPROVAL | APPROVED (`APPROVED`) | human; same guard, approvals reach `REQUIRED_APPROVALS` |
| PENDING_APPROVAL | REJECTED | human; approver ≠ proposer, reason required (min 5 chars) |
| PENDING_APPROVAL / APPROVED | DEPRECATED | human, via supersede |
| PENDING_APPROVAL / APPROVED | EXPIRED | system sweep (`APPROVAL_TTL_HOURS`) |
| PENDING_APPROVAL / APPROVED | CLOSED | human; any session user (`service.close_ticket`), withdraw without executing |
| APPROVED | EXECUTING | agent; caller ARN == assignee, `parametersHash` echo matches |
| EXECUTING | CLOSED / FAILED | agent; caller ARN == assignee |

Structural (from, to, actor-kind) rules live in `status_machine.py`; identity rules that need request context (approver ≠ proposer, caller ARN == assignee, etc.) live in `core/service.py`, which always calls `assert_transition` first.

`TAGS_UPDATED` (any session user, `service.update_tags`) and `COMMENT_ADDED` (any session user, `service.add_comment`) don't appear above because they never call `assert_transition` at all — `status` is untouched (`from_status == to_status`), so there's nothing for the status machine to authorize. The only guard on tag edits is rejecting an already-superseded ticket (`TicketSuperseded`); comments have no guard beyond the ticket existing, and are allowed on any status including terminal ones.

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

Human (session cookie, prefix `/api/tickets`): `GET ""` (filter by `status`/`assignee`/`tag=key=value`, cursor pagination) · `GET /{id}` (`{ticket, lineage[], auditEvents[]}`) · `POST /{id}/approve` (approver role; rejects proposer/duplicate approver; CAS on `seq` → 409 on a concurrent-approval race) · `POST /{id}/reject` (`{reason}`, min 5 chars) · `POST /{id}/supersede` (body = a new `TicketCreateRequest`; atomically deprecates the old ticket and creates the successor with `supersedes`/`lineageRootId` set and `proposedBy` = the editing human — who therefore can't also approve their own superseding ticket) · `POST /{id}/close` (`{reason?}`; any session role, only from PENDING_APPROVAL/APPROVED — 409 `INVALID_STATE` otherwise; withdraws the ticket without executing, appends a `CLOSED` audit event) · `POST /{id}/tags` (`{tags}`; any session role, no supersede, 409 `TICKET_SUPERSEDED` if the ticket's already superseded) · `POST /{id}/comments` (`{text}`, 1–2000 chars; any session role, any ticket status). Plus `GET /api/me` (now also returns `approvalTtlHours` so the frontend can compute a ticket's approval-due date without a separate config call) and public `GET /api/healthz`.

Every mutation is Pydantic-validated and appends its `AuditEvent` through `service.py` → `repo.append_event`/`transact_supersede`; errors surface as `{"error": {"code", "message"}}` via `api/errors.py`.

### Notifications & expiry

`notifications/ses.py:notify_ticket_created` — fire-and-forget (`asyncio.create_task`, boto3 SESv2 via `to_thread`), never raises into the request path; gated by `NOTIFY_ON_CREATE`/`SES_FROM_ADDRESS`/`SES_REGION`.

`jobs/expiry.py` — runs every 10 minutes from the FastAPI `lifespan`. `APPROVED` tickets expire `APPROVAL_TTL_HOURS` after their **last approval**; `PENDING_APPROVAL` tickets expire that many hours after `ticketDate`. `EXECUTING` and terminal tickets are never swept. A `ConflictError` from a concurrent transition is swallowed (the ticket moved on its own before the sweep reached it).

### UI (mirrors `gammon-powershell-portal`)

Brand tokens copied into `frontend/src/index.css` (navy `--primary: 217 49% 36%`, red `--secondary: 0 85% 49%`, `.section-title`) plus added `--success/--warning/--info`. Pages: dashboard (status-count cards + recent tickets), tickets (Active/History tabs, tag filter, 10s polling on active), ticket detail (lineage chain, hash-locked parameters view, audit timeline, approve/reject with confirm dialogs, supersede via react-hook-form + zod). TanStack Query `refetchInterval` stops once a ticket/list only contains terminal statuses. Approve/reject controls self-hide for the proposer/a duplicate approver/a viewer as a UX nicety — the server enforces all of it regardless (`tickets.py`, `service.py`).

Ticket detail additionally has: an "Approval due" field (derived client-side from `ticketDate`/last approval + `me.approvalTtlHours`, mirroring `expiry.py`'s own cutoff — no new stored field); a `TagsEditor` dialog (any role) that edits tags in place via `POST /{id}/tags`, rendering `gateTicketId`/`owner` as a fixed, non-editable block since the server reasserts them regardless of what's submitted; a Terraform-plan-style "Resources in scope" summary (`lib/resource-scope.ts`) above the raw ARN list, also shown live in the supersede dialog as the parameters JSON is edited; and a `CommentForm` at the bottom of the audit trail (any role, any ticket status) whose posts render inline in the same timeline as approvals/rejections/tag changes — comments needed no separate visibility rule since the audit trail was already visible to every session user. Fields in both the detail page and the supersede dialog are ordered Reason for changes → Planned action → Resources in scope → Action parameters (JSON); "Reason for changes" is a `Textarea` in both places. A `CloseTicketDialog` (any role, PENDING_APPROVAL/APPROVED only) sits next to `SupersedeDialog` for withdrawing a ticket without executing it.

### MCP-server contract (`docs/agent-contract.md`)

1. Build exact `actionDetails` (including `resourceArns` for every targeted resource and management `tags`) → `POST /api/agent/tickets` with an `Idempotency-Key`; surface the ticket URL to the operator. Read-only `Describe*` calls bypass the gate entirely.
2. Poll every 15–30s (jittered) until `APPROVED` (proceed), `REJECTED`/`EXPIRED` (stop, surface the reason), or `DEPRECATED` (follow `supersededBy` and re-confirm).
3. `execution/start` echoing `parametersHash`; execute using the `actionDetails` from *that* response; propagate `tags` verbatim onto the AWS resources where the operation supports tagging — `gateTicketId` is already in there (the gate sets it as a default tag at creation), so there's nothing extra to add.
4. Report the result with AWS RequestIds via `execution/result`.
5. Recommended hard enforcement (owned by the agent-role admin, not this gate): an IAM condition requiring `aws:RequestTag/gateTicketId` on mutating EC2 actions so an ungated call fails at IAM regardless of what the gate says; scope the agent role's `Resource`/ABAC condition to the ticket's `resourceArns`.

### Deployment specifics

`Dockerfile` — stage 1 `node:20-alpine` (`npm ci && vite build`); stage 2 `python:3.12-slim`, deps installed from `backend/requirements.txt` (cached layer, see Decision log), non-root `1001:1001`, `uvicorn app.main:app` on `:8000`.

`deploy/k8s/deployment.yaml` — `replicas: 1` with `strategy: Recreate` is **required** while `STORE_BACKEND=jsonl` (RWO PVC, single-writer store, in-memory replay cache); scale out only after moving to DynamoDB + a shared replay cache. `runAsNonRoot`/`fsGroup: 1001`, readiness/liveness on `/api/healthz`, env from a ConfigMap + Secret. Other manifests: `namespace.yaml`, `pvc.yaml` (1Gi RWO), `serviceaccount.yaml` (IRSA annotation), `service.yaml` (ClusterIP), `httproute.yaml` (Gateway API/NGF, attaches to a shared platform Gateway on an HTTPS listener — SigV4 identity headers must never traverse plaintext), `secret.yaml` (template only; recommend External/Sealed Secrets for `SESSION_SECRET`/`OIDC_CLIENT_SECRET`).

