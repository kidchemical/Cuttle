"""In-process notices that something happened in a chat (observer hooks).

Owners publish where the thing happens (``publish('tool', sid)`` when a harness
tool call finishes, ``publish('worktree_bound', sid)`` on a worktree bind);
consumers ``subscribe`` once. Publishers never learn who listens, so this module
imports nothing from Cuttle and owned layers can publish without new edges.

Publishing runs on the caller's thread (often a live agent stream): a listener
must only note the event and return, doing real work on its own thread. A
failing listener is logged and never reaches the publisher.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List

log = logging.getLogger(__name__)

Listener = Callable[[str, int, Dict[str, Any]], None]

_lock = threading.Lock()
_listeners: List[Listener] = []


def subscribe(listener: Listener) -> Callable[[], None]:
    with _lock:
        if listener not in _listeners:
            _listeners.append(listener)
    return lambda: unsubscribe(listener)


def unsubscribe(listener: Listener) -> None:
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


def publish(kind: str, session_id: Any, **detail: Any) -> None:
    """Tell listeners ``kind`` happened in chat ``session_id`` (int or ``db_session_N``)."""
    try:
        sid = int(str(session_id).strip().removeprefix('db_session_'))
    except (TypeError, ValueError):
        return
    with _lock:
        listeners = list(_listeners)
    for listener in listeners:
        try:
            listener(kind, sid, detail)
        except Exception:
            log.exception('session event listener failed')
