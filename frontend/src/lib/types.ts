// TypeScript mirrors of the backend Pydantic models (camelCase aliases).

export const TICKET_STATUSES = [
  "PENDING_APPROVAL",
  "APPROVED",
  "REJECTED",
  "DEPRECATED",
  "EXPIRED",
  "EXECUTING",
  "CLOSED",
  "FAILED",
] as const
export type TicketStatus = (typeof TICKET_STATUSES)[number]

export const TERMINAL_STATUSES: ReadonlySet<TicketStatus> = new Set([
  "REJECTED",
  "DEPRECATED",
  "EXPIRED",
  "CLOSED",
  "FAILED",
])

export interface ActionDetails {
  service: "ec2"
  operation: string
  region: string
  parameters: Record<string, unknown>
  parametersHash: string
  resourceArns: string[]
  reason?: string | null
}

export interface Approval {
  approvedBy: string
  approvedAt: string
}

export interface Execution {
  startedAt: string
  finishedAt?: string | null
  outcome?: "success" | "failure" | null
  message?: string | null
  awsRequestIds: string[]
}

export interface Ticket {
  ticketId: string
  subject: string
  ticketDate: string
  status: TicketStatus
  plannedDate: string
  plannedAction: string
  actionDetails: ActionDetails
  tags: Record<string, string>
  assignee: string
  proposedBy: string
  approvals: Approval[]
  rejectedBy?: string | null
  rejectedAt?: string | null
  rejectionReason?: string | null
  supersedes?: string | null
  supersededBy?: string | null
  lineageRootId: string
  idempotencyKey?: string | null
  execution?: Execution | null
  seq: number
}

export interface AuditEvent {
  eventId: string
  ticketId: string
  seq: number
  timestamp: string
  type:
    | "TICKET_CREATED"
    | "APPROVAL_ADDED"
    | "APPROVED"
    | "REJECTED"
    | "DEPRECATED"
    | "EXPIRED"
    | "EXECUTION_STARTED"
    | "EXECUTION_COMPLETED"
    | "EXECUTION_FAILED"
    | "TAGS_UPDATED"
    | "COMMENT_ADDED"
    | "CLOSED"
    | "SUPERSEDED"
  actor: { kind: "agent" | "human" | "system"; id: string }
  fromStatus?: TicketStatus | null
  toStatus?: TicketStatus | null
  details?: Record<string, unknown> | null
}

export interface TicketDetail {
  ticket: Ticket
  lineage: Ticket[]
  auditEvents: AuditEvent[]
}

export interface TicketList {
  items: Ticket[]
  cursor?: string | null
}

export interface Me {
  email: string
  name?: string | null
  role: "approver" | "viewer"
  approvalTtlHours: number
  allowSelfApproval: boolean
}

// What the unauthenticated /act landing page gets from a signed email link
// (backend/app/api/approval_link_actions.py) — a token stands in for a
// session, so this is intentionally a smaller shape than TicketDetail.
export interface ApprovalLinkPreview {
  ticketId: string
  subject: string
  status: TicketStatus
  plannedDate: string
  plannedAction: string
  actionDetails: ActionDetails
  proposedBy: string
  action: "approve" | "reject"
  actionable: boolean
  blockedReason?: "already_actioned" | "not_approver" | "self_approval" | "duplicate_approval" | null
}
