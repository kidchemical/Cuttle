"""Claude Code: subscription first, Anthropic API credit as the fallback.

A Claude Code turn runs on the CLI's own login (the user's subscription). When
that login is out of usage ("You've hit your session limit · resets 3pm"), the
adapter reruns the turn once with the Providers-page ``ANTHROPIC_API_KEY`` in
the CLI's environment — `claude -p` bills an API key over the claude.ai login —
and keeps using the key until the reset the error named. API-billed turns are
recorded in a small spend ledger in the Cuttle home; a monthly budget stops the
fallback once it is spent.

Settings (``settings.json``)::

    "agent_billing_fallback": {"claude": {"enabled": true, "monthly_budget_usd": 200}}

Off by default (it spends money). Kill switch: ``CUTTLE_CLAUDE_API_FALLBACK=0``
(also blocks API-first turns).

Billing order: ``"prefer": "subscription" | "api"`` in the same block sets the
default; a chat can pin its own (``claude_cli_session_store`` billing pin) and
one message can override with a leading ``/use-api`` / ``/use-subscription``.
API first runs on the key while one is set and the budget is not spent, and
drops to the subscription otherwise (or when the API rejects the key/credit).
The key itself is never stored here; it is read from the process environment
(the Providers page applies saves to ``os.environ``) or ``<home>/.env``.

The text helpers at the top are pure; state lives below them.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

# ── pure: limit messages and reset times ────────────────────────────────────

# Claude Code's own phrasings (2.1.x composes "You've hit your <window> limit
# · resets <when>"; older builds print "Claude AI usage limit reached|<epoch>").
_LIMIT_RE = re.compile(
    r"you['’]ve hit your\b[^\n]*\blimit"
    r"|you['’]ve reached your\b[^\n]*\blimit"
    r"|you['’]re out of usage credits"
    r"|your org is out of usage"
    r"|claude ai usage limit reached",
    re.I,
)
_EPOCH_RE = re.compile(r"usage limit reached\|(\d{9,11})", re.I)
_RESETS_IN_RE = re.compile(
    r"resets in\s+(?:(\d+)\s*d(?:ays?)?\s*)?(?:(\d+)\s*h(?:ours?|rs?)?\s*)?(?:(\d+)\s*m(?:in(?:utes?|s)?)?)?",
    re.I,
)
_RESETS_AT_RE = re.compile(
    r"resets\s+(?:(?P<mon>[A-Z][a-z]{2,8})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s*)?"
    r"(?:at\s+)?(?:(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?m\.?))?"
    r"(?:\s*\((?P<tz>[A-Za-z_]+(?:/[A-Za-z_+-]+)*)\))?",
    re.I,
)

DEFAULT_COOLDOWN_S = 30 * 60  # no reset time in the message: re-probe soon (a probe is free)
MAX_COOLDOWN_S = 8 * 24 * 3600


BILLING_MODES = ("subscription", "api")
_BILLING_PREFIX_RE = re.compile(r"^\s*/use-(api|subscription)(?=\s|$)[ \t]*\r?\n?", re.I)
# The API (not the subscription) refused: bad/missing key or no credit left.
_API_REFUSED_RE = re.compile(
    r"credit balance is too low|invalid x-api-key|invalid api key|authentication_error"
    r"|api key .{0,40}(?:invalid|revoked|disabled)",
    re.I,
)


def parse_billing_prefix(prompt: str):
    """``(mode, rest)`` for a leading ``/use-api`` / ``/use-subscription``.

    ``(None, prompt)`` when absent or when nothing follows the token (a bare
    token is not a turn).
    """
    text = prompt or ""
    m = _BILLING_PREFIX_RE.match(text)
    if not m:
        return None, text
    rest = text[m.end():]
    if not rest.strip():
        return None, text
    return m.group(1).lower(), rest


def is_api_refusal(text: str) -> bool:
    return bool(_API_REFUSED_RE.search(text or ""))


def is_subscription_limit(text: str) -> bool:
    """True when Claude Code says the claude.ai login is out of usage."""
    return bool(_LIMIT_RE.search(text or ""))


def _zone(name: Optional[str]):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return None


def reset_epoch(text: str, now: Optional[float] = None) -> Optional[float]:
    """When the limit named in ``text`` resets (epoch seconds), or None."""
    now = time.time() if now is None else now
    s = text or ""
    m = _EPOCH_RE.search(s)
    if m:
        ts = float(m.group(1))
        return ts if now < ts <= now + MAX_COOLDOWN_S else None
    m = _RESETS_IN_RE.search(s)
    if m and any(m.groups()):
        d, h, mi = (int(g or 0) for g in m.groups())
        secs = d * 86400 + h * 3600 + mi * 60
        return now + secs if 0 < secs <= MAX_COOLDOWN_S else None
    for m in _RESETS_AT_RE.finditer(s):
        if not (m.group("mon") or m.group("h")):
            continue
        tz = _zone(m.group("tz"))
        base = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now)
        hour, minute = 0, 0
        if m.group("h"):
            hour = int(m.group("h")) % 12 + (12 if m.group("ap").lower().startswith("p") else 0)
            minute = int(m.group("m") or 0)
        cand = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if m.group("mon"):
            try:
                month = datetime.strptime(m.group("mon")[:3].title(), "%b").month
                cand = cand.replace(month=month, day=int(m.group("day")))
            except ValueError:
                continue
            if cand.timestamp() <= now:
                cand = cand.replace(year=cand.year + 1)
        elif cand.timestamp() <= now:
            cand = cand + timedelta(days=1)
        ts = cand.timestamp()
        return ts if ts <= now + MAX_COOLDOWN_S else None
    return None


def month_key(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m")


def next_month_epoch(now: Optional[float] = None) -> float:
    d = datetime.fromtimestamp(time.time() if now is None else now)
    first = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first.replace(year=d.year + 1, month=1) if d.month == 12 else first.replace(month=d.month + 1)).timestamp()


def usd(value: float) -> str:
    """Dollars; sub-cent amounts keep enough digits to not read as $0.00."""
    return f"${value:,.4f}" if 0 < value < 0.01 else f"${value:,.2f}"


def footer(cost: Optional[float], month_usd: float, budget: Optional[float], until: Optional[float]) -> str:
    """One italic line saying this reply was billed to API credit."""
    parts = ["Billed to Claude API credit"]
    if cost is not None:
        parts[0] += f": {usd(cost)}"
    tail = usd(month_usd) + (f" of ${budget:,.0f}" if budget else "") + " this month"
    parts.append(tail)
    if until:
        parts.append("subscription resets " + datetime.fromtimestamp(until).strftime("%a %H:%M"))
    return "_" + " · ".join(parts) + "_"


# ── state: settings, key, exhaustion, spend ledger ──────────────────────────

_LOCK = threading.Lock()
_exhausted_until: float = 0.0  # process-local; a Flask restart re-probes once


def settings() -> Dict[str, Any]:
    try:
        from managers.settings_manager import get_settings_manager

        cfg = get_settings_manager().get_setting("agent_billing_fallback", None)
    except Exception:
        cfg = None
    cfg = (cfg or {}).get("claude") if isinstance(cfg, dict) else None
    return cfg if isinstance(cfg, dict) else {}


def enabled() -> bool:
    if killed():
        return False
    return bool(settings().get("enabled"))


def killed() -> bool:
    return (os.environ.get("CUTTLE_CLAUDE_API_FALLBACK") or "").strip().lower() in ("0", "false", "off", "no")


def default_billing() -> str:
    """Settings default: ``"api"`` only when ``prefer`` says so."""
    return "api" if str(settings().get("prefer") or "").strip().lower() == "api" else "subscription"


def resolve_billing(chat_session_id: Any = None, override: Optional[str] = None):
    """``(mode, source)``: this message → this chat's pin → settings default."""
    if override in BILLING_MODES:
        return override, "message"
    try:
        from scripts.utilities.claude_cli_session_store import load_claude_billing

        pin = load_claude_billing(chat_session_id)
    except Exception:
        pin = None
    if pin in BILLING_MODES:
        return pin, "chat"
    return default_billing(), "default"


