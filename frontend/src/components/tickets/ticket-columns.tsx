// Column defs split from the table, following the portal's log-columns.tsx pattern.
import type { ColumnDef } from "@tanstack/react-table"
import { format } from "date-fns"
import { Badge } from "@/components/ui/badge"
import { TicketStatusBadge } from "./ticket-status-badge"
import type { Ticket } from "@/lib/types"

function shortArn(arn: string): string {
  const parts = arn.split("/")
  return parts.length > 1 ? parts[1] : arn.split(":").pop() || arn
}

export const ticketColumns: ColumnDef<Ticket>[] = [
  {
    accessorKey: "ticketId",
    header: "Ticket",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.ticketId.slice(-8)}</span>
    ),
  },
  {
    accessorKey: "subject",
    header: "Subject",
    cell: ({ row }) => <span className="font-medium">{row.original.subject}</span>,
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <TicketStatusBadge status={row.original.status} />,
  },
  {
    id: "operation",
    header: "Operation",
    cell: ({ row }) => (
      <span className="font-mono text-xs">
        {row.original.actionDetails.service}:{row.original.actionDetails.operation}
      </span>
    ),
  },
  {
    accessorKey: "assignee",
    header: "Assignee (agent)",
    cell: ({ row }) => (
      <span className="font-mono text-xs" title={row.original.assignee}>
        {shortArn(row.original.assignee)}
      </span>
    ),
  },
  {
    accessorKey: "proposedBy",
    header: "Proposed by",
    cell: ({ row }) => (
      <span className="text-xs" title={row.original.proposedBy}>
        {shortArn(row.original.proposedBy)}
      </span>
    ),
  },
  {
    id: "tags",
    header: "Tags",
    cell: ({ row }) => (
      <div className="flex flex-wrap gap-1">
        {Object.entries(row.original.tags).map(([k, v]) => (
          <Badge key={k} variant="outline" className="font-normal">
            {k}={v}
          </Badge>
        ))}
      </div>
    ),
  },
  {
    accessorKey: "plannedDate",
    header: "Planned",
    cell: ({ row }) => format(new Date(row.original.plannedDate), "yyyy-MM-dd"),
  },
  {
    accessorKey: "ticketDate",
    header: "Created",
    cell: ({ row }) => format(new Date(row.original.ticketDate), "yyyy-MM-dd HH:mm"),
  },
]
