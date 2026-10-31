"""Orchestrator service: playbooks + chat bindings over the pure model.

Host adapter for ``api.orchestrator.model``. Owns user scoping, persistence
(``store``), agent-profile resolution (``api.subagents.profiles``) and the flag
gate. The Context Compiler calls ``brief_block`` so a bound chat's brief rides
every fresh context, including after a harness switch.
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, List, Optional

from api.orchestrator import store
from api.orchestrator.model import (
    PlaybookError,
    default_playbook,
    missing_profiles,
    normalize_playbook,
    render_brief,
    supervisor_argv,
    temporary_agents,
)

FLAG_ID = "orchestrator"
# Id of the built-in seed. The playbook ``/orchestrate`` uses without ``@id``
# is the user's chosen default (``default_id``), which falls back to this.
DEFAULT_ID = "default"


class OrchestratorError(ValueError):
    """Bad request against the orchestrator service."""


def enabled() -> bool:
    from api.experimental import is_enabled

    return is_enabled(FLAG_ID)


def _db(db=None):
    if db is not None:
        return db
    from api.auth_db import get_auth_db

    return get_auth_db()


def _handle(session_id: int) -> str:
    return f"CH-{int(session_id):06d}"


def parse_session(value: Any) -> int:
    from api.cuttle_ui_capabilities import parse_chat_handle

    raw = str(value or "").strip()
    if raw.isdigit():
        return int(raw)
    handle = parse_chat_handle(raw)
    if not handle or handle.get("session_id") is None:
        raise OrchestratorError(f"not a chat handle: {raw or '(empty)'}")
    return int(handle["session_id"])


def profile_map(user_id: int, *, db=None) -> Dict[str, Dict[str, Any]]:
    from api.subagents.profiles import list_profiles

    return {p["id"]: p for p in list_profiles(_db(db), int(user_id))}


def _annotate(doc: Dict[str, Any], profiles: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(doc)
    out["missing_profiles"] = missing_profiles(doc, profiles)
    return out


def _upgrade(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Stored docs may predate the current schema; reads see them upgraded."""
    try:
        return {**doc, **normalize_playbook(doc)}
    except PlaybookError:
        return doc


def _load(user_id: int, pid: str) -> Optional[Dict[str, Any]]:
    doc = store.get_playbook(user_id, pid)
    if doc is None and pid == DEFAULT_ID:
        return {**default_playbook(), "seed": True}
    return _upgrade(doc) if doc is not None else None


def default_id(user_id: int) -> str:
    """The playbook ``/orchestrate`` uses without ``@id`` (stale picks fall back)."""
    pid = store.default_playbook(user_id)
    if pid and pid != DEFAULT_ID and store.get_playbook(user_id, pid) is None:
        return DEFAULT_ID
    return pid or DEFAULT_ID


def set_default(user_id: int, playbook_id: str, *, db=None) -> str:
    pid = str(playbook_id or "").strip().lower() or DEFAULT_ID
    if _load(user_id, pid) is None:
        raise OrchestratorError(f"playbook {pid!r} not found")
    store.set_default_playbook(user_id, pid)
    return pid


def list_playbooks(user_id: int, *, db=None) -> List[Dict[str, Any]]:
    docs = [_upgrade(d) for d in store.list_playbooks(user_id)]
    if not any(d.get("id") == DEFAULT_ID for d in docs):
        # The seed is virtual until the user saves it; never written implicitly.
        docs.insert(0, {**default_playbook(), "seed": True})
    profiles = profile_map(user_id, db=db)
    chosen = default_id(user_id)
    return [{**_annotate(d, profiles), "is_default": d.get("id") == chosen} for d in docs]


def get_playbook(user_id: int, playbook_id: str = "", *, db=None) -> Dict[str, Any]:
    pid = str(playbook_id or "").strip().lower().lstrip("@") or default_id(user_id)
    doc = _load(user_id, pid)
    if doc is None:
        raise OrchestratorError(f"playbook {pid!r} not found")
    return {**_annotate(doc, profile_map(user_id, db=db)), "is_default": pid == default_id(user_id)}


def _check_temporary_agents(doc: Dict[str, Any]) -> None:
    """Temporary agents get the same pin check as a saved profile."""
    from api.agent_harness.agent_models import check_agent_pin
    from api.subagents.profiles import normalize_harness

    catalogs: Dict[str, Any] = {}
    for tier in doc["tiers"]:
        for member in tier["members"]:
            if member.get("profile"):
                continue
            member["agent"] = normalize_harness(member["agent"])
            if member["agent"] == "none":
                raise OrchestratorError(f"{tier['label']}: a temporary agent needs a harness")
            error = check_agent_pin(member["agent"], member["model"], member["effort"], catalogs=catalogs)
            if error:
                raise OrchestratorError(f"{tier['label']} · {member['name']}: {error}")


