"""Optional watch-card bars and parent Tasks gizmo for a sub-agent batch."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.subagents.types import (
    BatchRecord,
    ChildRecord,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
)


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


def _owner_for(child: ChildRecord) -> Dict[str, Any]:
    """The Tasks owner of a child chat: its handle, harness and display name."""
    owner: Dict[str, Any] = {
        "kind": "chat",
        "id": child.handle(),
        "label": (child.display_name or child.label or child.handle()).strip()[:60],
    }
    agent = (child.agent or "").strip().lower()[:40]
    if agent:
        owner["agent"] = agent
    return owner


def _list_payload(db, gizmo_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    """The owner's Tasks list, or None when it does not exist (or is not theirs)."""
    from api.gizmos import tasks
    from api.gizmos.service import GizmoError

    try:
        return tasks.get(gizmo_id, user_id=user_id, db=db)
    except GizmoError:
        return None


def _write_list(db, gizmo_id: str, ops: Dict[str, Any], *, user_id: int, session_id,
                batch_id: str) -> None:
    from api.gizmos import tasks
    from core.agent_cli_env import operation_actor

    actor = operation_actor(source="subagents", session_id=session_id, user_id=user_id)
    actor["batch_id"] = batch_id
    tasks.patch(gizmo_id, ops, user_id=user_id, session_id=session_id, db=db, actor=actor)


def preflight_task_refs(specs, *, user_id: int, db) -> None:
    """Every ``task`` names an existing, not-yet-done item of one of the user's lists.

    Runs before any child chat exists. Raises ``ValueError`` (the caller maps it
    to its own error type).
    """
    from api.gizmos import tasks_model as model
    from api.subagents.spec import parse_task_ref

    for index, spec in enumerate(specs, start=1):
        if not spec.task:
            continue
        list_id, item_id = parse_task_ref(spec.task)
        row = _list_payload(db, list_id, user_id)
        if row is None:
            raise ValueError(f"child {index} ({spec.title}): no Tasks list {list_id!r} is yours")
        items = model.normalize_tasks_payload(row.get("payload"))["items"]
        node = model._find_item(items, item_id)
        if node is None:
            raise ValueError(f"child {index} ({spec.title}): Tasks list {list_id!r} has no item {item_id!r}")
        if node.get("done"):
            raise ValueError(f"child {index} ({spec.title}): item {item_id!r} is already done")


def assign_child_task(db, child: ChildRecord, batch: BatchRecord) -> None:
    """Explicit assignment at spawn: the child owns the item and it is in progress.

    A previous holder of the item is replaced, and its later status cannot
    move the item (see ``settle_child_task``). Best effort, like the batch list.
    """
    if not child.task_gizmo_id or not child.task_item_id:
        return
    try:
        _write_list(
            db, child.task_gizmo_id,
            {"set_owner": {child.task_item_id: _owner_for(child)},
             "set_in_progress": [child.task_item_id]},
            user_id=batch.user_id, session_id=batch.parent_session_id, batch_id=batch.id,
        )
    except Exception:
        return


