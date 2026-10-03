"""Cuttle achievements (experimental, gated by the ``achievements`` flag).

Layering:

* :mod:`api.achievements.catalog`    — vocabulary (pure data)
* :mod:`api.achievements.evaluator`  — telemetry → metric numbers (read-only)
* :mod:`api.achievements.store`      — progress/unlock state (SQLite)
* :mod:`api.achievements.unlocks`    — snapshot → progress → diff → events
* :mod:`api.achievements.routes`     — Flask transport

Callers should use :func:`on_turn_saved` (fire-and-forget from the turn
persistence seam) and :func:`status`. Both no-op while the flag is off, so
every call site can be unconditional and the feature is removable by deleting
this package plus its flag row.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from api.achievements import catalog, store, unlocks  # noqa: F401


def is_enabled() -> bool:
    """Whether achievements are switched on for this install."""
    try:
        from api.experimental import is_enabled as flag_enabled

        return flag_enabled("achievements")
    except Exception:
        return False


def on_turn_saved(session_id: Any = None, result: Any = None) -> None:
    """Turn-persistence seam. Fire-and-forget; never blocks reply delivery.

    Called from ``api.chat_turn_persist`` once an assistant bubble is saved.
    No-ops when the flag is off, the run is a duplicate inside the throttle
    window, or anything at all raises.
    """
    if not is_enabled():
        return
    unlocks.evaluate_async()


def evaluate(*, force: bool = True, **kwargs) -> Dict[str, Any]:
    """Synchronous full evaluation (CLI / tests / manual rescan)."""
    if not is_enabled():
        return {"skipped": "disabled", "unlocked": [], "progress_updates": 0}
    return unlocks.evaluate(force=force, **kwargs)


def status() -> Dict[str, Any]:
    """Catalog + progress payload for the UI. Empty when the flag is off."""
    if not is_enabled():
        return {"items": [], "unlocked": 0, "total": 0, "unseen": 0}
    return unlocks.status()


__all__ = [
    "catalog",
    "evaluate",
    "is_enabled",
    "on_turn_saved",
    "status",
    "store",
    "unlocks",
]