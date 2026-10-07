"""Transport-neutral chat-turn envelope + agent-selection seam (Phase 5 P5-A).

Owns the pure request normalization and selection decisions that used to
live inline in the ``/api/chat`` route head and in
``process_message_with_bot``:

- ``normalize_chat_post`` — validate/shape the raw JSON body.
- ``classify_selection`` — native control vs harness slash vs router,
  with narrow injected matchers (no Flask, no DB, no runners).
- ``build_turn_context`` — the ``user_context``/``session_data`` dicts.
- ``split_db_session_id`` — ``db_session_<int>`` parsing for routing.

The Flask entry keeps authenticate/validate/resolve/call/serialize; the
coordinator core (execution, persistence, delivery — P5-B) consumes these
shapes. Behavior is verbatim from the pre-extraction call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


def strip_invisible_leading(s: str) -> str:
    """Remove BOM / ZW* chars that break slash-command detection."""
    if not isinstance(s, str):
        return s
    return s.lstrip('\ufeff\u200b\u200c\u200d\u2060')


def parse_stream_flag(value: Any) -> bool:
    """The ``stream`` body flag: only explicit falsy forms disable SSE."""
    if value is False:
        return False
    if isinstance(value, str) and value.strip().lower() in ('false', '0', 'no', 'off'):
        return False
    return True


@dataclass
class TurnRequest:
    """Normalized ``/api/chat`` POST body (validation errors as data)."""

    message: str = ""
    attachments: List[Any] = field(default_factory=list)
    session_id: Any = None
    wants_stream: bool = True
    error: Optional[str] = None


def normalize_chat_post(data: Optional[dict]) -> TurnRequest:
    """Shape the raw route body; ``error`` carries the 400 cases."""
    if not data or 'message' not in data:
        return TurnRequest(error='No message provided')
    message = strip_invisible_leading(data.get('message') or '').strip()
    attachments = data.get('attachments') or []
    if not message and not attachments:
        return TurnRequest(error='Empty message')
    return TurnRequest(
        message=message,
        attachments=attachments,
        session_id=data.get('session_id'),
        wants_stream=parse_stream_flag(data.get('stream', True)),
    )


@dataclass
class TurnSelection:
    """Agent-selection decision. ``kind`` selects the workflow arm."""

    kind: str  # restart | harness | harness_empty_prompt | router
    agent_id: Optional[str] = None
    prompt: Optional[str] = None
    block_message: Optional[str] = None


def classify_selection(
    message: str,
    *,
    match_harness: Callable[[str], Optional[Tuple[str, str]]],
    is_restart: Callable[[str], bool],
) -> TurnSelection:
    """Decide the turn arm. Native control wins over harness matches."""
    if is_restart(message):
        return TurnSelection(kind='restart')
    matched = match_harness(message)
    if matched:
        agent_id, prompt = matched
        if not prompt:
            return TurnSelection(
                kind='harness_empty_prompt',
                agent_id=agent_id,
                block_message=f'❌ Please provide a prompt after /{agent_id}.',
            )
        return TurnSelection(kind='harness', agent_id=agent_id, prompt=prompt)
    return TurnSelection(kind='router')


def split_db_session_id(session_id: Any) -> Optional[int]:
    """Parse ``db_session_<int>`` (or a raw int) for router dispatch."""
    if isinstance(session_id, str) and session_id.startswith('db_session_'):
        try:
            return int(session_id.rsplit('_', 1)[-1])
        except ValueError:
            return None
    if isinstance(session_id, int):
        return session_id
    return None


def build_turn_context(
    session_id: Any,
    session_kind: Optional[str],
    routing_key: Optional[str],
    is_owner: bool,
    recent_messages: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Assemble the ``user_context``/``session_data`` dict pair."""
    import time as _time

    user_context = {
        'display_name': 'Web User',
        'id': session_id,
        'is_owner': is_owner,
        'username': 'web_user',
        'discriminator': '0',
        'session_id': session_id,
        'recent_messages': recent_messages,
        'web_ui': True,
    }
    sk = session_kind if session_kind is not None else 'web_anon'
    rk = routing_key if routing_key is not None else f'web_anon_{session_id}'
    session_data = {
        'session_id': session_id,
        'user_id': f'web_{session_id}',
        'platform': 'webchat',
        'timestamp': _time.time(),
        'session_kind': sk,
        'routing_key': rk,
    }
    return user_context, session_data
