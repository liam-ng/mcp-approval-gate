import { useQuery } from "@tanstack/react-query"
import { addHours, format } from "date-fns"
import { ArrowLeft } from "lucide-react"
import { Link, useParams } from "react-router-dom"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { ApproveRejectActions } from "@/components/tickets/approve-reject-actions"
import { AuditTimeline } from "@/components/tickets/audit-timeline"
import { CommentForm } from "@/components/tickets/comment-form"
import { LineageChain } from "@/components/tickets/lineage-chain"
import { SupersedeDialog } from "@/components/tickets/supersede-dialog"
import { TagsEditor } from "@/components/tickets/tags-editor"
import { TicketStatusBadge } from "@/components/tickets/ticket-status-badge"
import { api } from "@/lib/api"
import { formatResourceScopeSummary, summarizeResourceScope } from "@/lib/resource-scope"
import { TERMINAL_STATUSES } from "@/lib/types"

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 break-all text-sm">{children}</div>
    </div>
  )
}

export default function TicketDetail() {
  const { id = "" } = useParams()
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 })
  const { data, isLoading } = useQuery({
    queryKey: ["ticket", id],
    queryFn: () => api.getTicket(id),
    refetchInterval: (q) =>
      q.state.data && TERMINAL_STATUSES.has(q.state.data.ticket.status) ? false : 10_000,
  })

  if (isLoading || !data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }
  const { ticket, lineage, auditEvents } = data
  const d = ticket.actionDetails
  const scope = summarizeResourceScope(d)

  // Mirrors backend/app/jobs/expiry.py's _expiry_start: the TTL clock starts
  // at the last approval once APPROVED, otherwise at ticket creation. Only
  // meaningful pre-expiry, since that's the only sweep this cutoff governs.
  const approvalDue =
    me && (ticket.status === "PENDING_APPROVAL" || ticket.status === "APPROVED")
      ? addHours(
          ticket.status === "APPROVED" && ticket.approvals.length
            ? new Date(Math.max(...ticket.approvals.map((a) => new Date(a.approvedAt).getTime())))
            : new Date(ticket.ticketDate),
          me.approvalTtlHours,
        )
      : null

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link to="/tickets">
            <ArrowLeft />
          </Link>
        </Button>
        <h1 className="text-xl font-bold text-primary">{ticket.subject}</h1>
        <TicketStatusBadge status={ticket.status} />
      </div>

      <LineageChain lineage={lineage} currentId={ticket.ticketId} />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle>Change request</CardTitle>
            <div className="flex gap-2">
              {me && <SupersedeDialog ticket={ticket} />}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Ticket ID">
                <span className="font-mono text-xs">{ticket.ticketId}</span>
              </Field>
              <Field label="Ticket date">{format(new Date(ticket.ticketDate), "yyyy-MM-dd HH:mm:ss")}</Field>
              <Field label="Planned date">{ticket.plannedDate}</Field>
              {approvalDue && (
                <Field label="Approval due">{format(approvalDue, "yyyy-MM-dd HH:mm:ss")}</Field>
              )}
              <Field label="Operation">
                <span className="font-mono text-xs">
                  {d.service}:{d.operation} ({d.region})
                </span>
              </Field>
              <Field label="Assignee (AI agent)">
                <span className="font-mono text-xs">{ticket.assignee}</span>
              </Field>
              <Field label="Proposed by">
                <span className="font-mono text-xs">{ticket.proposedBy}</span>
              </Field>
              <Field label="Approved by">
                {ticket.approvals.length
                  ? ticket.approvals.map((a) => (
                      <div key={a.approvedBy}>
                        {a.approvedBy}{" "}
                        <span className="text-xs text-muted-foreground">
                          ({format(new Date(a.approvedAt), "yyyy-MM-dd HH:mm")})
                        </span>
                      </div>
                    ))
                  : "—"}
              </Field>
              <Field label="Tags">
                <div className="flex flex-wrap items-center gap-1">
                  {Object.entries(ticket.tags).length
                    ? Object.entries(ticket.tags).map(([k, v]) => (
                        <Badge key={k} variant="outline" className="font-normal">
                          {k}={v}
                        </Badge>
                      ))
                    : <span className="text-muted-foreground">—</span>}
                  {me && <TagsEditor ticket={ticket} />}
                </div>
              </Field>
            </div>

            <Separator />
            {d.reason && <Field label="Reason for changes">{d.reason}</Field>}
            <Field label="Planned action">{ticket.plannedAction}</Field>
            <Field label="Resources in scope">
              <div className="font-medium">{formatResourceScopeSummary(scope)}</div>
              {d.resourceArns.length > 0 && (
                <ul className="mt-1 space-y-1">
                  {d.resourceArns.map((arn) => (
                    <li key={arn} className="font-mono text-xs text-muted-foreground">
                      {arn}
                    </li>
                  ))}
                </ul>
              )}
            </Field>
            <Field label="Action parameters (AWS boto3 API call parameters; Hash-locked)">
              <pre className="mt-1 max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 text-xs">
                {JSON.stringify(d.parameters, null, 2)}
              </pre>
              <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                sha256: {d.parametersHash}
              </div>
            </Field>
            {ticket.rejectionReason && (
              <Field label="Rejection reason">{ticket.rejectionReason}</Field>
            )}
            {ticket.execution && (
              <Field label="Execution">
                <div className="text-sm">
                  {ticket.execution.outcome ?? "in progress"} —{" "}
                  {ticket.execution.message ?? "no message"}
                  {ticket.execution.awsRequestIds.length > 0 && (
                    <div className="mt-1 font-mono text-xs text-muted-foreground">
                      AWS request ids: {ticket.execution.awsRequestIds.join(", ")}
                    </div>
                  )}
                </div>
              </Field>
            )}

            {me && (
              <>
                <Separator />
                <ApproveRejectActions ticket={ticket} me={me} />
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Audit trail</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <AuditTimeline events={auditEvents} />
            {me && (
              <>
                <Separator />
                <CommentForm ticketId={ticket.ticketId} />
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
