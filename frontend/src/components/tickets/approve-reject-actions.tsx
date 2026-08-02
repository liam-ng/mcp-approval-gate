// Approve/reject with confirm dialogs and mutual interlock while a mutation
// is in flight — the portal approval-list.tsx pattern, without the bulk.
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, Loader2, X } from "lucide-react"
import { toast } from "sonner"
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
  if (me.role !== "approver") {
    return <p className="text-sm text-muted-foreground">Approver role is required to action this ticket.</p>
  }
  if (isProposer) {
    return <p className="text-sm text-muted-foreground">You proposed this ticket — a peer or manager must approve it.</p>
  }
  if (alreadyApproved) {
    return <p className="text-sm text-muted-foreground">You already approved this ticket — waiting for another approver.</p>
  }

  return (
    <div className="flex gap-2">
      <Button variant="success" disabled={busy} onClick={() => setConfirmOpen("approve")}>
        {approve.isPending ? <Loader2 className="animate-spin" /> : <Check />}
        Approve
      </Button>
      <Button variant="destructive" disabled={busy} onClick={() => setConfirmOpen("reject")}>
        {reject.isPending ? <Loader2 className="animate-spin" /> : <X />}
        Reject
      </Button>

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
