// Approve/reject with confirm dialogs and mutual interlock while a mutation
// is in flight — the portal approval-list.tsx pattern, without the bulk.
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, Clock, Info, Loader2, ShieldAlert, TriangleAlert, X } from "lucide-react"
import { toast } from "sonner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api, ApiError } from "@/lib/api"
import type { Me, Ticket } from "@/lib/types"

export function ApproveRejectActions({ ticket, me }: { ticket: Ticket; me: Me }) {
  const queryClient = useQueryClient()
  const [confirmOpen, setConfirmOpen] = useState<"approve" | "reject" | null>(null)
  const [reason, setReason] = useState("")

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["tickets"] })
    queryClient.invalidateQueries({ queryKey: ["ticket", ticket.ticketId] })
  }
  const onError = (e: unknown) =>
    toast.error(e instanceof ApiError ? e.message : "Request failed")

  const approve = useMutation({
    mutationFn: () => api.approve(ticket.ticketId),
    onSuccess: (t) => {
      toast.success(t.status === "APPROVED" ? "Ticket approved" : "Approval recorded — more approvals required")
      setConfirmOpen(null)
      invalidate()
    },
    onError,
  })
  const reject = useMutation({
    mutationFn: () => api.reject(ticket.ticketId, reason),
    onSuccess: () => {
      toast.success("Ticket rejected")
      setConfirmOpen(null)
      invalidate()
    },
    onError,
  })

  const busy = approve.isPending || reject.isPending
  if (ticket.status !== "PENDING_APPROVAL") return null

  const isProposer = ticket.proposedBy.toLowerCase() === me.email.toLowerCase()
  const alreadyApproved = ticket.approvals.some(
    (a) => a.approvedBy.toLowerCase() === me.email.toLowerCase(),
  )
  // These three are mutually exclusive and render in the same slot, so they
  // share the callout treatment — a plain line of text next to a callout in
  // the same position reads as an inconsistency rather than a hierarchy.
  if (me.role !== "approver") {
    return (
      <Alert variant="info">
        <ShieldAlert />
        <div className="space-y-1">
          <AlertTitle>Approver role required</AlertTitle>
          <AlertDescription>
            Your account has the viewer role, so you can review this ticket but not approve or
            reject it.
          </AlertDescription>
        </div>
      </Alert>
    )
  }
  if (isProposer && !me.allowSelfApproval) {
    return (
      <Alert variant="info">
        <Info />
        <div className="space-y-1">
          <AlertTitle>You proposed this ticket</AlertTitle>
          <AlertDescription>
            A peer or manager must approve it — the proposer can't approve or reject their own
            change request.
          </AlertDescription>
        </div>
      </Alert>
    )
  }
  if (alreadyApproved) {
    return (
      <Alert variant="info">
        <Clock />
        <div className="space-y-1">
          <AlertTitle>You already approved this ticket</AlertTitle>
          <AlertDescription>
            Waiting for another approver before the agent can execute it.
          </AlertDescription>
        </div>
      </Alert>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      {isProposer && (
        <Alert variant="warning">
          <TriangleAlert />
          <div className="space-y-1">
            <AlertTitle>Self-approval enabled</AlertTitle>
            <AlertDescription>
              You proposed this ticket. This gate has <code>ALLOW_SELF_APPROVAL</code> turned on, so
              you can approve your own request — the usual peer review is being bypassed.
            </AlertDescription>
          </div>
        </Alert>
      )}
      <div className="flex gap-2">
        <Button variant="success" disabled={busy} onClick={() => setConfirmOpen("approve")}>
          {approve.isPending ? <Loader2 className="animate-spin" /> : <Check />}
          Approve
        </Button>
        <Button variant="destructive" disabled={busy} onClick={() => setConfirmOpen("reject")}>
          {reject.isPending ? <Loader2 className="animate-spin" /> : <X />}
          Reject
        </Button>
      </div>

      <Dialog open={confirmOpen === "approve"} onOpenChange={(o) => !o && setConfirmOpen(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve this change request?</DialogTitle>
            <DialogDescription>
              The AI agent will be allowed to execute{" "}
              <code>
                {ticket.actionDetails.service}:{ticket.actionDetails.operation}
              </code>{" "}
              with exactly the recorded parameters.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="success" onClick={() => approve.mutate()} disabled={busy}>
              {approve.isPending && <Loader2 className="animate-spin" />}
              Confirm approval
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirmOpen === "reject"} onOpenChange={(o) => !o && setConfirmOpen(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject this change request?</DialogTitle>
            <DialogDescription>The agent will be told the request was rejected.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="reject-reason">Reason (required, min 5 characters)</Label>
            <Textarea
              id="reject-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why is this change being rejected?"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(null)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => reject.mutate()}
              disabled={busy || reason.trim().length < 5}
            >
              {reject.isPending && <Loader2 className="animate-spin" />}
              Confirm rejection
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