def save_playbook(user_id: int, raw: Any, *, db=None) -> Dict[str, Any]:
    try:
        doc = normalize_playbook(raw)
    except PlaybookError as exc:
        raise OrchestratorError(str(exc)) from None
    missing = missing_profiles(doc, profile_map(user_id, db=db))
    if missing:
        raise OrchestratorError(
            "unknown agent profile(s): " + ", ".join(missing) + " — create them in Apps → Agents first"
        )
    if temporary_agents(doc):
        _check_temporary_agents(doc)
    store.put_playbook(user_id, doc)
    return get_playbook(user_id, doc["id"], db=db)


def delete_playbook(user_id: int, playbook_id: str) -> bool:
    return store.delete_playbook(user_id, str(playbook_id or "").strip().lower())


def _session_owner(session_id: int, db) -> int:
    row = db.get_chat_session_by_id(int(session_id))
    if not row:
        raise OrchestratorError(f"chat {_handle(session_id)} not found")
    return int(row["user_id"])


def bind(session: Any, playbook_id: str = "", goal: str = "", *, db=None,
         user_id: Optional[int] = None) -> Dict[str, Any]:
    db = _db(db)
    sid = parse_session(session)
    owner = _session_owner(sid, db)
    if user_id is not None and int(user_id) != owner:
        raise OrchestratorError("that chat belongs to another account")
    doc = get_playbook(owner, playbook_id, db=db)
    if doc.get("missing_profiles"):
        raise OrchestratorError(
            f"playbook {doc['id']!r} references missing profile(s): "
            + ", ".join(doc["missing_profiles"])
        )
    store.bind(sid, owner, doc["id"], str(goal or "").strip()[:2000])
    return brief(sid, db=db)


def unbind(session: Any, *, db=None, user_id: Optional[int] = None) -> bool:
    db = _db(db)
    sid = parse_session(session)
    if user_id is not None and _session_owner(sid, db) != int(user_id):
        raise OrchestratorError("that chat belongs to another account")
    return store.unbind(sid)


def brief(session: Any = None, *, playbook_id: str = "", user_id: Optional[int] = None,
          db=None) -> Dict[str, Any]:
    """Rendered brief for a bound chat, or a playbook preview when no chat."""
    db = _db(db)
    binding = None
    sid = None
    if session not in (None, ""):
        sid = parse_session(session)
        binding = store.binding(sid)
        owner = _session_owner(sid, db)
        if user_id is not None and owner != int(user_id):
            raise OrchestratorError("that chat belongs to another account")
        user_id = owner
    if user_id is None:
        raise OrchestratorError("brief needs a chat or a user")
    pid = playbook_id or (binding or {}).get("playbook_id") or ""
    doc = get_playbook(int(user_id), pid, db=db)
    profiles = profile_map(int(user_id), db=db)
    handle = _handle(sid) if sid is not None else ""
    target = handle or "CH-<orchestrator>"
    argv = supervisor_argv(doc, target, profiles)
    return {
        "playbook": doc,
        "session": handle or None,
        "bound": bool(binding),
        "goal": (binding or {}).get("goal") or "",
        "brief": render_brief(doc, profiles, chat_handle=handle, goal=(binding or {}).get("goal") or ""),
        "supervisor_argv": argv,
        "supervisor_command": "python -m api.chat_cli " + " ".join(shlex.quote(a) for a in argv),
    }


def _chat_title(sess: Dict[str, Any]) -> str:
    """Display title: auto-names start with the slash commands typed first."""
    import re

    raw = str(sess.get("session_name") or sess.get("title") or "").strip()
    return re.sub(r"^(?:/[\w-]+\s+)+", "", raw).strip() or raw


def list_bindings(user_id: int, *, db=None) -> List[Dict[str, Any]]:
    db = _db(db)
    out = []
    for row in store.list_bindings(user_id):
        sess = db.get_chat_session_by_id(int(row["session_id"]))
        if not sess or not sess.get("is_active", 1):
            continue
        out.append({
            "session": _handle(row["session_id"]),
            "title": _chat_title(sess),
            "project": str(sess.get("project_name") or ""),
            "playbook_id": row["playbook_id"],
            "goal": row["goal"],
            "bound_at": row["bound_at"],
        })
    return out


def brief_block(chat_session_id: Any) -> str:
    """Context Compiler hook: the bound chat's brief, or ``""``. Never raises."""
    try:
        if not enabled() or chat_session_id in (None, ""):
            return ""
        sid = parse_session(chat_session_id)
        if store.binding(sid) is None:
            return ""
        return brief(sid)["brief"].strip()
    except Exception:
        return ""
