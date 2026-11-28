"""Mid-turn steering for Cursor ``agent -p`` turns, delivered as a ``--resume`` segment.

``agent -p`` has no input channel once it starts, and ``agent acp`` takes a
mid-turn ``session/prompt`` only by cancelling the running prompt, which kills
the tool in flight (probed 2026-11-28, ``agent`` 2026.11.22). Cuttle already
runs one Cursor turn as several ``-p`` segments chained with ``--resume`` (the
timeout continue), so a steer is one more segment: the send is held, the
running segment is ended at the next safe boundary, and the next segment runs
``--resume <session>`` with the steer text as its prompt.

The safe boundary is the first model output after every running tool has
finished. Ending the process at ``tool_call/completed`` loses that tool's
result (the resumed session re-runs the command); once the model streams again
the result is already in the session. So a steer never interrupts a tool, like
Claude Code folding a steer in at its next tool boundary. A steer that arrives
while the segment is finishing on its own becomes the next segment instead of
being lost.

Each segment echoes its prompt as a stream-json ``user`` event; that echo is
the receipt (``SteerDelivery``), and output that starts afterwards is offered
to the receipt owner as the steer's reply excerpt.

Thread model: ``send`` runs on a Flask worker, every other method on the
segment reader thread; one lock guards the shared state.
"""

from __future__ import annotations

import concurrent.futures
import threading
from typing import Any, Callable, List, Optional

from api.agent_harness import steer as steer_registry
from api.agent_harness.steer_delivery import SteerDelivery, SteerReceipt

AGENT_LABEL = "Cursor Agent"


def _first_line(text: str, limit: int = 100) -> str:
    return " ".join(str(text or "").split())[:limit]


