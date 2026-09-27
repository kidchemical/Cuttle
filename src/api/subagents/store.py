"""SQLite persistence for sub-agent batches (cuttle_auth.db)."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from api.subagents.types import (
    BatchRecord,
    ChildRecord,
    ChildSpec,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCELLED,
    TERMINAL_BATCH,
    TERMINAL_CHILD,
)


def _row_child(row: Any) -> ChildRecord:
    d = dict(row)
    return ChildRecord(
        id=str(d.get("id") or ""),
        batch_id=str(d.get("batch_id") or ""),
        session_id=int(d["session_id"]),
        sort_index=int(d.get("sort_index") or 0),
        label=str(d.get("label") or ""),
        agent=str(d.get("agent") or "cursor"),
        model=str(d.get("model") or ""),
        effort=str(d.get("effort") or ""),
        prompt=str(d.get("prompt") or ""),
        status=str(d.get("status") or STATUS_PENDING),
        result=str(d.get("result") or ""),
        error=str(d.get("error") or ""),
        pid=d.get("pid"),
        profile_id=str(d.get("profile_id") or ""),
        display_name=str(d.get("display_name") or d.get("label") or ""),
        avatar=str(d.get("avatar") or ""),
    )


def _load_children(db, batch_id: str) -> List[ChildRecord]:
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM subagent_children
        WHERE batch_id = ?
        ORDER BY sort_index ASC, session_id ASC
        """,
        (batch_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_child(r) for r in rows]


def _row_batch(db, row: Any) -> BatchRecord:
    d = dict(row)
    bid = str(d.get("id") or "")
    meta = d.get("metadata")
    if isinstance(meta, str) and meta:
        try:
            json.loads(meta)
        except Exception:
            pass
    return BatchRecord(
        id=bid,
        parent_session_id=int(d["parent_session_id"]),
        user_id=int(d["user_id"]),
        collect=str(d.get("collect") or "all"),
        lifetime=str(d.get("lifetime") or "one_shot"),
        status=str(d.get("status") or STATUS_RUNNING),
        watch_id=str(d.get("watch_id") or ""),
        widget_id=str(d.get("widget_id") or ""),
        attach_message_id=d.get("attach_message_id"),
        children=_load_children(db, bid) if bid else [],
    )


def new_id() -> str:
    return uuid.uuid4().hex


def insert_batch(
    db,
    *,
    parent_session_id: int,
    user_id: int,
    collect: str,
    lifetime: str,
    watch_id: str = "",
    widget_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> BatchRecord:
    bid = new_id()
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO subagent_batches (
            id, parent_session_id, user_id, collect, lifetime, status,
            watch_id, widget_id, metadata
        )
        VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
        """,
        (
            bid,
            int(parent_session_id),
            int(user_id),
            collect,
            lifetime,
            watch_id or None,
            widget_id or None,
            json.dumps(metadata) if metadata else None,
        ),
    )
    conn.commit()
    conn.close()
    return get_batch(db, bid)


def insert_child(
    db,
    *,
    batch_id: str,
    session_id: int,
    sort_index: int,
    spec: ChildSpec,
    prompt: str,
) -> ChildRecord:
    cid = new_id()
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO subagent_children (
            id, batch_id, session_id, sort_index, label, agent, model, effort,
            prompt, status, profile_id, display_name, avatar
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            cid,
            batch_id,
            int(session_id),
            int(sort_index),
            spec.title,
            spec.agent,
            spec.model or None,
            spec.effort or None,
            prompt,
            spec.profile_id or None,
            (spec.display_name or spec.title) or None,
            spec.avatar or None,
        ),
    )
    conn.commit()
    conn.close()
    return ChildRecord(
        id=cid,
        batch_id=batch_id,
        session_id=int(session_id),
        sort_index=int(sort_index),
        label=spec.title,
        agent=spec.agent,
        model=spec.model,
        effort=spec.effort,
        prompt=prompt,
        status=STATUS_PENDING,
        profile_id=spec.profile_id,
        display_name=spec.display_name or spec.title,
        avatar=spec.avatar,
    )


def get_batch(db, batch_id: str) -> Optional[BatchRecord]:
    if not batch_id:
        return None
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM subagent_batches WHERE id = ?", (str(batch_id),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return _row_batch(db, row)


def list_batches_for_parent(db, parent_session_id: int) -> List[BatchRecord]:
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM subagent_batches
        WHERE parent_session_id = ?
        ORDER BY created_at DESC
        """,
        (int(parent_session_id),),
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_batch(db, r) for r in rows]


