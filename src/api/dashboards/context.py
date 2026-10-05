"""Context dashboard: briefing size and agent window fill over time.

Reads ``api.cuttle_brain.metrics`` (one row per harness turn). Answers:
is the Cuttle briefing growing, how often is it re-sent in full and why, and
is each chat's context window filling up between compactions.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

CONTEXT_ID = "cuttle-context"

RANGES = (
    {"id": "7d", "name": "7 days", "days": 7},
    {"id": "30d", "name": "30 days", "days": 30},
    {"id": "90d", "name": "90 days", "days": 90},
)

# Display order for briefing modes (kernel ``brain.mode``).
MODES = (
    {"id": "full", "name": "Full briefing"},
    {"id": "resume_delta", "name": "Rules-changed note"},
    {"id": "resume_handoff", "name": "Handoff only"},
    {"id": "resume", "name": "Prompt only"},
)

LAYERS = (
    {"id": "core_contract", "name": "Capabilities"},
    {"id": "global_rules", "name": "Global rules"},
    {"id": "project_rules", "name": "Project rules"},
    {"id": "runtime", "name": "Inventory + tasks"},
    {"id": "handoff", "name": "Handoff"},
    {"id": "chat_store", "name": "Chat-history hint"},
    {"id": "ranked_context", "name": "Ranked snippets"},
    {"id": "profile", "name": "Profile"},
)

_MAX_SESSIONS = 8


def _day(ts: float, tz_offset_minutes: Optional[int]) -> str:
    # One zone for the day axis and the buckets (usage dashboard's `_tz`):
    # mixing server-local axis with UTC buckets dropped evening turns.
    from api.dashboards.usage import _tz

    return datetime.fromtimestamp(ts, _tz(tz_offset_minutes)).date().isoformat()


def _handle(sid: Any) -> str:
    s = str(sid or "").strip()
    digits = s[len("db_session_"):] if s.startswith("db_session_") else s
    return f"CH-{int(digits):06d}" if digits.isdigit() else (s or "?")


def _rule_sizes() -> List[Dict[str, Any]]:
    from api.cuttle_brain.context_compiler import load_global_rules, load_project_rules

    root = Path(__file__).resolve().parents[3]
    rows = [
        {"name": name, "scope": "global", "chars": len(text)}
        for name, text in load_global_rules()
    ]
    rows += [
        {"name": name, "scope": "Cuttle project", "chars": len(text)}
        for name, text in load_project_rules(str(root))
    ]
    return sorted(rows, key=lambda r: -r["chars"])


def _session_series(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_chat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in turns:
        if t.get("chat_session_id") and t.get("context_tokens"):
            by_chat[str(t["chat_session_id"])].append(t)
    ranked = sorted(
        (rows for rows in by_chat.values() if len(rows) >= 2),
        key=lambda rows: -rows[-1]["ts"],
    )[:_MAX_SESSIONS]
    out = []
    for rows in ranked:
        agents = Counter(r.get("agent_id") or "?" for r in rows)
        limit = max((r.get("context_limit") or 0) for r in rows) or None
        out.append({
            "handle": _handle(rows[0]["chat_session_id"]),
            "agents": [a for a, _ in agents.most_common()],
            "limit": limit,
            "points": [
                {
                    "ts": r["ts"],
                    "tokens": r["context_tokens"],
                    "limit": r.get("context_limit"),
                    "agent": r.get("agent_id"),
                    "mode": r.get("mode"),
                    "compacted": bool(r.get("compacted")),
                }
                for r in rows
            ],
        })
    return out


def cuttle_context(
    *, range_id: str = "30d", tz_offset_minutes: Optional[int] = None
) -> Dict[str, Any]:
    from api.cuttle_brain.metrics import fetch_turns

    rng = next((r for r in RANGES if r["id"] == range_id), RANGES[1])
    since = time.time() - rng["days"] * 86400
    turns = fetch_turns(since_ts=since)

    days: List[str] = []
    start = date.fromisoformat(_day(since, tz_offset_minutes))
    end = date.fromisoformat(_day(time.time(), tz_offset_minutes))
    d = start
    while d <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    index = {day: i for i, day in enumerate(days)}

    modes = {m["id"]: [0] * len(days) for m in MODES}
    full_sizes: List[List[int]] = [[] for _ in days]
    layer_sum = {l["id"]: [0] * len(days) for l in LAYERS}
    layer_n = [0] * len(days)
    reasons: Counter = Counter()
    handoffs = compactions = 0
    full_all: List[int] = []

    for t in turns:
        i = index.get(_day(t["ts"], tz_offset_minutes))
        if i is None:
            continue
        mode = t.get("mode") or "resume"
        if mode in modes:
            modes[mode][i] += 1
        if mode == "full" and t.get("envelope_chars"):
            full_sizes[i].append(int(t["envelope_chars"]))
            full_all.append(int(t["envelope_chars"]))
            if t.get("full_reason"):
                reasons[t["full_reason"]] += 1
            layers = t.get("layer_chars") or {}
            if layers:
                layer_n[i] += 1
                for key in layer_sum:
                    layer_sum[key][i] += int(layers.get(key) or 0)
        if t.get("handoff_messages"):
            handoffs += 1
        if t.get("compacted"):
            compactions += 1

    fills = [
        100.0 * t["context_tokens"] / t["context_limit"]
        for t in turns
        if t.get("context_tokens") and t.get("context_limit")
    ]
    return {
        "success": True,
        "id": CONTEXT_ID,
        "selected_range": rng["id"],
        "available_ranges": [{"id": r["id"], "label": r["name"]} for r in RANGES],
        "days": days,
        "modes": [{**m, "series": modes[m["id"]]} for m in MODES],
        "full_median_chars": [int(median(v)) if v else None for v in full_sizes],
        "layers": [
            {
                **l,
                "series": [
                    round(layer_sum[l["id"]][i] / layer_n[i]) if layer_n[i] else None
                    for i in range(len(days))
                ],
            }
            for l in LAYERS
            if any(layer_sum[l["id"]])
        ],
        "full_reasons": [{"id": k, "count": v} for k, v in reasons.most_common()],
        "sessions": _session_series(turns),
        "rules": _rule_sizes(),
        "stats": {
            "turns": len(turns),
            "full": sum(modes["full"]),
            "full_median_chars": int(median(full_all)) if full_all else None,
            "handoffs": handoffs,
            "compactions": compactions,
            "median_fill_pct": round(median(fills), 1) if fills else None,
            "with_fill": sum(1 for t in turns if t.get("context_tokens")),
            "backfilled": sum(1 for t in turns if t.get("source") == "backfill"),
        },
    }
