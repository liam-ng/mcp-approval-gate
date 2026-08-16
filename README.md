# MCP Approval Gate

A **blocking approval gate** that adds auditability, traceability, and human control to changes an IDE AI agent made. Before any mutating action, the agent must create a change-request ticket, wait for human approval through mail or web portal, an executor will carry out exactly the approved parameters, and report the result back to portal.

<img width="1459" height="792" alt="image" src="https://github.com/user-attachments/assets/4e8b9fb9-e91d-42bd-a028-85dacb396ef5" />


## MCP Approval Gate Components


| Pods/Containers                | Purpose                                                                                                                | Port                   | Tech stack                                                                                    |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------- | ---------------------- | --------------------------------------------------------------------------------------------- |
| [frontend](frontend/README.md) | Dashboard Portal, review/approve workflow and ticket management.                                                       | `80 -> 8080`           | React 19, TypeScript, Vite, Tailwind; served by nginx-unprivileged (UID 101)                  |
| [backend](backend/README.md)   | Ticket API + data store (JSONL style): ticket handling, immutable audit log. Serves `/api/*`, `/mcp`, `/.well-known`. | `80 -> 8000`           | Python 3.14, uvicorn, FastAPI, official `mcp` SDK (Streamable HTTP)                           |
| [executor](executor/README.md) | Polls approved ticket, execute exact ticket params on AWS and report the result.                                       | `none` — outbound only | Container (1) Python 3.14 + boto3, (2)`aws-api-mcp-server`. No web framework, **no listener** |


Frontend and backend are separate images served by HTTPRoute
(F5 NGINX Gateway Fabric) that splits traffic by path, so the browser
sees a single origin and the session cookie needs no CORS setup.


## Features

- **Audit Trail** — every change is an appended audit event (local or append-only S3 bucket or dynamoDB).
- **Traceable resources** — the gate injects `TagSpecifications` carrying the ticket id into resource.
- **Approval rules**: 1 or 2 tiers of required approvals, approvers must be distinct other than the proposer.
- **AWS Cognito Identities**: provider-agnostic OIDC (user pool or IAM Identity Center or Entra ID).
- **IDE distribution**: this serves as a MCP tool to Cursor/VS Code via Streamable HTTP with OAuth2.1
- **Agent auth**: IAM SigV4 via presigned `sts:GetCallerIdentity` — no shared secrets.
- **Network Isolation**: never the upstream AWS MCP server directly, which is network-isolated (Istio) and SCP-restricted so it's
unreachable any other way.



## Architecture

**Kubernetes manifests live in a different repo**: kustomize `base/` + `overlays/` + `overlays/template`), reconciled by Argo CD.
One cross-repo coupling to keep in mind: `frontend/vite.config.ts`'s dev-proxy prefix list (`/api`, `/mcp`, `/.well-known`) must stay in step with that repo's `base/httproute.yaml`.

<img width="2532" height="1481" alt="image" src="https://github.com/user-attachments/assets/5f41c529-6cd7-45c2-b36e-776774f4549c" />


## Auth Flow

<img width="1908" height="967" alt="image" src="https://github.com/user-attachments/assets/904c7e8b-b67b-4ca7-8315-f33c91c31a94" />

### Ticket Lifecycle
```
PENDING_APPROVAL ─approve(n≥required)→ APPROVED ─start(hash echo)→ EXECUTING → CLOSED|FAILED
   │        │      │                     │   │      │
   │        │      └close→ CLOSED        │   │      └close→ CLOSED
   │        └reject→ REJECTED            │   └supersede→ DEPRECATED
   └supersede→ DEPRECATED                └TTL→ EXPIRED
   └TTL→ EXPIRED
```

`close` withdraws a PENDING_APPROVAL/APPROVED ticket without executing it — any signed-in user, not subject to approval workflow.

