"""Chat ↔ checkout binding — set which worktree a chat session runs in.

The owner for *changing* a chat's ``checkout_uid`` outside the composer chip
(agents via ``python -m api.worktrees bind``). It validates the checkout
against the chat's project, writes the auth session row, leaves a persisted
system note in the transcript, and reports how the caller relates to the
target chat so an agent rebinding someone else's chat is told so.

Relation of the acting chat to the target:

- ``self`` — the agent's own chat,
- ``descendant`` — one of its sub-agent child chats (any depth),
- ``foreign`` — any other chat (allowed, with a warning),
- ``unknown`` — no acting chat identified (allowed, with a warning).

Never imports the Flask entry module; the chip path stays in
``api.chat_session_project``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from api.worktrees import registry
from api.worktrees.binding import get_usable_checkout, parse_session_id


class ChatBindingError(RuntimeError):
    """The binding request was refused."""


def chat_handle(session_id: int) -> str:
    return f"CH-{int(session_id):06d}"


def _default_db():
    from api.auth_db import get_auth_db

    return get_auth_db()


def _default_project_lookup(project_id: int) -> Optional[Dict[str, Any]]:
    try:
        from managers.project_manager import project_manager
    except Exception:  # pragma: no cover - import guard
        return None
    return project_manager.get_project(int(project_id)) if project_manager is not None else None


def checkout_summary(checkout_uid, *, db_path_override: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Path/branch/state of a bound checkout for clients, or ``None``."""
    uid = str(checkout_uid or "").strip()
    if not uid:
        return None
    checkout = registry.get_checkout(uid, db_path_override=db_path_override)
    if checkout is None:
        return None
    return {
        "checkout_uid": checkout.checkout_uid,
        "project_id": checkout.project_id,
        "path": checkout.path,
        "branch": checkout.branch,
        "state": checkout.state,
    }


def relation(actor_session_id: Optional[int], target_session_id: int, *, db) -> str:
    """How the acting chat relates to the target chat."""
    if actor_session_id is None:
        return "unknown"
    if int(actor_session_id) == int(target_session_id):
        return "self"
    actor = db.get_chat_session_by_id(int(actor_session_id)) or {}
    user_id = actor.get("user_id")
    if user_id is not None and int(target_session_id) in set(
        db.descendant_session_ids(int(actor_session_id), int(user_id))
    ):
        return "descendant"
    return "foreign"


def _warning(rel: str, target: str, actor: Optional[str]) -> Optional[str]:
    if rel == "foreign":
        return (
            f"{target} is not your chat ({actor}) or one of your sub-agent chats. "
            f"You changed another chat's worktree: its next turn runs in the new "
            f"checkout. Tell the user, and undo it if that was not intended."
        )
    if rel == "unknown":
        return (
            f"No acting chat identified (pass --as CH-… or run inside a Cuttle turn), "
            f"so {target} could not be checked as yours. Confirm it was the intended chat."
        )
    return None


def get_chat_binding(session, *, db=None, db_path_override: Optional[Path] = None) -> Dict[str, Any]:
    """The chat's project and bound checkout (``checkout`` is None for Local)."""
    sid = parse_session_id(session)
    if sid is None:
        raise ChatBindingError(f"invalid chat handle: {session!r}")
    db = db or _default_db()
    row = db.get_chat_session_by_id(sid)
    if not row or not row.get("is_active", 1):
        raise ChatBindingError(f"{chat_handle(sid)} not found")
    return {
        "session_id": sid,
        "handle": chat_handle(sid),
        "project_id": row.get("project_id"),
        "project_name": row.get("project_name"),
        "project_path": row.get("project_path"),
        "checkout": checkout_summary(row.get("checkout_uid"), db_path_override=db_path_override),
    }


def bind_chat(
    session,
    checkout_uid: Optional[str],
    *,
    actor=None,
    db=None,
    project_lookup: Callable[[int], Optional[Dict[str, Any]]] = _default_project_lookup,
    note: bool = True,
    db_path_override: Optional[Path] = None,
) -> Dict[str, Any]:
    """Bind chat ``session`` to ``checkout_uid`` (``None``/'' = Local/primary).

    A chat with no project adopts the checkout's project; a chat on a
    different project is refused (switch the project first). Takes effect on
    the chat's next turn; a turn already running keeps its working directory.
    """
    sid = parse_session_id(session)
    if sid is None:
        raise ChatBindingError(f"invalid chat handle: {session!r}")
    actor_sid = parse_session_id(actor) if actor not in (None, "") else None
    db = db or _default_db()
    row = db.get_chat_session_by_id(sid)
    if not row or not row.get("is_active", 1):
        raise ChatBindingError(f"{chat_handle(sid)} not found")

    project_id = row.get("project_id")
    project_name = row.get("project_name")
    project_path = row.get("project_path")
    uid = str(checkout_uid or "").strip() or None
    checkout = None
    if uid:
        checkout = get_usable_checkout(uid, db_path_override=db_path_override)
        if checkout is None:
            raise ChatBindingError(f"checkout {uid} is unknown or not ready")
        if project_id in (None, ""):
            proj = project_lookup(int(checkout.project_id)) or {}
            project_id = int(checkout.project_id)
            project_name = proj.get("name") or project_name
            project_path = proj.get("path") or project_path
        elif int(project_id) != int(checkout.project_id):
            raise ChatBindingError(
                f"checkout {uid} belongs to project {checkout.project_id}, but "
                f"{chat_handle(sid)} is on project {project_id}; switch the chat's project first"
            )

    previous = str(row.get("checkout_uid") or "").strip() or None
    rel = relation(actor_sid, sid, db=db)
    target = chat_handle(sid)
    actor_handle = chat_handle(actor_sid) if actor_sid is not None else None
    result: Dict[str, Any] = {
        "session_id": sid,
        "handle": target,
        "previous_checkout_uid": previous,
        "checkout": checkout_summary(uid, db_path_override=db_path_override) if uid else None,
        "changed": previous != uid,
        "relation": rel,
        "actor": actor_handle,
    }
    warning = _warning(rel, target, actor_handle)
    if warning:
        result["warning"] = warning
    if previous == uid:
        return result

    db.set_session_project(
        sid, int(row["user_id"]),
        project_id=project_id, project_name=project_name,
        project_path=project_path, checkout_uid=uid,
    )
    if uid:
        registry.touch_checkout(uid, db_path_override=db_path_override)
    if note:
        label = (checkout.branch or uid) if checkout else "Local (primary checkout)"
        by = f" (set by {actor_handle})" if actor_handle and rel != "self" else ""
        db.add_message(
            sid, "system", f"\U0001F33F Worktree: {label}{by}",
            metadata={"kind": "worktree_binding", "checkout_uid": uid, "set_by": actor_handle},
        )
    return result
