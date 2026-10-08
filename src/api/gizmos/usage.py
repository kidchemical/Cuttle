"""Normalized plan-usage snapshots for usage-meter gizmos.

Every vendor reports plan limits differently (Codex 5-hour/weekly windows,
Claude 5-hour/weekly/model windows, Cursor billing-cycle percentages). A meter
must never parse ``/usage`` markdown, so each provider normalizes into one
shape::

    {
      "agent": "codex", "label": "Codex", "plan": "plus",
      "windows": [{"id": "five_hour", "label": "5-hour", "used_percent": 42.0,
                   "remaining_percent": 58.0, "reset_at": 1790000000}],
      "blocked": false,          # ordinary usage is refused right now
      "unblock_at": null,        # epoch when a blocked account runs again
      "next_reset_at": 1790000000,
      "extras": {"resets_available": 2, "credits": "None"},
      "updated_at": 1789990000.0,
      "error": null,             # set on refresh failure (last good kept)
    }

Adding an agent is one :func:`register_provider` call with a zero-arg fetch
that returns the normalized body (``windows`` / ``blocked`` / ``plan`` /
``extras`` / ``error``). Fetches are demand-driven and cached per agent, like
``api.usage_live``; nothing polls vendors in the background.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

TTL_S = 60
# A Refresh button must not turn into a vendor-API hammer.
FORCE_MIN_INTERVAL_S = 15


@dataclass(frozen=True)
class UsageProvider:
    agent: str
    label: str
    fetch: Callable[[], Dict[str, Any]]


_PROVIDERS: Dict[str, UsageProvider] = {}
_cache: Dict[str, tuple] = {}
_locks: Dict[str, threading.RLock] = {}
_guard = threading.Lock()


def register_provider(agent: str, label: str, fetch: Callable[[], Dict[str, Any]]) -> None:
    agent = str(agent or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", agent):
        raise ValueError(f"invalid usage provider id: {agent!r}")
    _PROVIDERS[agent] = UsageProvider(agent=agent, label=str(label or agent), fetch=fetch)


def get_provider(agent: Any) -> Optional[UsageProvider]:
    return _PROVIDERS.get(str(agent or "").strip().lower())


def providers() -> List[Dict[str, str]]:
    return [{"agent": p.agent, "label": p.label} for p in _PROVIDERS.values()]


# ── normalization helpers (pure) ────────────────────────────────────────────

def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_") or "window"


def make_window(wid: str, label: str, used_percent: Any, reset_at: Any = None) -> Optional[Dict[str, Any]]:
    used = _num(used_percent)
    if used is None:
        return None
    used = max(0.0, min(100.0, used))
    reset = _num(reset_at)
    return {
        "id": _slug(wid),
        "label": str(label or wid),
        "used_percent": round(used, 2),
        "remaining_percent": round(100.0 - used, 2),
        "reset_at": int(reset) if reset and reset > 0 else None,
    }


def _reset_epoch(window: Dict[str, Any], now: float) -> Optional[int]:
    at = _num(window.get("reset_at"))
    if at and at > 0:
        return int(at)
    after = _num(window.get("reset_after_seconds"))
    if after and after > 0:
        return int(now + after)
    return None


def finalize(body: Dict[str, Any], now: float) -> Dict[str, Any]:
    """Derive ``unblock_at`` / ``next_reset_at`` from the windows."""
    windows = [w for w in body.get("windows") or [] if isinstance(w, dict)]
    future = [w["reset_at"] for w in windows if w.get("reset_at") and w["reset_at"] > now]
    exhausted = [w["reset_at"] for w in windows
                 if w.get("used_percent", 0) >= 100 and w.get("reset_at") and w["reset_at"] > now]
    blocked = bool(body.get("blocked"))
    unblock_at = None
    if blocked:
        # Every exhausted window has to reset before ordinary usage runs again.
        unblock_at = max(exhausted) if exhausted else (min(future) if future else None)
    return {
        "plan": str(body.get("plan") or ""),
        "windows": windows,
        "blocked": blocked,
        "unblock_at": unblock_at,
        "next_reset_at": min(future) if future else None,
        "extras": body.get("extras") if isinstance(body.get("extras"), dict) else {},
        "error": body.get("error") or None,
    }


def normalize_codex(data: Dict[str, Any], now: float) -> Dict[str, Any]:
    if not isinstance(data, dict) or not data.get("success"):
        return {"error": str((data or {}).get("error") or "Codex usage unavailable")[:240]}
    rl = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else {}
    windows = []
    for wid, label, key in (("five_hour", "5-hour", "primary_window"),
                            ("weekly", "Weekly", "secondary_window")):
        raw = rl.get(key)
        if isinstance(raw, dict):
            win = make_window(wid, label, raw.get("used_percent"), _reset_epoch(raw, now))
            if win:
                windows.append(win)
    credits = data.get("credits") if isinstance(data.get("credits"), dict) else {}
    overflow = bool(credits.get("unlimited") or credits.get("has_credits"))
    blocked = (rl.get("allowed") is False or bool(rl.get("limit_reached"))) and not overflow
    if not blocked and not overflow and any(w["used_percent"] >= 100 for w in windows):
        blocked = True
    resets = data.get("rate_limit_reset_credits")
    extras: Dict[str, Any] = {}
    if isinstance(resets, dict):
        try:
            extras["resets_available"] = int(resets.get("available_count") or 0)
        except (TypeError, ValueError):
            pass
    if credits.get("unlimited"):
        extras["credits"] = "Unlimited"
    elif credits.get("has_credits"):
        extras["credits"] = str(credits.get("balance") or "Available")
    return {"plan": data.get("plan_type") or "", "windows": windows,
            "blocked": blocked, "extras": extras}


def normalize_claude(data: Dict[str, Any], now: float) -> Dict[str, Any]:
    if not isinstance(data, dict) or not data.get("success"):
        return {"error": str((data or {}).get("error") or "Claude usage unavailable")[:240]}
    windows = []
    for raw in data.get("windows") or []:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "Limit")
        win = make_window(label, label, raw.get("used_percent"), raw.get("reset_at"))
        if win:
            windows.append(win)
    extra = data.get("extra_usage") if isinstance(data.get("extra_usage"), dict) else {}
    overflow = bool(extra.get("is_enabled")) and not extra.get("spend_limit_reached")
    blocked = any(w["used_percent"] >= 100 for w in windows) and not overflow
    extras: Dict[str, Any] = {}
    if extra.get("is_enabled"):
        extras["credits"] = "Extra usage on" + (" (limit reached)" if extra.get("spend_limit_reached") else "")
    return {"plan": data.get("plan_type") or "", "windows": windows,
            "blocked": blocked, "extras": extras, "stale": bool(data.get("stale")),
            "error": data.get("error"), "updated_at": data.get("updated_at")}


def normalize_cursor(data: Dict[str, Any], now: float) -> Dict[str, Any]:
    if not isinstance(data, dict) or not data.get("success"):
        return {"error": str((data or {}).get("error") or "Cursor usage unavailable")[:240]}
    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    pu = data.get("plan_usage") if isinstance(data.get("plan_usage"), dict) else {}
    pu = pu or (period.get("planUsage") if isinstance(period.get("planUsage"), dict) else {})
    end = _num(period.get("billingCycleEnd"))
    reset = end / 1000.0 if end and end > 1e11 else end
    windows = []
    for wid, label, key in (("total", "Total", "totalPercentUsed"),
                            ("auto", "Auto", "autoPercentUsed"),
                            ("api", "API", "apiPercentUsed")):
        win = make_window(wid, label, pu.get(key), reset)
        if win:
            windows.append(win)
    plan_info = data.get("plan_info") if isinstance(data.get("plan_info"), dict) else {}
    extras: Dict[str, Any] = {}
    bonus_cents = _num(pu.get("bonusSpend"))
    if bonus_cents and bonus_cents > 0:
        extras["credits"] = f"${bonus_cents / 100:.2f} bonus used"
    if any(w["used_percent"] >= 100 for w in windows):
        extras["note"] = ("Included usage spent; running on Cursor bonus usage"
                          if pu.get("remainingBonus")
                          else "Included and bonus usage spent; Cursor may limit requests until reset")
    # Auto is metered on current plans, but Cursor keeps serving some requests
    # past 100% at its discretion, so this data never proves a hard block.
    return {"plan": plan_info.get("planName") or "", "windows": windows,
            "blocked": False, "extras": extras}


# ── built-in providers ──────────────────────────────────────────────────────

def _fetch_codex() -> Dict[str, Any]:
    from api.agent_usage import fetch_codex_account_usage

    return normalize_codex(fetch_codex_account_usage(), time.time())


def _fetch_claude() -> Dict[str, Any]:
    from api.agent_usage import fetch_claude_plan_limits

    return normalize_claude(fetch_claude_plan_limits(), time.time())


def _fetch_cursor() -> Dict[str, Any]:
    from api.cursor_agent_commands import fetch_cursor_account_usage

    return normalize_cursor(fetch_cursor_account_usage(), time.time())


register_provider("codex", "Codex", _fetch_codex)
register_provider("claude", "Claude Code", _fetch_claude)
register_provider("cursor", "Cursor", _fetch_cursor)


# ── cache ────────────────────────────────────────────────────────────────────

def snapshot(agent: Any, *, force: bool = False) -> Dict[str, Any]:
    """Cached normalized usage for one agent. Raises ``ValueError`` if unknown.

    A failed refresh keeps the last good windows and sets ``error`` + ``stale``.
    """
    provider = get_provider(agent)
    if provider is None:
        raise ValueError(f"unknown usage agent: {agent!r}")
    with _guard:
        lock = _locks.setdefault(provider.agent, threading.RLock())
    with lock:
        cached = _cache.get(provider.agent)
        age = time.monotonic() - cached[0] if cached else None
        if cached and (age < TTL_S and not force or age < FORCE_MIN_INTERVAL_S):
            return dict(cached[1])
        now = time.time()
        try:
            body = provider.fetch() or {}
        except Exception:
            body = {"error": "Usage refresh failed"}
        result = {"agent": provider.agent, "label": provider.label, **finalize(body, now),
                  "updated_at": body.get("updated_at") or now, "stale": bool(body.get("stale"))}
        if provider.agent != "claude" and result["error"] and not result["windows"] and cached and not cached[1].get("error"):
            result = {**cached[1], "error": result["error"], "stale": True}
        elif provider.agent != "claude" and result["error"] and not result["windows"] and cached and cached[1].get("stale"):
            result = {**cached[1], "error": result["error"]}
        _cache[provider.agent] = (time.monotonic(), result)
        return dict(result)


def set_snapshot(agent: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Test/seed hook: install a normalized snapshot without fetching."""
    provider = get_provider(agent)
    if provider is None:
        raise ValueError(f"unknown usage agent: {agent!r}")
    now = time.time()
    result = {"agent": provider.agent, "label": provider.label, **finalize(body, now),
              "updated_at": now, "stale": False}
    with _guard:
        _cache[provider.agent] = (time.monotonic(), result)
    return dict(result)


def clear() -> None:
    with _guard:
        _cache.clear()
