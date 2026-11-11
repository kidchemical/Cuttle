"""Orchestrator service: playbooks + chat bindings over the pure model.

Host adapter for ``api.orchestrator.model``. Owns user scoping, persistence
(``store``), agent-profile resolution (``api.subagents.profiles``) and the flag
gate. The Context Compiler calls ``brief_block`` so a bound chat's brief rides
every fresh context, including after a harness switch.
"""

from __future__ import annotations

import logging
import re
import shlex
from typing import Any, Dict, List, Optional

from api.orchestrator import store
from api.orchestrator.model import (
    PlaybookError,
    active_members,
    changed_sections,
    default_playbook,
    find_tier,
    missing_profiles,
    normalize_playbook,
    pick_member,
    render_brief,
    spawn_profile,
    supervisor_argv,
    supervisor_config,
    temporary_agents,
    tier_of,
)

FLAG_ID = "orchestrator"
# Id of the built-in seed. The playbook ``/orchestrate`` uses without ``@id``
# is the user's chosen default (``default_id``), which falls back to this.
DEFAULT_ID = "default"

log = logging.getLogger(__name__)


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
    from api.chat_ops.checkpoints import parse_checkpoints
    try:
        # Same validation (and normalized form) as a schedule's own checkpoints.
        doc["supervisor"]["checkpoints"] = parse_checkpoints(doc["supervisor"].get("checkpoints") or {})
    except ValueError as exc:
        raise OrchestratorError(f"supervisor.checkpoints: {exc}") from None
    missing = missing_profiles(doc, profile_map(user_id, db=db))
    if missing:
        raise OrchestratorError(
            "unknown agent profile(s): " + ", ".join(missing) + " — create them in Apps → Agents first"
        )
    if temporary_agents(doc):
        _check_temporary_agents(doc)
    before = _load(user_id, doc["id"])
    store.put_playbook(user_id, doc)
    if before is not None:
        try:
            relay_edit(user_id, before, doc, db=db)
        except Exception:  # the save stands; a missed relay is not a failed save
            log.exception("orchestrator: relaying playbook %s edit failed", doc["id"])
    from api.chat_ops.brief import focus_warnings
    out = get_playbook(user_id, doc["id"], db=db)
    warnings = focus_warnings(doc["supervisor"].get("instructions"))
    return {**out, "warnings": warnings} if warnings else out


def relay_edit(user_id: int, before: Dict[str, Any], after: Dict[str, Any], *, db=None) -> List[Dict[str, Any]]:
    """Carry a playbook edit to the chats running it.

    A bound orchestrator gets the new brief on its next turn by itself (the
    Context Compiler's resume delta diffs the brief), so the gap is a turn
    already running and the supervisor's schedule, which copied the playbook's
    supervisor settings when it started. Each supervision schedule on a bound
    chat takes the edited settings and gets one check-in naming the change, so
    the supervisor can steer a live turn. Returns per-chat summaries.
    """
    changed = changed_sections(before, after)
    if not changed:
        return []
    from api.chat_ops.service import resync_supervision

    db = _db(db)
    profiles = profile_map(user_id, db=db)
    previous, current = supervisor_config(before, profiles), supervisor_config(after, profiles)
    name = after.get("name") or after.get("id")
    out = []
    for row in store.list_bindings(user_id):
        if row["playbook_id"] != after["id"]:
            continue
        handle = _handle(row["session_id"])
        note = [
            f"The user edited playbook **{name}** (`{after['id']}`), which {handle} runs: "
            + ", ".join(changed) + ".",
            f"{handle} gets the new brief by itself at its next turn; a turn already running keeps the old "
            f"one. Full new brief: `python -m api.orchestrator brief {handle}`.",
        ]
        out += [{"session": handle, **r} for r in resync_supervision(row["session_id"], previous, current,
                                                                      note_lines=note, source=f"playbook {name}",
                                                                      db=db)]
    return out


def delete_playbook(user_id: int, playbook_id: str) -> bool:
    return store.delete_playbook(user_id, str(playbook_id or "").strip().lower())


def _session_owner(session_id: int, db) -> int:
    row = db.get_chat_session_by_id(int(session_id))
    if not row:
        raise OrchestratorError(f"chat {_handle(session_id)} not found")
    return int(row["user_id"])


