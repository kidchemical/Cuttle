"""Cross-turn memory of vendor accounts that are out of usage.

Per-turn handling (``dispatch.account_exhausted``) stops a single turn from
burning attempts on an exhausted account. This module carries that knowledge
to the *next* turns: once Cursor says "out of usage", routing stops picking
Cursor premium models until a cooldown passes, instead of failing (and
spending ~6s) on every turn until someone notices.

Process-local and in-memory by design: a Flask restart is a natural re-probe.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from api.agent_router.policy import is_auto_target
from api.agent_router.types import ExecutionTarget

# Without a reset time in the error, re-probe after this long.
DEFAULT_COOLDOWN_S = 3 * 3600
MAX_COOLDOWN_S = 7 * 24 * 3600

_LOCK = threading.Lock()
# agent → {"scope": "premium"|"all", "until": epoch, "reason": str}
_EXHAUSTED: Dict[str, Dict[str, Any]] = {}

_RESET_AT = re.compile(
    r"try again (?:at|after)\s+([A-Z][a-z]{2,8}\.? \d{1,2}(?:st|nd|rd|th)?,? \d{4},? \d{1,2}:\d{2}\s*[AP]M)",
    re.I,
)


def _parse_reset(text: str, now: float) -> Optional[float]:
    m = _RESET_AT.search(text or "")
    if not m:
        return None
    raw = re.sub(r"(\d)(st|nd|rd|th)", r"\1", m.group(1)).replace(",", "").replace(".", "")
    for fmt in ("%b %d %Y %I:%M %p", "%B %d %Y %I:%M %p"):
        try:
            ts = datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue
        if ts > now:
            return min(ts, now + MAX_COOLDOWN_S)
    return None


def mark_exhausted(target: ExecutionTarget, text: str = "", *, now: Optional[float] = None) -> None:
    """Record that ``target``'s account is out of usage."""
    now = time.time() if now is None else now
    until = _parse_reset(text, now) or (now + DEFAULT_COOLDOWN_S)
    scope = "all" if is_auto_target(target) else "premium"
    reason = (text or "").strip().splitlines()
    reason_line = next((ln for ln in reason if "usage" in ln.lower() or "limit" in ln.lower()), "")
    with _LOCK:
        prev = _EXHAUSTED.get(target.agent)
        if prev and prev["until"] > now and prev["scope"] == "all":
            scope = "all"
        _EXHAUSTED[target.agent] = {
            "scope": scope,
            "until": until,
            "reason": reason_line[:160],
        }


def clear(agent: Optional[str] = None) -> None:
    with _LOCK:
        if agent is None:
            _EXHAUSTED.clear()
        else:
            _EXHAUSTED.pop(agent, None)


def is_exhausted(target: ExecutionTarget, *, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with _LOCK:
        rec = _EXHAUSTED.get(target.agent)
        if not rec:
            return False
        if rec["until"] <= now:
            _EXHAUSTED.pop(target.agent, None)
            return False
        return rec["scope"] == "all" or not is_auto_target(target)


def snapshot(*, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Active cooldowns (for /router status)."""
    now = time.time() if now is None else now
    with _LOCK:
        return [
            {"agent": a, "scope": r["scope"], "until": r["until"],
             "minutes_left": max(0, int((r["until"] - now) / 60)), "reason": r["reason"]}
            for a, r in _EXHAUSTED.items()
            if r["until"] > now
        ]


def note_success(target: ExecutionTarget) -> None:
    """A run on the account succeeded — drop a now-stale cooldown for its tier."""
    with _LOCK:
        rec = _EXHAUSTED.get(target.agent)
        if not rec:
            return
        if not is_auto_target(target) or rec["scope"] == "all":
            _EXHAUSTED.pop(target.agent, None)
