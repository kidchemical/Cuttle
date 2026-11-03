"""Tasks App read model: every list a user owns, with the chats it reaches.

Read-only over :mod:`api.gizmos.tasks` rows (auth-owned ``chat_widgets``);
writes stay in that owner. ``related_chats`` suggests an orchestration family
for linking — sub-agent parent/children (``api.subagents.store``) and
supervisor/supervised chats (``api.chat_ops.store``). Those imports are
deferred: both packages already call into ``api.gizmos``.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from api.gizmos import tasks
from api.gizmos import tasks_model as model

MAX_CHATS = 200
MAX_FAMILY = 40


def handle(session_id: Any) -> str:
    return f"CH-{int(session_id):06d}"


def _chat_row(row: Dict[str, Any], *, archived: bool = False) -> Dict[str, Any]:
    sid = int(row["id"])
    return {
        "id": sid,
        "handle": handle(sid),
        "name": str(row.get("session_name") or "").strip() or "Untitled chat",
        "project_path": model._norm_project_path(row.get("project_path")),
        "last_activity": row.get("last_message_time") or row.get("last_activity") or "",
        "archived": bool(archived or row.get("is_archived")),
    }


def _chats(db, user_id: int) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for row in db.get_user_chat_sessions(user_id) or []:
        out[int(row["id"])] = _chat_row(row)
    try:
        archived = db.get_archived_chat_sessions(user_id) or []
    except Exception:
        archived = []
    for row in archived:
        out.setdefault(int(row["id"]), _chat_row(row, archived=True))
    return out


def _chat_for(db, user_id: int, sid: Optional[int], known: Dict[int, Dict[str, Any]]):
    if sid is None:
        return None
    sid = int(sid)
    if sid not in known:
        row = db.get_chat_session_by_id(sid)
        if not row or int(row.get("user_id") or 0) != int(user_id):
            return {"id": sid, "handle": handle(sid), "name": "Unavailable chat",
                    "project_path": "", "last_activity": "", "archived": False, "missing": True}
        known[sid] = _chat_row(row, archived=bool(row.get("is_archived")) or not row.get("is_active", 1))
    return known[sid]


def _summary(row: Dict[str, Any], db, user_id: int, known) -> Dict[str, Any]:
    items = (row.get("payload") or {}).get("items") or []
    done, total = model.count_tasks(items)
    home = _chat_for(db, user_id, row.get("session_id"), known) if row.get("scope") == "session" else None
    links = [_chat_for(db, user_id, sid, known) for sid in row.get("linked_sessions") or []]
    return {
        **tasks.public(row),
        "done": done,
        "total": total,
        "home_chat": home,
        "linked_chats": [c for c in links if c],
    }


def listing(*, user_id: int, db=None) -> Dict[str, Any]:
    """Every Tasks list of ``user_id`` (active + archived) plus recent chats."""
    db = tasks.database(db)
    known = _chats(db, user_id)
    rows: List[Dict[str, Any]] = []
    for status in ("active", "archived"):
        rows.extend(r for r in db.list_chat_widgets(user_id=user_id, status=status)
                    if r.get("type") == "tasks")
    lists = [_summary(r, db, user_id, known) for r in rows]
    lists.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    chats = sorted(known.values(), key=lambda c: str(c.get("last_activity") or ""), reverse=True)
    return {
        "lists": lists,
        "chats": [c for c in chats if not c.get("missing")][:MAX_CHATS],
        "revision": db.widgets_revision_for(user_id=user_id),
    }


def summary(gizmo_id: str, *, user_id: int, db=None) -> Dict[str, Any]:
    db = tasks.database(db)
    return _summary(db.get_chat_widget(gizmo_id, user_id=user_id) or tasks.get(gizmo_id, user_id=user_id),
                    db, user_id, _chats(db, user_id))


# ── Orchestration family ─────────────────────────────────────────────────


def _subagent_parent(db, sid: int) -> Optional[int]:
    from api.subagents import store as sub_store
    child = sub_store.get_child_by_session(db, sid)
    if not child:
        return None
    batch = sub_store.get_batch(db, child.batch_id)
    return int(batch.parent_session_id) if batch and batch.parent_session_id else None


def _subagent_children(db, sid: int) -> Iterable[int]:
    from api.subagents import store as sub_store
    for batch in sub_store.list_batches_for_parent(db, sid):
        for child in batch.children:
            yield int(child.session_id)


def _supervision_links() -> Dict[int, int]:
    """``{supervised: supervisor}`` for live supervision schedules."""
    try:
        from api.chat_ops import store as ops_store
        return {int(t): int(a) for t, a in ops_store.supervisor_links().items()}
    except Exception:
        return {}


def related_chats(session_id: Any, *, user_id: int, db=None) -> List[Dict[str, Any]]:
    """Chats in the same orchestration as ``session_id`` (excluding it).

    Climbs sub-agent parents to the root, then walks sub-agent children and
    live supervision links. Each entry carries a ``relation`` label.
    """
    db = tasks.database(db)
    _, start, _ = tasks.session_context(session_id, user_id=user_id, db=db)
    relation: Dict[int, str] = {start: "self"}
    root = start
    for _ in range(8):
        try:
            parent = _subagent_parent(db, root)
        except Exception:
            parent = None
        if not parent or parent in relation:
            break
        relation[parent] = "parent" if root == start else "ancestor"
        root = parent
    supervisor_of = _supervision_links()
    supervised_by: Dict[int, List[int]] = {}
    for target, actor in supervisor_of.items():
        supervised_by.setdefault(actor, []).append(target)
    queue = list(relation)
    while queue and len(relation) <= MAX_FAMILY:
        sid = queue.pop(0)
        try:
            kids = list(_subagent_children(db, sid))
        except Exception:
            kids = []
        kid_label = "sibling" if relation.get(sid) == "parent" else "sub-agent"
        peers = [(kid, kid_label) for kid in kids]
        if sid in supervisor_of:
            peers.append((supervisor_of[sid], "supervisor"))
        peers.extend((t, "supervised") for t in supervised_by.get(sid, []))
        for peer, label in peers:
            if peer not in relation:
                relation[peer] = label
                queue.append(peer)
    known = _chats(db, user_id)
    out = []
    for sid, rel in relation.items():
        if sid == start:
            continue
        chat = _chat_for(db, user_id, sid, known)
        if chat and not chat.get("missing"):
            out.append({**chat, "relation": rel})
    return out