def api_ready() -> str:
    """'' when an API-first turn may run on the key, else why not.

    Unlike :func:`available` this ignores the automatic-fallback toggle: an
    explicit API-first choice is the consent. Budget and kill switch still apply.
    """
    if killed():
        return "disabled"
    if not api_key():
        return "no_key"
    if over_budget():
        return "over_budget"
    return ""


def settings_view() -> Dict[str, Any]:
    cfg = settings()
    return {
        "prefer": default_billing(),
        "fallback_enabled": bool(cfg.get("enabled")),
        "monthly_budget_usd": budget_usd(),
        "has_key": bool(api_key()),
        "killed": killed(),
        "month_usd": month_spend()["usd"],
    }


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and merge a partial Claude billing block. Raises ``ValueError``."""
    if not isinstance(patch, dict):
        raise ValueError("Expected an object.")
    from managers.settings_manager import get_settings_manager

    mgr = get_settings_manager()
    whole = mgr.get_setting("agent_billing_fallback", None)
    whole = dict(whole) if isinstance(whole, dict) else {}
    cfg = dict(whole.get("claude") or {}) if isinstance(whole.get("claude"), dict) else {}
    if "prefer" in patch:
        prefer = str(patch["prefer"] or "").strip().lower()
        if prefer not in BILLING_MODES:
            raise ValueError("prefer must be 'subscription' or 'api'.")
        cfg["prefer"] = prefer
    if "fallback_enabled" in patch:
        cfg["enabled"] = bool(patch["fallback_enabled"])
    if "monthly_budget_usd" in patch:
        raw = patch["monthly_budget_usd"]
        if raw in (None, ""):
            cfg.pop("monthly_budget_usd", None)
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError("monthly_budget_usd must be a number.") from None
            if value < 0 or value > 1_000_000:
                raise ValueError("monthly_budget_usd must be between 0 and 1,000,000.")
            cfg["monthly_budget_usd"] = value
    whole["claude"] = cfg
    mgr.set_setting("agent_billing_fallback", whole)
    return settings_view()


def budget_usd() -> Optional[float]:
    try:
        v = float(settings().get("monthly_budget_usd"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def api_key() -> str:
    """The Providers-page key: process env first, then ``<home>/.env``."""
    v = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if v:
        return v
    try:
        from dotenv import dotenv_values

        from core.runtime_paths import env_file

        path = env_file()
        if path.exists():
            return str((dotenv_values(path) or {}).get("ANTHROPIC_API_KEY") or "").strip()
    except Exception:
        pass
    return ""


def mark_exhausted(text: str, now: Optional[float] = None) -> float:
    global _exhausted_until
    now = time.time() if now is None else now
    until = reset_epoch(text, now) or (now + DEFAULT_COOLDOWN_S)
    with _LOCK:
        _exhausted_until = max(_exhausted_until if _exhausted_until > now else 0.0, until)
        return _exhausted_until


def exhausted_until(now: Optional[float] = None) -> Optional[float]:
    global _exhausted_until
    now = time.time() if now is None else now
    with _LOCK:
        if _exhausted_until and _exhausted_until <= now:
            _exhausted_until = 0.0
        return _exhausted_until or None


def clear_exhausted() -> None:
    global _exhausted_until
    with _LOCK:
        _exhausted_until = 0.0


def _db() -> sqlite3.Connection:
    from core.runtime_paths import data_db_dir

    conn = sqlite3.connect(str(data_db_dir() / "claude_api_spend.db"), timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS spend (ts REAL NOT NULL, month TEXT NOT NULL,"
        " chat_session_id TEXT, model TEXT, cost_usd REAL NOT NULL)"
    )
    return conn


def record_spend(cost: Optional[float], *, chat_session_id: Optional[str], model: str,
                 now: Optional[float] = None) -> None:
    if not cost or cost <= 0:
        return
    now = time.time() if now is None else now
    try:
        with _db() as conn:
            conn.execute("INSERT INTO spend VALUES (?,?,?,?,?)",
                         (now, month_key(now), str(chat_session_id or ""), model or "", float(cost)))
    except sqlite3.Error as exc:
        print(f"[claude api_fallback] spend not recorded: {exc}", flush=True)


def month_spend(now: Optional[float] = None) -> Dict[str, float]:
    try:
        with _db() as conn:
            total, turns = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0), COUNT(*) FROM spend WHERE month=?",
                (month_key(now),),
            ).fetchone()
    except sqlite3.Error:
        total, turns = 0.0, 0
    return {"usd": round(float(total), 6), "turns": int(turns)}


def over_budget(now: Optional[float] = None) -> bool:
    cap = budget_usd()
    return bool(cap and month_spend(now)["usd"] >= cap)


def available() -> str:
    """'' when an API-billed run may start, else why not (for the reply)."""
    if not enabled():
        return "disabled"
    if not api_key():
        return "no_key"
    if over_budget():
        return "over_budget"
    return ""


# ── Claude Code usage meter: optional "API credit" row (config ``api_credit``) ──

def credit_status(now: Optional[float] = None) -> str:
    """One line for the meter: off / no key / standby / active / budget spent."""
    state = available()
    if state == "disabled":
        return "fallback off"
    if state == "no_key":
        return "no Anthropic key in Settings → Providers"
    if state == "over_budget":
        return "monthly budget spent"
    until = exhausted_until(now)
    if until:
        return "in use until the subscription resets " + datetime.fromtimestamp(until).strftime("%a %H:%M")
    return "standby (subscription in use)"


def merge_claude_usage(snap: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """The Claude Code usage snapshot plus an ``api_credit`` window.

    While the fallback is covering an exhausted subscription the meter is not
    blocked: the API-credit window becomes its headline (``primary_window``).
    """
    now = time.time() if now is None else now
    out = dict(snap or {})
    spent = month_spend(now)
    cap = budget_usd()
    used_pct = min(100.0, spent["usd"] / cap * 100.0) if cap else 0.0
    window = {
        "id": "api_credit", "label": "API credit",
        "used_percent": round(used_pct, 2), "remaining_percent": round(100.0 - used_pct, 2),
        "reset_at": int(next_month_epoch(now)),
        "value_text": (usd(max(0.0, cap - spent["usd"])) + " left") if cap else (usd(spent["usd"]) + " used"),
    }
    out["windows"] = [w for w in out.get("windows") or [] if isinstance(w, dict) and w.get("id") != "api_credit"] + [window]
    extras = dict(out.get("extras") or {})
    extras["api_credit"] = (f"{usd(spent['usd'])}" + (f" of ${cap:,.0f}" if cap else "")
                            + f" this month ({spent['turns']} turns) · {credit_status(now)}")
    out["extras"] = extras
    if out.get("blocked") and not available():
        out.update(blocked=False, unblock_at=None, primary_window="api_credit")
    return out
