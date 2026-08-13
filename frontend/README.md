# Frontend

**Purpose:** let humans review and approve (or reject/supersede) change tickets before the executor can run them.

**What it is:** the React approval portal. Tickets are created by agents/`/mcp`, not here.

## Structure

```
src/
  routes/              Dashboard, tickets list, ticket detail, login, approve-by-link
  components/tickets/  Status badge, lineage, audit timeline, approve/reject/supersede
  components/ui/       shadcn primitives
  components/layout/   Header, sidebar
  lib/                 Typed API client, types, helpers
Dockerfile             Vite build → nginx-unprivileged on :8080
nginx.conf
```

## Workflow

1. User signs in (OIDC in prod, or `AUTH_MODE=dev` login URL locally).
2. Dashboard / tickets list poll via TanStack Query until tickets are terminal.
3. Detail page shows lineage, audit trail, and actions (approve, reject, supersede, tags).
4. All calls go to `/api/*` on the same origin (Vite proxy in dev; HTTPRoute in cluster).

## Run

```bash
npm install
npm run dev      # :5173, proxies /api → :8000
npm run build    # tsc --noEmit && vite build
```
