"""Activity helpers shared by adapters (Cursor-style status lines).

Methodology (match Cursor Agent in chat):

1. Map stream events to short status lines:
   - ``thinking: {preview}…``
   - ``writing: {preview}`` (leading ellipsis only when shortened)
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

Adapters without a documented mid-run event stream may use
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


def put_status(status_queue: Any, message: str, *, preview_only: bool = False) -> None:
    if status_queue is None or not message:
        return
    try:
        put = getattr(status_queue, "put_preview", None) if preview_only else None
        (put if callable(put) else status_queue.put)(("status", message))
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
        record_text_previews: bool = True,
        record_tool_previews: bool = True,
    ) -> None:
        self.status_queue = status_queue
        self.agent_label = (agent_label or "Agent").strip() or "Agent"
        self.throttle_sec = float(throttle_sec)
        self.heartbeat_sec = float(heartbeat_sec)
        self.record_text_previews = record_text_previews
        self.record_tool_previews = record_tool_previews
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
        preview_only = (bool(kind) and not self.record_text_previews) or (
            text.startswith(("tool ", "tool failed:")) and not self.record_tool_previews
        )
        put_status(self.status_queue, text, preview_only=preview_only)
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


def text_preview(text: str) -> str:
    flat = " ".join(text.split())
    return ("…" if len(flat) > 120 else "") + flat[-120:]


class TextActivityLog:
    """Keep item text for the inspector, separate from throttled live previews."""

    def __init__(self, agent_id: str) -> None:
        from uuid import uuid4

        self.source_id = f"{agent_id}:{uuid4().hex}"
        self.buffers: Dict[tuple, str] = {}
        self.current: Dict[str, str] = {}
        self.sequence = 0

    def key(self, kind: str, item_id: Any = None) -> tuple:
        return kind, str(item_id or self.current.get(kind) or "pending")

    def start(self, kind: str, item_id: Any = None) -> None:
        self.sequence += 1
        self.current[kind] = str(item_id or f"item-{self.sequence}")

    def delta(self, kind: str, text: str, item_id: Any = None) -> str:
        """Append a vendor chunk; save on a live tick or flush at completion."""
        key = self.key(kind, item_id)
        self.buffers[key] = self.buffers.get(key, "") + text
        return self.buffers[key]

    def save(self, kind: str, text: str = "", item_id: Any = None) -> None:
        """Replace with a cumulative snapshot, or publish the accumulated chunks."""
        from api.query_events import record_agent_text

        key = self.key(kind, item_id)
        if text:
            self.buffers[key] = text
        record_agent_text(kind, self.buffers.get(key, ""), f"{self.source_id}:{key[1]}")

    def flush(self) -> None:
        for kind, item_id in list(self.buffers):
            self.save(kind, item_id=item_id)


class ToolActivityLog:
    """Number and record tools by vendor id, independent of display throttling."""

    def __init__(self, agent_id: str, emitter: ActivityEmitter) -> None:
        from uuid import uuid4
        self.source_id = f"{agent_id}:{uuid4().hex}"
        self.agent_id = agent_id
        self.emitter = emitter
        self.tools: dict = {}
        # Tools whose edits the vendor reported natively need no step snapshot.
        self.native_edit_tools: set = set()

    def record(self, tool_id: Any, name: str = "", args: Any = None, *,
               phase: str = "started", result: Any = None, failed: bool = False) -> None:
        from api.query_events import record_agent_tool
        key = str(tool_id) if tool_id is not None else f"anonymous-{len(self.tools)+1}"
        previous = self.tools.get(key)
        index, old_name, old_args = previous or (len(self.tools) + 1, "tool", None)
        name = name or old_name
        args = args if args is not None else old_args
        self.tools[key] = (index, name, args)
        detail = ""
        if isinstance(args, dict):
            for field in ("command", "CommandLine", "file_path", "path", "pattern", "query", "description"):
                if isinstance(args.get(field), str) and args[field].strip():
                    detail = " " + text_preview(args[field])
                    break
        summary = name + detail
        record_agent_tool(f"{self.source_id}:{key}", summary, phase=phase,
                          args=args, result=result, failed=failed)
        if (phase == "completed" and self.agent_id not in ("codex", "claude", "opencode")
                and key not in self.native_edit_tools):
            from api.query_events import current_query_id
            if any(part in name.lower() for part in ("edit", "write", "patch", "bash", "shell", "exec")):
                try:
                    from api.agent_events.snapshots import record_step
                    record_step(current_query_id(), f"{self.source_id}:{key}")
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("Step snapshot unavailable")
        if failed:
            self.emitter.emit(f"tool failed: {summary}", force=True)
        elif previous is None or (phase == "started" and (name != old_name or args != old_args)):
            self.emitter.emit(f"tool {index}: {summary}", force=True)

    def record_edit(self, tool_id: Any, path: str, patch: Any, *, change: Any = 'modify', source: str = 'native') -> None:
        """Preserve vendor edit evidence without interpreting it in the kernel."""
        from api.query_events import record_agent_edit
        if source == 'native':
            self.native_edit_tools.add(str(tool_id))
        record_agent_edit(f'{self.source_id}:{tool_id}:edit:{path}', path, patch,
                          change=change, source=source, tool_id=f'{self.source_id}:{tool_id}')
