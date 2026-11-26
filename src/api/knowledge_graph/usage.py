"""Usage log: one JSON line per agent-facing read (query, explain, node-path,
notes, note), so "is the graph used?" has an answer.

``<home>/<store_root>/usage.jsonl``. Recording never fails a read.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict

USAGE_FILE = "usage.jsonl"


def record(store_root: Path, verb: str, **fields: Any) -> None:
    entry = {
        "ts": round(time.time(), 1),
        "verb": verb,
        "agent": os.environ.get("CUTTLE_AGENT_ID") or "",
        "chat": os.environ.get("CUTTLE_CHAT_SESSION_ID") or "",
        **{k: v for k, v in fields.items() if v not in (None, "")},
    }
    try:
        store_root.mkdir(parents=True, exist_ok=True)
        with (store_root / USAGE_FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def summary(store_root: Path, *, days: int = 30) -> Dict[str, Any]:
    since = time.time() - max(1, int(days)) * 86400
    rows = []
    try:
        for line in (store_root / USAGE_FILE).read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and float(row.get("ts") or 0) >= since:
                rows.append(row)
    except OSError:
        pass
    refreshed = [r for r in rows if r.get("refreshed")]
    return {
        "days": int(days),
        "calls": len(rows),
        "chats": len({r["chat"] for r in rows if r.get("chat")}),
        "by_verb": dict(Counter(r.get("verb") or "?" for r in rows).most_common()),
        "by_project": dict(Counter(r.get("project") or "?" for r in rows).most_common()),
        "by_agent": dict(Counter(r.get("agent") or "unknown" for r in rows).most_common()),
        "refreshes": len(refreshed),
        "refresh_seconds_avg": (round(sum(float(r.get("refresh_seconds") or 0) for r in refreshed)
                                      / len(refreshed), 1) if refreshed else None),
        "failed": sum(1 for r in rows if r.get("ok") is False),
    }
