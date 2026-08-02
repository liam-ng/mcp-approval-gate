from __future__ import annotations

from app.repo.base import TicketRepository
from app.repo.factory import get_repository


def get_repo() -> TicketRepository:
    return get_repository()
