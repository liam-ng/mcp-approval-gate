// Changes a ticket's tags in place (POST /api/tickets/:id/tags -> a
// TAGS_UPDATED audit event) without superseding — tags are metadata, not
// part of the hash-locked action, so this doesn't need a fresh approval.
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react"
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
import { Input } from "@/components/ui/input"
import { api, ApiError } from "@/lib/api"
import type { Ticket } from "@/lib/types"

// Set by the gate at creation (service.build_ticket) and reasserted server-side
// on every update (service.update_tags) — never user-editable. gateTicketId
// also backs IAM enforcement (aws:RequestTag/gateTicketId), so it must always
// match this ticket's own id.
const RESERVED_TAG_KEYS = ["gateTicketId", "owner"]

export function TagsEditor({ ticket }: { ticket: Ticket }) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<{ key: string; value: string }[]>([])
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (tags: Record<string, string>) => api.updateTags(ticket.ticketId, tags),
    onSuccess: () => {
      toast.success("Tags updated")
      queryClient.invalidateQueries({ queryKey: ["ticket", ticket.ticketId] })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
      setOpen(false)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Request failed"),
  })

  function handleOpenChange(next: boolean) {
    if (next) {
      setRows(
        Object.entries(ticket.tags)
          .filter(([key]) => !RESERVED_TAG_KEYS.includes(key))
          .map(([key, value]) => ({ key, value })),
      )
    }
    setOpen(next)
  }

  function submit() {
    const tags: Record<string, string> = {}
    for (const { key, value } of rows) {
      const k = key.trim()
      if (k) tags[k] = value
    }
    mutation.mutate(tags)
  }

  if (ticket.supersededBy) return null

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">
          <Pencil className="h-3.5 w-3.5" />
          Edit tags
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Edit tags</DialogTitle>
          <DialogDescription>
            Tags are metadata, not part of the approved action — this updates the ticket
            directly and is recorded in the audit trail, no new approval required.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1 rounded-md border bg-muted/40 p-2 text-xs text-muted-foreground">
          {RESERVED_TAG_KEYS.map((key) => (
            <div key={key}>
              {key}={ticket.tags[key] ?? "—"} <span className="italic">(fixed)</span>
            </div>
          ))}
        </div>
        <div className="space-y-2">
          {rows.map((row, i) => (
            <div key={i} className="flex gap-2">
              <Input
                placeholder="key"
                value={row.key}
                onChange={(e) =>
                  setRows(rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))
                }
              />
              <Input
                placeholder="value"
                value={row.value}
                onChange={(e) =>
                  setRows(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))
                }
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setRows(rows.filter((_, j) => j !== i))}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setRows([...rows, { key: "", value: "" }])}
          >
            <Plus className="h-3.5 w-3.5" />
            Add tag
          </Button>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={submit} disabled={mutation.isPending}>
            {mutation.isPending && <Loader2 className="animate-spin" />}
            Save tags
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
