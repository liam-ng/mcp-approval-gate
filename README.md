# MCP Approval Gate

A **blocking approval gate** that adds auditability, traceability, and human
control to changes an AI agent (AWS MCP server). Before any mutating action, the agent must create a change-request ticket, wait for
human approval through mail or web portal, execute exactly the approved parameters, and report the result.

## README

| Component | Purpose |
|-----------|---------|
| [frontend](frontend/README.md) | Human review/approve portal. Does not create tickets. |
| [backend](backend/README.md) | Gate API + ticket store: approve before mutate, immutable audit log. |
| [executor](executor/README.md) | After approval, run exact ticket params on AWS and report the result. |

## Features

- **Audit Trail** — every change is an appended audit event (local or append-only S3 bucket or dynamoDB).
- **Approval rules**: 1 or 2 tiers of required approvals, approvers must be distinct other than the proposer.
- **IDE distribution**: this serves as a MCP tool to Cursor/VS Code via Streamable HTTP with OAuth2.1

- **AWS Cognito Identities**: provider-agnostic OIDC (user pool or IAM Identity Center or Entra ID).
- **Agent auth**: IAM SigV4 via presigned `sts:GetCallerIdentity` — no shared secrets.
- **Network Isolation**: never the upstream AWS MCP server directly, which is network-isolated (Istio) and SCP-restricted so it's
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
executor/   Headless poller that performs approved tickets
deploy/iam/ IRSA trust policy + inline policy for the executor role, and a
            separate Describe-only role for the portal form's resource pickers
deploy/scp/ AWS Organizations SCP restricting mutating EC2 actions to the gate's executor role
```

**Kubernetes manifests live in a different repo**:
[`liam-ng/liam-dev-k8s-argoCD`](https://github.com/liam-ng/liam-dev-k8s-argoCD),
under `apps/mcp-approval-gate/` (kustomize `base/` + `overlays/liam-dev` +
`overlays/template`), reconciled by Argo CD. They used to be `deploy/k8s/` here.
Only the AWS JSON policy documents above stayed behind — nothing reconciles those.

One cross-repo coupling to keep in mind: `frontend/vite.config.ts`'s dev-proxy
prefix list (`/api`, `/mcp`, `/.well-known`) must stay in step with that repo's
`base/httproute.yaml`. Change one, change the other.

## Ticket lifecycle

```
PENDING_APPROVAL ─approve(n≥required)→ APPROVED ─start(hash echo)→ EXECUTING → CLOSED|FAILED
   │        │      │                     │   │      │
   │        │      └close→ CLOSED        │   │      └close→ CLOSED
   │        └reject→ REJECTED            │   └supersede→ DEPRECATED
   └supersede→ DEPRECATED                └TTL→ EXPIRED
   └TTL→ EXPIRED
```

`close` withdraws a PENDING_APPROVAL/APPROVED ticket without executing it — any signed-in user, not approver-gated like approve/reject.

## Local development

```bash
# Backend (Python 3.14)
cd backend
# py -m venv .venv-mcp-gate
# source .venv-mcp-gate/bin/activate
pip install -e ".[dev]"
cp ../.env.example .env
uvicorn app.main:app --reload          # http://localhost:8000
# ENV_FILE=.env.liam-mcp uvicorn app.main:app --reload --port 8001

# Frontend
cd frontend
# npx npm-check-updates -u
npm install
npm run dev                            # http://localhost:5173, proxies /api

# Dev login (AUTH_MODE=dev): http://localhost:8000/api/auth/login?email=you@x.com&role=approver
```

## Tests

```bash
cd backend && python -m pytest
cd frontend && npm run build           # includes tsc --noEmit
```

## Deployment

Three images, one per deployable, built and pushed by their own workflows on
merge to `main`:

```bash
docker build -t <REGISTRY>/mcp-approval-gate-backend:TAG ./backend
docker build -t <REGISTRY>/mcp-approval-gate-frontend:TAG ./frontend
docker build -t <REGISTRY>/mcp-approval-gate-executor:TAG ./executor
```

Rollout is GitOps, not `kubectl`: Argo CD reconciles
[`liam-ng/liam-dev-k8s-argoCD`](https://github.com/liam-ng/liam-dev-k8s-argoCD)
`apps/mcp-approval-gate/overlays/liam-dev`. To render or drift-check from a
clone of that repo:

```bash
kubectl kustomize apps/mcp-approval-gate/overlays/liam-dev | kubectl diff -f -
```

Secrets (`SESSION_SECRET`, `OIDC_CLIENT_SECRET`) come from Azure Key Vault via
External Secrets — see that overlay's `eso-*.yaml`. No secret value belongs in
either repo.

## Security notes

- TLS is mandatory end-to-end; agent identity headers are replay-protected
  (single-use signatures, ±5 min window, gate-bound `X-Gate-Server-Id`).
- The gate needs **no** AWS permissions to verify agents; IRSA is used for
  SES (and later DynamoDB/S3).
- The gate proves the agent's *intent* matches the approval (hash echo). For
  hard enforcement, add an IAM condition on the agent role requiring the
  `gateTicketId` request tag — see docs/agent-contract.md.
