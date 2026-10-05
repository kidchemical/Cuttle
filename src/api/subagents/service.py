"""Spawn / wait / cancel / message for Cuttle sub-agent child chats."""

from __future__ import annotations

import json
import os
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
    STATUS_PENDING,
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


def _reconcile(db, batch: Optional[BatchRecord] = None) -> None:
    """Flip children whose owning process died to terminal, then re-read.

    A child turn lives inside the process that spawned it. When that host dies
    the row is left saying ``running`` with no agent behind it, so the pane
    spins forever and the batch never finalizes. Every observation point calls
    this; ``batch`` short-circuits when nothing is outstanding.
    """
    if batch is not None and all(c.status in TERMINAL_CHILD for c in batch.children):
        return
    try:
        store.reconcile_orphan_children(db)
    except Exception:
        pass


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

    timed_out = False
    if collect_n == COLLECT_SERIAL:
        if wait:
            _run_serial(db, batch, created, runner=runner, project_path=project_path)
            waited = wait_batch(batch.id, timeout=timeout, db=db, runner=None, start=False)
            timed_out = bool(waited.get("timed_out"))
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
            waited = wait_batch(batch.id, timeout=timeout, db=db, runner=runner, start=False)
            timed_out = bool(waited.get("timed_out"))

    batch = _refresh(db, batch.id)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    payload["timed_out"] = timed_out
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
    advance: bool = True,
) -> Dict[str, Any]:
    """Poll a batch round to terminal-or-timeout, reporting ``timed_out``.

    With ``start=True`` pending children are kicked in this process; serial
    batches additionally advance one pending child per poll (``advance=True``,
    the default that preserves in-process supervision). Observation-only
    callers pass ``start=False, advance=False``: rows are read and finalized
    but never started, so a ``wait`` on stale pending rows cannot revive
    execution nobody owns.
    """
    db = _open_db(db)
    deadline = time.time() + max(1.0, float(timeout))
    batch = _refresh(db, batch_id)
    if start:
        _kick_pending(db, batch, runner=runner)
    while time.time() < deadline:
        batch = _refresh(db, batch_id)
        _reconcile(db, batch)
        batch = _refresh(db, batch_id)
        if advance and batch.collect == COLLECT_SERIAL:
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
    # This process runs the turn below, so record it as the owner now: a host
    # death between here and the turn start must not leave an ownerless
    # pending row nobody can reconcile.
    store.update_child(
        db, child.id, status="pending", result="", error="", owner_pid=os.getpid()
    )
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
        _reconcile(db, batch)
        batch = _refresh(db, batch_id)
        _finalize_if_complete(db, batch)
        return {"ok": True, **batch.public()}
    if parent_session_id is None:
        return {"ok": False, "error": "batch or parent required"}
    for batch in store.list_batches_for_parent(db, int(parent_session_id)):
        _reconcile(db, batch)
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
    card = _launcher_fn()
    for batch in bound:
        for child in batch.children:
            pub = card(child)
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
        card = _launcher_fn()
        launchers: List[Dict[str, Any]] = []
        for batch in store.list_live_for_parent(db, int(sid)):
            for child in batch.children:
                launchers.append(card(child))
        return launchers
    except Exception:
        return []


def _launcher_fn():
    """Fleet cards when ``subagent_fleet_cards`` is on, else slim launchers."""
    from api.subagents import fleet

    if fleet.fleet_enabled():
        return fleet.fleet_entry
    return lambda child: public_launcher(child.public())


def hydrate_parent_fleet(db, messages: List[Dict[str, Any]]) -> None:
    """Refresh fleet cards on saved parent bubbles from the child rows.

    Metadata holds the launcher snapshot taken when the reply was saved;
    the child row is the source of truth, so reload shows the current
    outcome (a conversational follow-up, a later cancel, a lost host).
    """
    from api.subagents import fleet

    if not messages or not fleet.fleet_enabled():
        return
    targets = []
    for msg in messages:
        meta = msg.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (TypeError, ValueError):
                continue
        if msg.get("role") == "assistant" and isinstance(meta, dict) and isinstance(meta.get("subagents"), list):
            targets.append((msg, meta))
    if not targets:
        return
    children = {}
    for _, meta in targets:
        for entry in meta["subagents"]:
            cid = str((entry or {}).get("id") or "") if isinstance(entry, dict) else ""
            if cid and cid not in children:
                children[cid] = store.get_child(db, cid)
    if any(c and c.status not in TERMINAL_CHILD and c.owner_pid for c in children.values()):
        _reconcile(db)
        children = {cid: store.get_child(db, cid) for cid in children}
    for msg, meta in targets:
        meta = dict(meta)
        meta["subagents"] = [
            fleet.fleet_entry(children[str(e.get("id"))])
            if isinstance(e, dict) and children.get(str(e.get("id") or "")) else e
            for e in meta["subagents"]
        ]
        msg["metadata"] = meta


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
    # Repair before reporting: a non-terminal row whose host died must not be
    # reported as generating, or the pane spins forever with no agent behind it.
    # Gated on an actual outstanding child so ordinary live-status polls for
    # non-sub-agent chats stay a single indexed read.
    if child.status in (STATUS_PENDING, STATUS_RUNNING) and child.owner_pid:
        _reconcile(db)
        child = store.get_child_by_session(db, int(sid)) or child
    generating = child.status in ("pending", STATUS_RUNNING)
    report_url = (
        f"/query_log.html?id={child.query_id}" if child.query_id else None
    )
    return {
        "generating": generating,
        "status": f"{child.label or 'Subagent'} — {child.status}",
        "subagent": child.public(),
        # Carried so the child pane's query-log button gets a real id while the
        # sub-agent is still working, not only after its reply is persisted.
        "query_id": child.query_id or None,
        "report_url": report_url,
    }
