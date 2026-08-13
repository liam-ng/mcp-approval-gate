# Backend

**Purpose:** enforce human approval before any agent-driven AWS mutation, and keep an immutable audit trail of what was asked, approved, and run.

**What it is:** the FastAPI gate — ticket store, status machine, human/agent/MCP APIs.

## Structure

```
app/
  core/          Ticket model, status machine, parametersHash, business rules
  repo/          Event-sourced store (jsonl / dynamodb / s3 stubs)
  auth/          Human OIDC session, agent SigV4, MCP bearer verify
  api/           /api/tickets, /api/agent/*, /mcp, auth routes
  jobs/          Expiry sweep for stale PENDING/APPROVED
  notifications/ SES on ticket create (fire-and-forget)
  settings.py    Env validated at import
tests/
Dockerfile
```

## Workflow

1. **Create** — agent (SigV4) or IDE (`/mcp`) opens a ticket with frozen action details; gate computes `parametersHash`.
2. **Approve / reject** — human session in the portal (or email link); approver ≠ proposer.
3. **Execute** — executor calls `execution/start` (must echo the same hash), then `execution/result`.
4. **Supersede** — editing means a new ticket; old one becomes `DEPRECATED` (lineage kept).

Tickets are a fold over append-only audit events. Only `MUTABLE_FIELDS` can change after create.

## Run

```bash
pip install -e ".[dev]"
cp ../.env.example .env
uvicorn app.main:app --reload   # :8000
python -m pytest                # from backend/
```
