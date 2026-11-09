"""Cross-turn memory of vendor accounts that are out of usage.

Per-turn handling (``dispatch.account_exhausted``) stops a single turn from
burning attempts on an exhausted account. This module carries that knowledge
to the *next* turns: once Cursor says "out of usage", routing stops picking
Cursor premium models until a cooldown passes, instead of failing (and
spending ~6s) on every turn until someone notices.

Mirrored to ``<home>/cache/router_quota.json`` so every process that runs agent
turns shares it: Flask (routed turns) and ``python -m api.subagents`` (child
turns, which orchestration picks agents for). Each mark expires at the reset
time its error named (or the cooldown); deleting the file only causes a re-probe.
"""

from __future__ import annotations

import json
import os
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
_FILE_SEEN: Optional[int] = None  # mtime of the mirror last loaded/written


def _path():
    from core.runtime_paths import runtime_cache_path

    return runtime_cache_path("router_quota.json")


def _sync() -> None:
    """Load another process's marks when the mirror changed. Caller holds _LOCK."""
    global _FILE_SEEN
    try:
        path = _path()
        mtime = path.stat().st_mtime_ns
    except OSError:
        return
    if mtime == _FILE_SEEN:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    _FILE_SEEN = mtime
    if isinstance(data, dict):
        _EXHAUSTED.clear()
        _EXHAUSTED.update({str(k): v for k, v in data.items()
                           if isinstance(v, dict) and isinstance(v.get("until"), (int, float))})


def _save() -> None:
    """Write the marks for other processes. Caller holds _LOCK; never raises."""
    global _FILE_SEEN
    try:
        path = _path()
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(_EXHAUSTED), encoding="utf-8")
        os.replace(tmp, path)
        _FILE_SEEN = path.stat().st_mtime_ns
    except OSError:
        pass

_RESET_AT = re.compile(
    r"try again (?:at|after)\s+([A-Z][a-z]{2,8}\.? \d{1,2}(?:st|nd|rd|th)?,? \d{4},? \d{1,2}:\d{2}\s*[AP]M)",
    re.I,
)
# "try again at 4:46 AM": a time of day only (Codex), the next one from now.
_RESET_TIME = re.compile(r"try again (?:at|after)\s+(\d{1,2}:\d{2}\s*[AP]M)\b", re.I)


def reset_at(text: str, now: Optional[float] = None) -> Optional[float]:
    """When a usage-limit message says the account is usable again (epoch), or None."""
    now = time.time() if now is None else now
    m = _RESET_AT.search(text or "")
    if m:
        raw = re.sub(r"(\d)(st|nd|rd|th)", r"\1", m.group(1)).replace(",", "").replace(".", "")
        for fmt in ("%b %d %Y %I:%M %p", "%B %d %Y %I:%M %p"):
            try:
                ts = datetime.strptime(raw, fmt).timestamp()
            except ValueError:
                continue
            if ts > now:
                return min(ts, now + MAX_COOLDOWN_S)
        return None
    m = _RESET_TIME.search(text or "")
    if not m:
        return None
    try:
        clock = datetime.strptime(re.sub(r"\s+", "", m.group(1)).upper(), "%I:%M%p")
    except ValueError:
        return None
    base = datetime.fromtimestamp(now).replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    ts = base.timestamp()
    return ts if ts > now else ts + 24 * 3600


def mark_exhausted(target: ExecutionTarget, text: str = "", *, now: Optional[float] = None) -> None:
    """Record that ``target``'s account is out of usage."""
    now = time.time() if now is None else now
    until = reset_at(text, now) or (now + DEFAULT_COOLDOWN_S)
    scope = "all" if is_auto_target(target) else "premium"
    reason = (text or "").strip().splitlines()
    reason_line = next((ln for ln in reason if "usage" in ln.lower() or "limit" in ln.lower()), "")
    with _LOCK:
        _sync()
        prev = _EXHAUSTED.get(target.agent)
        if prev and prev["until"] > now and prev["scope"] == "all":
            scope = "all"
        _EXHAUSTED[target.agent] = {
            "scope": scope,
            "until": until,
            "reason": reason_line[:160],
        }
        _save()


def clear(agent: Optional[str] = None) -> None:
    with _LOCK:
        if agent is None:
            _EXHAUSTED.clear()
        else:
            _sync()
            _EXHAUSTED.pop(agent, None)
        _save()


def is_exhausted(target: ExecutionTarget, *, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    with _LOCK:
        _sync()
        rec = _EXHAUSTED.get(target.agent)
        if not rec:
            return False
        if rec["until"] <= now:
            return False
        return rec["scope"] == "all" or not is_auto_target(target)


def reason(target: ExecutionTarget) -> str:
    """Why ``target``'s account is cooling down ('' when it is not)."""
    if not is_exhausted(target):
        return ""
    with _LOCK:
        rec = _EXHAUSTED.get(target.agent) or {}
    when = datetime.fromtimestamp(rec.get("until") or 0).strftime("%b %d %H:%M")
    return f"out of usage until {when}"


def snapshot(*, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Active cooldowns (for /router status)."""
    now = time.time() if now is None else now
    with _LOCK:
        _sync()
        return [
            {"agent": a, "scope": r["scope"], "until": r["until"],
             "minutes_left": max(0, int((r["until"] - now) / 60)), "reason": r["reason"]}
            for a, r in _EXHAUSTED.items()
            if r["until"] > now
        ]


def note_success(target: ExecutionTarget) -> None:
    """A run on the account succeeded — drop a now-stale cooldown for its tier."""
    with _LOCK:
        _sync()
        rec = _EXHAUSTED.get(target.agent)
        if not rec:
            return
        if not is_auto_target(target) or rec["scope"] == "all":
            _EXHAUSTED.pop(target.agent, None)
            _save()
