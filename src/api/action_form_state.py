"""Host-owned action-form lifecycle state.

Chat history stores each card's render payload (the ``<cuttle_action_form*>``
JSON block and its message metadata). That payload is enough to *draw* a card
but not to answer "is this form still waiting on a person?" without re-parsing
free text on every read and re-deriving restart-generation semantics on every
client.

This owner keeps the authoritative answer in one durable row per
``(session_id, form_id)`` inside the auth database, written in the same
transaction as the message it belongs to. Attention/history dots and the
restart reconciliation derive from it, so a card locked on the phone is locked
on the desktop and a restart that finished cannot keep a chat blue or spinning.

The table is an *index* of the persisted history, never a second render source:
content/metadata stay canonical for what the card looks like, and every write
path that mutates a message re-syncs the row from the (patched) content.
Module imports stay stdlib-only; the auth database is reached lazily by callers
that already hold the connection.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

_FORM_RE = re.compile(
    r"<cuttle_action_form(?:_pending)?\b([^>]*)>(.*?)</cuttle_action_form(?:_pending)?\s*>",
    re.IGNORECASE | re.DOTALL,
)
_LOCKED_ATTR_RE = re.compile(r"\blocked\s*=\s*[\"']1[\"']", re.IGNORECASE)
_RESTART_GROUP_RE = re.compile(r"^flask-restart-g(\d+)$", re.IGNORECASE)

STATUS_OPEN = "open"
STATUS_LOCKED = "locked"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

KIND_RESTART = "restart"
KIND_WATCH = "watch"
KIND_QUESTION = "question"
KIND_ACTION = "action"

_TERMINAL = frozenset({STATUS_LOCKED, STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED})
_RESTART_TERMINAL_STATES = frozenset(
    {"healthy", "failed", "timed_out", "rejected", "cancelled"}
)


def restart_generation_for(form_id: Any) -> Optional[int]:
    """Epoch encoded in ``flask-restart-g{N}`` ids, else None."""
    m = _RESTART_GROUP_RE.fullmatch(str(form_id or "").strip())
    return int(m.group(1)) if m else None


def _attr(attrs: str, name: str) -> str:
    m = re.search(r'\b' + re.escape(name) + r'\s*=\s*(["\'])(.*?)\1', attrs or "", re.I)
    return m.group(2) if m else ""


def classify_spec(spec: Dict[str, Any], form_id: str) -> Tuple[str, bool]:
    """Return ``(kind, interactive)`` for one normalized spec.

    ``interactive`` mirrors the question/action predicate: a card that still
    offers a choice, field, or submit action *and* is not running work.
    """
    options = spec.get("options") if isinstance(spec.get("options"), list) else []
    fields = spec.get("fields") if isinstance(spec.get("fields"), list) else []
    submit = spec.get("submit") if isinstance(spec.get("submit"), dict) else {}
    has_watch_btn = any(
        str((o or {}).get("action") or "") in ("__watch_resume__", "__watch_park__", "__watch_cancel__")
        for o in options
    )
    is_restart = (
        restart_generation_for(form_id) is not None
        or any(str((o or {}).get("action") or "") in ("flask.restart", "__native_restart__") for o in options)
        or "restart flask" in str(spec.get("title") or "").strip().lower()
    )
    has_side_effect = bool(
        spec.get("watch")
        or submit.get("action")
        or any(str((b or {}).get("action") or "") for b in (spec.get("buttons") or []) if isinstance(b, dict))
        or any(str((o or {}).get("action") or "") for o in options if isinstance(o, dict))
    )
    if is_restart:
        kind = KIND_RESTART
    elif spec.get("watch") or has_watch_btn:
        kind = KIND_WATCH
    elif has_side_effect:
        kind = KIND_ACTION
    else:
        kind = KIND_QUESTION
    interactive = bool(options or fields or submit.get("action"))
    return kind, interactive


def parse_forms(content: Any) -> List[Dict[str, Any]]:
    """Extract form records from persisted assistant content (pure)."""
    text = content or ""
    records: List[Dict[str, Any]] = []
    for idx, (attrs, body) in enumerate(_FORM_RE.findall(text)):
        try:
            spec = json.loads((body or "").strip())
        except (TypeError, ValueError):
            continue
        if not isinstance(spec, dict):
            continue
        form_id = str(spec.get("id") or "").strip() or _attr(attrs, "id")
        if not form_id:
            # Legacy / un-rewritten cards carry no id; index them positionally so
            # a question still counts as awaiting input.
            form_id = f"__anon_{idx}"
        group = str(spec.get("restartFormGroup") or "").strip()
        epoch = restart_generation_for(form_id)
        if epoch is None:
            epoch = restart_generation_for(group)
        kind, interactive = classify_spec(spec, form_id)
        watch = spec.get("watch") if isinstance(spec.get("watch"), dict) else {}
        snapshot = watch.get("snapshot") if isinstance(watch.get("snapshot"), dict) else {}
        locked_attr = bool(_LOCKED_ATTR_RE.search(attrs or ""))
        locked = bool(spec.get("locked")) or locked_attr
        watch_terminal = bool(watch.get("terminal")) or bool(snapshot.get("terminal"))
        watch_state = str(snapshot.get("state") or "").strip().lower()
        if locked:
            status = STATUS_LOCKED
        elif kind == KIND_WATCH and watch_terminal:
            status = STATUS_FAILED if watch_state == "failed" else STATUS_DONE
        elif spec.get("restartId"):
            status = STATUS_RUNNING
        else:
            status = STATUS_OPEN
        records.append(
            {
                "form_id": form_id,
                "kind": kind,
                "interactive": interactive,
                "locked": locked,
                "status": status,
                "selected": list(spec.get("selected") or []) if isinstance(spec.get("selected"), list) else [],
                "toast": str(spec.get("toast") or ""),
                "restart_id": str(spec.get("restartId") or ""),
                "restart_generation": epoch,
            }
        )
    return records


def ensure_schema(conn) -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS action_form_state (
            session_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL DEFAULT 0,
            form_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT '',
            interactive INTEGER NOT NULL DEFAULT 0,
            locked INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            selected TEXT NOT NULL DEFAULT '[]',
            toast TEXT NOT NULL DEFAULT '',
            restart_id TEXT NOT NULL DEFAULT '',
            restart_generation INTEGER,
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (session_id, message_id, form_id))"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_form_state_msg "
        "ON action_form_state(session_id, message_id)"
    )


def sync_message(
    conn,
    session_id: int,
    message_id: Optional[int],
    content: Any,
    metadata: Any = None,
) -> int:
    """Replace this message's form rows with what its content now says.

    Content is the render payload and stays canonical; this keeps the index in
    lockstep with it. Metadata ``action_form_result`` entries overlay the parsed
    status so a lock/watch write that only touched metadata is honored too.
    """
    if not session_id:
        return 0
    mid = int(message_id) if message_id is not None else 0
    # Fast path: most messages carry no card, so skip the parse entirely.
    if "cuttle_action_form" in str(content or "").lower():
        rows = parse_forms(content)
    else:
        rows = []
    meta = metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            meta = {}
    meta = meta if isinstance(meta, dict) else {}
    result = meta.get("action_form_result") if isinstance(meta.get("action_form_result"), dict) else {}
    forms_meta = result.get("forms") if isinstance(result.get("forms"), dict) else {}

    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "DELETE FROM action_form_state WHERE session_id=? AND message_id=?",
        (int(session_id), mid),
    )
    now = time.time()
    count = 0
    for rec in rows:
        entry = forms_meta.get(rec["form_id"]) if isinstance(forms_meta.get(rec["form_id"]), dict) else {}
        status = rec["status"]
        locked = rec["locked"]
        selected = rec["selected"]
        toast = rec["toast"]
        restart_id = rec["restart_id"]
        if entry:
            if entry.get("locked"):
                locked, status = True, STATUS_LOCKED
            if "selected" in entry and isinstance(entry.get("selected"), list):
                selected = entry["selected"]
            if entry.get("toast"):
                toast = str(entry["toast"])
            if "restartId" in entry:
                restart_id = str(entry.get("restartId") or "")
            if entry.get("watch_terminal"):
                status = STATUS_FAILED if rec["kind"] == KIND_WATCH and str(
                    (entry.get("watch_snapshot") or {}).get("state") or ""
                ).lower() == "failed" else STATUS_DONE
        conn.execute(
            """INSERT OR REPLACE INTO action_form_state
               (session_id, message_id, form_id, kind, interactive, locked, status,
                selected, toast, restart_id, restart_generation, revision, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(session_id), mid, rec["form_id"],
                rec["kind"], int(rec["interactive"]), int(locked), status,
                json.dumps(selected), toast, restart_id, rec["restart_generation"],
                int(now * 1000) & 0x7FFFFFFF, now,
            ),
        )
        count += 1
    return count


