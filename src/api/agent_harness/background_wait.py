"""Detached background waits: a harness turn that ends while its CLI still works.

Claude Code can finish a root ``result`` while its own background tasks
(``Bash`` with ``run_in_background``, ``Monitor``, …) are still running; the CLI
answers again once one finishes. Instead of holding the chat busy for the whole
wait, the turn ends with its reply plus a watch card (``api.job_watch`` status
file, the standard watch/progress card), and the CLI keeps running detached:

* ``BackgroundWait.start`` writes the running status and returns the card markup
  the CLI tool appends to the first reply.
* each later root ``result`` is ``followup`` → a new assistant bubble, persisted
  through the same saver that persisted the first one (``bind_saver``, called by
  ``api.chat_turn_persist``); follow-ups that arrive before that bind wait here.
* ``finish`` marks the card done/failed and may post a closing notice bubble.
* ``steer_detached`` lets ``/api/chat`` (``api.chat_coordinator``) put the next
  message into the still-open run through ``api.agent_harness.steer`` instead of
  starting a second ``claude --resume`` on the same session.

The CLI process stays registered in ``api.chat_run_registry`` (Stop / chat
deletion kill it) and the job id is bound to the chat so ``/api/chat-cancel``
cancels it; the card's own Stop kills the recorded pid via ``job_watch``.

State is process-lifetime (the runs die with Flask). Off switch: settings
``agent_background_detach`` → ``{"claude": false}`` or env
``CUTTLE_BACKGROUND_DETACH=0`` restores the single-bubble wait.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

DETACHABLE_AGENTS = ("claude",)
# Keys that describe one bubble only; follow-ups never inherit them.
_PER_BUBBLE = frozenset({
    "response", "output", "usage", "cost", "success", "type", "error", "steered",
    "background_job", "drift_warning", "meta_command", "undelivered_steers",
})
_FINISHED_TTL_SEC = 3600.0

_lock = threading.Lock()
_active: Dict[str, "BackgroundWait"] = {}


def detach_enabled(agent_id: str) -> bool:
    """True unless the env kill switch or the per-agent setting turns detaching off."""
    agent = (agent_id or "").strip().lower()
    if agent not in DETACHABLE_AGENTS:
        return False
    if (os.getenv("CUTTLE_BACKGROUND_DETACH") or "").strip().lower() in ("0", "false", "off", "no"):
        return False
    try:
        from managers.settings_manager import get_settings_manager

        cfg = get_settings_manager().get_setting("agent_background_detach", None)
    except Exception:
        cfg = None
    if isinstance(cfg, dict) and agent in cfg:
        return bool(cfg.get(agent))
    return True


def _key(chat_session_id: Any) -> str:
    try:
        from api.cuttle_ui_capabilities import numeric_chat_session_id

        num = numeric_chat_session_id(chat_session_id)
    except Exception:
        num = None
    return str(num) if num is not None else str(chat_session_id or "").strip()


def waiting_label(tasks: List[str]) -> str:
    names = [t for t in tasks if t] or ["background task"]
    more = f" (+{len(names) - 1} more)" if len(names) > 1 else ""
    return f"Waiting on: {names[0]}{more}"


class BackgroundWait:
    """One detached wait: its watch status, follow-up bubbles and end state."""

    def __init__(self, chat_session_id: Any, agent_id: str = "claude", *, label: str = "Claude Code"):
        self.chat_session_id = chat_session_id
        self.agent_id = agent_id
        self.label = label
        self.key = _key(chat_session_id)
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", self.key).strip("-")[:24] or "chat"
        self.job_id = f"{agent_id}-bg-{slug}-{secrets.token_hex(3)}"
        self._lock = threading.Lock()
        self._state: Optional[str] = None  # None until start → running → done/failed
        self._started_at = ""
        self._t0 = 0.0
        self._ended_at = 0.0
        self._pid: Optional[int] = None
        self._sink: Optional[Callable[[Dict[str, Any]], None]] = None
        self._base: Dict[str, Any] = {}
        self._pending: List[Dict[str, Any]] = []

    # -- CLI side ---------------------------------------------------------

    def start(self, tasks: List[str], *, pid: Optional[int]) -> str:
        """Begin the wait; returns the watch-card markup for the first reply."""
        self._started_at = datetime.now().isoformat(timespec="seconds")
        self._t0 = time.monotonic()
        self._pid = int(pid) if pid else None
        self._write("running", waiting_label(tasks))
        with self._lock:
            self._state = "running"
        _purge_finished()
        with _lock:
            _active[self.job_id] = self
        try:
            from api.chat_run_registry import attach_job

            attach_job(self.chat_session_id, self.job_id)
        except Exception:
            pass
        return self._card()

    @property
    def running(self) -> bool:
        return self._state == "running"

    def progress(self, label: str) -> None:
        if self.running:
            self._write("running", label)

    def followup(self, text: str, *, usage: Optional[Dict[str, Any]] = None,
                 errors: Optional[List[str]] = None) -> None:
        """Persist one later CLI answer as its own assistant bubble."""
        body_text = (text or "").strip() or (errors[0] if errors else "")
        if not body_text:
            return
        body: Dict[str, Any] = {
            "success": True,
            "response": body_text,
            "type": f"{self.agent_id}_error" if errors else f"{self.agent_id}_command",
            "background_followup": self.job_id,
        }
        clean_usage = {k: v for k, v in (usage or {}).items()
                       if k in ("prompt_tokens", "completion_tokens", "total_tokens", "context_tokens",
                                "cache_read_tokens", "cache_write_tokens", "cost") and v is not None}
        if clean_usage:
            body["usage"] = clean_usage
            if clean_usage.get("cost") is not None:
                body["cost"] = clean_usage["cost"]
        with self._lock:
            sink = self._sink
            if sink is None:
                self._pending.append(body)
                return
            base = self._base
        _deliver(sink, base, body)

    def finish(self, state: str, label: str, *, notice: str = "") -> None:
        """End the wait: terminal card state plus an optional closing bubble.

        A wait the card's own Stop already cancelled keeps that status and
        posts nothing.
        """
        with self._lock:
            if self._state in ("done", "failed"):
                return
            self._state = state
            self._ended_at = time.monotonic()
        card_stopped = stopped = False
        try:
            from api.job_watch import read_status

            card_stopped = bool((read_status(self.job_id) or {}).get("cancelled"))
        except Exception:
            pass
        try:
            from api.chat_run_registry import is_run_cancelled

            # Stop / chat deletion may kill the CLI before its run sees the flag.
            stopped = is_run_cancelled(self.chat_session_id)
        except Exception:
            pass
        if stopped and not card_stopped:
            self._write("failed", "Stopped", final=True)
        elif not card_stopped:
            self._write(state, label, final=True)
            if notice:
                self.followup(notice)
        try:
            from api.chat_run_registry import clear_session_jobs

            clear_session_jobs(self.chat_session_id, self.job_id)
        except Exception:
            pass
        self._maybe_drop()

    # -- persistence side -------------------------------------------------

    def bind(self, saver: Callable[[Dict[str, Any]], None], base: Dict[str, Any]) -> None:
        with self._lock:
            self._sink = saver
            self._base = {k: v for k, v in (base or {}).items() if k not in _PER_BUBBLE}
            pending, self._pending = self._pending, []
            base_copy = self._base
        for body in pending:
            _deliver(saver, base_copy, body)
        self._maybe_drop()

    # -- internals --------------------------------------------------------

    def _write(self, state: str, label: str, *, final: bool = False) -> None:
        extra: Dict[str, Any] = {
            "started_at": self._started_at,
            "run_id": self.job_id,
            "agent": self.agent_id,
            "chat_session_id": self.key,
            "pid": 0 if final else (self._pid or 0),
        }
        if final and self._t0:
            extra["elapsed_sec"] = int(time.monotonic() - self._t0)
        try:
            from api.job_watch import write_status

            write_status(self.job_id, state=state, percent=100 if state == "done" else 0,
                         label=label, extra=extra)
        except Exception as exc:
            print(f"[background_wait] status write failed for {self.job_id}: {exc}", flush=True)
            if not final and self._state is None:
                raise

    def _card(self) -> str:
        from api.job_watch import read_status, stamp_watch_run, watch_form_spec, watch_snapshot_from_status

        spec = watch_form_spec(self.job_id, title=f"{self.label} — background work")
        spec["watch"].pop("resume_message", None)
        spec["options"] = [
            {"id": "stop", "label": "Stop background work", "action": "__watch_cancel__"},
        ]
        status = read_status(self.job_id)
        spec["watch"] = stamp_watch_run(spec["watch"], status)
        spec["watch"]["snapshot"] = watch_snapshot_from_status(status)
        return "<cuttle_action_form>\n" + json.dumps(spec, ensure_ascii=False, indent=2) + "\n</cuttle_action_form>"

    def _maybe_drop(self) -> None:
        with self._lock:
            done = self._state in ("done", "failed") and self._sink is not None and not self._pending
        if done:
            with _lock:
                if _active.get(self.job_id) is self:
                    del _active[self.job_id]


def _deliver(saver: Callable[[Dict[str, Any]], None], base: Dict[str, Any], body: Dict[str, Any]) -> None:
    try:
        saver({**base, **body})
    except Exception as exc:
        print(f"[background_wait] follow-up persist failed: {exc}", flush=True)


def _purge_finished() -> None:
    """Drop finished waits whose first bubble was never saved (no saver bound)."""
    now = time.monotonic()
    with _lock:
        for job_id, wait in list(_active.items()):
            if wait._ended_at and now - wait._ended_at > _FINISHED_TTL_SEC:
                del _active[job_id]


def bind_saver(job_id: Any, saver: Callable[[Dict[str, Any]], None], base: Dict[str, Any]) -> bool:
    """Route a wait's follow-up bubbles through the saver that saved its first reply."""
    with _lock:
        wait = _active.get(str(job_id or ""))
    if wait is None:
        return False
    wait.bind(saver, base)
    return True


def detached_wait(chat_session_id: Any, agent_id: Optional[str] = None) -> Optional[BackgroundWait]:
    """The chat's running detached wait (optionally for one agent), if any."""
    key = _key(chat_session_id)
    with _lock:
        waits = list(_active.values())
    for wait in waits:
        if wait.key == key and wait.running and (agent_id is None or wait.agent_id == agent_id):
            return wait
    return None


def steer_detached(chat_session_id: Any, agent_id: Optional[str], text: str) -> Optional[Dict[str, Any]]:
    """Steer ``text`` into this chat's detached run; None when there is none or it refused."""
    if not agent_id or detached_wait(chat_session_id, agent_id) is None:
        return None
    from api.agent_harness import steer

    if steer.active_agent(chat_session_id) != agent_id:
        return None
    info = steer.steer(chat_session_id, text)
    return info if info.get("steered") else None