def bind(session: Any, playbook_id: str = "", goal: str = "", *, db=None,
         user_id: Optional[int] = None, source: str = "cli") -> Dict[str, Any]:
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
    previous = store.binding(sid)
    if previous and previous["playbook_id"] != doc["id"]:
        # Rebinding to another playbook is a switch: same path as the chip.
        rebind(sid, owner, playbook_id=doc["id"], goal=goal, db=db, source=source)
        return brief(sid, db=db)
    store.bind(sid, owner, doc["id"], str(goal or "").strip()[:2000],
               action="goal" if previous else "bind", source=source)
    return brief(sid, db=db)


def unbind(session: Any, *, db=None, user_id: Optional[int] = None, source: str = "cli") -> bool:
    db = _db(db)
    sid = parse_session(session)
    if user_id is not None and _session_owner(sid, db) != int(user_id):
        raise OrchestratorError("that chat belongs to another account")
    return store.unbind(sid, source=source)


def get_binding(session: Any, *, user_id: Optional[int] = None, db=None) -> Dict[str, Any]:
    """The chat's binding as the header chip shows it (``bound: False`` when none)."""
    db = _db(db)
    sid = parse_session(session)
    owner = _session_owner(sid, db)
    if user_id is not None and owner != int(user_id):
        raise OrchestratorError("that chat belongs to another account")
    row = store.binding(sid)
    if not row:
        return {"session": _handle(sid), "bound": False}
    try:
        doc = get_playbook(owner, row["playbook_id"], db=db)
        name, missing = doc.get("name") or doc["id"], False
    except OrchestratorError:
        name, missing = row["playbook_id"], True
    return {"session": _handle(sid), "bound": True, "playbook_id": row["playbook_id"],
            "playbook_name": name, "playbook_missing": missing, "goal": row["goal"],
            "bound_at": row["bound_at"], "agents": team(sid, db=db),
            "hold": row.get("hold") or "", "hold_at": row.get("hold_at")}


TEAM_LIMIT = 12


def team(session_id: int, *, db=None, limit: int = TEAM_LIMIT) -> List[Dict[str, Any]]:
    """The orchestrator's sub-agent children, newest first and running ones on top,
    one row per child chat: what the header chip's popover shows as agent chips."""
    try:
        from api.subagents import store as sub_store

        batches = sub_store.list_batches_for_parent(_db(db), int(session_id))
    except Exception:
        return []
    out, seen = [], set()
    for batch in batches:
        for child in sorted(batch.children, key=lambda c: c.sort_index):
            if child.session_id in seen:
                continue
            seen.add(child.session_id)
            out.append({"handle": _handle(child.session_id), "name": child.display_name or child.label,
                        "avatar": child.avatar, "agent": child.agent, "model": child.model,
                        "effort": child.effort, "status": child.status,
                        "profile": child.profile_id, "finished_at": child.finished_at})
        if len(out) >= limit * 2:
            break
    live = ("pending", "running")
    out.sort(key=lambda r: r["status"] not in live)  # stable: newest-first within each group
    return out[:limit]


def rebind(session: Any, user_id: int, *, playbook_id: Optional[str] = None, goal: Optional[str] = None,
           db=None, source: str = "ui") -> Dict[str, Any]:
    """Switch a bound chat's playbook and/or edit its goal; ``None`` keeps a field.

    The agent gets the new brief at its next turn (the resume delta diffs it); a
    turn already running keeps the old one. A playbook switch also carries the
    new playbook's supervisor settings into each supervision schedule on the
    chat, exactly like a playbook edit (``resync_supervision``)."""
    db = _db(db)
    sid = parse_session(session)
    owner = _session_owner(sid, db)
    if int(user_id) != owner:
        raise OrchestratorError("that chat belongs to another account")
    row = store.binding(sid)
    if not row:
        raise OrchestratorError(f"{_handle(sid)} is not orchestrating; start it with /orchestrate")
    new_goal = row["goal"] if goal is None else str(goal).strip()[:2000]
    new_pid = row["playbook_id"] if playbook_id in (None, "") else str(playbook_id).strip().lower().lstrip("@")
    doc = get_playbook(owner, new_pid, db=db)
    if doc.get("missing_profiles"):
        raise OrchestratorError(f"playbook {doc['id']!r} references missing profile(s): "
                                + ", ".join(doc["missing_profiles"]))
    switched = doc["id"] != row["playbook_id"]
    if not switched and new_goal == row["goal"]:
        return {**get_binding(sid, db=db), "changed": []}
    store.bind(sid, owner, doc["id"], new_goal, action="switch" if switched else "goal", source=source)
    changed = (["playbook"] if switched else []) + (["goal"] if new_goal != row["goal"] else [])
    resynced = []
    if switched:
        try:
            old_doc = get_playbook(owner, row["playbook_id"], db=db)
        except OrchestratorError:
            old_doc = {}
        from api.chat_ops.service import resync_supervision

        profiles = profile_map(owner, db=db)
        handle = _handle(sid)
        note = [f"The user switched {handle} from playbook `{row['playbook_id']}` to "
                f"**{doc.get('name') or doc['id']}** (`{doc['id']}`).",
                f"{handle} gets the new brief by itself at its next turn; a turn already running keeps the old "
                f"one. Full new brief: `python -m api.orchestrator brief {handle}`."]
        resynced = resync_supervision(sid, supervisor_config(old_doc, profiles) if old_doc else {},
                                      supervisor_config(doc, profiles), note_lines=note,
                                      source=f"playbook {doc.get('name') or doc['id']}", db=db)
    return {**get_binding(sid, db=db), "changed": changed, "supervision": resynced}


