"""S3 + Object Lock store — compliance/WORM backend (not yet implemented).

Design (bucket from S3_BUCKET; versioning + Object Lock enabled, Governance
mode with a configured retention period, e.g. 1-7 years):

- One immutable object per AuditEvent: tickets/{ticket_id}/events/{seq:06d}.json
  TICKET_CREATED embeds the full ticket, exactly like the jsonl lines.
- CAS: PutObject with `IfNoneMatch="*"` — a seq collision returns HTTP 412,
  mapped to ConflictError. This gives append_event the same semantics as the
  jsonl/DynamoDB implementations.
- Boot: ListObjectsV2 + GetObject, folded with repo.base.apply_event (the
  fold is shared; only the line source differs from jsonl).
- transact_supersede is two conditional puts (S3 has no transactions); a
  crash between them leaves a ticket whose superseded_by points at a successor
  that was never written, repaired on boot by the same revert rule as
  jsonl_store._repair_partial_supersede. Detect it by the dangling
  superseded_by, NOT by status == DEPRECATED: superseding a FAILED/CLOSED
  ticket emits SUPERSEDED and deliberately leaves the status alone.
- Trade-offs: boot time grows with event count (add a snapshot object later);
  higher read latency than DynamoDB. WORM makes the audit trail tamper-proof
  even against account admins — strongest fit for the compliance goal.
- Middle ground worth considering: DynamoDB as operational store + dual-write
  audit mirror to this bucket (AUDIT_MIRROR_S3_BUCKET).

IRSA policy: s3:PutObject, s3:GetObject, s3:ListBucket, s3:PutObjectRetention
on the audit bucket only.
"""

from __future__ import annotations

from app.repo.base import TicketRepository


class S3TicketRepository(TicketRepository):  # pragma: no cover - stub
    def __init__(self, bucket: str):
        raise NotImplementedError(
            "S3 Object Lock store is planned; see module docstring for the design. "
            "Set STORE_BACKEND=jsonl for now."
        )
