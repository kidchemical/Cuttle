"""Single-writer ownership for Codex app-server threads (process-local).

A Codex thread accepts one writer at a time: a second ``app-server``
resuming the same thread id fails with a thread-store conflict (or hangs).
The turn runner, the context-token probe, and compaction all resume threads
through separate servers, so they coordinate here before spawning.

Keyed by thread id (not chat session): the vendor conflict is per thread.
``try_acquire`` never blocks — observers skip gracefully when busy;
``await_acquire`` is the cancel-aware wait for async runners (it sleeps on
the loop, never blocks a thread). Steer registration is untouched; this
seam owns vendor writes, not steering.

A lease is released ONLY after the owned vendor child is confirmed exited
(``reap_confirmed``). A kill request is not proof of exit: if cleanup
cannot confirm the exit (reap timeout, ``wait()`` returning without a
returncode, any error), the lease is RETAINED fail-closed so no second
writer can be admitted onto a thread a still-live server may hold. A
lease held this way is exceptional — restarting the owning process is the
recovery path.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()
_holders: Dict[str, Tuple[str, int]] = {}
_tokens = itertools.count(1)


def _norm(thread_id: object) -> str:
    return str(thread_id or "").strip()


def try_acquire(thread_id: object, owner: str) -> Optional[int]:
    """Take thread ownership once; return a release token, or None if held."""
    tid = _norm(thread_id)
    if not tid:
        return None
    with _lock:
        if tid in _holders:
            return None
        token = next(_tokens)
        _holders[tid] = (str(owner), token)
        return token


def release(thread_id: object, token: Optional[int]) -> bool:
    """Release only if ``token`` still owns the thread. Never raises."""
    tid = _norm(thread_id)
    if not tid or token is None:
        return False
    with _lock:
        cur = _holders.get(tid)
        if cur is not None and cur[1] == token:
            del _holders[tid]
            return True
    return False


def owner_of(thread_id: object) -> Optional[str]:
    """Current owner name, or None when the thread is free."""
    tid = _norm(thread_id)
    if not tid:
        return None
    with _lock:
        cur = _holders.get(tid)
        return cur[0] if cur is not None else None


def _cancel_requested(cancel_event: object) -> bool:
    try:
        return bool(cancel_event is not None and cancel_event.is_set())
    except Exception:
        return False


async def await_acquire(
    thread_id: object,
    owner: str,
    *,
    timeout: float,
    cancel_event: object = None,
    poll_sec: float = 0.05,
) -> Optional[int]:
    """Bounded wait for ownership that never stalls the event loop.

    Returns the release token, or None on timeout / cancellation / empty
    thread id. Shared by the app-server turn runner and the exec runner.
    """
    import asyncio

    tid = _norm(thread_id)
    if not tid:
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout))
    while True:
        if _cancel_requested(cancel_event):
            return None
        token = try_acquire(tid, owner)
        if token is not None:
            return token
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(max(0.001, float(poll_sec)))


async def reap_confirmed(proc: Any, *, timeout: float = 10.0) -> bool:
    """Bounded reap of an owned vendor child; True ONLY on confirmed exit.

    True when there is nothing to reap (``proc`` is None), the returncode
    is already set, or ``wait()`` returns with a returncode set. A reap
    timeout, a ``wait()`` that returns without a returncode, or any error
    returns False — callers must then RETAIN the thread lease fail-closed
    (see module docstring) instead of releasing it. Never raises.
    """
    if proc is None:
        return True
    try:
        if proc.returncode is not None:
            return True
    except Exception:
        return False
    try:
        await asyncio.wait_for(proc.wait(), max(0.1, float(timeout)))
    except Exception:
        return False
    try:
        return proc.returncode is not None
    except Exception:
        return False
