"""Streaming status-queue registry + emit fanout (P4-2 owned service).

Single owner of the session-scoped status queues that carry
``('status', message)`` events from turn producers to the SSE transport
loop. Moved verbatim out of the Flask entry module so producers depend
on this purpose-specific service instead of reverse importing it.

State ownership and lifetimes:

- ``_QUEUES``: process lifetime, plain dict, same semantics as before
  (one entry per session with an attached transport queue; a Flask
  restart wipes it alongside the live-status store — restart-sensitive
  by design, never moved into transient per-request objects).
- Legacy queue objects are registered by their callers. Stream-turn queues
  are created by the workflow; subscribers drain them. ``TurnStatusQueue``
  fans progress out at the producer, through injected turn-scoped callbacks.
- Cancel policy: ``emit_status`` requires an explicit ``is_cancelled``
  predicate and an explicit ``publish_live`` callback per call — there
  are no silent unguarded defaults for active writes. The composition
  root (entry-module wrapper) injects the turn-cancelled predicate and
  the live-status publish.

No Flask routes, no SSE serialization, no delivery orchestration here.
Backpressure: ``put_nowait`` with ``Full`` dropped (queues are
unbounded in practice; a bounded test double must never raise out).
"""
from __future__ import annotations

import queue as queue_module
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Optional

# Session-scoped queues for streaming status updates during turn
# execution. Keys: session_id. Values: queue.Queue receiving
# ('status', message) or ('done', result).
_QUEUES: Dict[Any, Any] = {}
_TURN_QUEUE = ContextVar("chat_turn_status_queue", default=None)


@contextmanager
def turn_status_scope(session_id, status_queue):
    """Bind legacy session-only emitters to their producer's actual turn."""
    token = _TURN_QUEUE.set((session_id, status_queue))
    try:
        yield
    finally:
        _TURN_QUEUE.reset(token)


class TurnStatusQueue(queue_module.Queue):
    """Producer-side status fanout, independent of a transport subscriber.

    The workflow supplies turn freshness and live-store callbacks. Finishing
    closes publication before releasing the busy slot; draining buffered SSE
    events never writes them back into the live store.
    """

    def __init__(self, *, is_superseded, publish_live, clear_live):
        super().__init__()
        self._publication_lock = threading.Lock()
        self._finished = False
        self._is_superseded = is_superseded
        self._publish_live = publish_live
        self._clear_live = clear_live

    def publish(self, kind, payload):
        with self._publication_lock:
            if self._finished or self._is_superseded():
                return
            try:
                self._publish_live(kind, payload)
            except Exception:
                pass

    def put(self, item, block=True, timeout=None):
        if isinstance(item, tuple) and len(item) >= 2 and item[0] in (
            "status", "query_started",
        ):
            self.publish(item[0], item[1])
        return super().put(item, block=block, timeout=timeout)

    def finish(self):
        with self._publication_lock:
            if self._finished:
                return
            self._finished = True
            try:
                self._clear_live()
            except Exception:
                pass


def register_status_queue(session_id: Any, status_queue: Any) -> None:
    """Attach a transport queue for a session (replaces any previous one)."""
    if status_queue:
        _QUEUES[session_id] = status_queue


def unregister_status_queue(session_id: Any) -> None:
    """Detach a session's transport queue (turn completion / cleanup)."""
    _QUEUES.pop(session_id, None)


def get_status_queue(session_id: Any) -> Optional[Any]:
    """Look up a session's transport queue (None when detached)."""
    return _QUEUES.get(session_id)


def emit_status(
    session_id: Any,
    message: str,
    *,
    is_cancelled: Callable[[Any], bool],
    publish_live: Callable[[str], None],
) -> None:
    """Fan out one status line to live-status publish + transport queue.

    A cancelled turn publishes and queues nothing (a dying worker must
    not narrate over the turn that replaced it or resurrect activity
    after Stop). Both effects stay exception-safe for producers.
    """
    if is_cancelled is not None:
        try:
            if is_cancelled(session_id):
                return
        except Exception:
            pass
    scoped = _TURN_QUEUE.get()
    if scoped is not None:
        from api.session_keys import chat_session_keys

        scoped_sid, scoped_queue = scoped
        if set(chat_session_keys(session_id)).intersection(chat_session_keys(scoped_sid)):
            # The turn queue owns both the freshness guard and live fanout.
            # Looking up the registry here would hand old-worker progress to
            # whichever turn most recently registered the same session id.
            scoped_queue.put_nowait(('status', message))
            return
    if message:
        try:
            publish_live(message)
        except Exception:
            pass
    q = _QUEUES.get(session_id)
    if q:
        try:
            q.put_nowait(('status', message))
        except queue_module.Full:
            pass