def settle_child_task(db, child_id: str, status: str) -> None:
    """Follow a child's status onto the item it was assigned to.

    done → checked off; failed / cancelled / lost → no longer in progress (the
    owner stays and the item stays open); running → in progress again (a new
    round). Only the child that currently owns the item may move it, so an old
    child cannot finish an item that was reassigned. Best effort.
    """
    from api.subagents import store

    child = store.get_child(db, child_id)
    if child is None or not child.task_gizmo_id or not child.task_item_id:
        return
    if status == STATUS_DONE:
        want = "done"
    elif status in (STATUS_FAILED, STATUS_CANCELLED):
        want = "release"
    elif status == STATUS_RUNNING:
        want = "working"
    else:
        return
    batch = store.get_batch(db, child.batch_id)
    if batch is None:
        return
    from api.gizmos import tasks_model as model
    from api.gizmos.service import GizmoError

    for _ in range(3):  # re-read when another writer moved the list's revision
        row = _list_payload(db, child.task_gizmo_id, batch.user_id)
        if row is None:
            return
        node = model._find_item(model.normalize_tasks_payload(row.get("payload"))["items"],
                                child.task_item_id)
        if node is None:
            return
        owner = node.get("owner") or {}
        if owner.get("kind") != "chat" or owner.get("id") != child.handle():
            return  # reassigned (or released): not this child's to move
        if want == "done":
            if node.get("done"):
                return
            ops: Dict[str, Any] = {"set_done": [child.task_item_id]}
        elif want == "release":
            if not node.get("in_progress"):
                return
            ops = {"clear_in_progress": [child.task_item_id]}
        else:
            if node.get("done") or node.get("in_progress"):
                return
            ops = {"set_in_progress": [child.task_item_id]}
        ops["expected_revision"] = row.get("revision")
        try:
            _write_list(db, child.task_gizmo_id, ops, user_id=batch.user_id,
                        session_id=batch.parent_session_id, batch_id=batch.id)
            return
        except GizmoError as exc:
            if exc.code != "conflict":
                return


def ensure_parent_tasks(db, batch: BatchRecord) -> Optional[str]:
    """Mirror the batch's children into its Tasks list, incrementally.

    Only the children's own items are touched (matched by id). Owners and the
    in-progress flag follow the child; items the user added are left alone.
    """
    if not batch.widget_id:
        return None
    try:
        from api.gizmos import tasks
        from api.gizmos import tasks_model as model
        from core.agent_cli_env import operation_actor
        actor = operation_actor(source="subagents", session_id=batch.parent_session_id,
                                user_id=batch.user_id)
        actor["batch_id"] = batch.id
        wanted = {}
        for child in batch.children:
            done = child.status == STATUS_DONE
            wanted[child.id[:12]] = {
                "text": f"{child.label} ({child.handle()})",
                "done": done,
                "owner": _owner_for(child),
                "in_progress": (not done) and child.status in (STATUS_PENDING, STATUS_RUNNING),
            }
        existing = db.get_chat_widget(batch.widget_id, user_id=batch.user_id)
        if existing:
            current = {n["id"]: n for n in model.normalize_tasks_payload(existing.get("payload"))["items"]}
            ops: Dict[str, Any] = {"add": [], "set_done": [], "set_undone": [], "set_text": {},
                                   "set_owner": {}, "set_in_progress": [], "clear_in_progress": []}
            for iid, want in wanted.items():
                node = current.get(iid)
                if node is None:
                    ops["add"].append({"item": {"id": iid, **want}})
                    continue
                if node.get("text") != want["text"]:
                    ops["set_text"][iid] = want["text"]
                if bool(node.get("done")) != want["done"]:
                    ops["set_done" if want["done"] else "set_undone"].append(iid)
                if node.get("owner") != want["owner"]:
                    ops["set_owner"][iid] = want["owner"]
                if not want["done"] and bool(node.get("in_progress")) != want["in_progress"]:
                    ops["set_in_progress" if want["in_progress"] else "clear_in_progress"].append(iid)
            ops = {key: value for key, value in ops.items() if value}
            if ops:
                tasks.patch(batch.widget_id, ops, user_id=batch.user_id,
                            session_id=batch.parent_session_id, db=db, actor=actor)
        elif wanted:
            items = [{"id": iid, **want} for iid, want in wanted.items()]
            tasks.create(gizmo_id=batch.widget_id, title="Subagents", items=items,
                         session_id=batch.parent_session_id, user_id=batch.user_id,
                         description="High-level sub-agent objectives for this chat.", db=db, actor=actor)
    except Exception:
        return None
    return batch.widget_id


def patch_parent_tasks(db, batch: BatchRecord) -> None:
    ensure_parent_tasks(db, batch)
