"""Version 1 job activity snapshots; pure, bounded and independent of Cuttle.

Producers report facts, not estimated percentages. Persistence, process health
and chat delivery belong to the host. Unknown versions degrade to no activity.
"""
from __future__ import annotations

import math
from typing import Any

STEP_STATES = ("pending", "running", "done", "failed", "skipped", "cancelled")


def timestamp(value: Any):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def normalize(raw: Any):
    if not isinstance(raw, dict) or raw.get("schema_version", 1) != 1:
        return None
    steps, seen = [], set()
    for item in (raw.get("steps") if isinstance(raw.get("steps"), list) else [])[:32]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()[:64]
        label = str(item.get("label") or "").strip()[:160]
        if not key or key in seen or not label:
            continue
        seen.add(key)
        state = item.get("state", "pending")
        steps.append({"id": key, "label": label,
                      "state": state if state in STEP_STATES else "pending",
                      "detail": str(item.get("detail") or "")[:240]})
    return {"schema_version": 1, "steps": steps,
            "summary": str(raw.get("summary") or "")[:240],
            "output": str(raw.get("output") or "")[-4000:],
            "updated_ts": timestamp(raw.get("updated_ts")),
            "deadline_ts": timestamp(raw.get("deadline_ts")),
            "stale_after_sec": min(86400, max(30, timestamp(raw.get("stale_after_sec")) or 600))}
