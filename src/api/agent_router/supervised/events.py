"""Structured telemetry events for supervised coordination (no dashboard yet)."""

from __future__ import annotations

from typing import Any


def supervised_log(event: str, **fields: Any) -> None:
    safe = {}
    for k, v in fields.items():
        if v is None:
            continue
        key = str(k)
        if any(s in key.lower() for s in ("key", "token", "secret", "password", "auth")):
            continue
        if isinstance(v, str) and len(v) > 240:
            v = v[:237] + "..."
        safe[key] = v
    parts = " ".join(f"{k}={safe[k]!r}" for k in sorted(safe))
    print(f"[SUPERVISED] event={event} {parts}".rstrip(), flush=True)
