"""Hot-swap handoff: Cuttle-owned transcript deltas between agents.

Each harness agent keeps its own native resume id for a chat. When the sticky
agent changes, we still prefer the *target* agent's resume (so we do not discard
its abilities), and inject a short neutral transcript delta covering recent
turns it may have missed.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


_lock = threading.Lock()


def _repo_root() -> Path:
    # .../Cuttle/src/api/cuttle_brain/this_file.py -> Cuttle
    return Path(__file__).resolve().parents[3]


def _map_file() -> Path:
    d = _repo_root() / "src" / "data" / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d / "harness_last_agent_map.json"


def _sid_key(chat_session_id: Any) -> Optional[str]:
    if chat_session_id is None:
        return None
    s = str(chat_session_id).strip()
    return s or None


def _load_all() -> Dict[str, Any]:
    path = _map_file()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_all(data: Dict[str, Any]) -> None:
    path = _map_file()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def get_last_agent(chat_session_id: Any) -> Optional[str]:
    key = _sid_key(chat_session_id)
    if not key:
        return None
    with _lock:
        raw = _load_all().get(key)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    return s or None


def record_last_agent(chat_session_id: Any, agent_id: str) -> None:
    key = _sid_key(chat_session_id)
    aid = (agent_id or "").strip()
    if not key or not aid:
        return
    with _lock:
        data = _load_all()
        data[key] = aid
        _write_all(data)


def clear_last_agent(chat_session_id: Any) -> None:
    key = _sid_key(chat_session_id)
    if not key:
        return
    with _lock:
        data = _load_all()
        if key in data:
            del data[key]
            _write_all(data)


@dataclass(frozen=True)
class AgentHandoff:
    from_agent: str
    to_agent: str
    text: str
    message_count: int = 0


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


def fetch_recent_transcript(
    chat_session_id: Any,
    *,
    limit: int = 12,
) -> List[Dict[str, str]]:
    """Return chronological recent turns from Cuttle's neutral chat store."""
    sid = _numeric_session_id(chat_session_id)
    if sid is None:
        return []
    try:
        from api.auth_db import get_auth_db

        rows = get_auth_db().get_messages(sid, limit=limit)
    except Exception:
        return []
    # get_messages with limit returns newest-first; normalize to chronological.
    if limit:
        rows = list(reversed(rows))
    out: List[Dict[str, str]] = []
    for row in rows:
        role = str(row.get("role") or "").strip().lower() or "unknown"
        content = _strip_injected_blocks(str(row.get("content") or ""))
        if not content:
            continue
        # Keep handoff compact.
        if len(content) > 1200:
            content = content[:1197].rstrip() + "..."
        out.append({"role": role, "content": content})
    return out


def format_handoff_delta(
    *,
    from_agent: str,
    to_agent: str,
    messages: List[Dict[str, str]],
) -> str:
    lines = [
        "## Agent handoff (Cuttle transcript)",
        (
            f"Sticky agent changed: `{from_agent}` → `{to_agent}`. "
            "Prefer your native resume for this chat when available; "
            "use the delta below for turns you may have missed."
        ),
    ]
    if not messages:
        lines.append("(No recent Cuttle transcript rows available.)")
        return "\n".join(lines)
    lines.append("")
    lines.append("Recent turns:")
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
    limit: int = 12,
) -> Optional[AgentHandoff]:
    """Build a handoff when the sticky agent changed; else None."""
    target = (to_agent or "").strip()
    if not target:
        return None
    previous = (from_agent if from_agent is not None else get_last_agent(chat_session_id) or "").strip()
    if not previous or previous == target:
        return None
    messages = fetch_recent_transcript(chat_session_id, limit=limit)
    text = format_handoff_delta(
        from_agent=previous,
        to_agent=target,
        messages=messages,
    )
    return AgentHandoff(
        from_agent=previous,
        to_agent=target,
        text=text,
        message_count=len(messages),
    )
