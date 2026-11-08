"""SQLite persistence for sub-agent batches (cuttle_auth.db)."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional

from api.subagents.types import (
    ORPHAN_ERROR,
    BatchRecord,
    ChildRecord,
    ChildSpec,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_CANCELLED,
    STATUS_FAILED,
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
        owner_pid=d.get("owner_pid"),
        query_id=str(d.get("query_id") or ""),
        profile_id=str(d.get("profile_id") or ""),
        display_name=str(d.get("display_name") or d.get("label") or ""),
        avatar=str(d.get("avatar") or ""),
        started_at=str(d.get("started_at") or ""),
        finished_at=str(d.get("finished_at") or ""),
        live_status=str(d.get("live_status") or ""),
        live_status_at=str(d.get("live_status_at") or ""),
        live_turn_id=str(d.get("live_turn_id") or ""),
        stop_requested_at=_float_or_none(d.get("stop_requested_at")),
        card_message_id=d.get("card_message_id"),
        card_pending=bool(d.get("card_pending")),
        card_turn_id=str(d.get("card_turn_id") or ""),
    )


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# Batches spawned under turn-anchored cards (api.chat_turn_id) carry this in
# their metadata. Older batches keep their old rule, so the unattached batches
# already in a chat's history never resurface in its pinned background card.
CARDS_BY_TURN = 2


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
    parsed: Dict[str, Any] = {}
    if isinstance(meta, str) and meta:
        try:
            parsed = json.loads(meta) or {}
        except Exception:
            parsed = {}
    return BatchRecord(
        cards_by_turn=bool(isinstance(parsed, dict) and parsed.get("cards") == CARDS_BY_TURN),
        id=bid,
        parent_session_id=int(d["parent_session_id"]),
        user_id=int(d["user_id"]),
        collect=str(d.get("collect") or "all"),
        lifetime=str(d.get("lifetime") or "one_shot"),
        status=str(d.get("status") or STATUS_RUNNING),
        watch_id=str(d.get("watch_id") or ""),
        widget_id=str(d.get("widget_id") or ""),
        attach_message_id=d.get("attach_message_id"),
        created_at=str(d.get("created_at") or ""),
        finished_at=str(d.get("finished_at") or ""),
        children=_load_children(db, bid) if bid else [],
        turn_id=str(d.get("turn_id") or ""),
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
    turn_id: str = "",
) -> BatchRecord:
    bid = new_id()
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO subagent_batches (
            id, parent_session_id, user_id, collect, lifetime, status,
            watch_id, widget_id, metadata, turn_id
        )
        VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)
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
            turn_id or None,
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
    child = ChildRecord(
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
    from api.orchestration_telemetry import record

    record.child_spawned(db, child)
    return child


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
    """Batches whose cards show live in the parent chat right now.

    A batch with a child still working, always. A finished one while the turn
    it belongs to is still running (bound to that turn's in-progress row,
    api.chat_turn_row): its cards stay on that live bubble until the reply saves
    them. A finished one bound nowhere (the parent has no bubble to carry it)
    stays in the pinned background card — the page never puts it on another turn's bubble.
    No timing window and no harness assumptions; a finished batch bound to a
    saved bubble shows from history (``service.leave_cards_on_last_bubble``).
    """
    out: List[BatchRecord] = []
    for batch in list_batches_for_parent(db, parent_session_id):
        working = batch.status not in TERMINAL_BATCH and (
            not batch.children or any(c.status not in TERMINAL_CHILD for c in batch.children))
        if working:
            out.append(batch)
        elif batch.attach_message_id and _row_in_progress(db, batch.attach_message_id):
            out.append(batch)
        elif not batch.attach_message_id and batch.cards_by_turn:
            out.append(batch)
    return out


def _row_in_progress(db, message_id) -> bool:
    getter = getattr(db, "get_message_by_id", None)
    if not callable(getter):
        return False
    try:
        row = getter(int(message_id)) or {}
    except Exception:
        return False
    return '"in_progress": true' in str(row.get("metadata") or "")


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
    owner_pid: Optional[int] = None,
    query_id: Optional[str] = None,
    live_turn_id: Optional[str] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    sets = []
    args: List[Any] = []
    if model is not None:
        sets.append("model = ?")
        args.append(str(model) or None)
    if effort is not None:
        sets.append("effort = ?")
        args.append(str(effort) or None)
    if status is not None:
        sets.append("status = ?")
        args.append(status)
        if status == STATUS_PENDING:
            sets.extend(["live_status = NULL", "live_status_at = NULL", "live_turn_id = NULL"])
    if result is not None:
        sets.append("result = ?")
        args.append(result)
    if error is not None:
        sets.append("error = ?")
        args.append(error)
    if pid is not None:
        sets.append("pid = ?")
        args.append(int(pid))
    if owner_pid is not None:
        sets.append("owner_pid = ?")
        args.append(int(owner_pid))
    if query_id is not None:
        sets.append("query_id = ?")
        args.append(str(query_id) or None)
    if started:
        sets.append("started_at = CURRENT_TIMESTAMP")
        sets.extend(["live_status = NULL", "live_status_at = NULL", "query_id = NULL",
                     "finished_at = NULL", "live_turn_id = ?"])
        args.append(new_id())
    if finished:
        sets.append("finished_at = CURRENT_TIMESTAMP")
        sets.extend(["live_status = NULL", "live_status_at = NULL"])
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
    guard = ""
    if live_turn_id is not None:
        guard = " AND live_turn_id = ? AND status = ?"
        args.extend([live_turn_id, STATUS_RUNNING])
    cur.execute(
        f"UPDATE subagent_children SET {', '.join(sets)} WHERE id = ?{guard}",
        tuple(args),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if changed and (started or finished):
        from api.orchestration_telemetry import record

        record.child_round(db, child_id, started=started, finished=finished)


def request_stop(db, child_id: str, *, at: Optional[float] = None) -> None:
    """Record a Stop for this child, whatever its status.

    The owning process (``api.subagents.stop_watch``) kills the child's CLI
    when it sees this; a caller in another process cannot (it may only signal
    its own descendants). Set on terminal rows too: a finished Claude turn can
    leave its CLI running a detached background wait.
    """
    import time

    conn = db._get_connection()
    try:
        conn.execute(
            "UPDATE subagent_children SET stop_requested_at = ? WHERE id = ?",
            (float(at if at is not None else time.time()), str(child_id)),
        )
        conn.commit()
    finally:
        conn.close()


def update_child_live_status(db, child_id: str, turn_id: str, text: str) -> bool:
    """Persist only this running turn's latest bounded status, never a late callback."""
    if not turn_id:
        return False
    text = " ".join(str(text or "").split())[:600]
    if not text:
        return False
    conn = db._get_connection()
    try:
        cursor = conn.execute(
            "UPDATE subagent_children SET live_status = ?, live_status_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND live_turn_id = ? AND status = ? "
            "AND COALESCE(live_status, '') != ?",
            (text, child_id, turn_id, STATUS_RUNNING, text))
        conn.commit()
        return cursor.rowcount == 1
    finally:
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


