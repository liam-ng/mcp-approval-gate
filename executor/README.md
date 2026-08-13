# Executor

**Purpose:** after approval, run exactly the ticket’s parameters against AWS and report success or failure — nothing more, nothing less.

**What it is:** a headless poller (botocore). Shares a pod/identity with `aws-api-mcp-server` but does not execute through it.

## Structure

```
app/
  main.py         Poll loop, SIGTERM-friendly sleep, process one ticket at a time
  gate_client.py  SigV4 identity header + agent API (list / start / result)
  aws_exec.py     botocore call from actionDetails (or DRY_RUN log)
  settings.py     Env validated at import (bad config = crash at boot)
Dockerfile
requirements.txt
```

## Workflow

1. Poll `GET /api/agent/tickets?status=APPROVED` (own assignee only).
2. `execution/start` with the ticket’s `parametersHash` — gate rejects drift (`409`).
3. Run the approved `service` / `operation` / `region` / parameters via botocore.
4. `execution/result` success or failure (never leave a ticket stuck in `EXECUTING`).

`DRY_RUN` defaults **on**: tickets still close, but no real AWS mutation. Single replica only (no distributed lock).

## Run

```bash
pip install -r requirements.txt
GATE_SERVER_ID=dev python -m app.main

# CI / smoke (from repo root)
./pipelines/executor/ci.sh test
./pipelines/executor/ci.sh smoke   # needs a built image
```
