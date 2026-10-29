"""Hot-swap handoff: Cuttle-owned transcript deltas between agents.

Each harness agent keeps its own native resume id for a chat. Cuttle tracks,
per chat, the newest message each agent has seen (its *seen cursor*) and the
workspace it last ran in. Before a turn the target agent gets every message
after its cursor that it did not produce itself — turns answered by other
agents, plain LLM / router replies, or failed turns — within a char budget
(older rows are counted as omitted with a ``chat_cli`` pointer, never dropped
silently). Agents without native resume get the recent conversation every turn.
When only a recent window is sent, the user's earlier messages ride along in a
compact section: they hold standing instructions the incoming agent never saw.

A CLI resume id is keyed by workspace, so switching the chat's project keeps
the same agent but silently drops its native session. The seen cursor would
then read as "caught up" and the agent would lose the whole conversation, so a
project transition forces the recent transcript back in (``project_changed`` in
``build_handoff``). This is the one case worse than an agent switch, where the
incoming agent has no cursor and already gets the transcript.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import runtime_state_path
from api.cuttle_brain.key_store import KeyStore


_lock = threading.Lock()



def _map_file() -> Path:
    return runtime_state_path("sessions", "harness_last_agent_map.json")


def _sid_key(chat_session_id: Any) -> Optional[str]:
    if chat_session_id is None:
        return None
    s = str(chat_session_id).strip()
    return s or None


def _store() -> KeyStore:
    return KeyStore(_map_file())


def _load_all() -> Dict[str, Any]:
    """Maintenance-only enumeration; normal turns use indexed keys."""
    return _store().all()


def _write_all(data: Dict[str, Any]) -> None:
    _store().replace_all(data)


def _entry(chat_session_id: Any) -> Dict[str, Any]:
    """Normalized state for one chat.

    ``{"last_agent": str|None, "seen": {agent: id}, "projects": {agent: path}}``.

    Legacy rows stored only the last agent id as a bare string; they read as
    a state with no seen cursors or project records (``legacy`` True).
    """
    key = _sid_key(chat_session_id)
    if not key:
        return {"last_agent": None, "seen": {}, "projects": {}, "legacy": False}
    with _lock:
        raw = _store().get(key)
    if isinstance(raw, str):
        return {"last_agent": raw.strip() or None, "seen": {}, "projects": {}, "legacy": True}
    if not isinstance(raw, dict):
        return {"last_agent": None, "seen": {}, "projects": {}, "legacy": False}
    last = raw.get("last_agent")
    seen = raw.get("seen") if isinstance(raw.get("seen"), dict) else {}
    clean: Dict[str, int] = {}
    for aid, mid in seen.items():
        try:
            clean[str(aid)] = int(mid)
        except (TypeError, ValueError):
            continue
    projects_raw = raw.get("projects") if isinstance(raw.get("projects"), dict) else {}
    projects: Dict[str, str] = {}
    for aid, path in projects_raw.items():
        p = str(path or "").strip()
        if p:
            projects[str(aid)] = p
    return {
        "last_agent": (str(last).strip() or None) if last else None,
        "seen": clean,
        "projects": projects,
        "legacy": False,
    }


def get_last_agent(chat_session_id: Any) -> Optional[str]:
    return _entry(chat_session_id).get("last_agent")


def get_seen_cursor(chat_session_id: Any, agent_id: str) -> Optional[int]:
    """Newest chat message id ``agent_id`` saw in this chat (None if never)."""
    return _entry(chat_session_id)["seen"].get((agent_id or "").strip())


def get_last_project(chat_session_id: Any, agent_id: str) -> Optional[str]:
    """Workspace ``agent_id`` last ran this chat in (None if never recorded)."""
    return _entry(chat_session_id)["projects"].get((agent_id or "").strip())


def _norm_project(path: Optional[str]) -> str:
    text = (path or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).resolve()).lower()
    except OSError:
        return text.lower()


def _same_project(left: Optional[str], right: Optional[str]) -> bool:
    """Same workspace, including ``src``/``source`` aliases (owner: ``cwd``)."""
    if not left or not right:
        return False
    try:
        from api.agent_harness.cwd import same_project

        return bool(same_project(left, right))
    except Exception:
        return _norm_project(left) == _norm_project(right)


def _latest_message_id(chat_session_id: Any) -> int:
    sid = _numeric_session_id(chat_session_id)
    if sid is None:
        return 0
    try:
        from api.auth_db import get_auth_db

        rows = get_auth_db().get_messages(sid, limit=1)
    except Exception:
        return 0
    try:
        return int(rows[0].get("id") or 0) if rows else 0
    except (TypeError, ValueError, AttributeError):
        return 0


def record_last_agent(
    chat_session_id: Any,
    agent_id: str,
    *,
    through_message_id: Optional[int] = None,
    project_path: Optional[str] = None,
) -> None:
    """Record a completed agent turn: last agent + that agent's seen cursor.

    Call after a successful turn that actually reached the CLI (never for
    meta/settings commands). The cursor defaults to the newest stored chat
    message — the turn's own user row plus any steers persisted mid-run; the
    agent's reply lands after it and is skipped as its own on the next build.

    ``project_path`` records the workspace the turn ran in, so a later turn in
    a *different* project can detect that the CLI's per-cwd resume id does not
    cover this conversation and resend the transcript (see ``build_handoff``).
    """
    key = _sid_key(chat_session_id)
    aid = (agent_id or "").strip()
    if not key or not aid:
        return
    cursor = (
        int(through_message_id)
        if through_message_id is not None
        else _latest_message_id(chat_session_id)
    )
    proj = _norm_project(project_path)

    def change(raw):
        seen = dict(raw["seen"]) if isinstance(raw, dict) and isinstance(raw.get("seen"), dict) else {}
        seen[aid] = cursor
        projects = {}
        if isinstance(raw, dict) and isinstance(raw.get("projects"), dict):
            projects = {str(k): str(v) for k, v in raw["projects"].items() if v}
        if proj:
            projects[aid] = proj
        return {"last_agent": aid, "seen": seen, "projects": projects}

    with _lock:
        _store().update(key, change)


def clear_last_agent(chat_session_id: Any) -> None:
    """Forget handoff state for a chat (all key spellings)."""
    from api.cuttle_brain.context_delta import sid_variants

    keys = sid_variants(chat_session_id)
    if not keys:
        return
    with _lock:
        _store().delete(keys)


@dataclass(frozen=True)
class AgentHandoff:
    from_agent: str
    to_agent: str
    text: str
    message_count: int = 0
    truncated_count: int = 0
    # Why this transcript was injected: "switch" (agent changed; its native
    # resume covers turns before its cursor), "join" (first turn of this agent
    # in the chat: no native session here at all), "reset" (same agent,
    # project/workspace changed so the per-cwd resume is gone), or "history"
    # (agent keeps no native session). Consumers use it to keep conversation
    # out of briefing-size metrics.
    mode: str = "switch"



def _numeric_session_id(chat_session_id: Any) -> Optional[int]:
    if chat_session_id is None:
        return None
    if isinstance(chat_session_id, int):
        return chat_session_id
    s = str(chat_session_id).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    # db_session_148 / CH-000148 style
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits:
        try:
            return int(digits.lstrip("0") or "0") or None
        except ValueError:
            return None
    return None


def _strip_injected_blocks(content: str) -> str:
    text = content or ""
    try:
        from api.cuttle_ui_capabilities import strip_cuttle_ui_capabilities

        text = strip_cuttle_ui_capabilities(text)
    except Exception:
        pass
    try:
        from api.cuttle_brain.context_compiler import strip_cuttle_context

        text = strip_cuttle_context(text)
    except Exception:
        pass
    # Never forward another model's raw reasoning to the next harness: the
    # postprocessing wrapper keeps the unedited reply (often with <think>),
    # and provider safeguards reject prompts carrying foreign chain-of-thought.
    from api.transcript_text import agent_visible_text

    return agent_visible_text(text)


# Handoff budget: newest messages win; older ones are listed as omitted with a
# pointer to the full transcript instead of being dropped silently.
_MAX_MESSAGE_CHARS = 1500
_MAX_HANDOFF_CHARS = 12000
_RECENT_WINDOW = 40
# Earlier user rows outside the window still carry standing instructions
# ("use sub-agents", "don't ask me", reviewer choice); an agent without the
# conversation in its own session would otherwise never see them.
_EARLIER_USER_MESSAGE_CHARS = 500
_EARLIER_USER_CHARS = 4000
_EARLIER_USER_SCAN = 2000


def fetch_recent_transcript(
    chat_session_id: Any,
    *,
    limit: Optional[int] = _RECENT_WINDOW,
    after_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Chronological user/assistant turns from Cuttle's neutral chat store.

    ``after_id`` returns every message newer than that id; otherwise the
    newest ``limit`` messages. Rows keep their ``id``.
    """
    sid = _numeric_session_id(chat_session_id)
    if sid is None:
        return []
    try:
        from api.auth_db import get_auth_db

        db = get_auth_db()
        if after_id is not None:
            rows = db.get_messages(sid, after_id=int(after_id))
        else:
            rows = db.get_messages(sid, limit=limit)
    except Exception:
        return []
    rows = sorted(rows, key=lambda r: int(r.get("id") or 0))
    out: List[Dict[str, Any]] = []
    for row in rows:
        role = str(row.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = _strip_injected_blocks(str(row.get("content") or ""))
        if not content:
            continue
        out.append({"id": row.get("id"), "role": role, "content": content})
    return out


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _older_visible_count(chat_session_id: Any, oldest_id: Any) -> int:
    """Visible rows older than the oldest fetched row.

    The window-limited paths fetch only the newest ``limit`` rows, so turns
    before that window are never seen by ``_fit_budget`` and would otherwise
    be dropped without the ``chat_cli`` pointer. Best-effort (0 on any error);
    ``message_page_meta`` already counts non-system rows the same way the
    rendered transcript does.
    """
    sid = _numeric_session_id(chat_session_id)
    if sid is None or oldest_id is None:
        return 0
    try:
        from api.auth_db import get_auth_db

        meta = get_auth_db().message_page_meta(sid, int(oldest_id))
        return int(meta.get("older_visible_count") or 0)
    except Exception:
        return 0


def _drop_current_turn(
    messages: List[Dict[str, Any]], current_prompt: Optional[str]
) -> List[Dict[str, Any]]:
    """Drop the trailing user row when it is the turn being sent right now.

    The user row is persisted before the agent runs, so without this the
    prompt would appear twice (handoff + ``## User request``).
    """
    if not messages or not current_prompt:
        return messages
    last = messages[-1]
    if last.get("role") != "user":
        return messages
    probe = _norm(current_prompt)[:80]
    stored = _norm(str(last.get("content") or ""))
    if probe and probe in stored:
        return messages[:-1]
    return messages


def _skip_own_reply(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows right after an agent's cursor that are replies are its own answer."""
    i = 0
    while i < len(messages) and messages[i].get("role") == "assistant":
        i += 1
    return messages[i:]


def _fit_budget(
    messages: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int, int]:
    """Keep the newest messages within the char budget; return (kept, omitted, truncated).

    ``truncated`` counts only kept rows whose content was cut to
    ``_MAX_MESSAGE_CHARS`` — rows discarded by the total budget count as
    omitted, never as truncated.
    """
    kept: List[Dict[str, Any]] = []
    used = 0
    truncated = 0
    for msg in reversed(messages):
        content = str(msg.get("content") or "")
        was_truncated = len(content) > _MAX_MESSAGE_CHARS
        if was_truncated:
            content = content[: _MAX_MESSAGE_CHARS - 3].rstrip() + "..."
        if kept and used + len(content) > _MAX_HANDOFF_CHARS:
            break
        if was_truncated:
            truncated += 1
        kept.append({**msg, "content": content})
        used += len(content)
    kept.reverse()
    return kept, len(messages) - len(kept), truncated


def _earlier_user_messages(
    chat_session_id: Any, before_id: Any
) -> tuple[List[Dict[str, Any]], int]:
    """User rows older than ``before_id``, oldest first, within a small budget.

    Returns (kept, omitted). Each row is cut to ``_EARLIER_USER_MESSAGE_CHARS``;
    native control commands (``/restart``) carry no instructions and are skipped.
    """
    sid = _numeric_session_id(chat_session_id)
    if sid is None or before_id is None:
        return [], 0
    try:
        from api.auth_db import get_auth_db

        rows = get_auth_db().get_messages(
            sid, before_id=int(before_id), limit=_EARLIER_USER_SCAN
        )
    except Exception:
        return [], 0
    users: List[Dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: int(r.get("id") or 0)):
        if str(row.get("role") or "").strip().lower() != "user":
            continue
        content = _strip_injected_blocks(str(row.get("content") or ""))
        if not content or re.match(r"^/restart\b", content):
            continue
        users.append({"id": row.get("id"), "role": "user", "content": content})
    kept: List[Dict[str, Any]] = []
    used = 0
    for msg in users:
        content = msg["content"]
        if len(content) > _EARLIER_USER_MESSAGE_CHARS:
            content = content[: _EARLIER_USER_MESSAGE_CHARS - 3].rstrip() + "..."
        if kept and used + len(content) > _EARLIER_USER_CHARS:
            break
        kept.append({**msg, "content": content})
        used += len(content)
    return kept, len(users) - len(kept)


def _chat_handle(chat_session_id: Any) -> Optional[str]:
    sid = _numeric_session_id(chat_session_id)
    return f"CH-{sid:06d}" if sid is not None else None


def format_handoff_delta(
    *,
    from_agent: str,
    to_agent: str,
    messages: List[Dict[str, Any]],
    omitted: int = 0,
    truncated: int = 0,
    chat_handle: Optional[str] = None,
    mode: str = "switch",
    earlier_user: Optional[List[Dict[str, Any]]] = None,
    earlier_user_omitted: int = 0,
) -> str:
    if mode == "join":
        lines = ["## Conversation so far (Cuttle transcript)"]
        if from_agent and from_agent != to_agent:
            lines.append(f"Sticky agent changed: `{from_agent}` → `{to_agent}`.")
        lines.append(
            f"`{to_agent}` has not run in this chat before, so its own session "
            "holds none of it. This transcript is the conversation it is "
            "continuing — treat it as prior context, not a fresh request, and "
            "keep following the user's standing instructions in it."
        )
    elif mode == "history":
        lines = [
            "## Conversation so far (Cuttle transcript)",
            (
                f"`{to_agent}` keeps no native session for this chat, so this is "
                "the recent conversation it is continuing."
            ),
        ]
    elif mode == "reset":
        lines = [
            "## Conversation so far (Cuttle transcript)",
            (
                "This chat moved to a different project/workspace, and "
                f"`{to_agent}`'s native CLI session is per-workspace, so the "
                "earlier turns are not in its session. Here is the recent "
                "conversation it is continuing — treat it as prior context, "
                "not a fresh request."
            ),
        ]
    else:
        lines = ["## Agent handoff (Cuttle transcript)"]
        if from_agent and from_agent != to_agent:
            lines.append(f"Sticky agent changed: `{from_agent}` → `{to_agent}`.")
        lines.append(
            "These chat messages happened since your last turn here (other "
            "agents, models, or interrupted turns). Prefer your native resume "
            "for everything before them."
        )
    if not messages:
        lines.append("(No recent Cuttle transcript rows available.)")
        return "\n".join(lines)
    # `get` alone returns a session summary; whole omitted rows need the
    # full transcript. `--full` defeats the CLI's default content cap
    # (get: 2000 chars, session: 500 chars) for long-tail recovery. One
    # pointer total: combined when both losses occur.
    cmd = (
        f"python -m api.chat_cli session {chat_handle} --all --json --full"
        if chat_handle
        else ""
    )
    if omitted > 0 and truncated > 0:
        where = f" — full transcript: `{cmd}`" if cmd else ""
        lines.append(
            f"({omitted} earlier messages not shown; "
            f"{truncated} message(s) truncated to {_MAX_MESSAGE_CHARS} chars{where}.)"
        )
    elif omitted > 0:
        where = f" — full transcript: `{cmd}`" if cmd else ""
        lines.append(f"({omitted} earlier messages not shown{where}.)")
    elif truncated > 0:
        where = f" — full text: `{cmd}`" if cmd else ""
        lines.append(
            f"({truncated} message(s) truncated to {_MAX_MESSAGE_CHARS} chars{where}.)"
        )
    if earlier_user:
        lines.append("")
        lines.append(
            "### Earlier user messages (oldest first; may hold standing instructions)"
        )
        for msg in earlier_user:
            content = (msg.get("content") or "").replace("\n", " ").strip()
            lines.append(f"- {content}")
        if earlier_user_omitted > 0:
            lines.append(f"- … {earlier_user_omitted} more earlier user message(s) not shown.")
        lines.append("")
        lines.append("### Recent conversation")
    lines.append("")
    for msg in messages:
        role = msg.get("role") or "unknown"
        content = (msg.get("content") or "").replace("\n", " ").strip()
        lines.append(f"- **{role}**: {content}")
    return "\n".join(lines)


def build_handoff(
    chat_session_id: Any,
    *,
    to_agent: str,
    from_agent: Optional[str] = None,
    limit: int = _RECENT_WINDOW,
    current_prompt: Optional[str] = None,
    full_history: bool = False,
    project_path: Optional[str] = None,
    has_native_session: Optional[bool] = None,
) -> Optional[AgentHandoff]:
    """Chat messages ``to_agent`` has not seen yet; None when it is caught up.

    * ``full_history`` (agents without native resume): the recent
      conversation every turn — the agent remembers nothing between turns.
    * Same agent, **different project**: the CLI resume id is keyed by
      workspace, so the agent's native session does not cover the new
      project. Its seen cursor cannot be trusted as "caught up" — resend the
      recent transcript (its own prior replies included) under a reset notice.
    * Agent with a seen cursor: every message after it, minus its own reply
      to that turn. Covers plain-LLM / router turns and settings commands,
      which never move any agent's cursor.
    * No cursor yet (first turn of this agent in the chat): the recent
      window under a "join" notice (its own session holds none of the chat),
      unless a legacy record says this agent already was last.

    Whenever only a window is sent, earlier user rows outside it follow in a
    compact section, so standing instructions survive the handoff.
    The trailing user row for ``current_prompt`` is excluded either way.
    """
    target = (to_agent or "").strip()
    if not target:
        return None
    state = _entry(chat_session_id)
    previous = (
        from_agent if from_agent is not None else state.get("last_agent") or ""
    ).strip()
    cursor = state["seen"].get(target)
    prev_project = state["projects"].get(target)
    project_changed = bool(
        cursor is not None
        and (from_agent is None or previous == target)
        and prev_project
        and project_path
        and not _same_project(prev_project, project_path)
    )
    mode = "switch"
    # Window-limited paths fetch only the newest ``limit`` rows; turns before
    # that window must still be reported as omitted (with the chat_cli pointer)
    # rather than silently dropped. The cursor path fetches everything since the
    # cursor, so its head is covered and needs no such count.
    window_limited = False
    if full_history:
        mode = "history"
        messages = fetch_recent_transcript(chat_session_id, limit=limit)
        window_limited = True
    elif project_changed:
        mode = "reset"
        messages = fetch_recent_transcript(chat_session_id, limit=limit)
        window_limited = True
    elif cursor is not None and from_agent is None:
        messages = _skip_own_reply(
            fetch_recent_transcript(chat_session_id, after_id=cursor)
        )
    else:
        if from_agent is None and (not previous or previous == target) and state.get("legacy"):
            return None
        if from_agent is not None and (not previous or previous == target):
            return None
        if cursor is None and not state.get("legacy") and not has_native_session:
            mode = "join"
        messages = fetch_recent_transcript(chat_session_id, limit=limit)
        window_limited = True
    messages = _drop_current_turn(messages, current_prompt)
    if not messages:
        return None
    head_dropped = (
        _older_visible_count(chat_session_id, messages[0].get("id"))
        if window_limited
        else 0
    )
    kept, omitted, truncated = _fit_budget(messages)
    earlier_user: List[Dict[str, Any]] = []
    earlier_user_omitted = 0
    if window_limited and kept and (omitted or head_dropped):
        earlier_user, earlier_user_omitted = _earlier_user_messages(
            chat_session_id, kept[0].get("id")
        )
    text = format_handoff_delta(
        from_agent=previous,
        to_agent=target,
        messages=kept,
        omitted=omitted + head_dropped,
        truncated=truncated,
        chat_handle=_chat_handle(chat_session_id),
        mode=mode,
        earlier_user=earlier_user,
        earlier_user_omitted=earlier_user_omitted,
    )
    return AgentHandoff(
        from_agent=previous,
        to_agent=target,
        text=text,
        message_count=len(kept),
        truncated_count=truncated,
        mode=mode,
    )
