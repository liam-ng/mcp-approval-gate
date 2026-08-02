import { Badge } from "@/components/ui/badge"
import type { TicketStatus } from "@/lib/types"

const VARIANT: Record<TicketStatus, "default" | "secondary" | "destructive" | "success" | "warning" | "info" | "outline"> = {
  PENDING_APPROVAL: "warning",
  APPROVED: "success",
  REJECTED: "destructive",
  DEPRECATED: "outline",
  EXPIRED: "outline",
  EXECUTING: "info",
  COMPLETED: "default",
  FAILED: "destructive",
}

const LABEL: Record<TicketStatus, string> = {
  PENDING_APPROVAL: "Pending approval",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  DEPRECATED: "Deprecated",
  EXPIRED: "Expired",
  EXECUTING: "Executing",
  COMPLETED: "Completed",
  FAILED: "Failed",
}

export function TicketStatusBadge({ status }: { status: TicketStatus }) {
  return <Badge variant={VARIANT[status]}>{LABEL[status]}</Badge>
}