_COMMAND_ARGS = re.compile(r"^\s*(?:@([a-z][a-z0-9_-]{0,39})\b)?\s*(.*)$", re.S | re.I)


def bind_from_command(session_id: Any, args: str, *, db=None) -> Dict[str, Any]:
    """Host step for a typed ``/orchestrate [@playbook] <goal>``: bind before the agent runs.

    Returns ``{"note": …}`` to append to the agent prompt, or ``{"reply": …}``
    for a terminal Cuttle reply (orchestrator off, unknown playbook, no goal on
    an unbound chat). The agent never runs the bind itself."""
    if not enabled():
        return {"reply": "Orchestrator is off. Enable it in Settings → Experimental."}
    db = _db(db)
    sid = parse_session(session_id)
    owner = _session_owner(sid, db)
    m = _COMMAND_ARGS.match(args or "")
    pid, goal = (m.group(1) or "").lower(), (m.group(2) or "").strip()
    current = store.binding(sid)
    if not goal and not current:
        return {"reply": "Give the goal: `/orchestrate [@playbook] <goal>`, or use ☰ → **Orchestrate this chat…**."}
    try:
        if current:
            out = rebind(sid, owner, playbook_id=pid or None, goal=goal or None, db=db, source="command")
        else:
            bind(sid, pid, goal, db=db, user_id=owner, source="command")
            out = get_binding(sid, db=db)
    except (OrchestratorError, PlaybookError) as exc:
        return {"reply": f"Could not start orchestration: {exc}. Check Apps → Orchestrator / Apps → Agents."}
    return {"note": f"Cuttle bound this chat to playbook **{out['playbook_name']}** (`{out['playbook_id']}`) "
                    f"before this turn. Goal: {out['goal'] or '(none)'}. The playbook brief is in your context."}


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


# ── Budget-aware tier picks (sub-agent spawns) ─────────────────────────────


def _bound_playbook(session_id: int, db) -> Optional[Dict[str, Any]]:
    """The playbook bound to this chat or its nearest bound ancestor (a child of an
    orchestrator spawning its own children still follows the orchestrator's ladder)."""
    sid, seen = int(session_id), set()
    while sid and sid not in seen and len(seen) < 8:
        seen.add(sid)
        row = store.binding(sid)
        sess = db.get_chat_session_by_id(sid) or {}
        if row and sess:
            try:
                return get_playbook(int(sess["user_id"]), row["playbook_id"], db=db) | {"_owner": int(sess["user_id"])}
            except OrchestratorError:
                return None
        sid = int(sess.get("parent_session_id") or 0)
    return None


def _member_name(member: Dict[str, Any], profiles: Dict[str, Dict[str, Any]]) -> str:
    if not member.get("profile"):
        return str(member.get("name") or member.get("agent") or "temporary agent")
    p = profiles.get(member["profile"])
    return str((p or {}).get("name") or member["profile"])


def _member_harness(member: Dict[str, Any], profiles: Dict[str, Dict[str, Any]]) -> str:
    from api.subagents.profiles import normalize_harness

    src = member if not member.get("profile") else profiles.get(member["profile"]) or {}
    return normalize_harness(src.get("agent"))


