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

// Parameter shape of one EC2 operation, from GET /api/aws/ec2/operations/{op}.
// Derived from botocore's models on the server (backend/app/core/aws_schema.py)
// plus the hand-curated conditional layer (aws_conditional.py) — never
// re-declared here, because a second copy of AWS's parameter rules in
// TypeScript would drift from the one the gate actually enforces.
export interface OperationSchema {
  operation: string
  /** Names AWS's own model marks mandatory. */
  required: string[]
  /** Every accepted name -> its botocore type ("string", "integer", "list", ...). */
  accepted: Record<string, string>
  /** "At least one of these" rules the model cannot express. */
  conditional: { oneOf: string[]; because: string }[]
  /** Resource types the gate tags itself — the form must not ask for these. */
  gateTags: string[]
}

// Account lookups from /api/aws/ec2/discover/*. Read-only, and optional: the
// gate holds no AWS credentials unless AWS_DISCOVERY_ENABLED is set, so
// `enabled: false` is a normal answer and means "let them type the id".
export type DiscoveryKind =
  | "vpcs"
  | "subnets"
  | "security-groups"
  | "key-pairs"
  | "instances"
  | "volumes"
  | "images"

export interface DiscoveryItem {
  id: string
  label: string
  detail?: string
  vpcId?: string | null
}

export interface DiscoveryResult {
  items: DiscoveryItem[]
  enabled: boolean
  error?: string | null
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
