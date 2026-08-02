"""DynamoDB store — production backend (not yet implemented).

Single-table design (table name from DYNAMODB_TABLE):

  Item            PK                  SK             Attributes
  ticket META     TICKET#<id>         META           materialized Ticket + seq
  audit event     TICKET#<id>         EVENT#<seq:06> AuditEvent payload

  GSI1  STATUS#<status>       / <ticketDate>   -> query_by_status
  GSI2  LINEAGE#<rootId>      / <ticketId>     -> query_lineage
  GSI3  IDEM#<arn>#<key>      / -              -> find_by_idempotency_key

- append_event: TransactWriteItems [ Update META with ConditionExpression
  seq = :expected (ConflictError on cancellation), Put EVENT#<seq> ].
- transact_supersede: one TransactWriteItems across both tickets' META +
  EVENT items — true multi-item atomicity, removing the jsonl repair rule.
- create_ticket: TransactWriteItems [ Put META (attribute_not_exists(PK)),
  Put GSI3 marker (attribute_not_exists) ] -> DuplicateError on cancellation.

IRSA policy: dynamodb:GetItem/Query/PutItem/UpdateItem/TransactWriteItems on
this table + its indexes only. HA note: with DynamoDB the gate can run >=2
replicas once the agent-auth replay cache also moves to a shared store.
"""

from __future__ import annotations

from app.repo.base import TicketRepository


class DynamoDbTicketRepository(TicketRepository):  # pragma: no cover - stub
    def __init__(self, table_name: str):
        raise NotImplementedError(
            "DynamoDB store is planned for production; see module docstring for the design. "
            "Set STORE_BACKEND=jsonl for now."
        )
