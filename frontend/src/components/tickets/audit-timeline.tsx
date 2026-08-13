import { format } from "date-fns"
import { Archive, Bot, CircleCheck, CircleX, Clock, FilePen, MessageSquare, Tag, UserRound, Cog } from "lucide-react"
import type { AuditEvent } from "@/lib/types"

const ICON: Record<AuditEvent["type"], typeof Clock> = {
  TICKET_CREATED: Clock,
  APPROVAL_ADDED: CircleCheck,
  APPROVED: CircleCheck,
  REJECTED: CircleX,
  DEPRECATED: CircleX,
  EXPIRED: Clock,
  EXECUTION_STARTED: Cog,
  EXECUTION_COMPLETED: CircleCheck,
  EXECUTION_FAILED: CircleX,
  TAGS_UPDATED: Tag,
  COMMENT_ADDED: MessageSquare,
  CLOSED: Archive,
  SUPERSEDED: FilePen,
}

const LABEL: Record<AuditEvent["type"], string> = {
  TICKET_CREATED: "Ticket created",
  APPROVAL_ADDED: "Approval added",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  DEPRECATED: "Deprecated (superseded)",
  EXPIRED: "Expired",
  EXECUTION_STARTED: "Execution started",
  EXECUTION_COMPLETED: "Execution completed",
  EXECUTION_FAILED: "Execution failed",
  TAGS_UPDATED: "Tags updated",
  COMMENT_ADDED: "Comment",
  CLOSED: "Closed",
  // Distinct from DEPRECATED: this ticket kept its own outcome and gained a
  // follow-up, rather than being replaced before it ran.
  SUPERSEDED: "Followed up (superseded)",
}

function formatTags(tags: unknown): string {
  if (typeof tags !== "object" || tags === null) return ""
  const entries = Object.entries(tags as Record<string, string>)
  return entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(", ") : "(none)"
}

function eventDetail(e: AuditEvent): string | null {
  const d = e.details ?? {}
  if (e.type === "REJECTED" && typeof d.reason === "string") return `Reason: ${d.reason}`
  if ((e.type === "DEPRECATED" || e.type === "SUPERSEDED") && typeof d.supersededBy === "string")
    return `Superseded by ${d.supersededBy.slice(-8)}`
  if ((e.type === "EXECUTION_COMPLETED" || e.type === "EXECUTION_FAILED") && typeof d.message === "string")
    return d.message
  if (e.type === "TAGS_UPDATED") return `${formatTags(d.oldTags)} → ${formatTags(d.tags)}`
  if (e.type === "CLOSED" && typeof d.reason === "string") return `Reason: ${d.reason}`
  return null
}

export function AuditTimeline({ events }: { events: AuditEvent[] }) {
  return (
    <ol className="relative ml-3 space-y-6 border-l pl-6">
      {events.map((e) => {
        const Icon = ICON[e.type]
        const ActorIcon = e.actor.kind === "agent" ? Bot : e.actor.kind === "human" ? UserRound : Cog
        return (
          <li key={e.eventId} className="relative">
            <span className="absolute -left-[31px] flex h-5 w-5 items-center justify-center rounded-full border bg-background">
              <Icon className="h-3 w-3 text-primary" />
            </span>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">{LABEL[e.type]}</span>
              <span className="text-xs text-muted-foreground">
                {format(new Date(e.timestamp), "yyyy-MM-dd HH:mm:ss")}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <ActorIcon className="h-3.5 w-3.5" />
              <span className="break-all">{e.actor.id}</span>
            </div>
            {eventDetail(e) && <p className="mt-1 text-xs text-muted-foreground">{eventDetail(e)}</p>}
            {e.type === "COMMENT_ADDED" && typeof e.details?.text === "string" && (
              <p className="mt-1 whitespace-pre-wrap rounded-md border bg-muted/40 p-2 text-sm">
                {e.details.text}
              </p>
            )}
          </li>
        )
      })}
    </ol>
  )
}
