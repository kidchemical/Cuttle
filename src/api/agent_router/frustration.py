"""Frustration ("rage") detection for the CuttleRouter.

The user saying *"still not fixed"*, *"still broken"*, *"you said you fixed
it"* is the strongest free quality signal available: explicit negative
feedback on the previous attempts, in their own words, at zero token cost.

Detection is deliberately conservative — a **first** message like "login is
not working" is a fresh bug report, not frustration, so phrases target
frustration *about prior attempts* ("still …", "you said …", "for the Nth
time"). The phrase list and the feature switch are configurable in
``settings.json`` → ``agent_router.rage`` — no LLM, no tokens.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

SETTINGS_KEY = "agent_router"
RAGE_SUBKEY = "rage"

DEFAULT_PHRASES: List[str] = [
    # explicit "prior attempt failed"
    "still not fixed",
    "still broken",
    "still fails",
    "still failing",
    "still doesn't work",
    "still does not work",
    "still not working",
    "still not fixed",
    "still there",
    "still happening",
    "still the same",
    "still exists",
    "isn't fixed",
    "is not fixed",
    "not fixed yet",
    "didn't fix",
    "did not fix",
    "you said you fixed",
    "you said it was fixed",
    "you said this was fixed",
    "you claimed",
    "you didn't actually",
    "you did not actually",
    "i told you",
    "wrong again",
    "broke it again",
    "failed again",
    "same error again",
    "same problem again",
    "same issue again",
    "back again",
]

_NTH_TIME_RE = re.compile(r"\bfor the \w+ time\b", re.I)


def default_rage_config() -> Dict[str, Any]:
    return {
        "enabled": True,
        "phrases": list(DEFAULT_PHRASES),
        "investigator": {
            "enabled": True,
            "agent": "jev",
            "model": "jev-latest",
            "timeout_s": 30,
        },
    }


def _norm_investigator(raw: Any) -> Dict[str, Any]:
    """Validate the investigator block; unknown agents fall back to cursor."""
    base = default_rage_config()["investigator"]
    if not isinstance(raw, dict):
        return dict(base)
    agent = str(raw.get("agent") or base["agent"]).strip().lower()
    if agent in ("jev", "jev-latest", "typesafe", "typesafe-jev"):
        try:
            timeout = int(raw.get("timeout_s") or 30)
        except (TypeError, ValueError):
            timeout = 30
        return {
            "enabled": raw.get("enabled", True) is not False,
            "agent": "jev",
            "model": str(raw.get("model") or "jev-latest").strip() or "jev-latest",
            "timeout_s": max(10, min(timeout, 120)),
        }
    try:
        from api.agent_router.registry import normalize_agent_id

        agent = normalize_agent_id(agent) or base["agent"]
    except Exception:
        agent = base["agent"]
    if agent == "jev":
        try:
            timeout = int(raw.get("timeout_s") or 30)
        except (TypeError, ValueError):
            timeout = 30
        return {
            "enabled": raw.get("enabled", True) is not False,
            "agent": "jev",
            "model": str(raw.get("model") or "jev-latest").strip() or "jev-latest",
            "timeout_s": max(10, min(timeout, 120)),
        }
    try:
        timeout = int(raw.get("timeout_s") or base["timeout_s"])
    except (TypeError, ValueError):
        timeout = base["timeout_s"]
    return {
        "enabled": raw.get("enabled", True) is not False,
        "agent": agent,
        "model": str(raw.get("model") or "").strip() or ("auto" if agent == "cursor" else ""),
        "timeout_s": max(30, min(timeout, 1800)),
    }


def load_rage_config() -> Dict[str, Any]:
    """Read agent_router.rage from disk. Empty/missing phrases → defaults."""
    from managers.settings_manager import get_settings_manager

    try:
        sm = get_settings_manager()
        try:
            sm.reload()
        except Exception:
            pass
        raw = sm.get_setting(SETTINGS_KEY)
        cfg = raw.get(RAGE_SUBKEY) if isinstance(raw, dict) else None
        if not isinstance(cfg, dict):
            return default_rage_config()
        phrases = cfg.get("phrases")
        if not isinstance(phrases, list) or not phrases:
            phrases = list(DEFAULT_PHRASES)
        return {
            "enabled": cfg.get("enabled", True) is not False,
            "phrases": [str(p).lower() for p in phrases if str(p).strip()],
            "investigator": _norm_investigator(cfg.get("investigator")),
        }
    except Exception:
        return default_rage_config()


def save_rage_config(raw: Any) -> tuple:
    """Validate + persist agent_router.rage. Returns (saved, error)."""
    from managers.settings_manager import get_settings_manager

    if not isinstance(raw, dict):
        return load_rage_config(), "rage must be an object"
    phrases = raw.get("phrases")
    if phrases is not None and not isinstance(phrases, list):
        return load_rage_config(), "rage.phrases must be a list"
    inv_raw = raw.get("investigator")
    sm = get_settings_manager()
    try:
        sm.reload()
    except Exception:
        pass
    root = sm.get_setting(SETTINGS_KEY)
    if not isinstance(root, dict):
        root = {}
    merged = dict(load_rage_config())
    if isinstance(raw.get("enabled"), bool):
        merged["enabled"] = raw["enabled"]
    if isinstance(phrases, list):
        merged["phrases"] = [str(p).lower().strip() for p in phrases if str(p).strip()]
    if inv_raw is not None:
        merged["investigator"] = _norm_investigator(inv_raw)
    root[RAGE_SUBKEY] = merged
    sm.set_setting(SETTINGS_KEY, root)
    return load_rage_config(), None


def detect_frustration(text: str, *, config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Return the matched frustration phrase, or None."""
    cfg = config or load_rage_config()
    if not cfg.get("enabled", True):
        return None
    low = (text or "").strip().lower()
    if not low:
        return None
    for phrase in cfg.get("phrases", []):
        if phrase and phrase in low:
            return phrase
    if _NTH_TIME_RE.search(low):
        return _NTH_TIME_RE.search(low).group(0)
    return None


def rage_config_to_dict() -> Dict[str, Any]:
    """Current effective config (for the Router page / /router status later)."""
    cfg = load_rage_config()
    return {
        "enabled": bool(cfg.get("enabled")),
        "phrases": list(cfg.get("phrases") or []),
        "investigator": dict(cfg.get("investigator") or {}),
        "default_phrases": list(DEFAULT_PHRASES),
        "defaults": default_rage_config(),
    }
