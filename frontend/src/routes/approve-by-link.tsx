// Landing page for the signed approve/reject links mailed by the backend
// (notifications/ses.py -> api/approval_link_actions.py). Deliberately
// outside <Shell> in App.tsx: Header/Sidebar call api.me(), which would
// bounce an unauthenticated approver straight to /login before they ever
// see this page. No session is used or required here — the token in the
// URL is the only credential, and GET never mutates anything (safe against
// email-client link prefetching); only the explicit "Confirm" click below
// fires the POST that actually approves or rejects.
import { useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { CheckCircle2, Loader2, ShieldCheck, XCircle } from "lucide-react"
import { useSearchParams } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { TicketStatusBadge } from "@/components/tickets/ticket-status-badge"
import { api, ApiError } from "@/lib/api"
import { formatResourceScopeSummary, summarizeResourceScope } from "@/lib/resource-scope"
import type { ApprovalLinkPreview as ApprovalLinkPreviewT } from "@/lib/types"

// One message per reason the backend can refuse to act (approval_link_actions.py's
// blocked_reason) — same courtesy the portal gives a session approver
// (approve-reject-actions.tsx) for the equivalent role/proposer/duplicate checks.
const BLOCKED_MESSAGES: Record<
  NonNullable<ApprovalLinkPreviewT["blockedReason"]> | "already_actioned",
  (p: ApprovalLinkPreviewT) => { title: string; description: React.ReactNode }
> = {
  already_actioned: (p) => ({
    title: "Already actioned",
    description: (
      <>
        <span className="font-medium">{p.subject}</span> is no longer pending approval (status:{" "}
        <TicketStatusBadge status={p.status} />). No action needed — sign in to the portal for the
        full history.
      </>
    ),
  }),
  not_approver: () => ({
    title: "Approver access required",
    description:
      "Only approvers can approve or reject a change request, and this address is no longer on the approver list. Sign in to the portal if you believe this is a mistake.",
  }),
  self_approval: () => ({
    title: "You proposed this ticket",
    description: "A ticket's proposer can't approve or reject their own request — a peer or manager must action it instead.",
  }),
  duplicate_approval: () => ({
    title: "Already approved",
    description: "You already approved this ticket — it's waiting on another approver.",
  }),
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-background to-muted/50 p-4">
      <Card className="w-full max-w-lg">{children}</Card>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 break-all text-sm">{children}</div>
    </div>
  )
}

export default function ApproveByLink() {
  const [params] = useSearchParams()
  const token = params.get("token") ?? ""
  const [reason, setReason] = useState("")

  const { data: preview, isLoading, error } = useQuery({
    queryKey: ["link-preview", token],
    queryFn: () => api.previewByLink(token),
    enabled: !!token,
    retry: false,
  })

  const act = useMutation({
    mutationFn: () => api.actByLink(token, reason),
  })

  if (!token) {
    return (
      <Shell>
        <CardHeader className="items-center text-center">
          <XCircle className="mb-2 h-10 w-10 text-destructive" />
          <CardTitle>Missing link</CardTitle>
          <CardDescription>This page needs the token from the email link.</CardDescription>
        </CardHeader>
      </Shell>
    )
  }

  if (isLoading) {
    return (
      <Shell>
        <CardContent className="space-y-3 pt-6">
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Shell>
    )
  }

  if (error || !preview) {
    const message = error instanceof ApiError ? error.message : "This link could not be verified."
    return (
      <Shell>
        <CardHeader className="items-center text-center">
          <XCircle className="mb-2 h-10 w-10 text-destructive" />
          <CardTitle>Link invalid or expired</CardTitle>
          <CardDescription>{message} Sign in to the portal to review the ticket instead.</CardDescription>
        </CardHeader>
      </Shell>
    )
  }

  if (act.isSuccess) {
    const result = act.data
    return (
      <Shell>
        <CardHeader className="items-center text-center">
          <CheckCircle2 className="mb-2 h-10 w-10 text-success" />
          <CardTitle>
            {result.action === "approve" ? "Approval recorded" : "Ticket rejected"}
          </CardTitle>
          <CardDescription>
            <span className="font-medium">{preview.subject}</span> is now{" "}
            <TicketStatusBadge status={result.status} />.
          </CardDescription>
        </CardHeader>
      </Shell>
    )
  }

  if (!preview.actionable) {
    // Mirrors the portal's approve-reject-actions.tsx: explain why the
    // confirm button isn't offered instead of just failing on click.
    const { title, description } = BLOCKED_MESSAGES[preview.blockedReason ?? "already_actioned"](
      preview,
    )
    return (
      <Shell>
        <CardHeader className="items-center text-center">
          <ShieldCheck className="mb-2 h-10 w-10 text-muted-foreground" />
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
      </Shell>
    )
  }

  const d = preview.actionDetails
  const scope = summarizeResourceScope(d)
  const isApprove = preview.action === "approve"

  return (
    <Shell>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-6 w-6 text-primary" />
          <CardTitle>{isApprove ? "Approve this change request?" : "Reject this change request?"}</CardTitle>
        </div>
        <CardDescription>
          {isApprove
            ? "The AI agent will be allowed to execute exactly the parameters recorded below."
            : "The agent will be told the request was rejected."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Subject">{preview.subject}</Field>
          <Field label="Planned date">{preview.plannedDate}</Field>
          <Field label="Operation">
            <span className="font-mono text-xs">
              {d.service}:{d.operation} ({d.region})
            </span>
          </Field>
          <Field label="Proposed by">
            <span className="font-mono text-xs">{preview.proposedBy}</span>
          </Field>
        </div>
        <Field label="Planned action">{preview.plannedAction}</Field>
        <Field label="Resources in scope">{formatResourceScopeSummary(scope)}</Field>
        <Field label="Action parameters (hash-locked)">
          <pre className="mt-1 max-h-40 overflow-auto rounded-md border bg-muted/40 p-3 text-xs">
            {JSON.stringify(d.parameters, null, 2)}
          </pre>
        </Field>

        {!isApprove && (
          <div className="space-y-2">
            <Label htmlFor="reject-reason">Reason (required, min 5 characters)</Label>
            <Textarea
              id="reject-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this change being rejected?"
            />
          </div>
        )}

        {act.isError && (
          <p className="text-sm text-destructive">
            {act.error instanceof ApiError ? act.error.message : "Request failed"}
          </p>
        )}

        <Separator />
        <Button
          className="w-full"
          variant={isApprove ? "success" : "destructive"}
          disabled={act.isPending || (!isApprove && reason.trim().length < 5)}
          onClick={() => act.mutate()}
        >
          {act.isPending && <Loader2 className="animate-spin" />}
          {isApprove ? "Confirm approval" : "Confirm rejection"}
        </Button>
      </CardContent>
    </Shell>
  )
}
