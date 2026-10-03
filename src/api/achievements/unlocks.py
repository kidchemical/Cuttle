"""Unlock orchestration: snapshot → progress → diff → events.

The only place that knows the sequence. Everything it touches is injected
(``snapshot_fn``, ``store`` module), so the flow is testable without SQLite
fixtures and the feature can be unwired by deleting one call site.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from api.achievements import catalog, evaluator, store

# Coalesce bursts of turn completions: one evaluation per window at most.
_MIN_INTERVAL_S = 5.0

_lock = threading.Lock()
_last_run_at: float = 0.0


def _now() -> float:
    return time.time()


def evaluate(
    *,
    snapshot_fn: Optional[Callable[[], Dict[str, float]]] = None,
    achievements_db=None,
    outcomes_db=None,
    auth_db=None,
    force: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Recompute progress and unlock anything newly crossed.

    Idempotent: re-running after an unlock never re-unlocks and never lowers
    progress. ``force`` bypasses the rate-limit window.
    """
    global _last_run_at
    stamp = _now() if now is None else float(now)
    if not force and (stamp - _last_run_at) < _MIN_INTERVAL_S:
        return {"skipped": "throttled", "unlocked": [], "progress_updates": 0}

    with _lock:
        _last_run_at = stamp
        snap = (snapshot_fn or (lambda: evaluator.snapshot(
            outcomes_db=outcomes_db, auth_db=auth_db)))()
        prior_state = store.load_state(achievements_db)
        progress_updates = 0
        unlocked: List[Dict[str, Any]] = []
        for ach in catalog.all_achievements():
            value = float(snap.get(ach.metric, 0.0) or 0.0)
            already = (prior_state.get(ach.id) or {}).get("unlocked_at") is not None
            result = store.record_progress(
                ach.id, value, ach.threshold, db_path=achievements_db
            )
            if result.get("changed"):
                progress_updates += 1
            if already or value < ach.threshold:
                continue
            detail = {
                "metric": ach.metric,
                "value": value,
                "threshold": ach.threshold,
                "observed_at": stamp,
            }
            if store.mark_unlocked(ach.id, detail, db_path=achievements_db):
                unlocked.append(
                    {
                        "id": ach.id,
                        "title": ach.title,
                        "icon": ach.icon,
                        "rarity": ach.rarity,
                        "description": ach.description,
                        "detail": detail,
                    }
                )
        return {
            "skipped": None,
            "unlocked": unlocked,
            "progress_updates": progress_updates,
            "evaluated_at": stamp,
        }


def evaluate_async(**kwargs) -> None:
    """Fire-and-forget wrapper: never blocks or raises into the chat turn."""
    def _run():
        try:
            evaluate(**kwargs)
        except Exception as e:  # pragma: no cover - defensive
            print(f"[ACHIEVEMENTS] evaluation failed: {e}", flush=True)

    threading.Thread(target=_run, name="cuttle-achievements", daemon=True).start()


def unlock_count(achievements_db=None) -> int:
    state = store.load_state(achievements_db)
    return sum(1 for row in state.values() if row.get("unlocked_at") is not None)


def unseen_count(achievements_db=None) -> int:
    return len(store.pending_unlocks(catalog.catalog_payload(), db_path=achievements_db))


def status(achievements_db=None) -> Dict[str, Any]:
    """Full client payload: catalog + progress + unlock counts."""
    state = store.load_state(achievements_db)
    items = []
    for entry in catalog.catalog_payload():
        row = state.get(entry["id"], {})
        entry["progress"] = float(row.get("progress") or entry.get("progress") or 0.0)
        entry["unlocked"] = row.get("unlocked_at") is not None
        entry["unlocked_at"] = row.get("unlocked_at")
        entry["seen"] = row.get("seen_at") is not None
        if entry["threshold"]:
            entry["percent"] = round(
                min(100.0, entry["progress"] / float(entry["threshold"]) * 100.0), 2
            )
        items.append(entry)
    unlocked = sum(1 for i in items if i["unlocked"])
    pending = store.pending_unlocks(items, db_path=achievements_db)
    return {
        "items": items,
        "unlocked": unlocked,
        "total": len(items),
        "unseen": len(pending),
        "rarities": catalog.RARITY_META,
        "summary": catalog.summary(),
    }