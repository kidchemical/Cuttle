"""Spawn / wait / cancel / message for Cuttle sub-agent child chats."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import api.subagents.extras as extras
import api.subagents.store as store
from api.subagents.profiles import apply_profile
from api.subagents.spec import apply_router, parse_children, public_launcher
from api.subagents.turns import TurnRunner, run_child_turn, run_child_turn_async
from api.subagents.types import (
    COLLECT_ALL,
    COLLECT_FIRST,
    COLLECT_SERIAL,
    DEFAULT_POLL_SEC,
    DEFAULT_TIMEOUT_SEC,
    LIFETIME_CONVERSATIONAL,
    MAX_CHILDREN,
    MAX_DEPTH,
    STATUS_CANCELLED,
    STATUS_CLOSED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    TERMINAL_BATCH,
    TERMINAL_CHILD,
    BatchRecord,
    ChildSpec,
    chat_handle,
    normalize_collect,
    normalize_lifetime,
)


class SubagentError(ValueError):
    pass


def _open_db(db=None):
    if db is not None:
        return db
    from api.auth_db import get_auth_db

    return get_auth_db()


def _bare_sid(session_id: Any) -> Optional[int]:
    try:
        from api.cuttle_ui_capabilities import numeric_chat_session_id

        return numeric_chat_session_id(session_id)
    except Exception:
        try:
            return int(str(session_id).replace("db_session_", "").split("-")[0])
        except (TypeError, ValueError):
            return None


def _parent_row(db, parent_session_id: int) -> Dict[str, Any]:
    row = db.get_chat_session_by_id(int(parent_session_id))
    if not row:
        raise SubagentError(f"parent session {parent_session_id} not found")
    return row


def _refresh(db, batch_id: str) -> BatchRecord:
    batch = store.get_batch(db, batch_id)
    if not batch:
        raise SubagentError(f"batch {batch_id} not found")
    return batch


def _maybe_extras(db, batch: BatchRecord) -> None:
    extras.write_watch(batch)
    extras.patch_parent_tasks(db, batch)


def _finalize_if_complete(db, batch: BatchRecord) -> BatchRecord:
    batch = _refresh(db, batch.id)
    if batch.status in TERMINAL_BATCH:
        return batch
    kids = batch.children
    if not kids:
        return batch
    if batch.collect == COLLECT_FIRST:
        winner = next((c for c in kids if c.status == STATUS_DONE), None)
        if winner:
            for c in kids:
                if c.id != winner.id and c.status not in TERMINAL_CHILD:
                    _cancel_child(db, c.session_id)
            store.update_batch(db, batch.id, status=STATUS_DONE, finished=True)
            return _refresh(db, batch.id)
        if all(c.status in TERMINAL_CHILD for c in kids):
            failed = all(c.status != STATUS_DONE for c in kids)
            store.update_batch(
                db,
                batch.id,
                status=STATUS_FAILED if failed else STATUS_DONE,
                finished=True,
            )
            return _refresh(db, batch.id)
        return batch
    if all(c.status in TERMINAL_CHILD for c in kids):
        failed = any(c.status == STATUS_FAILED for c in kids) and not any(
            c.status == STATUS_DONE for c in kids
        )
        cancelled = all(c.status == STATUS_CANCELLED for c in kids)
        status = STATUS_CANCELLED if cancelled else (STATUS_FAILED if failed else STATUS_DONE)
        if batch.lifetime == LIFETIME_CONVERSATIONAL and status == STATUS_DONE:
            # One round finished; keep the batch open for follow-ups.
            return batch
        store.update_batch(db, batch.id, status=status, finished=True)
        return _refresh(db, batch.id)
    return batch


def _cancel_child(db, session_id: int) -> None:
    child = store.get_child_by_session(db, int(session_id))
    if child and child.status not in TERMINAL_CHILD:
        store.update_child(
            db, child.id, status=STATUS_CANCELLED, finished=True
        )
    try:
        from api.chat_run_registry import cancel_session_runs

        cancel_session_runs(session_id)
    except Exception:
        pass
    if child and child.pid:
        _kill_pid(int(child.pid))


def _kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    try:
        import os
        import signal

        os.kill(pid, signal.SIGTERM)
    except Exception:
        try:
            import subprocess

            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=8,
                check=False,
            )
        except Exception:
            pass


def spawn(
    *,
    parent_session_id: int,
    children: List[Any],
    collect: str = COLLECT_ALL,
    lifetime: str = "one_shot",
    wait: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    route: bool = False,
    watch: bool = False,
    tasks: bool = False,
    runner: Optional[TurnRunner] = None,
    db=None,
) -> Dict[str, Any]:
    db = _open_db(db)
    parent = _parent_row(db, int(parent_session_id))
    user_id = int(parent["user_id"])
    depth = db.session_nesting_depth(int(parent_session_id))
    if depth >= MAX_DEPTH:
        raise SubagentError(
            f"sub-agent nesting is capped at {MAX_DEPTH} (this chat is already depth {depth})"
        )
    specs = parse_children(children)
    if not specs:
        raise SubagentError("spawn requires at least one child")
    if len(specs) > MAX_CHILDREN:
        raise SubagentError(f"at most {MAX_CHILDREN} sub-agents per batch")
    specs = [apply_profile(s, db=db, user_id=user_id) for s in specs]
    collect_n = normalize_collect(collect)
    lifetime_n = normalize_lifetime(lifetime)
    project_path = str(parent.get("project_path") or "")
    project_id = parent.get("project_id")
    project_name = parent.get("project_name") or ""

    watch_id = ""
    widget_id = ""
    batch_stub = store.new_id()
    if watch:
        watch_id = f"subagents-{batch_stub[:12]}"
    if tasks:
        widget_id = f"subagents-{batch_stub[:12]}"

    batch = store.insert_batch(
        db,
        parent_session_id=int(parent_session_id),
        user_id=user_id,
        collect=collect_n,
        lifetime=lifetime_n,
        watch_id=watch_id,
        widget_id=widget_id,
        metadata={"route": bool(route)},
    )
    # Re-key watch/widget to the real batch id when we generated from a stub.
    if watch_id:
        watch_id = f"subagents-{batch.id[:12]}"
        store.update_batch(db, batch.id, watch_id=watch_id)
        batch.watch_id = watch_id
    if widget_id:
        widget_id = f"subagents-{batch.id[:12]}"
        store.update_batch(db, batch.id, widget_id=widget_id)
        batch.widget_id = widget_id

    created: List[Dict[str, Any]] = []
    for i, spec in enumerate(specs):
        if route or spec.route:
            spec = apply_router(
                spec, session_id=parent_session_id, project_path=project_path
            )
        sid = db.create_subagent_chat_session(
            user_id,
            parent_session_id=int(parent_session_id),
            session_name=spec.title,
            project_id=project_id,
            project_name=project_name,
            project_path=project_path,
            display_name=spec.display_name or spec.title,
            avatar=spec.avatar,
            agent_profile_id=spec.profile_id or None,
        )
        prompt = spec.message
        child = store.insert_child(
            db,
            batch_id=batch.id,
            session_id=sid,
            sort_index=i,
            spec=spec,
            prompt=prompt,
        )
        created.append({"spec": spec, "child": child})

    batch = _refresh(db, batch.id)
    extras.ensure_parent_tasks(db, batch)
    extras.write_watch(batch)

    if collect_n == COLLECT_SERIAL:
        if wait:
            _run_serial(db, batch, created, runner=runner, project_path=project_path)
            wait_batch(batch.id, timeout=timeout, db=db, runner=None, start=False)
        else:
            # Start only the first; wait/message will continue.
            spec0, child0 = created[0]["spec"], created[0]["child"]
            run_child_turn_async(
                db, child0, spec0, project_path=project_path, runner=runner
            )
    else:
        for item in created:
            run_child_turn_async(
                db,
                item["child"],
                item["spec"],
                project_path=project_path,
                runner=runner,
            )
        if wait:
            wait_batch(batch.id, timeout=timeout, db=db, runner=runner, start=False)

    batch = _refresh(db, batch.id)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    return payload


def _run_serial(
    db,
    batch: BatchRecord,
    created: List[Dict[str, Any]],
    *,
    runner: Optional[TurnRunner],
    project_path: str,
) -> None:
    for item in created:
        child = store.get_child(db, item["child"].id)
        if not child or child.status == STATUS_CANCELLED:
            break
        run_child_turn(
            db,
            child,
            item["spec"],
            project_path=project_path,
            runner=runner,
        )
        batch = _finalize_if_complete(db, batch)
        _maybe_extras(db, batch)
        if batch.collect == COLLECT_FIRST and batch.status in TERMINAL_BATCH:
            break


def wait_batch(
    batch_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    poll: float = DEFAULT_POLL_SEC,
    db=None,
    runner: Optional[TurnRunner] = None,
    start: bool = False,
) -> Dict[str, Any]:
    db = _open_db(db)
    deadline = time.time() + max(1.0, float(timeout))
    batch = _refresh(db, batch_id)
    if start:
        _kick_pending(db, batch, runner=runner)
    while time.time() < deadline:
        batch = _refresh(db, batch_id)
        if batch.collect == COLLECT_SERIAL:
            _kick_next_serial(db, batch, runner=runner)
        batch = _finalize_if_complete(db, batch)
        _maybe_extras(db, batch)
        if batch.collect == COLLECT_FIRST:
            if any(c.status == STATUS_DONE for c in batch.children):
                batch = _finalize_if_complete(db, batch)
                _maybe_extras(db, batch)
                break
        if batch.lifetime == LIFETIME_CONVERSATIONAL:
            if all(c.status in TERMINAL_CHILD for c in batch.children):
                break
        elif batch.status in TERMINAL_BATCH:
            break
        elif all(c.status in TERMINAL_CHILD for c in batch.children):
            break
        time.sleep(max(0.05, float(poll)))
    batch = _finalize_if_complete(db, batch)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    payload["timed_out"] = batch.status not in TERMINAL_BATCH and not (
        batch.lifetime == LIFETIME_CONVERSATIONAL
        and all(c.status in TERMINAL_CHILD for c in batch.children)
    )
    return payload


def _kick_pending(db, batch: BatchRecord, *, runner: Optional[TurnRunner]) -> None:
    parent = _parent_row(db, batch.parent_session_id)
    project_path = str(parent.get("project_path") or "")
    if batch.collect == COLLECT_SERIAL:
        _kick_next_serial(db, batch, runner=runner)
        return
    for child in batch.children:
        if child.status == "pending":
            spec = ChildSpec(
                title=child.label,
                message=child.prompt,
                agent=child.agent,
                model=child.model,
                effort=child.effort,
                profile_id=child.profile_id,
                display_name=child.display_name,
                avatar=child.avatar,
            )
            run_child_turn_async(
                db, child, spec, project_path=project_path, runner=runner
            )


def _kick_next_serial(db, batch: BatchRecord, *, runner: Optional[TurnRunner]) -> None:
    if any(c.status == STATUS_RUNNING for c in batch.children):
        return
    parent = _parent_row(db, batch.parent_session_id)
    project_path = str(parent.get("project_path") or "")
    for child in batch.children:
        if child.status == "pending":
            spec = ChildSpec(
                title=child.label,
                message=child.prompt,
                agent=child.agent,
                model=child.model,
                effort=child.effort,
                profile_id=child.profile_id,
                display_name=child.display_name,
                avatar=child.avatar,
            )
            run_child_turn_async(
                db, child, spec, project_path=project_path, runner=runner
            )
            return


def message_child(
    session_id: int,
    text: str,
    *,
    wait: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    runner: Optional[TurnRunner] = None,
    db=None,
) -> Dict[str, Any]:
    db = _open_db(db)
    child = store.get_child_by_session(db, int(session_id))
    if not child:
        raise SubagentError(f"session {session_id} is not a sub-agent chat")
    if child.status == STATUS_CANCELLED:
        raise SubagentError("that sub-agent was cancelled")
    store.update_child(db, child.id, status="pending", result="", error="")
    spec = ChildSpec(
        title=child.label,
        message=text,
        agent=child.agent,
        model=child.model,
        effort=child.effort,
        profile_id=child.profile_id,
        display_name=child.display_name,
        avatar=child.avatar,
    )
    parent = store.get_batch(db, child.batch_id)
    project_path = ""
    if parent:
        prow = db.get_chat_session_by_id(parent.parent_session_id) or {}
        project_path = str(prow.get("project_path") or "")
        if parent.status in TERMINAL_BATCH:
            store.update_batch(db, parent.id, status=STATUS_RUNNING)
    if wait:
        result = run_child_turn(
            db, child, spec, project_path=project_path, runner=runner
        )
        batch = _finalize_if_complete(db, _refresh(db, child.batch_id))
        _maybe_extras(db, batch)
        return {
            "ok": True,
            "child": store.get_child(db, child.id).public(),
            "batch": batch.public(),
            "response": (result or {}).get("response") or "",
        }
    run_child_turn_async(db, child, spec, project_path=project_path, runner=runner)
    return {
        "ok": True,
        "child": store.get_child(db, child.id).public(),
        "started": True,
    }


def cancel_batch(batch_id: str, *, db=None) -> Dict[str, Any]:
    db = _open_db(db)
    batch = _refresh(db, batch_id)
    for child in batch.children:
        if child.status not in TERMINAL_CHILD:
            _cancel_child(db, child.session_id)
    store.update_batch(db, batch.id, status=STATUS_CANCELLED, finished=True)
    batch = _refresh(db, batch.id)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    return payload


def cancel_session(session_id: int, *, db=None) -> Dict[str, Any]:
    """Cancel one child, or every running child of a parent chat."""
    db = _open_db(db)
    child = store.get_child_by_session(db, int(session_id))
    if child:
        _cancel_child(db, int(session_id))
        batch = _finalize_if_complete(db, _refresh(db, child.batch_id))
        _maybe_extras(db, batch)
        return {"ok": True, "cancelled": "child", "batch": batch.public()}
    batches = store.list_running_for_parent(db, int(session_id))
    out = []
    for batch in batches:
        out.append(cancel_batch(batch.id, db=db))
    return {"ok": True, "cancelled": "parent", "batches": out}


def close_batch(batch_id: str, *, db=None) -> Dict[str, Any]:
    db = _open_db(db)
    batch = _refresh(db, batch_id)
    store.update_batch(db, batch.id, status=STATUS_CLOSED, finished=True)
    batch = _refresh(db, batch.id)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    return payload


def status_payload(batch_id: Optional[str] = None, parent_session_id: Optional[int] = None, *, db=None) -> Dict[str, Any]:
    db = _open_db(db)
    if batch_id:
        batch = store.get_batch(db, batch_id)
        if not batch:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, **batch.public()}
    if parent_session_id is None:
        return {"ok": False, "error": "batch or parent required"}
    batches = [b.public() for b in store.list_batches_for_parent(db, int(parent_session_id))]
    return {
        "ok": True,
        "parent_session_id": int(parent_session_id),
        "parent_handle": chat_handle(int(parent_session_id)),
        "batches": batches,
    }


def attach_batches_to_assistant_meta(
    parent_session_id: int,
    meta: Optional[Dict[str, Any]],
    *,
    message_id: Optional[int] = None,
    db=None,
) -> Dict[str, Any]:
    """Merge launcher payloads onto the parent assistant bubble metadata."""
    db = _open_db(db)
    out = dict(meta or {})
    bound = []
    if message_id is not None:
        bound = store.bind_unattached_batches(db, int(parent_session_id), int(message_id))
    else:
        bound = [
            b
            for b in store.list_batches_for_parent(db, int(parent_session_id))
            if not b.attach_message_id
        ]
        # Don't persist attach without an id; still expose launchers this turn.
    launchers: List[Dict[str, Any]] = list(out.get("subagents") or [])
    seen = {str(x.get("handle") or x.get("session_id")) for x in launchers}
    for batch in bound:
        for child in batch.children:
            pub = public_launcher(child.public())
            key = str(pub.get("handle") or pub.get("session_id"))
            if key in seen:
                continue
            seen.add(key)
            launchers.append(pub)
        if batch.id and "subagent_batch_id" not in out:
            out["subagent_batch_id"] = batch.id
    if launchers:
        out["subagents"] = launchers
    return out


def on_session_cancelled(session_id: Any) -> None:
    sid = _bare_sid(session_id)
    if sid is None:
        return
    try:
        cancel_session(int(sid))
    except Exception:
        pass


def public_parent_subagents(session_id: Any, *, db=None) -> List[Dict[str, Any]]:
    sid = _bare_sid(session_id)
    if sid is None:
        return []
    try:
        db = _open_db(db)
        launchers: List[Dict[str, Any]] = []
        for batch in store.list_live_for_parent(db, int(sid)):
            for child in batch.children:
                launchers.append(public_launcher(child.public()))
        return launchers
    except Exception:
        return []


def child_live_status(session_id: Any, *, db=None) -> Optional[Dict[str, Any]]:
    """If this session is a sub-agent, return overlay flags for live-status."""
    sid = _bare_sid(session_id)
    if sid is None:
        return None
    try:
        db = _open_db(db)
        child = store.get_child_by_session(db, int(sid))
    except Exception:
        return None
    if not child:
        return None
    generating = child.status in ("pending", STATUS_RUNNING)
    return {
        "generating": generating,
        "status": f"{child.label or 'Subagent'} — {child.status}",
        "subagent": child.public(),
    }
