import { Link } from "react-router"
import { ArrowRight } from "lucide-react"
import { TicketStatusBadge } from "./ticket-status-badge"
import type { Ticket } from "@/lib/types"
import { cn } from "@/lib/utils"

export function LineageChain({ lineage, currentId }: { lineage: Ticket[]; currentId: string }) {
  if (lineage.length <= 1) return null
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/40 p-3 text-sm">
      <span className="mr-1 font-medium text-muted-foreground">Lineage:</span>
      {lineage.map((t, i) => (
        <span key={t.ticketId} className="flex items-center gap-2">
          {i > 0 && <ArrowRight className="h-4 w-4 text-muted-foreground" />}
          <Link
            to={`/tickets/${t.ticketId}`}
            className={cn(
              "flex items-center gap-1.5 rounded-md border bg-background px-2 py-1",
              t.ticketId === currentId && "border-primary ring-1 ring-primary",
            )}
          >
            <span className="font-mono text-xs">{t.ticketId.slice(-8)}</span>
            <TicketStatusBadge status={t.status} />
          </Link>
        </span>
      ))}
    </div>
  )
}
