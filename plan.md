 Here is Claude's plan:
╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
p-approval-gate — Implementation Plan

ntext

 An AI agent (AWS MCP server, running on the existing k8s cluster) can view/create/manage EC2. Today its changes lack auditability and human control. This project builds a blocking approval gate: before any mutating EC2 action, the agent must create a change-request ticket, wait for human approval in a web portal, execute only the approved parameters, and report the result. Tickets are immutable — edits create a perseding ticket and mark the old one Deprecated — giving a tamper-evident audit chain.

 New app built from scratch in the empty dir /mnt/c/Users/LiamNg/Documents/GitHub/mcp-approval-gate/. UI mirrors the conventions of /mnt/c/Users/LiamNg/Documents/GitHub-GTI/gammon-powershell-portal/ (Next.js 15+ shadcn/ui + Tailwind HSL brand tokens), while fixing its known anti-patterns.

 User decisions (confirmed)

 ┌────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │      Decision      │                                                                   Choice                                                                   │
 ├────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ Enforcement        │ Blocking gate: create → poll → execute-on-Approved → report                                                                                │
 ├────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ Human auth         │ OIDC vs IAM Identity Center now; provider-agnostic so Azure AD/Entra ID is a config-only swap later                                        │
 ├────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ Agent auth         │ IAM SigV4 (presigned sts:GetCallerIdentity pattern); fully separate middleware path from human auth                                        │
 ├────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ Storage            │ MVP: local JSONL on PVC (single replica), behind a DynamoDB-shaped repository interface; production swap to DynamoDB via STORE_BACKEND env │
 ├────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ Approvals required │ Env-configurable: REQUIRED_APPROVALS=1|2; approvers must be distinct and never the proposer                                                │
 ├────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ Notifications      │ SES email in MVP — email approvers on ticket creation                                                                                      │
 ├────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ Expiry             │ TTL-based: APPROVAL_TTL_HOURS (default 72); stale Pending/Approved → EXPIRED                                                               │
 └────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

 Tech stack

 - Next.js 15 App Router + React 19 + TypeScript strict — single full-stack app (route handlers = API for both browser and agent). No separate backend.
 - shadcn/ui + Tailwind 3.4 — brand tokens copied from the reference portal.
 - Auth.js (next-auth v5) with one generic OIDC provider built from env vars.
 - AWS SDK v3: @aws-sdk/client-sesv2 (notifications) only in v1; DynamoDB clients later. STS verification uses plain fetch (forwarding the agent's presigned request — the gate needs no STS permission).
 - zod (env validation + shared request/response schemas), react-hook-form (forms), @tanstack/react-table, ulid (sortable IDs), Vitest (tests).

 Directory structure

 app/
   layout.tsx                # server component; providers.tsx is the client child
   providers.tsx             # ThemeProvider, SessionProvider, Toaster
   globals.css               # single CSS file; brand HSL vars copied from portal
   login/page.tsx
   (portal)/
     layout.tsx              # Sidebar + Header shell
     page.tsx                # dashboard: status counts + recent tickets
     tickets/page.tsx        # list: tabs + filters + DataTable
     tickets/[id]/page.tsx   # detail: fields, lineage chain, audit timeline, actions
   api/
     auth/[...nextauth]/route.ts
     healthz/route.ts
     me/route.ts
     agent/tickets/route.ts                       # POST create (SigV4)
     agent/tickets/[id]/route.ts                  # GET poll (SigV4)
     agent/tickets/[id]/execution/start/route.ts  # POST (SigV4)
     agent/tickets/[id]/execution/result/route.ts # POST (SigV4)
     tickets/route.ts                             # GET list (session)
     tickets/[id]/route.ts                        # GET detail (session)
     tickets/[id]/approve/route.ts                # POST (session, approver)
     tickets/[id]/reject/route.ts                 # POST (session, approver)
     tickets/[id]/supersede/route.ts              # POST (session)
 components/
   layout/sidebar.tsx, header.tsx
   tickets/ticket-table.tsx, ticket-columns.tsx, ticket-filters.tsx,
           ticket-status-badge.tsx, ticket-detail-card.tsx, lineage-chain.tsx,
           audit-timeline.tsx, approve-reject-actions.tsx, supersede-dialog.tsx
   ui/                       # ~15 shadcn primitives copied from portal
 lib/
   env.ts                    # zod-validated env, fail-fast at boot
   utils.ts                  # cn()
   auth/auth.ts              # Auth.js v5 generic OIDC config
   auth/rbac.ts              # groups-claim OR APPROVER_EMAILS fallback → role
   auth/agent-auth.ts        # withAgentAuth(): presigned-STS SigV4 verification
   auth/replay-cache.ts      # in-memory TTL nonce cache
   core/types.ts             # Ticket, AuditEvent, statuses
   core/status-machine.ts    # transition table + guards (single source of truth)
   core/schemas.ts           # zod schemas shared by API and forms
   core/canonical-json.ts    # canonicalization + sha256 for parametersHash
   core/ticket-service.ts    # business rules
   notifications/ses.ts      # email approvers on TICKET_CREATED
   repo/repository.ts        # interface (DynamoDB-shaped)
   repo/jsonl-store.ts       # MVP implementation
   repo/dynamodb-store.ts    # stub + documented single-table design
   repo/index.ts             # factory: STORE_BACKEND=jsonl|dynamodb
 middleware.ts               # real route protection (human paths only)
 docs/agent-contract.md      # MCP-server integration contract
 deploy/k8s/                 # deployment, service, ingress, sa, pvc, configmap, secret
 tests/                      # Vitest
 Dockerfile                  # multi-stage, standalone output, non-root
 next.config.ts              # ONE config; output:'standalone'; no ignoreBuildErrors
 .env.example                # placeholders only, no secrets

 Data model (lib/core/types.ts)

 type TicketStatus = "PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "DEPRECATED"
                   | "EXPIRED" | "EXECUTING" | "COMPLETED" | "FAILED";

 interface ActionDetails {
   service: "ec2";                      // v1 scope
   operation: string;                   // e.g. "RunInstances", "StopInstances"
   region: string;
   parameters: Record<string, unknown>; // exact intended SDK params
   parametersHash: string;              // sha256 of canonical JSON, computed BY THE GATE
   reason?: string;
 }

 interface Approval { approvedBy: string; approvedAt: string; }

 interface Ticket {
   ticketId: string;          // ULID
   subject: string;
   ticketDate: string;        // set by gate
   status: TicketStatus;
   plannedDate: string;
   plannedAction: string;     // summary
   actionDetails: ActionDetails;
   assignee: string;          // VERIFIED agent IAM ARN from STS — never client-supplied
   proposedBy: string;        // = assignee (agent-created) or human email (supersede)
   approvals: Approval[];     // status→APPROVED when length >= REQUIRED_APPROVALS
   rejectedBy?: string; rejectedAt?: string; rejectionReason?: string;
   supersedes?: string; supersededBy?: string;
   lineageRootId: string;     // first ticket in chain
   idempotencyKey?: string;
   execution?: { startedAt: string; finishedAt?: string;
                 outcome?: "success"|"failure"; message?: string; awsRequestIds?: string[] };
   seq: number;               // optimistic-concurrency token (CAS)
 }

 interface AuditEvent {
   eventId: string; ticketId: string; seq: number; timestamp: string;
   type: "TICKET_CREATED"|"APPROVAL_ADDED"|"APPROVED"|"REJECTED"|"DEPRECATED"
        |"EXPIRED"|"EXECUTION_STARTED"|"EXECUTION_COMPLETED"|"EXECUTION_FAILED";
   actor: { kind: "agent"|"human"|"system"; id: string };
   fromStatus?: TicketStatus; toStatus?: TicketStatus;
   details?: Record<string, unknown>;
 }

 Immutability: every change is an appended AuditEvent; the ticket is a fold over its events. Frozen fields (subject, actionDetails, plannedDate, …) can never change — edits require a superseding ticket.

 Transitions (status-machine.ts):

 ┌─────────────────────────────┬──────────────────────────────┬──────────────────────────────────────────────────────────────────────┐
 │            From             │              To              │                            Actor / guard                             │
 ├─────────────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
 │ PENDING_APPROVAL            │ PENDING_APPROVAL (+approval) │ approver, ≠ proposer, not already approved by them, count < required │
 ├─────────────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
 │ PENDING_APPROVAL            │ APPROVED                     │ same, when approvals reach REQUIRED_APPROVALS                        │
 ├─────────────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
 │ PENDING_APPROVAL            │ REJECTED                     │ approver, ≠ proposer, reason required                                │
 ├─────────────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
 │ PENDING_APPROVAL / APPROVED │ DEPRECATED                   │ human via supersede (Approved only before execution starts)          │
 ├─────────────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
 │ PENDING_APPROVAL / APPROVED │ EXPIRED                      │ system sweep (APPROVAL_TTL_HOURS)                                    │
 ├─────────────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
 │ APPROVED                    │ EXECUTING                    │ agent, caller ARN == assignee, parametersHash echo matches           │
 ├─────────────────────────────┼──────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
 │ EXECUTING                   │ COMPLETED / FAILED           │ agent, caller ARN == assignee                                        │
 └─────────────────────────────┴──────────────────────────────┴──────────────────────────────────────────────────────────────────────┘

 Storage: JSONL store + repository interface

 JSONL append-log (not lowdb): one AuditEvent per line in /data/tickets.jsonl (TICKET_CREATED embeds the full ticket). Append-only is crash-safer than full-file rewrites, matches the event-sourced model, andconverts line-by-line to DynamoDB writes at migration. On boot: stream-read, fold into in-memory Map + indexes (byStatus, byLineageRoot, byIdempotencyKey). Writes serialized through an in-process mutex; fsync after approval/execution events.

 interface TicketRepository {
   createTicket(t: Ticket, created: AuditEvent): Promise<void>;
   getTicket(id: string): Promise<Ticket | null>;
   findByIdempotencyKey(arn: string, key: string): Promise<Ticket | null>;
   queryByStatus(s: TicketStatus, opts: {limit: number; cursor?: string}): Promise<{items: Ticket[]; cursor?: string}>;
   queryLineage(rootId: string): Promise<Ticket[]>;
   listAuditEvents(ticketId: string): Promise<AuditEvent[]>;
   appendEvent(ticketId: string, expectedSeq: number, e: AuditEvent): Promise<Ticket>; // CAS
   transactSupersede(oldId: string, expectedSeq: number, depEvent: AuditEvent,
                     newTicket: Ticket, createdEvent: AuditEvent): Promise<void>;      // atomic
 }

 DynamoDB migration (documented in dynamodb-store.ts stub): single table, PK=TICKET#id / SK=META|EVENT#seq; GSI1 STATUS#status / ticketDate; GSI2 LINEAGE#rootId; GSI3 IDEM#arn#key. appendEvent →TransactWriteItems with ConditionExpression seq = :expected. Swap = STORE_BACKEND=dynamodb, zero call-site changes.

 Auth design

 Human — Auth.js v5, generic OIDC (Entra-swappable)

 - One provider object built entirely from env: OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, OIDC_SCOPES, OIDC_GROUPS_CLAIM. No provider-specific imports. Migrating IAM Identity Center → Entra ID = change these vars only.
 - JWT session strategy (no DB adapter). jwt callback maps groups claim → role: approver|viewer via OIDC_APPROVER_GROUPS; fallback APPROVER_EMAILS allowlist because IAM Identity Center's OIDC group-claim support is weak (verify the org can register a customer-managed OIDC app there — flagged risk).
 - middleware.ts protects everything EXCEPT /login, /api/auth/*, /api/healthz, /api/agent/*, static assets. Pages redirect to /login; /api/tickets/* returns 401 JSON. Approver-role checks live in route handlers.

 Agent — SigV4 via presigned sts:GetCallerIdentity (Vault / aws-iam-authenticator pattern)

 withAgentAuth() wrapper on all /api/agent/* handlers (Node runtime):
 1. Agent sends X-Gate-Identity: base64 JSON of a presigned STS GetCallerIdentity request signed with its own IRSA credentials, which MUST include header X-Gate-Server-Id: <GATE_SERVER_ID> inside SignedHeaders(binds signature to this gate).
 2. Gate validates: STS host/path/body exact-match, X-Amz-Date within ±5 min, server-id present and signed.
 3. Replay check: sha256(signature) against in-memory TTL cache (6 min); TLS mandatory at ingress.
 4. Gate forwards the request verbatim to STS via fetch → parses caller Arn/Account (gate itself needs no STS permission).
 5. Normalize assumed-role ARN; check against ALLOWED_AGENT_ARNS glob allowlist. Attach callerArn to context.

 This path is untouched by any future Entra migration.

 API surface

 Agent (SigV4): POST /api/agent/tickets (create; Idempotency-Key header; gate sets assignee=proposedBy=callerArn, computes parametersHash) · GET /api/agent/tickets/{id} (poll; assignee-only; returns status + supersededBy pointer) · POST .../execution/start (body {parametersHash}; 409 on mismatch; response echoes approved actionDetails — agent must execute from this response) · POST .../execution/result ({outcome,message?, awsRequestIds?}).

 Human (session): GET /api/tickets (filter status/assignee/date, cursor pagination) · GET /api/tickets/{id} ({ticket, lineage[], auditEvents[]}) · POST .../approve (approver role; email ≠ proposedBy; not already approved by them; status PENDING_APPROVAL; latest-in-lineage only; CAS on seq → 409 on race; flips to APPROVED at threshold) · POST .../reject ({reason} required) · POST .../supersede (old ∈ {PENDING_APPROVAL, APPROVED} and not superseded; atomic: new ticket with supersedes/lineageRootId/proposedBy=session email + old → DEPRECATED with supersededBy) · GET /api/me.

 All bodies zod-validated (schemas.ts, shared with forms); errors {error: {code, message}}; every mutation appends its AuditEvent atomically.

 SES notifications (lib/notifications/ses.ts): on TICKET_CREATED, email the approver list (from APPROVER_EMAILS / group mapping) with subject, planned action, and ticket URL. Fire-and-forget with logged failures (never block ticket creation). IRSA policy: ses:SendEmail on the configured identity. Env: SES_FROM_ADDRESS, SES_REGION, NOTIFY_ON_CREATE=true.

 UI (mirrors gammon-powershell-portal)

 Copy from the portal: styles/globals.css HSL brand vars (navy --primary: 217 49% 36%, red --secondary: 0 85% 49%, .section-title) → single app/globals.css + add --success/--warning/--info vars;tailwind.config.ts + components.json; ~15 components/ui/* primitives; components/log-table.tsx → generic ticket-table.tsx (near verbatim); log-columns.tsx pattern → ticket-columns.tsx; sidebar/header trimmed to Dashboard + Tickets; approval-list.tsx ideas only (pending/history tabs, filters, per-row loading interlock, polling that stops on terminal states) split into ≤200-line components.

 Fixes vs the portal: server-component layout.tsx + client providers.tsx; real middleware.ts instead of per-page useEffect guards; react-hook-form + zodResolver in supersede-dialog.tsx; extend badge.tsx CVA with success/warning/info variants; one next config, one globals.css; no ignoreBuildErrors.

 Pages: dashboard (status-count cards + recent table), tickets list (tabs Pending/History, filters, DataTable, 10 s usePolling on pending tab), ticket detail (detail card, lineage chain root→latest, audit timeline, approve/reject with confirm dialogs, supersede dialog).

 MCP-server contract (docs/agent-contract.md — code changes to the MCP server are out of scope; the doc is the deliverable)

 1. Before any mutating EC2 call, build exact actionDetails → POST /api/agent/tickets with Idempotency-Key; surface ticket URL to the operator. Read-only Describe* bypasses the gate.
 2. Poll every 15–30 s (jitter) until: APPROVED → proceed; REJECTED/EXPIRED → stop with reason; DEPRECATED → follow supersededBy and re-confirm.
 3. execution/start echoing parametersHash; execute using the actionDetails from this response; report result with AWS RequestIds.
 4. Strong enforcement (document for the agent-role owner): IAM condition requiring aws:RequestTag/gateTicketId on mutating EC2 actions so ungated calls fail at IAM; optional nightly CloudTrail correlation via awsRequestIds.

 Deployment

 - Dockerfile: deps → build (output:'standalone') → run on node:20-alpine, USER 1001, CMD ["node","server.js"].
 - deploy/k8s/: Deployment replicas: 1, strategy: Recreate (RWO PVC + single-writer store), /data volumeMount, healthz probes, runAsNonRoot/fsGroup 1001 · PVC 1 Gi RWO (gp3) · ServiceAccount with IRSA (ses:SendEmail now; DynamoDB policy later) · ClusterIP Service + Ingress (TLS required) · ConfigMap (OIDC vars, GATE_SERVER_ID, ALLOWED_AGENT_ARNS, REQUIRED_APPROVALS, APPROVAL_TTL_HOURS, STORE_BACKEND=jsonl,DATA_DIR=/data, SES vars) · Secret template (OIDC_CLIENT_SECRET, AUTH_SECRET) — recommend External/Sealed Secrets.
 - lib/env.ts zod schema validates all of the above at boot; invalid env crashes before readiness.

 Build order (each phase ends green on npm run typecheck && npm test)

 1. Scaffold + domain core — create-next-app, brand tokens, env.ts, types, status-machine, canonical-json, schemas. Tests: exhaustive transition matrix, canonical-JSON stability, approval-threshold logic.
 2. Repository — interface, JSONL store, factory. Tests: fold/rebuild, torn-last-line recovery, CAS conflict, transactSupersede atomicity, idempotency, frozen-field immutability.
 3. Agent API — agent-auth + replay cache + 4 routes. Tests (mocked STS fetch): wrong host, missing/unsigned server-id, stale date, replayed signature, non-allowlisted ARN, idempotent create, hash-mismatch 409,wrong-ARN 403.
 4. Human auth + API — Auth.js, rbac, middleware, 6 routes, ticket-service, SES notifier. Tests: approver==proposer rejected, duplicate approver rejected, threshold 1 vs 2 via env, viewer rejected, non-latestrejected, double-approve race 409, reject requires reason, supersede links both tickets.
 5. UI — shell, dashboard, list, detail, dialogs; manual pass + badge-variant component test.
 6. Packaging + docs — Dockerfile, k8s manifests, agent-contract.md, README, .env.example.
 7. Hardening — expiry sweep (interval timer → EXPIRED events), pino structured logging, rate limit on /api/agent/*, DynamoDB store behind flag.

 Verification

 - npm run typecheck && npm test (Vitest) per phase; key suites listed above.
 - End-to-end local run: npm run dev with a stub OIDC issuer (or dev-mode credentials provider guarded by NODE_ENV!=production) + a small script that builds a presigned GetCallerIdentity request with local AWS creds to exercise the full agent flow: create → approve in UI → start (hash echo) → result → verify audit timeline and lineage in UI and in /data/tickets.jsonl.
 - docker build + kubectl apply --dry-run=client -f deploy/k8s/.

 Open risks (accepted / to watch)

 - JSONL on PVC: single-AZ EBS, no PITR — schedule EBS/Velero snapshots; DynamoDB is the durability fix. Single replica means gate-down blocks the agent entirely (acceptable for MVP; HA path = DynamoDB + shared replay cache + ≥2 replicas).
 - Replay window: nonce cache resets on pod restart; bounded by ±5-min SigV4 window + mandatory TLS + server-id binding.
 - Fidelity gap: hash echo proves intent, not the actual AWS call — the IAM gateTicketId request-tag condition is the strong enforcement; coordinate with the agent-role owner.
 - IAM Identity Center OIDC: confirm a customer-managed OIDC app can be registered; group claims are limited → APPROVER_EMAILS fallback exists.
 - Not in v1 (flagged): quorum >2, ticket CSV export/retention policy, CloudTrail correlation job, time-zone display preference (defaults to browser locale).