A `FAILED` or `CLOSED` ticket can still be followed up: that links the new
ticket to the old one and leaves the original status untouched, so the record
that AWS was already touched survives the retry.

At execution `start`, the executor echoes the `parametersHash` and **the gate** decides —
a mismatch or invalid parameters  and the ticket stays put. The AWS call is then made by the
executor's own boto3 client, byte-verbatim. The `aws-api-mcp-server` container
takes CLI *command strings*.

## Local development

```bash
# ----- Backend -----

cd backend
# py -m venv .venv-mcp-gate
# source .venv-mcp-gate/bin/activate
pip install -e ".[dev]"

cp ../.env.example .env
uvicorn app.main:app --reload          # http://localhost:8000

# ENV_FILE=.env.liam-mcp uvicorn app.main:app --reload --port 8000

# ----- Frontend -----

cd frontend
# npx npm-check-updates -u
npm install
npm run dev                            # http://localhost:5173, proxies /api

# Dev login (AUTH_MODE=dev): http://localhost:8000/api/auth/login?email=you@x.com&role=approver
```



## Tests

```bash
cd backend  && python -m pytest        # MUST run from backend/ — pyproject sets asyncio_mode
cd executor && python -m pytest
cd frontend && npm run build           # includes tsc --noEmit
```

`backend/tests/conftest.py` points `ENV_FILE` at a nonexistent path, so every
test declares the settings it needs. Without that, a local `backend/.env.*`
supplies mandatory values that CI does not have, and a test that forgot one
passes locally and fails in the pipeline.

## Delivery

Three images, one per deployable, built and pushed by their own workflows on
merge to `main`:

```bash
docker build -t <REGISTRY>/mcp-approval-gate-backend:TAG ./backend
docker build -t <REGISTRY>/mcp-approval-gate-frontend:TAG ./frontend
docker build -t <REGISTRY>/mcp-approval-gate-executor:TAG ./executor
```



# (NOT Covered) Deployment

<img width="1080" height="480" alt="image" src="https://github.com/user-attachments/assets/c5ab50ac-85bd-47de-a6df-afbcd2b563be" />


Current designed rollout pattern is GitOps, not `kubectl`. Argo CD reconciles the manifests which is not included in this repo.
To render or drift-check from a clone of that repo:

```bash
kubectl kustomize apps/mcp-approval-gate/overlays/liam-dev | kubectl diff -f -
```

- Secrets (`SESSION_SECRET`, `OIDC_CLIENT_SECRET`) come from Azure Key Vault via External Secrets — see overlay's `eso-*.yaml`. No secret value stored in either repo.
- Argo CD `prune` deletes cluster objects that leave the overlay, so removing a manifest from git is the undeploy path (one Application per app, so the blast radius is that namespace).
- Argo CD `selfHeal` reverts live drift back to the kustomize overlay — git is the desired state, not a one-shot `kubectl apply`.
- ArgoCD repo stores one read-only Deploy Key for ArgoCD to monitor changes to the repo, and one writeable Deploy Key for APP CI Workflow to push image tags to the k8s manifest.


## Security notes

- TLS is mandatory end-to-end; agent identity headers are replay-protected
(single-use signatures, ±5 min window, gate-bound `X-Gate-Server-Id`).
- Calico Network Policy isolates the gate from the agent and vice versa.
- Istio SCP restricts the gate's access to the agent to the MCP server's endpoints. 
- The gate needs **no** AWS permissions to verify agents — it forwards the presigned request to STS and trusts the ARN that comes back. 
- IRSA can be used for SES (and later DynamoDB/S3). 
- One opt-in exception: `AWS_DISCOVERY_ENABLED` let backend assume a *separate, Describe-only* role so the portal ticket create form can offer real subnets and AMIs. The default value is `False`.
- Hash echo proves the agent's *intent* matches the approval, and enforced by the executor's identity policy and the SCP, both deny resource creation unless the request carries `aws:RequestTag/gateTicketId`.

