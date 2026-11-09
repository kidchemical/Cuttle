"""The turn id: the anchor that ties work to the agent turn that did it (owner).

Every agent turn has a query id from the moment it starts (``api.chat_turn_row``
opens the turn's row and inspector record under it). That id is the **turn id**:

- the harness kernel exports it to every agent CLI process, whatever the
  harness, as ``CUTTLE_TURN_ID`` — with ``CUTTLE_TURN_MESSAGE_ID`` /
  ``CUTTLE_TURN_MESSAGE`` naming the user bubble that started the turn
  (``core.agent_cli_env.agent_operation_context``), so a
  ``python -m api.subagents spawn`` / ``message`` run from inside a turn knows
  which turn it belongs to;
- every assistant row is stamped with ``metadata.turn_id`` when it is written
  (``AuthDatabase.add_message`` / ``add_message_once`` /
  ``update_message_content`` call :func:`stamp`), whatever path saves it;
- sub-agent batches and follow-up rounds store the turn id they were started
  in, and bind to the assistant row that carries the same turn id
  (``api.subagents.service.bind_turn_cards``) — never to "the next reply".

Manual CLI runs with no turn around them fall back to ``--anchor CH-…-N``,
else the parent's latest bubble.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

ENV = 'CUTTLE_TURN_ID'
KEY = 'turn_id'


def _bare(session_id: Any) -> str:
    s = str(session_id if session_id is not None else '').strip()
    s = s.removeprefix('db_session_')
    if s.upper().startswith('CH-'):
        s = s[3:].split('-', 1)[0].lstrip('0') or '0'
    return s


def _clean(value: Any) -> str:
    text = str(value or '').strip()
    return text if text and len(text) <= 64 else ''


def turn_id_of(metadata: Any) -> str:
    """The turn id a row's metadata names (explicit, the turn row's, or its query id)."""
    if not isinstance(metadata, dict):
        return ''
    return _clean(metadata.get(KEY) or metadata.get('turn_query_id') or metadata.get('query_id'))


def ambient(session_id: Any) -> str:
    """The turn running for ``session_id`` right now, as seen from this process.

    The chat's open turn row (Flask, the turn workflow) or — in an agent CLI
    process — ``CUTTLE_TURN_ID`` when that process is working for this chat."""
    try:
        from api.chat_turn_row import current

        entry = current(session_id)
        if entry and entry.get('query_id'):
            return _clean(entry['query_id'])
    except Exception:
        pass
    env_turn = _clean(os.environ.get(ENV))
    if env_turn and _bare(os.environ.get('CUTTLE_CHAT_SESSION_ID')) == _bare(session_id):
        return env_turn
    return ''


def stamp(session_id: Any, metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """``metadata`` with ``turn_id`` set when one is known (a copy; None stays None if none)."""
    tid = turn_id_of(metadata) or ambient(session_id)
    if not tid:
        return metadata
    out = dict(metadata or {})
    out[KEY] = tid
    return out


def for_spawn(parent_session_id: Any, explicit: Any = None) -> str:
    """The turn a ``spawn`` / ``message`` belongs to: explicit, else the calling turn."""
    return _clean(explicit) or ambient(parent_session_id)


def origin(session_id: Any) -> Dict[str, Any]:
    """The calling turn's identity for ``session_id``: turn id plus the user bubble
    that started it (``message_id`` row id, ``message`` ``CH-…-N``) when known.

    Same for every harness: the kernel exports ``CUTTLE_TURN_ID``,
    ``CUTTLE_TURN_MESSAGE_ID`` and ``CUTTLE_TURN_MESSAGE`` to the agent CLI."""
    out: Dict[str, Any] = {'turn_id': '', 'message_id': None, 'message': ''}
    try:
        from api.chat_turn_row import current

        entry = current(session_id)
    except Exception:
        entry = None
    if entry and entry.get('query_id'):
        return {'turn_id': _clean(entry['query_id']), 'message_id': entry.get('user_message_id'),
                'message': entry.get('user_message') or ''}
    if _clean(os.environ.get(ENV)) and _bare(os.environ.get('CUTTLE_CHAT_SESSION_ID')) == _bare(session_id):
        raw = str(os.environ.get('CUTTLE_TURN_MESSAGE_ID') or '').strip()
        out.update(turn_id=_clean(os.environ.get(ENV)), message_id=int(raw) if raw.isdigit() else None,
                   message=_clean(os.environ.get('CUTTLE_TURN_MESSAGE')))
    return out
