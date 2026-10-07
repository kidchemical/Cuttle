"""Optional watch-card bars and parent Tasks gizmo for a sub-agent batch."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.subagents.types import BatchRecord, STATUS_DONE, STATUS_FAILED, STATUS_RUNNING


def _percent(batch: BatchRecord) -> int:
    kids = batch.children or []
    if not kids:
        return 0
    done = sum(1 for c in kids if c.status in (STATUS_DONE, STATUS_FAILED, "cancelled"))
    return int(round(100.0 * done / len(kids)))


def write_watch(batch: BatchRecord) -> Optional[str]:
    if not batch.watch_id:
        return None
    try:
        from api.job_watch import write_status
    except Exception:
        return None
    kids = batch.children or []
    n = len(kids)
    done = sum(1 for c in kids if c.status == STATUS_DONE)
    failed = sum(1 for c in kids if c.status == STATUS_FAILED)
    running = sum(1 for c in kids if c.status == STATUS_RUNNING)
    cancelled = sum(1 for c in kids if c.status == "cancelled")
    terminal = batch.status not in ("running",)
    state = "running"
    if terminal:
        state = "failed" if failed and not done else "done"
        if batch.status == "cancelled":
            state = "failed"
    bars: List[Dict[str, Any]] = [
        {
            "id": "overall",
            "label": "Overall",
            "percent": _percent(batch),
            "kind": "primary",
            "detail": f"{done}/{n} done",
        }
    ]
    for child in kids:
        pct = 100 if child.status == STATUS_DONE else (
            0 if child.status in ("pending", "cancelled", STATUS_FAILED) else 50
        )
        if child.status == STATUS_FAILED:
            pct = 100
        bars.append(
            {
                "id": child.handle(),
                "label": child.label or child.handle(),
                "percent": pct,
                "kind": "worker",
                "detail": child.status,
            }
        )
    label = f"Subagents — {done}/{n} done"
    if running:
        label += f", {running} running"
    extra = {
        "batch_id": batch.id,
        "done": done,
        "failed": failed,
        "cancelled": cancelled,
    }
    try:
        write_status(
            batch.watch_id,
            state=state,
            percent=_percent(batch),
            label=label,
            bars=bars,
            extra=extra,
        )
    except Exception:
        return None
    return batch.watch_id


def ensure_parent_tasks(db, batch: BatchRecord) -> Optional[str]:
    if not batch.widget_id:
        return None
    items = []
    for child in batch.children:
        items.append(
            {
                "id": child.id[:12],
                "text": f"{child.label} ({child.handle()})",
                "done": child.status == STATUS_DONE,
                "children": [],
            }
        )
    try:
        from api.gizmos import tasks
        from core.agent_cli_env import operation_actor
        actor = operation_actor(source="subagents", session_id=batch.parent_session_id,
                                user_id=batch.user_id)
        actor["batch_id"] = batch.id
        existing = db.get_chat_widget(batch.widget_id, user_id=batch.user_id)
        if existing:
            tasks.patch(batch.widget_id, {"items": items}, user_id=batch.user_id,
                        session_id=batch.parent_session_id, db=db, actor=actor)
        elif items:
            tasks.create(gizmo_id=batch.widget_id, title="Subagents", items=items,
                         session_id=batch.parent_session_id, user_id=batch.user_id,
                         description="High-level sub-agent objectives for this chat.", db=db, actor=actor)
    except Exception:
        return None
    return batch.widget_id


def patch_parent_tasks(db, batch: BatchRecord) -> None:
    ensure_parent_tasks(db, batch)
