"""Hot-swap handoff: Cuttle-owned transcript deltas between agents.

Each harness agent keeps its own native resume id for a chat. Cuttle tracks,
per chat, the newest message each agent has seen (its *seen cursor*). Before a
turn the target agent gets every message after its cursor that it did not
produce itself — turns answered by other agents, plain LLM / router replies,
or failed turns — within a char budget (older rows are counted as omitted with
a ``chat_cli`` pointer, never dropped silently). Agents without native resume
get the recent conversation every turn.
"""

from __future__ import annotations

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
    """Normalized state for one chat: ``{"last_agent": str|None, "seen": {agent: id}}``.

    Legacy rows stored only the last agent id as a bare string; they read as
    a state with no seen cursors (``legacy`` True).
    """
    key = _sid_key(chat_session_id)
    if not key:
        return {"last_agent": None, "seen": {}, "legacy": False}
    with _lock:
        raw = _store().get(key)
    if isinstance(raw, str):
        return {"last_agent": raw.strip() or None, "seen": {}, "legacy": True}
    if not isinstance(raw, dict):
        return {"last_agent": None, "seen": {}, "legacy": False}
    last = raw.get("last_agent")
    seen = raw.get("seen") if isinstance(raw.get("seen"), dict) else {}
    clean: Dict[str, int] = {}
    for aid, mid in seen.items():
        try:
            clean[str(aid)] = int(mid)
        except (TypeError, ValueError):
            continue
    return {
        "last_agent": (str(last).strip() or None) if last else None,
        "seen": clean,
        "legacy": False,
    }


def get_last_agent(chat_session_id: Any) -> Optional[str]:
    return _entry(chat_session_id).get("last_agent")


def get_seen_cursor(chat_session_id: Any, agent_id: str) -> Optional[int]:
    """Newest chat message id ``agent_id`` saw in this chat (None if never)."""
    return _entry(chat_session_id)["seen"].get((agent_id or "").strip())


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
) -> None:
    """Record a completed agent turn: last agent + that agent's seen cursor.

    Call after a successful turn that actually reached the CLI (never for
    meta/settings commands). The cursor defaults to the newest stored chat
    message — the turn's own user row plus any steers persisted mid-run; the
    agent's reply lands after it and is skipped as its own on the next build.
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
    def change(raw):
        seen = dict(raw["seen"]) if isinstance(raw, dict) and isinstance(raw.get("seen"), dict) else {}
        seen[aid] = cursor
        return {"last_agent": aid, "seen": seen}

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
    return (text or "").strip()


# Handoff budget: newest messages win; older ones are listed as omitted with a
# pointer to the full transcript instead of being dropped silently.
_MAX_MESSAGE_CHARS = 1500
_MAX_HANDOFF_CHARS = 12000
_RECENT_WINDOW = 40


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
) -> str:
    if mode == "history":
        lines = [
            "## Conversation so far (Cuttle transcript)",
            (
                f"`{to_agent}` keeps no native session for this chat, so this is "
                "the recent conversation it is continuing."
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
) -> Optional[AgentHandoff]:
    """Chat messages ``to_agent`` has not seen yet; None when it is caught up.

    * ``full_history`` (agents without native resume): the recent
      conversation every turn — the agent remembers nothing between turns.
    * Agent with a seen cursor: every message after it, minus its own reply
      to that turn. Covers plain-LLM / router turns and settings commands,
      which never move any agent's cursor.
    * No cursor yet (first turn of this agent in the chat): the recent
      window, unless a legacy record says this agent already was last.

    The trailing user row for ``current_prompt`` is excluded either way.
    """
    target = (to_agent or "").strip()
    if not target:
        return None
    state = _entry(chat_session_id)
    previous = (
        from_agent if from_agent is not None else state.get("last_agent") or ""
    ).strip()
    mode = "switch"
    if full_history:
        mode = "history"
        messages = fetch_recent_transcript(chat_session_id, limit=limit)
    else:
        cursor = state["seen"].get(target)
        if cursor is not None and from_agent is None:
            messages = _skip_own_reply(
                fetch_recent_transcript(chat_session_id, after_id=cursor)
            )
        else:
            if from_agent is None and (not previous or previous == target) and state.get("legacy"):
                return None
            if from_agent is not None and (not previous or previous == target):
                return None
            messages = fetch_recent_transcript(chat_session_id, limit=limit)
    messages = _drop_current_turn(messages, current_prompt)
    if not messages:
        return None
    kept, omitted, truncated = _fit_budget(messages)
    text = format_handoff_delta(
        from_agent=previous,
        to_agent=target,
        messages=kept,
        omitted=omitted,
        truncated=truncated,
        chat_handle=_chat_handle(chat_session_id),
        mode=mode,
    )
    return AgentHandoff(
        from_agent=previous,
        to_agent=target,
        text=text,
        message_count=len(kept),
        truncated_count=truncated,
    )
