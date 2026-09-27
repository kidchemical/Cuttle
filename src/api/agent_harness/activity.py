"""Activity helpers shared by adapters (Cursor-style status lines).

Methodology (match Cursor Agent in chat):

1. Map stream events to short status lines:
   - ``thinking: {preview}…``
   - ``writing: …{preview}``
   - ``tool {N}: {summary}``
   - ``tool failed: {summary}``
2. Throttle high-churn kinds (thinking/writing) so the strip does not flicker.
3. Idle heartbeat only when the stream is silent — never overwrite a fresh
   ``tool N:`` / ``thinking:`` line every few seconds. Format:
   ``{Agent} working… {elapsed}s ({last_activity})``.

Adapters that already stream rich events (Cursor, Muse, OpenCode, Codex, Hermes)
must use :class:`ActivityEmitter` (or equivalent) inside the CLI drain — do **not**
also run a coarse ``heartbeat_status("X working")`` in the adapter. That stomps
the useful lines (see Codex ``Codex working… Ns`` dogfood).

Adapters with no mid-run stream (Claude JSON blob, DeepSeek, Antigravity) may use
:func:`heartbeat_status` with a longer interval as a coarse progress tick only.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional


# Match Cursor / Muse / OpenCode defaults.
DEFAULT_THROTTLE_SEC = 1.2
DEFAULT_HEARTBEAT_SEC = 15.0

_THROTTLED_PREFIXES = ("writing: ", "thinking: ")


def throttle_kind(activity: str) -> str:
    """Rate-limit runs of the same streaming kind, not the first line of a kind.

    Only thinking/writing are throttled — each ``tool N:`` must publish immediately
    (Cursor emits every tool start).
    """
    text = activity or ""
    for prefix in _THROTTLED_PREFIXES:
        if text.startswith(prefix):
            return prefix
    return ""


def put_status(status_queue: Any, message: str) -> None:
    if status_queue is None or not message:
        return
    try:
        status_queue.put(("status", message))
    except Exception:
        pass


async def heartbeat_status(
    status_queue: Any,
    *,
    label: str = "Working",
    interval: float = DEFAULT_HEARTBEAT_SEC,
    stop_event: Optional[asyncio.Event] = None,
    last_activity: Optional[str] = None,
) -> None:
    """Emit coarse ``status`` ticks until ``stop_event`` is set.

    Prefer :class:`ActivityEmitter` when the CLI streams events. This helper is
    for agents that only have a single blocking ``communicate()`` with no NDJSON.
    """
    if status_queue is None:
        return
    stop = stop_event or asyncio.Event()
    t0 = time.time()
    activity = (last_activity or "starting").strip() or "starting"
    try:
        status_queue.put(("status", f"{label}…"))
    except Exception:
        return
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            elapsed = int(time.time() - t0)
            try:
                status_queue.put(
                    ("status", f"{label}… {elapsed}s ({activity})")
                )
            except Exception:
                break


class ActivityEmitter:
    """Cursor-style status strip: event lines + silent contextual heartbeat."""

    def __init__(
        self,
        status_queue: Any,
        *,
        agent_label: str = "Agent",
        throttle_sec: float = DEFAULT_THROTTLE_SEC,
        heartbeat_sec: float = DEFAULT_HEARTBEAT_SEC,
    ) -> None:
        self.status_queue = status_queue
        self.agent_label = (agent_label or "Agent").strip() or "Agent"
        self.throttle_sec = float(throttle_sec)
        self.heartbeat_sec = float(heartbeat_sec)
        self._started = time.monotonic()
        self._last_emit = self._started
        self.last_activity = "starting"

    def emit(self, activity: str, *, force: bool = False) -> bool:
        """Queue a status line. Returns True when the line was published."""
        text = (activity or "").strip()
        if not text or self.status_queue is None:
            return False
        now = time.monotonic()
        if not force and text == self.last_activity:
            return False
        kind = throttle_kind(text)
        if (
            not force
            and kind
            and kind == throttle_kind(self.last_activity)
            and (now - self._last_emit) < self.throttle_sec
        ):
            # Still advance last_activity for heartbeat context on thinking/writing.
            if kind in ("thinking: ", "writing: "):
                self.last_activity = text
            return False
        self._last_emit = now
        self.last_activity = text
        put_status(self.status_queue, text)
        return True

    def note(self, activity: str) -> None:
        """Update last_activity for heartbeat context without publishing."""
        text = (activity or "").strip()
        if text:
            self.last_activity = text

    async def heartbeat_loop(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Only ticks after ``heartbeat_sec`` of silence; includes last_activity."""
        if self.status_queue is None:
            return
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_sec)
                return
            except asyncio.TimeoutError:
                now = time.monotonic()
                if now - self._last_emit < self.heartbeat_sec:
                    continue
                self._last_emit = now
                elapsed = int(now - self._started)
                put_status(
                    self.status_queue,
                    f"{self.agent_label} working… {elapsed}s ({self.last_activity})",
                )
