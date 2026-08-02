# MCP Approval Gate

A **blocking approval gate** that adds auditability, traceability, and human
control to changes an AI agent (AWS MCP server) makes on AWS. Before any
mutating EC2 action, the agent must create a change-request ticket, wait for
human approval in the web portal, execute exactly the approved parameters, and
report the result.

- **Immutable tickets** — every change is an appended audit event; editing a
  submitted ticket creates a *superseding* ticket and marks the original
  `DEPRECATED`, preserving the full lineage.
- **Human auth**: provider-agnostic OIDC (IAM Identity Center or Entra ID).
- **Agent auth**: IAM SigV4 via presigned `sts:GetCallerIdentity` — no shared
  secrets.
- **Approval rules**: 1 or 2 required approvals,
  approvers must be distinct and never the proposer.
- **Storage**: JSONL append-log on a PVC (MVP) behind a repository interface
  shaped for **DynamoDB** or **S3 + Object Lock (WORM)**.
- **IDE distribution**: end users add *this gate* to Cursor/VS Code as a
  remote MCP tool (`/mcp`, OAuth2.1) — never the upstream AWS MCP server
  directly, which is network-isolated (Istio) and SCP-restricted so it's
  unreachable any other way.

Note that this is a monolithic design as MVP project.

See [docs/plan.md](docs/plan.md) (living plan),
[docs/agent-contract.md](docs/agent-contract.md) (MCP-server integration),
and [docs/mcp-gateway.md](docs/mcp-gateway.md) (IDE/MCP setup, OAuth flow,
Istio + SCP isolation).

## Architecture

Single container: FastAPI serves `/api/*` and the built React SPA.

```
frontend/   React 19 + Vite + shadcn/ui + TanStack Query/Table (portal brand tokens)
backend/    FastAPI + Pydantic v2; core domain, repo layer, auth, SES notifier, /mcp gateway
deploy/k8s/ Deployment (1 replica, Recreate), PVC, IRSA ServiceAccount, Ingress (TLS), Istio isolation
deploy/scp/ AWS Organizations SCP restricting mutating EC2 actions to the gate's executor role
```

## Ticket lifecycle

```
PENDING_APPROVAL ─approve(n≥required)→ APPROVED ─start(hash echo)→ EXECUTING → COMPLETED|FAILED
   │        │                            │   │
   │        └reject→ REJECTED            │   └supersede→ DEPRECATED
   └supersede→ DEPRECATED                └TTL→ EXPIRED
   └TTL→ EXPIRED
```

## Local development

```bash
# Backend (Python 3.12)
cd backend
pip install -e ".[dev]"
cp ../.env.example .env
uvicorn app.main:app --reload          # http://localhost:8000

# Frontend
cd frontend
npm install
npm run dev                            # http://localhost:5173, proxies /api

# Dev login (AUTH_MODE=dev): http://localhost:8000/api/auth/login?email=you@x.com&role=approver
```

Tests:

```bash
cd backend && python -m pytest
cd frontend && npm run build           # includes tsc --noEmit
```

## Deployment

```bash
docker build -t REGISTRY/mcp-approval-gate:TAG .
kubectl apply -f deploy/k8s/           # edit configmap/secret/ingress first
```

Secrets (`SESSION_SECRET`, `OIDC_CLIENT_SECRET`) should come from External
Secrets / Sealed Secrets — `deploy/k8s/secret.yaml` is a template only.

## Security notes

- TLS is mandatory end-to-end; agent identity headers are replay-protected
  (single-use signatures, ±5 min window, gate-bound `X-Gate-Server-Id`).
- The gate needs **no** AWS permissions to verify agents; IRSA is used for
  SES (and later DynamoDB/S3).
- The gate proves the agent's *intent* matches the approval (hash echo). For
  hard enforcement, add an IAM condition on the agent role requiring the
  `gateTicketId` request tag — see docs/agent-contract.md.
