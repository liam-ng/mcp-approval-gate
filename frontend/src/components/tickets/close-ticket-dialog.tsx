// Withdraw a ticket without executing it (POST /api/tickets/:id/close ->
// a CLOSED audit event). Alternative to reject/supersede for "no longer
// needed" — open to any session role like supersede, not approver-gated
// like approve/reject, since nothing gets executed either way.
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Archive, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api, ApiError } from "@/lib/api"
import type { Ticket } from "@/lib/types"

export function CloseTicketDialog({ ticket }: { ticket: Ticket }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState("")
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => api.close(ticket.ticketId, reason.trim() || undefined),
    onSuccess: () => {
      toast.success("Ticket closed")
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
      queryClient.invalidateQueries({ queryKey: ["ticket", ticket.ticketId] })
      setOpen(false)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Request failed"),
  })

  const canClose =
    (ticket.status === "PENDING_APPROVAL" || ticket.status === "APPROVED") && !ticket.supersededBy
  if (!canClose) return null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Archive />
          Close
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Close this ticket?</DialogTitle>
          <DialogDescription>
            The ticket is withdrawn without executing — no agent will act on it. This is
            recorded in the audit trail and can't be undone; propose a new ticket if the
            change is needed later.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="close-reason">Reason (optional)</Label>
          <Textarea
            id="close-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why is this ticket being closed?"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending && <Loader2 className="animate-spin" />}
            Confirm close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
