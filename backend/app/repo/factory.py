from __future__ import annotations

from functools import lru_cache

from app.repo.base import TicketRepository
from app.settings import get_settings


@lru_cache(maxsize=1)
def get_repository() -> TicketRepository:
    settings = get_settings()
    if settings.store_backend == "jsonl":
        from app.repo.jsonl_store import JsonlTicketRepository

        return JsonlTicketRepository(settings.data_dir)
    if settings.store_backend == "dynamodb":
        from app.repo.dynamodb_store import DynamoDbTicketRepository

        return DynamoDbTicketRepository(settings.dynamodb_table)  # type: ignore[arg-type]
    if settings.store_backend == "s3":
        from app.repo.s3_store import S3TicketRepository

        return S3TicketRepository(settings.s3_bucket)  # type: ignore[arg-type]
    raise ValueError(f"unknown STORE_BACKEND {settings.store_backend}")