def list_running_for_parent(db, parent_session_id: int) -> List[BatchRecord]:
    return [
        b
        for b in list_batches_for_parent(db, parent_session_id)
        if b.status not in TERMINAL_BATCH
    ]


def list_live_for_parent(db, parent_session_id: int) -> List[BatchRecord]:
    """Batches that should paint extra orbs on the parent typing indicator.

    Running batches always. Unattached batches (even after ``--wait`` finishes)
    stay until the parent assistant bubble binds chips, so orbs last through
    synthesis instead of vanishing the moment children complete.
    """
    out: List[BatchRecord] = []
    for batch in list_batches_for_parent(db, parent_session_id):
        if batch.status not in TERMINAL_BATCH:
            out.append(batch)
        elif not batch.attach_message_id:
            out.append(batch)
    return out


def get_child(db, child_id: str) -> Optional[ChildRecord]:
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM subagent_children WHERE id = ?", (str(child_id),))
    row = cur.fetchone()
    conn.close()
    return _row_child(row) if row else None


def get_child_by_session(db, session_id: int) -> Optional[ChildRecord]:
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM subagent_children
        WHERE session_id = ?
        ORDER BY rowid DESC LIMIT 1
        """,
        (int(session_id),),
    )
    row = cur.fetchone()
    conn.close()
    return _row_child(row) if row else None


def update_child(
    db,
    child_id: str,
    *,
    status: Optional[str] = None,
    result: Optional[str] = None,
    error: Optional[str] = None,
    pid: Optional[int] = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    sets = []
    args: List[Any] = []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if result is not None:
        sets.append("result = ?")
        args.append(result)
    if error is not None:
        sets.append("error = ?")
        args.append(error)
    if pid is not None:
        sets.append("pid = ?")
        args.append(int(pid))
    if started:
        sets.append("started_at = CURRENT_TIMESTAMP")
    if finished:
        sets.append("finished_at = CURRENT_TIMESTAMP")
    if not sets:
        return
    args.append(child_id)
    conn = db._get_connection()
    cur = conn.cursor()
    if status is not None and status != STATUS_PENDING:
        cur.execute(
            "SELECT status FROM subagent_children WHERE id = ?",
            (child_id,),
        )
        row = cur.fetchone()
        current = str(row["status"] or "") if row else ""
        if current == STATUS_CANCELLED:
            conn.close()
            return
        if status == STATUS_RUNNING and current in TERMINAL_CHILD:
            conn.close()
            return
    cur.execute(
        f"UPDATE subagent_children SET {', '.join(sets)} WHERE id = ?",
        tuple(args),
    )
    conn.commit()
    conn.close()


def update_batch(
    db,
    batch_id: str,
    *,
    status: Optional[str] = None,
    attach_message_id: Optional[int] = None,
    finished: bool = False,
    watch_id: Optional[str] = None,
    widget_id: Optional[str] = None,
) -> None:
    sets = []
    args: List[Any] = []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if attach_message_id is not None:
        sets.append("attach_message_id = ?")
        args.append(int(attach_message_id))
    if watch_id is not None:
        sets.append("watch_id = ?")
        args.append(watch_id)
    if widget_id is not None:
        sets.append("widget_id = ?")
        args.append(widget_id)
    if finished:
        sets.append("finished_at = CURRENT_TIMESTAMP")
    if not sets:
        return
    args.append(batch_id)
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE subagent_batches SET {', '.join(sets)} WHERE id = ?",
        tuple(args),
    )
    conn.commit()
    conn.close()


def latest_assistant_text(db, session_id: int) -> str:
    messages = db.get_messages(int(session_id), limit=40) or []
    for msg in reversed(messages):
        if str(msg.get("role") or "") == "assistant":
            return str(msg.get("content") or "")
    return ""


def bind_unattached_batches(db, parent_session_id: int, message_id: int) -> List[BatchRecord]:
    """Attach running/recent batches to the first parent assistant bubble after spawn."""
    bound: List[BatchRecord] = []
    for batch in list_batches_for_parent(db, parent_session_id):
        if batch.attach_message_id:
            continue
        update_batch(db, batch.id, attach_message_id=int(message_id))
        batch.attach_message_id = int(message_id)
        bound.append(batch)
    return bound
