// Supersede = the only way to modify a submitted ticket: creates a new
// PENDING_APPROVAL ticket and deprecates this one. react-hook-form + zod.
import { useState } from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { FilePen, Loader2 } from "lucide-react"
import { useForm } from "react-hook-form"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { z } from "zod"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api, ApiError } from "@/lib/api"
import { formatResourceScopeSummary, summarizeResourceScope } from "@/lib/resource-scope"
import type { Ticket } from "@/lib/types"

const schema = z.object({
  subject: z.string().min(3).max(200),
  plannedDate: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "YYYY-MM-DD"),
  plannedAction: z.string().min(3).max(500),
  parameters: z.string().refine((s) => {
    try {
      const v = JSON.parse(s)
      return typeof v === "object" && v !== null && !Array.isArray(v)
    } catch {
      return false
    }
  }, "must be a JSON object"),
  reason: z.string().max(500).optional(),
})
type FormValues = z.infer<typeof schema>

export function SupersedeDialog({ ticket }: { ticket: Ticket }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      subject: ticket.subject,
      plannedDate: ticket.plannedDate,
      plannedAction: ticket.plannedAction,
      parameters: JSON.stringify(ticket.actionDetails.parameters, null, 2),
      reason: ticket.actionDetails.reason ?? "",
    },
  })

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      api.supersede(ticket.ticketId, {
        subject: values.subject,
        plannedDate: values.plannedDate,
        plannedAction: values.plannedAction,
        actionDetails: {
          service: ticket.actionDetails.service,
          operation: ticket.actionDetails.operation,
          region: ticket.actionDetails.region,
          parameters: JSON.parse(values.parameters),
          resourceArns: ticket.actionDetails.resourceArns,
          reason: values.reason || null,
        },
        tags: ticket.tags,
      }),
    onSuccess: (newTicket) => {
      toast.success("Superseding ticket created — the previous ticket is now deprecated")
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
      queryClient.invalidateQueries({ queryKey: ["ticket"] })
      setOpen(false)
      navigate(`/tickets/${newTicket.ticketId}`)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Request failed"),
  })

  const canSupersede =
    (ticket.status === "PENDING_APPROVAL" || ticket.status === "APPROVED") && !ticket.supersededBy
  if (!canSupersede) return null

  const errors = form.formState.errors

  // Live preview: operation/resourceArns are fixed (not editable here), only
  // parameters can change, so re-derive the scope summary as the user types.
  const watchedParameters = form.watch("parameters")
  let scopeSummary: string | null = null
  try {
    scopeSummary = formatResourceScopeSummary(
      summarizeResourceScope({
        service: ticket.actionDetails.service,
        operation: ticket.actionDetails.operation,
        parameters: JSON.parse(watchedParameters),
        resourceArns: ticket.actionDetails.resourceArns,
      }),
    )
  } catch {
    scopeSummary = null // invalid JSON mid-edit; parameters' own error already shows
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <FilePen />
          Supersede
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Supersede this ticket</DialogTitle>
          <DialogDescription>
            Submitted tickets are immutable. Your edit becomes a new ticket requiring fresh
            approval; this one is marked Deprecated. You become the proposer, so you cannot
            approve the new ticket yourself.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
        >
          <div className="space-y-2">
            <Label htmlFor="subject">Subject</Label>
            <Input id="subject" {...form.register("subject")} />
            {errors.subject && <p className="text-xs text-destructive">{errors.subject.message}</p>}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="plannedDate">Planned date</Label>
              <Input id="plannedDate" type="date" {...form.register("plannedDate")} />
              {errors.plannedDate && (
                <p className="text-xs text-destructive">{errors.plannedDate.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label>Operation (fixed)</Label>
              <Input
                disabled
                value={`${ticket.actionDetails.service}:${ticket.actionDetails.operation}`}
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="reason">Reason for changes</Label>
            <Textarea
              id="reason"
              {...form.register("reason")}
              placeholder="Why is the original ticket being replaced?"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="plannedAction">Planned action (summary)</Label>
            <Textarea id="plannedAction" {...form.register("plannedAction")} />
            {errors.plannedAction && (
              <p className="text-xs text-destructive">{errors.plannedAction.message}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label>Resources in scope</Label>
            <p className="text-sm text-muted-foreground">{scopeSummary ?? "—"}</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="parameters">Action parameters (JSON)</Label>
            <Textarea id="parameters" className="min-h-32 font-mono text-xs" {...form.register("parameters")} />
            {errors.parameters && (
              <p className="text-xs text-destructive">{errors.parameters.message}</p>
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending && <Loader2 className="animate-spin" />}
              Create superseding ticket
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
