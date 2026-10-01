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
- Queue objects are created and drained by the transport/route layer;
  this service only registers, unregisters, looks up, and puts.
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
from typing import Any, Callable, Dict, Optional

# Session-scoped queues for streaming status updates during turn
# execution. Keys: session_id. Values: queue.Queue receiving
# ('status', message) or ('done', result).
_QUEUES: Dict[Any, Any] = {}


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
