"""Flask-owned trigger: a chat's ``turn_end`` arms an auto-compact check.

``api.chat_delivery.end`` publishes ``turn_end`` (``api.session_events``) when
a turn releases the chat. The listener notes the chat and returns; this
module's thread waits the policy's quiet period (a follow-up the user sends
right away just starts the next turn, and that turn's end re-arms it), then
runs ``service.evaluate``. When the chat still has a live CLI (a Claude run
waiting on background tasks), the check is retried every ``RETRY_SEC`` until
``RETRY_FOR_SEC``. Started when the routes blueprint is registered; off in
tests, shadow instances (``CUTTLE_TEST_MODE=1``) and with ``CUTTLE_AUTO_COMPACT=0``.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Dict, Optional

log = logging.getLogger(__name__)

TURN_END = "turn_end"
RETRY_SEC = 60.0
RETRY_FOR_SEC = 2 * 3600.0
IDLE_WAIT_SEC = 300.0

_lock = threading.Lock()
_due: Dict[int, Dict[str, float]] = {}  # sid -> {"at": due time, "until": retry deadline, "first": 1/0}
_wake = threading.Event()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def _quiet_seconds() -> float:
    try:
        from api.auto_compact.service import settings

        return float(settings().get("quiet_seconds") or 0)
    except Exception:
        return 20.0


def arm(session_id: int, *, delay: Optional[float] = None, now: Optional[float] = None) -> None:
    """Check ``session_id`` after ``delay`` seconds (default: the quiet period)."""
    now = time.time() if now is None else now
    at = now + (_quiet_seconds() if delay is None else float(delay))
    with _lock:
        _due[int(session_id)] = {"at": at, "until": now + RETRY_FOR_SEC, "first": 1.0}
    _wake.set()


def on_session_event(kind, session_id, detail):
    """Runs on the publisher's thread (a live agent stream): note it and return."""
    if kind != TURN_END:
        return
    try:
        from api.auto_compact.service import available

        if not available():
            return
    except Exception:
        return
    arm(int(session_id))


def take_due(now: Optional[float] = None):
    """``[(sid, first)]`` whose check is due; removes them from the queue."""
    now = time.time() if now is None else now
    with _lock:
        ready = [(sid, entry) for sid, entry in _due.items() if entry["at"] <= now]
        for sid, _entry in ready:
            _due.pop(sid, None)
    return [(sid, entry) for sid, entry in ready]


def run_due(now: Optional[float] = None, *, evaluate=None):
    """Evaluate every due chat; re-queue the ones held by a live CLI. Returns results."""
    if evaluate is None:
        from api.auto_compact.service import evaluate
    now = time.time() if now is None else now
    out = []
    for sid, entry in take_due(now):
        result = evaluate(sid, trigger="turn_end", count_turn=bool(entry.get("first")))
        out.append(result)
        if result.get("reason") in ("live_cli", "leased") and now + RETRY_SEC < entry["until"]:
            with _lock:
                if sid not in _due:  # a newer turn end already re-armed it
                    _due[sid] = {"at": now + RETRY_SEC, "until": entry["until"], "first": 0.0}
    return out


def _next_wait(now: float) -> float:
    with _lock:
        if not _due:
            return IDLE_WAIT_SEC
        return max(0.05, min(e["at"] for e in _due.values()) - now)


def _loop():
    while not _stop.is_set():
        _wake.wait(_next_wait(time.time()))
        _wake.clear()
        if _stop.is_set():
            return
        try:
            for result in run_due():
                if result.get("reason") in ("compacted", "failed", "error"):
                    log.info("auto-compact %s: %s", result.get("handle"), result.get("reason"))
        except Exception:
            log.exception("auto-compact pump failed")


def start() -> bool:
    if "pytest" in sys.modules or os.environ.get("CUTTLE_TEST_MODE") == "1" \
            or os.environ.get("CUTTLE_AUTO_COMPACT") == "0":
        return False
    global _thread
    from api.session_events import subscribe

    subscribe(on_session_event)
    with _lock:
        if _thread and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="cuttle-auto-compact", daemon=True)
        _thread.start()
    return True


def stop() -> None:
    _stop.set()
    _wake.set()