def get_many(conn, session_id: int) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM action_form_state WHERE session_id=? ORDER BY message_id, form_id",
        (int(session_id),),
    ).fetchall()
    # Keyed by message then form: a shared restart id can appear in several
    # bubbles of one chat, so form_id alone is not unique within a session.
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        mid = str(r["message_id"] if r["message_id"] is not None else 0)
        out.setdefault(mid, {})[r["form_id"]] = snapshot(r)
    return out


def has_message(conn, session_id: int, message_id: Optional[int]) -> bool:
    if message_id is None:
        return False
    return conn.execute(
        "SELECT 1 FROM action_form_state WHERE session_id=? AND message_id=? LIMIT 1",
        (int(session_id), int(message_id)),
    ).fetchone() is not None


def force_terminal(conn, session_id: int, message_id: int, form_id: str) -> None:
    """Terminalize one row without a history patch (content already gone)."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """UPDATE action_form_state SET status=?, locked=1, revision=revision+1, updated_at=?
           WHERE session_id=? AND message_id=? AND form_id=?""",
        (STATUS_DONE, time.time(), int(session_id), int(message_id), str(form_id)),
    )


def snapshot(row) -> Dict[str, Any]:
    r = dict(row)
    try:
        selected = json.loads(r.get("selected") or "[]")
    except (TypeError, ValueError):
        selected = []
    return {
        "form_id": r.get("form_id"),
        "message_id": r.get("message_id"),
        "kind": r.get("kind") or "",
        "status": r.get("status") or STATUS_OPEN,
        "locked": bool(r.get("locked")),
        "selected": selected if isinstance(selected, list) else [],
        "toast": r.get("toast") or "",
        "restart_id": r.get("restart_id") or "",
        "restart_generation": r.get("restart_generation"),
    }


def is_superseded_restart(row, live_generation: Optional[int]) -> bool:
    """A restart card from an earlier Flask generation is done, not awaiting."""
    epoch = row["restart_generation"]
    if epoch is None or live_generation is None:
        return False
    return int(live_generation) > int(epoch)


def awaiting_input(
    conn,
    session_id: int,
    message_id: Optional[int],
    live_generation: Optional[int] = None,
) -> bool:
    """True only when THIS reply still waits on a person.

    Scoped to the reply's message id so an old, long-answered form lower in the
    transcript can never light a chat up again.
    """
    if message_id is None:
        return False
    rows = conn.execute(
        "SELECT * FROM action_form_state WHERE session_id=? AND message_id=?",
        (int(session_id), int(message_id)),
    ).fetchall()
    if not rows:
        return False
    for row in rows:
        status = str(row["status"] or "")
        if status in _TERMINAL or status == STATUS_RUNNING:
            continue
        if row["kind"] == KIND_WATCH:
            continue
        if row["restart_id"]:
            continue
        if row["kind"] == KIND_RESTART and is_superseded_restart(row, live_generation):
            continue
        if row["interactive"]:
            return True
    return False


def superseded_restart_forms(
    conn,
    *,
    live_generation: Optional[int] = None,
    status: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Open restart cards whose restart is over and should be terminalized.

    Two independent signals count as "over": a newer Flask generation exists
    (superseded), or the daemon's last restart reached a terminal state and this
    card tracks that generation/restart id (finished). Both are host facts, so
    every client reaches the same conclusion without local inference.
    """
    status = status if isinstance(status, dict) else {}
    state = str(status.get("state") or "").strip().lower()
    status_rid = str(status.get("restart_id") or "").strip()
    status_gen = status.get("generation")
    try:
        status_gen = int(status_gen) if status_gen is not None and str(status_gen).strip() else None
    except (TypeError, ValueError):
        status_gen = None
    if status_gen is not None and live_generation is not None and status_gen > int(live_generation):
        # The durable status file outlives the daemon, whose generation counter
        # restarts at 1. A status newer than the live Flask belongs to an earlier
        # daemon lifetime, so its generation says nothing about current cards
        # (it locked every fresh card as "Flask restarted" before any click).
        status_gen = None
    terminal = state in _RESTART_TERMINAL_STATES and state != "healthy"
    healthy = state == "healthy"
    rows = conn.execute(
        "SELECT * FROM action_form_state WHERE kind=? AND status NOT IN (?,?,?)",
        (KIND_RESTART, STATUS_LOCKED, STATUS_DONE, STATUS_FAILED),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        epoch = row["restart_generation"]
        rid = str(row["restart_id"] or "")
        if is_superseded_restart(row, live_generation):
            reasons = "superseded"
        elif (healthy or terminal) and (
            (status_rid and rid and rid == status_rid)
            # The status generation is the Flask generation that restart
            # *produced*. A card stamped with that same generation was created
            # after the restart and has not run yet, so only strictly older
            # generations are finished here. (<= wrongly locked every fresh
            # card in the current generation: a card showed "Flask restarted"
            # before anyone pressed it.)
            or (status_gen is not None and epoch is not None and epoch < status_gen)
        ):
            reasons = "finished" if healthy else "failed"
        else:
            continue
        out.append({
            "session_id": int(row["session_id"]),
            "message_id": int(row["message_id"]) if row["message_id"] is not None else 0,
            "form_id": row["form_id"],
            "reason": reasons,
        })
    return out
