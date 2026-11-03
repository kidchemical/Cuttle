"""Owner-side Stop for sub-agent child runs.

A child turn's CLI is a subprocess of the process that runs the turn: the
``python -m api.subagents spawn`` CLI, or Flask for a ``message`` follow-up.
Only that process can stop it. ``api.chat_run_registry`` tracks the CLI in that
process's memory, and ``api.process_kill_safety`` refuses to signal anything
that is not a descendant of the caller. So Stop, ``/api/chat-cancel`` and the
agent feed's End, all handled in Flask, only record a stop request on the child
row (``store.request_stop``). This watcher runs in the owning process, sees the
request and cancels the child session's runs there, which kills the CLI tree
and invalidates the delivery turn.

Lifetime: one watcher per child turn. It lasts while the turn runs and, after
the turn returns, while this process still tracks a live process for the child
session. A Claude turn with background tasks returns its reply and leaves the
CLI running a detached wait (``api.agent_harness.background_wait``).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import api.subagents.store as store
from api.subagents.types import STATUS_CANCELLED, ChildRecord

POLL_SEC = 1.0


def stop_requested(child: Optional[ChildRecord], *, since: float, in_flight: bool) -> bool:
    """True when this watch must stop the child's runs.

    A request recorded at or after ``since`` applies to this watch only. One
    from an earlier turn of a conversational child is ignored. A row flipped to
    ``cancelled`` while the turn is in flight also counts, which covers a cancel
    that landed just before the watch started.
    """
    if child is None:
        return False
    at = child.stop_requested_at
    if at is not None and float(at) >= float(since):
        return True
    return bool(in_flight and child.status == STATUS_CANCELLED)


def _cancel_runs(session_id: int) -> None:
    from api.chat_run_registry import cancel_session_runs

    cancel_session_runs(int(session_id))


def _has_live_process(session_id: int) -> bool:
    from api.chat_run_registry import has_live_process

    return bool(has_live_process(int(session_id)))


class ChildStopWatch:
    """Poll one child row from the owning process; stop its runs on request."""

    def __init__(
        self,
        db,
        child_id: str,
        session_id: int,
        *,
        poll: Optional[float] = None,
        cancel: Optional[Callable[[int], None]] = None,
        has_live: Optional[Callable[[int], bool]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._child_id = str(child_id)
        self._session_id = int(session_id)
        self._poll = max(0.01, float(POLL_SEC if poll is None else poll))
        self._cancel = cancel or _cancel_runs
        self._has_live = has_live or _has_live_process
        self.since = float(clock())
        self.fired = False
        self._turn_done = threading.Event()
        self._closed = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "ChildStopWatch":
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"subagent-stop-{self._child_id[:8]}"
        )
        self._thread.start()
        return self

    def turn_finished(self) -> None:
        """The turn returned; keep watching only while its CLI is still alive."""
        self._turn_done.set()

    def close(self, timeout: float = 2.0) -> None:
        self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def check(self) -> bool:
        """One poll. Returns False when the watch is over."""
        try:
            child = store.get_child(self._db, self._child_id)
        except Exception:
            return True
        if child is None:
            return False
        if stop_requested(child, since=self.since, in_flight=not self._turn_done.is_set()):
            self.fired = True
            try:
                self._cancel(self._session_id)
            except Exception:
                pass
            return False
        if self._turn_done.is_set():
            try:
                return self._has_live(self._session_id)
            except Exception:
                return False
        return True

    def _loop(self) -> None:
        while not self._closed.wait(self._poll):
            if not self.check():
                return
