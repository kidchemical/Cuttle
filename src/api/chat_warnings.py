"""Canonical per-chat warning/error log (CH-000419 guideline 5).

Badges must not lie. When the model/effort a turn actually ran differs from
what the composer chip promised, the kernel records a drift entry here and
returns it as ``drift_warning`` in the web response so the UI can paint a
warning icon above the bubbles.

In-memory ring per chat session (survives for the Flask process lifetime);
query reports remain the durable per-turn record.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional

_MAX_PER_SESSION = 50

_log: Dict[str, Deque[Dict[str, Any]]] = defaultdict(lambda: deque(maxlen=_MAX_PER_SESSION))


def _sid(chat_session_id: Any) -> str:
    return str(chat_session_id).strip() if chat_session_id is not None else ""


def record_warning(
    chat_session_id: Any,
    kind: str,
    message: str,
    *,
    agent_id: Optional[str] = None,
    expected: Optional[str] = None,
    actual: Optional[str] = None,
) -> Dict[str, Any]:
    entry = {
        "ts": time.time(),
        "kind": str(kind or "drift").strip() or "drift",
        "message": str(message or "").strip(),
        "agent_id": (str(agent_id or "").strip() or None),
        "expected": expected,
        "actual": actual,
    }
    key = _sid(chat_session_id) or "_global"
    _log[key].append(entry)
    print(
        f"[chat-warning] session={key} kind={entry['kind']} {entry['message']}",
        flush=True,
    )
    return entry


def record_model_drift(
    chat_session_id: Any,
    *,
    agent_id: str,
    expected: Optional[str],
    actual: Optional[str],
    source: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    exp = (str(expected or "").strip() or None)
    act = (str(actual or "").strip() or None)
    if exp == act:
        return None
    msg = (
        f"{agent_id}: ran `{act or 'CLI default'}` but the composer promised "
        f"`{exp or 'CLI default'}` (source={source or 'unknown'})."
    )
    return record_warning(
        chat_session_id, "model_drift", msg,
        agent_id=agent_id, expected=exp, actual=act,
    )


def get_warnings(chat_session_id: Any, limit: int = 50) -> List[Dict[str, Any]]:
    rows = list(_log.get(_sid(chat_session_id) or "_global", []))
    try:
        n = max(1, min(int(limit), _MAX_PER_SESSION))
    except (TypeError, ValueError):
        n = _MAX_PER_SESSION
    return rows[-n:]


def clear_warnings(chat_session_id: Any) -> None:
    _log.pop(_sid(chat_session_id) or "_global", None)
