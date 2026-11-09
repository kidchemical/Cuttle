"""Auto-compact policy: settings shape, layering and the compact decision.

Pure and Cuttle-free: callers pass plain dicts (stored settings, a chat's
override, a snapshot of its context fill and history) and get plain dicts back.

Why compact here at all when every CLI auto-compacts: the CLIs compact
mid-task near the window edge (~83-95%), the worst moment. Cuttle sees turn
boundaries, so it compacts *between* turns at a lower threshold, and uses an
absolute token cap too, because a percentage of a 1M window is far past where
answers degrade. A Tasks milestone (an item just checked off) is the cleanest
boundary of all and gets its own, lower threshold.

Settings (``settings.json`` key ``auto_compact``)::

    {"chats":     {"enabled": true, "percent": 70, "max_tokens": 300000},
     "managed":   {"enabled": true, "percent": 60, "max_tokens": 250000},
     "milestone": {"enabled": true, "percent": 40, "max_tokens": 150000},
     "mode": "auto", "quiet_seconds": 20}

``managed`` chats are the ones agents drive: supervised targets, supervisor
chats, sub-agent children and orchestrator-bound chats. ``max_tokens`` 0 means
no absolute cap. ``mode``: ``auto`` (the CLI's own compact; a managed fresh
session if that did not bring the fill down), ``native`` or ``fresh``.

A chat override (gauge popover / CLI) may set ``enabled``, ``percent`` and
``milestone``; a missing key or ``None`` inherits.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

SCHEMA_VERSION = 1
MODES = ("auto", "native", "fresh")
SCOPES = ("chats", "managed", "milestone")
PERCENT_RANGE = (20, 95)
MILESTONE_PERCENT_RANGE = (10, 95)
MAX_TOKENS_RANGE = (0, 10_000_000)
QUIET_RANGE = (0, 600)
# Give up after this many failed attempts in a row (until the fill drops
# below the threshold, a compact succeeds, or the override changes).
MAX_FAILURES = 4

DEFAULTS: Dict[str, Any] = {
    "chats": {"enabled": True, "percent": 70, "max_tokens": 300_000},
    "managed": {"enabled": True, "percent": 60, "max_tokens": 250_000},
    "milestone": {"enabled": True, "percent": 40, "max_tokens": 150_000},
    "mode": "auto",
    "quiet_seconds": 20,
}

OVERRIDE_KEYS = ("enabled", "percent", "milestone")


class PolicyError(ValueError):
    """A settings or override value is out of shape or range."""


def defaults() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULTS)


def _int_in(value: Any, lo: int, hi: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"{name} must be a number")
    out = int(value)
    if not lo <= out <= hi:
        raise PolicyError(f"{name} must be {lo}–{hi}")
    return out


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyError(f"{name} must be true or false")
    return value


def _scope_range(scope: str):
    return MILESTONE_PERCENT_RANGE if scope == "milestone" else PERCENT_RANGE


def validate_patch(patch: Any) -> Dict[str, Any]:
    """Check a settings patch (strict). Returns the cleaned patch."""
    if not isinstance(patch, dict):
        raise PolicyError("settings must be an object")
    out: Dict[str, Any] = {}
    for key, value in patch.items():
        if key in SCOPES:
            if not isinstance(value, dict):
                raise PolicyError(f"{key} must be an object")
            scope: Dict[str, Any] = {}
            for field, v in value.items():
                name = f"{key}.{field}"
                if field == "enabled":
                    scope[field] = _bool(v, name)
                elif field == "percent":
                    scope[field] = _int_in(v, *_scope_range(key), name)
                elif field == "max_tokens":
                    scope[field] = _int_in(v, *MAX_TOKENS_RANGE, name)
                else:
                    raise PolicyError(f"unknown setting {name}")
            out[key] = scope
        elif key == "mode":
            if value not in MODES:
                raise PolicyError("mode must be auto, native or fresh")
            out[key] = value
        elif key == "quiet_seconds":
            out[key] = _int_in(value, *QUIET_RANGE, "quiet_seconds")
        else:
            raise PolicyError(f"unknown setting {key}")
    return out


def merge_settings(stored: Any, patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Defaults, then the stored settings, then ``patch``. Bad stored values
    fall back to the default one by one (a hand-edited file never breaks it)."""
    out = defaults()
    for layer in (stored, patch):
        if not isinstance(layer, dict):
            continue
        for key, value in layer.items():
            try:
                clean = validate_patch({key: value})
            except PolicyError:
                if key in SCOPES and isinstance(value, dict):
                    # Keep the scope's good fields.
                    for field, v in value.items():
                        try:
                            out[key].update(validate_patch({key: {field: v}})[key])
                        except PolicyError:
                            pass
                continue
            if key in SCOPES:
                out[key].update(clean[key])
            else:
                out[key] = clean[key]
    return out