class CursorSteer:
    """Steers for one Cursor turn; register once, hand pending text to the segment loop."""

    def __init__(self, chat_session_id: Any, emit: Optional[Callable[..., Any]] = None) -> None:
        self.chat_session_id = chat_session_id
        self._emit = emit
        self._lock = threading.Lock()
        self._closed = False
        self._pending: List[SteerReceipt] = []
        self._batch: List[SteerReceipt] = []
        self._batch_prompt: Optional[str] = None
        self._wake: Optional[Callable[[], None]] = None
        self.delivery = SteerDelivery()
        self.token: Optional[int] = None
        # Segment state, reset by ``begin_segment``.
        self._session_ready = False
        self._tools: set = set()
        self._output_since_tool = True
        self._item_seq = 0
        self._item: Optional[str] = None
        self._item_text = ""

    # ── registry ───────────────────────────────────────────────────────────
    def register(self) -> Optional[int]:
        self.token = steer_registry.register(self.chat_session_id, "cursor", self.send)
        return self.token

    def close(self) -> None:
        """No more sends; drop the registration. Pending sends stay undelivered."""
        with self._lock:
            self._closed = True
        if self.token is not None:
            steer_registry.unregister(self.chat_session_id, self.token)

    def send(self, text: str, steer_id: Optional[str] = None) -> "concurrent.futures.Future":
        out: "concurrent.futures.Future" = concurrent.futures.Future()
        with self._lock:
            if self._closed:
                out.set_result((False, "turn is no longer active"))
                return out
            receipt = self.delivery.begin(text, steer_id)
            self.delivery.acknowledge(receipt, succeeded=True)
            self._pending.append(receipt)
            wake = self._wake
        self._status(f"steer queued: {_first_line(text)} (sent at Cursor's next tool boundary)")
        if wake is not None:
            try:
                wake()
            except Exception:
                pass
        out.set_result((True, None))
        return out

    # ── segment loop (reader thread) ───────────────────────────────────────
    def begin_segment(self, prompt: str, wake: Optional[Callable[[], None]] = None) -> None:
        with self._lock:
            self._wake = wake
            self._session_ready = False
            self._tools = set()
            self._output_since_tool = True
            self._item = None
            self._item_text = ""
            if self._batch and prompt != self._batch_prompt:
                self._batch, self._batch_prompt = [], None

    def take_pending(self) -> Optional[str]:
        """Pending steer text for the next segment (several sends join), or None."""
        with self._lock:
            if not self._pending:
                return None
            self._batch, self._pending = self._pending, []
            self._batch_prompt = "\n\n".join(r.text.strip() for r in self._batch)
            return self._batch_prompt

    def finish_or_take(self) -> Optional[str]:
        """At the turn's end: pending text to run first, or close so later sends queue."""
        with self._lock:
            if not self._pending:
                self._closed = True
                return None
        return self.take_pending()

    def should_cut(self) -> bool:
        """End the running segment now: a steer waits and no tool result is at risk."""
        with self._lock:
            return bool(
                self._pending and self._session_ready and not self._tools and self._output_since_tool
            )

    def on_event(self, et: Any, sub: Any, evt: dict) -> None:
        """Track one stream-json event of the running segment."""
        if et == "system" and sub == "init":
            with self._lock:
                self._session_ready = True
        elif et == "user":
            self._user_echo(evt)
        elif et == "tool_call":
            call_id = str(evt.get("call_id") or "tool")
            self._close_item()
            with self._lock:
                if sub == "started":
                    self._tools.add(call_id)
                elif sub == "completed":
                    self._tools.discard(call_id)
                    self._output_since_tool = False
        elif et in ("thinking", "assistant"):
            with self._lock:
                self._output_since_tool = True
        elif et == "result":
            self._close_item()
            if not evt.get("is_error"):
                self._confirm_batch()

    def output_text(self, text: str) -> None:
        """Assistant text streamed so far for the current output item."""
        if self.token is None or not (text or "").strip():
            return
        if self._item is None:
            self._item_seq += 1
            self._item = f"cursor-msg-{self._item_seq}"
            self._note(steer_registry.note_output_start, self._item)
        self._item_text = text

    def closing_notice(self) -> Optional[str]:
        with self._lock:
            return self.delivery.closing_notice(AGENT_LABEL)

    @property
    def received_count(self) -> int:
        with self._lock:
            return self.delivery.received_count

    # ── internals ──────────────────────────────────────────────────────────
    def _user_echo(self, evt: dict) -> None:
        msg = evt.get("message") if isinstance(evt.get("message"), dict) else {}
        content = msg.get("content")
        text = ""
        if isinstance(content, list):
            text = "".join(
                str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        elif isinstance(content, str):
            text = content
        with self._lock:
            batch_prompt = self._batch_prompt
        if batch_prompt is not None and text.strip() == batch_prompt.strip():
            self._confirm_batch(echo=True)

    def _confirm_batch(self, *, echo: bool = False) -> None:
        with self._lock:
            batch, self._batch, self._batch_prompt = self._batch, [], None
            fresh = [r for r in batch if self.delivery.confirm(r, notify=False)]
        if not fresh:
            return
        # Without an echo (the segment finished, echo text differed) delivery is
        # retroactive: it must not claim the following output as its reply.
        note = steer_registry.note_echo if echo else steer_registry.note_delivered
        for receipt in fresh:
            if receipt.steer_id:
                self._note(note, receipt.steer_id)
        self._status(f"steer received: {_first_line(fresh[-1].text)}")

    def _close_item(self) -> None:
        item, self._item = self._item, None
        if item is not None:
            self._note(steer_registry.note_output_text, item, self._item_text)

    def _note(self, fn: Callable[..., Any], *args: Any) -> None:
        if self.token is None:
            return
        try:
            fn(self.chat_session_id, self.token, *args)
        except Exception:
            pass

    def _status(self, msg: str) -> None:
        if self._emit is None:
            return
        try:
            self._emit(msg)
        except Exception:
            pass


def build_cursor_steer(chat_session_id: Any, emit: Optional[Callable[..., Any]] = None) -> Optional[CursorSteer]:
    """A registered steer owner for this turn, or None when steering is off."""
    if chat_session_id is None or not steer_registry.steer_enabled("cursor"):
        return None
    owner = CursorSteer(chat_session_id, emit)
    if owner.register() is None:
        return None
    return owner