def member_states(members: List[Dict[str, Any]], profiles: Dict[str, Dict[str, Any]], *,
                  accounts: Any) -> List[tuple]:
    """(state, why) per member — see ``api.agent_router.availability``. A profile on
    "any harness" (or a missing one) reads ``unknown`` and keeps its place."""
    from api.agent_router.availability import target_state
    from api.agent_router.types import ExecutionTarget

    out = []
    for m in members:
        agent = _member_harness(m, profiles)
        if agent in ("", "none"):
            out.append(("unknown", ""))
            continue
        src = m if not m.get("profile") else profiles.get(m["profile"]) or {}
        out.append(target_state(ExecutionTarget(agent, str(src.get("model") or "")),
                                accounts=accounts, verbose=True))
    return out


def pick_tier_member(session: Any, tier_name: str, *, db=None, accounts: Any = None) -> Dict[str, Any]:
    """The tier member a spawn should run: the first switched-on one whose account
    can run (out-of-budget ones skipped, nearly-spent ones behind healthy ones when
    router budget awareness is on). Raises OrchestratorError — before any child
    chat exists — when the chat has no playbook, the tier is unknown, or every
    member is blocked."""
    db = _db(db)
    sid = parse_session(session)
    doc = _bound_playbook(sid, db)
    if not doc:
        raise OrchestratorError(
            f"{_handle(sid)} is not bound to an orchestrator playbook; spawn with a profile instead of a tier")
    tier = find_tier(doc, tier_name)
    if not tier:
        names = ", ".join(str(t.get("id")) for t in doc.get("tiers") or [])
        raise OrchestratorError(f"playbook {doc.get('id')!r} has no tier {tier_name!r} (tiers: {names})")
    label = str(tier.get("label") or tier.get("id"))
    members = active_members(tier)
    if not members:
        raise OrchestratorError(f"every agent in the {label} tier is switched off")
    profiles = profile_map(doc["_owner"], db=db)
    if accounts is None:
        from api.agent_router.budget import cached_snapshot

        accounts = cached_snapshot()
    states = member_states(members, profiles, accounts=accounts)
    idx = pick_member([st for st, _ in states])
    if idx is None:
        detail = "; ".join(f"{_member_name(m, profiles)}: {why or 'blocked'}" for m, (_, why) in zip(members, states))
        raise OrchestratorError(
            f"every agent in the {label} tier is out of budget ({detail}). "
            "No chat was created; escalate to the next tier or wait for a reset.")
    member = members[idx]
    return {
        "tier": str(tier.get("id")), "tier_label": label,
        "profile": spawn_profile(member), "name": _member_name(member, profiles),
        "pinned": _member_harness(member, profiles) not in ("", "none"),
        "skipped": [{"name": _member_name(m, profiles), "state": st, "why": why}
                    for m, (st, why) in zip(members[:idx], states[:idx])],
    }


def runnable_alternative(session: Any, *, profile_id: str = "", agent: str = "", model: str = "",
                         effort: str = "", db=None, accounts: Any = None) -> Optional[Dict[str, Any]]:
    """For a blocked child spawned by name: its tier and the member a tier spawn would
    run instead (None when the chat has no playbook or the agent is in no tier)."""
    try:
        db = _db(db)
        doc = _bound_playbook(parse_session(session), db)
        hit = doc and tier_of(doc, profile_id=profile_id, agent=agent, model=model, effort=effort)
        if not hit:
            return None
        return pick_tier_member(session, hit["tier"], db=db, accounts=accounts)
    except OrchestratorError:
        return None


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


def mark_orchestrators(sessions: List[Dict[str, Any]], user_id: int) -> None:
    """Set ``orchestrator`` on session-list rows bound to a playbook."""
    bound = {int(b["session_id"]) for b in store.list_bindings(user_id)} if enabled() else set()
    for row in sessions:
        try:
            row["orchestrator"] = int(row.get("id")) in bound
        except (TypeError, ValueError):
            row["orchestrator"] = False


def brief_block(chat_session_id: Any) -> str:
    """Context Compiler hook: the bound chat's brief, or ``""``. Never raises."""
    try:
        if not enabled() or chat_session_id in (None, ""):
            return ""
        sid = parse_session(chat_session_id)
        if store.binding(sid) is None:
            return ""
        text = brief(sid)["brief"].strip()
        from api.orchestrator.control import hold_note
        note = hold_note(sid)
        return f"{note}\n\n{text}" if note else text
    except Exception:
        return ""
