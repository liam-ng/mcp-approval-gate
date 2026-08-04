"""Request logging + a small in-process rate limiter for unauthenticated-ish
surfaces: /api/agent/* (SigV4, but verification itself costs a forwarded STS
call) and /api/tickets/by-link/* (a signed token instead of a session —
nothing stops a scraper from hammering it looking for a valid one, however
unlikely that is against itsdangerous' HMAC).

Deliberately dependency-free (stdlib logging, sliding window in memory) —
sound for the single-replica MVP; swap for a shared limiter when scaling out.
"""

from __future__ import annotations

import logging
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("gate.access")

AGENT_PREFIX = "/api/agent/"
APPROVAL_LINK_PREFIX = "/api/tickets/by-link/"
RATE_LIMITED_PREFIXES = (AGENT_PREFIX, APPROVAL_LINK_PREFIX)
RATE_LIMIT_MAX = 60          # requests
RATE_LIMIT_WINDOW = 60.0     # seconds


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(now)
        return True


def install_middleware(app: FastAPI) -> None:
    limiter = SlidingWindowLimiter(RATE_LIMIT_MAX, RATE_LIMIT_WINDOW)

    @app.middleware("http")
    async def access_log_and_rate_limit(request: Request, call_next):
        if request.url.path.startswith(RATE_LIMITED_PREFIXES):
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                return JSONResponse(
                    status_code=429,
                    content={"error": {"code": "RATE_LIMITED", "message": "too many requests"}},
                )
        start = time.monotonic()
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            logger.info(
                "%s %s -> %d (%.1f ms)",
                request.method,
                request.url.path,
                response.status_code,
                (time.monotonic() - start) * 1000,
            )
        return response
