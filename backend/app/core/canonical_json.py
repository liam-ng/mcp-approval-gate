"""Canonical JSON serialization and hashing for parametersHash.

Stable across processes and languages that apply the same rules: keys sorted,
no whitespace, UTF-8, NaN/Infinity rejected. This is a pragmatic subset of
RFC 8785 (JCS) — sufficient because action parameters originate as JSON and
the agent echoes the gate-computed hash rather than recomputing it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonicalize(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def parameters_hash(parameters: dict[str, Any]) -> str:
    return hashlib.sha256(canonicalize(parameters).encode("utf-8")).hexdigest()