def _pid_alive(pid: Any) -> bool:
    """True while *pid* still names a live process."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours to signal
    except OSError:
        return False
    return True


EXTERNAL_ROUND = "external"


def begin_external_round(db, child_id: str) -> None:
    """A round driven outside ``run_child_turn`` (``chat_cli send`` to the child):
    the child works now, its turn runs in Flask like any chat turn (no CLI host
    to own it, so ``owner_pid`` is cleared), and its end is read from the turn
    (``service.on_child_turns_ended``)."""
    conn = db._get_connection()
    try:
        conn.execute(
            "UPDATE subagent_children SET status = ?, result = '', error = '', owner_pid = NULL, "
            "started_at = CURRENT_TIMESTAMP, finished_at = NULL, live_status = NULL, "
            "live_status_at = NULL, query_id = NULL, live_turn_id = ? WHERE id = ?",
            (STATUS_RUNNING, EXTERNAL_ROUND, str(child_id)))
        conn.commit()
    finally:
        conn.close()


def reconcile_orphan_children(db) -> List[str]:
    """Fail non-terminal children whose owning process is gone.

    A child turn runs inside the process that spawned it (the
    ``python -m api.subagents`` CLI, itself a tool child of a parent agent). If
    that host dies — SIGKILL runs no cleanup — the row keeps saying ``running``
    forever: the child pane spins with no agent behind it and the batch never
    finalizes. Flip those rows terminal so observers recover on the next poll.
    Returns the reconciled child ids.
    """
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, owner_pid FROM subagent_children "
            "WHERE status IN (?, ?) AND owner_pid IS NOT NULL",
            (STATUS_PENDING, STATUS_RUNNING),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    orphans = [
        str(r["id"]) for r in rows if not _pid_alive(r["owner_pid"])
    ]
    for cid in orphans:
        update_child(
            db, cid, status=STATUS_FAILED, error=ORPHAN_ERROR, finished=True
        )
    return orphans


def latest_assistant_text(db, session_id: int) -> str:
    messages = db.get_messages(int(session_id), limit=40) or []
    for msg in reversed(messages):
        if str(msg.get("role") or "") == "assistant":
            return str(msg.get("content") or "")
    return ""


def pending_card_children(db, parent_session_id: int) -> List[ChildRecord]:
    """Children of this parent whose follow-up round awaits a card on the next reply."""
    conn = db._get_connection()
    try:
        rows = conn.execute(
            "SELECT c.* FROM subagent_children c JOIN subagent_batches b ON b.id = c.batch_id "
            "WHERE b.parent_session_id = ? AND c.card_pending = 1 "
            "ORDER BY c.sort_index ASC, c.session_id ASC",
            (int(parent_session_id),),
        ).fetchall()
    finally:
        conn.close()
    return [_row_child(r) for r in rows]


def set_card_round(db, child_id: str, *, message_id: Optional[int] = None, pending: bool,
                   turn_id: Optional[str] = None) -> None:
    """Record a child's card bubble (when minted) or mark a new round pending.

    ``turn_id`` names the parent turn a new round was sent in (its card binds
    to that turn's row); None leaves it unchanged."""
    conn = db._get_connection()
    try:
        if message_id is not None:
            conn.execute(
                "UPDATE subagent_children SET card_message_id = ?, card_pending = ? WHERE id = ?",
                (int(message_id), 1 if pending else 0, str(child_id)))
        else:
            conn.execute(
                "UPDATE subagent_children SET card_pending = ? WHERE id = ?",
                (1 if pending else 0, str(child_id)))
        if turn_id is not None:
            conn.execute("UPDATE subagent_children SET card_turn_id = ? WHERE id = ?",
                         (turn_id or None, str(child_id)))
        conn.commit()
    finally:
        conn.close()


def card_bubble_id(db, child: ChildRecord) -> Optional[int]:
    """The parent bubble currently carrying this child's live card, if any."""
    if child.card_message_id:
        return int(child.card_message_id)
    batch = get_batch(db, child.batch_id)
    return int(batch.attach_message_id) if batch and batch.attach_message_id else None


def bind_unattached_batches(db, parent_session_id: int, message_id: int,
                            turn_id: str = "") -> List[BatchRecord]:
    """Attach the unbound batches (and pending rounds) of turn ``turn_id`` to its row.

    Matched by turn id only: a reply never takes another turn's cards, and a
    manual run's cards (no turn) were bound at spawn (anchor / latest bubble).
    """
    if not turn_id:
        return []  # a reply only ever carries its own turn's cards
    for child in pending_card_children(db, parent_session_id):
        if child.card_turn_id != turn_id:
            continue
        set_card_round(db, child.id, message_id=int(message_id), pending=False)
    bound: List[BatchRecord] = []
    for batch in list_batches_for_parent(db, parent_session_id):
        if batch.attach_message_id:
            continue
        if batch.turn_id != turn_id:
            continue  # another turn's batch (or a manual run's): never this reply's
        update_batch(db, batch.id, attach_message_id=int(message_id))
        batch.attach_message_id = int(message_id)
        bound.append(batch)
    return bound
