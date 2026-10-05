"""Budget-aware routing — read live plan usage, route to what will actually run.

A fixed default → escalate → fallback walk discovers an exhausted account the
expensive way: by failing on it. Cuttle already knows the answer — the same
plan windows ``/usage`` shows (Codex 5-hour/weekly, Claude 5-hour/weekly,
Cursor included API usage). With budget awareness on, the router reads them
*before* picking a target: blocked accounts move to the back of the chain,
nearly-spent ones behind healthy ones, and the declared order is kept
otherwise (it is still your preference).

Fetches run on a background thread and are cached; routing never waits on a
vendor API. Accounts with no usage API (Muse, OpenCode, Hermes, …) read as
``unknown`` and keep their declared position.

Settings: ``settings.json → agent_router.budget``
``{"enabled": false, "low_headroom": 0.15, "refresh_s": 300}``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from api.agent_router.policy import is_auto_target
from api.agent_router.types import ExecutionTarget

DEFAULT_LOW_HEADROOM = 0.15
DEFAULT_REFRESH_S = 300


@dataclass
class Account:
    agent: str
    blocked: bool = False  # every model on the account
    premium_blocked: bool = False  # named/premium models only (Cursor Auto still runs)
    headroom: Optional[float] = None  # 0..1 left in the tightest window; None = unknown
    reset_at: Optional[float] = None
    label: str = ""  # "5-hour 100% · weekly 63%"
    fetched_at: float = 0.0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def budget_settings(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if raw is None:
        try:
            from managers.settings_manager import get_settings_manager

            ar = get_settings_manager().get_setting("agent_router") or {}
            raw = ar.get("budget") if isinstance(ar, dict) else None
        except Exception:
            raw = None
    raw = raw if isinstance(raw, dict) else {}

    def _num(key, default, lo, hi):
        try:
            return max(lo, min(hi, float(raw.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": raw.get("enabled", False) is True,
        "low_headroom": _num("low_headroom", DEFAULT_LOW_HEADROOM, 0.0, 0.9),
        "refresh_s": int(_num("refresh_s", DEFAULT_REFRESH_S, 60, 3600)),
    }


def _pct(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_reset(ts: Optional[float]) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%-I:%M %p")
    except Exception:
        return ""


# ── per-vendor parsers (pure; tested with recorded payloads) ────────────────

def parse_codex(data: Dict[str, Any], now: float) -> Account:
    acc = Account("codex", fetched_at=now)
    if not data or not data.get("success"):
        acc.error = str((data or {}).get("error") or "no data")[:120]
        return acc
    rl = data.get("rate_limit") or {}
    windows = [("5-hour", rl.get("primary_window")), ("weekly", rl.get("secondary_window"))]
    used, parts, resets = [], [], []
    for name, w in windows:
        if not isinstance(w, dict):
            continue
        p = _pct(w.get("used_percent"))
        if p is None:
            continue
        used.append(p)
        parts.append(f"{name} {p:.0f}%")
        if p >= 100 and w.get("reset_at"):
            resets.append(float(w["reset_at"]))
    credits = data.get("credits") or {}
    has_credits = bool(credits.get("unlimited") or credits.get("has_credits"))
    acc.blocked = (rl.get("allowed") is False or bool(rl.get("limit_reached"))) and not has_credits
    acc.headroom = None if not used else max(0.0, 1.0 - max(used) / 100.0)
    acc.reset_at = min(resets) if resets else None
    acc.label = " · ".join(parts)
    return acc


def parse_claude(data: Dict[str, Any], now: float) -> Account:
    acc = Account("claude", fetched_at=now)
    if not data or not data.get("success"):
        acc.error = str((data or {}).get("error") or "no data")[:120]
        return acc
    used, parts, resets = [], [], []
    for w in data.get("windows") or []:
        if not isinstance(w, dict):
            continue
        p = _pct(w.get("used_percent"))
        if p is None:
            continue
        used.append(p)
        parts.append(f"{w.get('label') or 'window'} {p:.0f}%")
        if p >= 100 and w.get("reset_at"):
            resets.append(float(w["reset_at"]))
    extra = data.get("extra_usage") or {}
    overflow = bool(extra.get("is_enabled")) and not extra.get("spend_limit_reached")
    acc.blocked = bool(resets) and not overflow
    acc.headroom = None if not used else max(0.0, 1.0 - max(used) / 100.0)
    acc.reset_at = min(resets) if resets else None
    acc.label = " · ".join(parts)
    return acc


def parse_cursor(data: Dict[str, Any], now: float) -> Account:
    acc = Account("cursor", fetched_at=now)
    if not data or not data.get("success"):
        acc.error = str((data or {}).get("error") or "no data")[:120]
        return acc
    pu = data.get("plan_usage") or ((data.get("period") or {}).get("planUsage")) or {}
    api_used = _pct(pu.get("apiPercentUsed"))
    bonus = bool(pu.get("remainingBonus"))
    # Auto keeps running past the included pool (bonus/promo capacity), so
    # only named premium models are ever blocked from this data.
    acc.premium_blocked = api_used is not None and api_used >= 100 and not bonus
    acc.headroom = None if api_used is None else max(0.0, 1.0 - api_used / 100.0)
    end = (data.get("period") or {}).get("billingCycleEnd")
    try:
        acc.reset_at = float(end) / 1000.0 if acc.premium_blocked and end else None
    except (TypeError, ValueError):
        acc.reset_at = None
    if api_used is not None:
        acc.label = f"included API usage {api_used:.0f}%" + (" (bonus left)" if bonus else "")
    return acc


def _fetchers() -> Dict[str, Tuple[Callable[[], Dict[str, Any]], Callable[[Dict[str, Any], float], Account]]]:
    def codex():
        from api.agent_usage import fetch_codex_account_usage

        return fetch_codex_account_usage()

    def claude():
        from api.agent_usage import fetch_claude_plan_limits

        return fetch_claude_plan_limits()

    def cursor():
        from api.cursor_agent_commands import fetch_cursor_account_usage

        return fetch_cursor_account_usage()

    return {"codex": (codex, parse_codex), "claude": (claude, parse_claude), "cursor": (cursor, parse_cursor)}


# ── cache ────────────────────────────────────────────────────────────────────

_LOCK = threading.Lock()
_ACCOUNTS: Dict[str, Account] = {}
_FETCHED_AT = 0.0
_REFRESHING: Optional[threading.Thread] = None


def _refresh_now() -> None:
    global _FETCHED_AT, _REFRESHING
    now = time.time()
    fresh: Dict[str, Account] = {}
    for agent, (fetch, parse) in _fetchers().items():
        try:
            fresh[agent] = parse(fetch() or {}, now)
        except Exception as exc:
            fresh[agent] = Account(agent, fetched_at=now, error=str(exc)[:120])
    with _LOCK:
        _ACCOUNTS.clear()
        _ACCOUNTS.update(fresh)
        _FETCHED_AT = now
        _REFRESHING = None


def snapshot(*, wait: bool = False, timeout: float = 12.0, force: bool = False) -> Dict[str, Account]:
    """Cached accounts; kicks a background refresh when stale. Never raises."""
    global _REFRESHING
    max_age = budget_settings()["refresh_s"]
    with _LOCK:
        stale = force or (time.time() - _FETCHED_AT) > max_age
        thread = _REFRESHING
        if stale and thread is None:
            thread = threading.Thread(target=_refresh_now, name="router-budget", daemon=True)
            _REFRESHING = thread
            thread.start()
    if wait and thread is not None:
        thread.join(timeout)
    with _LOCK:
        return dict(_ACCOUNTS)


def set_accounts(accounts: List[Account]) -> None:
    """Test/seed hook: install a snapshot without fetching."""
    global _FETCHED_AT
    with _LOCK:
        _ACCOUNTS.clear()
        _ACCOUNTS.update({a.agent: a for a in accounts})
        _FETCHED_AT = time.time()


def clear() -> None:
    global _FETCHED_AT
    with _LOCK:
        _ACCOUNTS.clear()
        _FETCHED_AT = 0.0


def target_state(target: ExecutionTarget, accounts: Dict[str, Account], *, low_headroom: float) -> Tuple[str, str]:
    """("blocked"|"low"|"ok"|"unknown", short why) for one target."""
    acc = accounts.get(target.agent)
    if acc is None or acc.error:
        return "unknown", ""
    reset = _fmt_reset(acc.reset_at)
    tail = f", resets {reset}" if reset else ""
    if acc.blocked or (acc.premium_blocked and not is_auto_target(target)):
        return "blocked", f"{acc.label or 'limit reached'}{tail}"
    if acc.agent == "cursor" and is_auto_target(target):
        return "ok", ""  # Auto is not metered by the included pool
    if acc.headroom is not None and acc.headroom < low_headroom:
        return "low", f"{acc.label} — {acc.headroom * 100:.0f}% left"
    return "ok", ""


def save_budget_settings(raw: Any) -> Tuple[Dict[str, Any], Optional[str]]:
    """Validate + persist ``agent_router.budget``. Returns (settings, error)."""
    if not isinstance(raw, dict):
        return budget_settings(), "budget must be an object"
    current = budget_settings()
    merged = {**current, **raw}
    merged["enabled"] = (raw["enabled"] is True) if "enabled" in raw else current["enabled"]
    clean = budget_settings(merged)
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    try:
        sm.reload()
    except Exception:
        pass
    ar = sm.get_setting("agent_router") or {}
    ar = ar if isinstance(ar, dict) else {}
    ar["budget"] = clean
    sm.set_setting("agent_router", ar)
    return clean, None


def accounts_payload(*, wait: bool = False, force: bool = False) -> List[Dict[str, Any]]:
    """Live accounts for the Router page (fetches only when asked)."""
    accs = snapshot(wait=wait, force=force)
    out = []
    for acc in sorted(accs.values(), key=lambda a: a.agent):
        d = acc.to_dict()
        d["reset_label"] = _fmt_reset(acc.reset_at)
        out.append(d)
    return out
