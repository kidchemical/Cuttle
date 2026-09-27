"""Background hourly regress watcher (Flask-owned)."""

from __future__ import annotations

import os
import sys
import threading
from typing import Optional

_LOCK = threading.Lock()
_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()


def _in_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def _loop() -> None:
    from api.jev.config import load_jev_config
    from api.jev.client import jev_available
    from api.jev.regress import run_regress_once

    # Don't fire on Flask boot — wait a full interval first.
    first = True
    while not _STOP.is_set():
        try:
            cfg = load_jev_config()
            interval = int(cfg.regress.interval_s)
            if first:
                first = False
                if _STOP.wait(timeout=float(interval)):
                    return
                continue
            if cfg.enabled and cfg.regress.enabled and jev_available():
                run_regress_once()
            if cfg.enabled and cfg.label_turns and jev_available():
                from api.jev.labels import label_pending_async

                label_pending_async()
        except Exception as exc:
            print(f"[JEV] regress watch tick failed: {str(exc)[:200]}", flush=True)
        if _STOP.wait(timeout=float(load_jev_config().regress.interval_s)):
            return


def ensure_started() -> bool:
    """Idempotent. No-op under pytest or when Jev is disabled/unkeyed."""
    if _in_pytest():
        return False
    if os.environ.get("CUTTLE_JEV_WATCH", "1").strip() in ("0", "false", "no"):
        return False
    from api.jev.client import jev_available
    from api.jev.config import load_jev_config

    cfg = load_jev_config()
    if not (cfg.enabled and cfg.regress.enabled and jev_available()):
        return False
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return True
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, name="cuttle-jev-regress", daemon=True)
        _THREAD.start()
        print("[JEV] hourly regress watcher started", flush=True)
        return True


def stop_for_tests() -> None:
    _STOP.set()
