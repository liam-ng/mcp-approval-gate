"""In-memory TTL nonce cache for agent-auth replay protection.

Sound for a single replica (k8s strategy: Recreate). The cache resets on pod
restart, leaving a window bounded by the ±5-minute SigV4 date check plus
mandatory TLS; move this to a shared store before scaling to >1 replica.
"""

from __future__ import annotations

import time


class ReplayCache:
    def __init__(self, ttl_seconds: float = 360.0):
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def check_and_add(self, nonce: str) -> bool:
        """Returns True if the nonce is fresh (and records it); False on replay."""
        now = time.monotonic()
        self._prune(now)
        if nonce in self._seen:
            return False
        self._seen[nonce] = now + self._ttl
        return True

    def _prune(self, now: float) -> None:
        expired = [k for k, exp in self._seen.items() if exp <= now]
        for k in expired:
            del self._seen[k]