def validate_override(raw: Any) -> Dict[str, Any]:
    """A chat override: ``{enabled?, percent?, milestone?}``; ``None`` inherits."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyError("override must be an object")
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in OVERRIDE_KEYS:
            raise PolicyError(f"unknown override {key}")
        if value is None:
            continue
        if key == "percent":
            out[key] = _int_in(value, *PERCENT_RANGE, "percent")
        else:
            out[key] = _bool(value, key)
    return out


def effective(settings: Dict[str, Any], *, managed: bool,
              override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The policy one chat runs under, with where each value came from."""
    s = merge_settings(settings)
    scope = "managed" if managed else "chats"
    base, ms = s[scope], s["milestone"]
    ov = {k: v for k, v in (override or {}).items() if k in OVERRIDE_KEYS and v is not None}
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "enabled": bool(ov.get("enabled", base["enabled"])),
        "percent": int(ov.get("percent", base["percent"])),
        "max_tokens": int(base["max_tokens"]),
        "milestone": bool(ov.get("milestone", ms["enabled"])),
        "milestone_percent": int(ms["percent"]),
        "milestone_max_tokens": int(ms["max_tokens"]),
        "mode": s["mode"],
        "quiet_seconds": int(s["quiet_seconds"]),
        "source": {k: ("chat" if k in ov else scope if k != "milestone" else "milestone")
                   for k in OVERRIDE_KEYS},
    }


def over_threshold(policy: Dict[str, Any], percent: float, used: int, *, milestone: bool = False) -> bool:
    if milestone:
        pct, cap = policy["milestone_percent"], policy["milestone_max_tokens"]
    else:
        pct, cap = policy["percent"], policy["max_tokens"]
    return float(percent or 0) >= pct or bool(cap and int(used or 0) >= cap)


def retry_wait_turns(fail_streak: int) -> int:
    """Turns to wait after the ``fail_streak``-th failure in a row: 1, 2, 4, 8."""
    return min(2 ** max(int(fail_streak) - 1, 0), 8) if fail_streak > 0 else 0


def decide(policy: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
    """Whether to compact now.

    ``snap`` keys: ``supported``, ``percent``, ``used_tokens``,
    ``token_source``, ``native_available``, ``fresh_available``, ``busy``,
    ``live_cli``, ``turns_since_compact`` (None = never compacted),
    ``turns_since_attempt``, ``fail_streak``, ``ineffective`` (compacts in a
    row that left the fill over the threshold), ``milestone``.

    Returns ``{compact, reason, mode, trigger}``; ``reason`` names the gate
    that said no (``live_cli`` / ``busy`` mean "try again later")."""
    def no(reason):
        return {"compact": False, "reason": reason, "mode": None, "trigger": None}

    if not policy.get("enabled"):
        return no("disabled")
    if not snap.get("supported"):
        return no("unsupported")
    used = int(snap.get("used_tokens") or 0)
    if used <= 0 or snap.get("token_source") == "aggregated":
        return no("no_reading")
    if snap.get("busy"):
        return no("busy")
    if snap.get("live_cli"):
        return no("live_cli")
    if snap.get("turns_since_compact") == 0:
        return no("just_compacted")
    streak = int(snap.get("fail_streak") or 0)
    if streak >= MAX_FAILURES:
        return no("failing")
    if streak and int(snap.get("turns_since_attempt") or 0) < retry_wait_turns(streak):
        return no("backoff")
    percent = float(snap.get("percent") or 0)
    if over_threshold(policy, percent, used):
        trigger = "threshold"
    elif policy.get("milestone") and snap.get("milestone") and over_threshold(policy, percent, used, milestone=True):
        trigger = "milestone"
    else:
        return no("below_threshold")
    native, fresh = bool(snap.get("native_available")), bool(snap.get("fresh_available"))
    mode = policy.get("mode") or "auto"
    if mode == "native":
        chosen = "native" if native else None
    elif mode == "fresh":
        chosen = "fresh" if fresh else None
    elif int(snap.get("ineffective") or 0) > 0:
        # The CLI's summary left the window over the threshold: start fresh.
        chosen = "fresh" if fresh else None
    else:
        # No headless compact (Antigravity manages its own window): leave it be.
        chosen = "native" if native else None
    if chosen is None:
        return no("ineffective" if mode == "auto" and snap.get("ineffective") else "no_method")
    return {"compact": True, "reason": trigger, "mode": chosen, "trigger": trigger}
