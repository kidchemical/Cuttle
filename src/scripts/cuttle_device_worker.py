#!/usr/bin/env python3
"""
Standalone Cuttle device-worker sidecar (dev / host tools).

Prefer the Electron-bundled stdlib sidecar at electron/device-worker/
for Client mode. This entry still uses the api.device_workers package
when a full Cuttle tree + venv are available.

Env:
  CUTTLE_DEVICE_WORKERS_COORDINATOR_URL  e.g. https://192.168.1.20:8080
  CUTTLE_DEVICE_WORKERS_TOKEN            optional shared bearer
  CUTTLE_DEVICE_WORKER_ID                optional stable id (default: hostname)
  CUTTLE_DEVICE_WORKER_LOG               optional log path
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path


def _log_path() -> Path:
    explicit = (os.environ.get("CUTTLE_DEVICE_WORKER_LOG") or "").strip()
    if explicit:
        return Path(explicit)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or "."
    return Path(base) / "Cuttle" / "device-worker.log"


def _log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main() -> int:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        from api.device_workers.config import coordinator_base_url, device_workers_enabled
        from api.device_workers.worker_loop import run_remote_worker_loop
    except Exception:
        _log("FATAL import failed:\n" + traceback.format_exc())
        return 1

    if not device_workers_enabled():
        _log("device workers disabled — exiting")
        return 0

    url = coordinator_base_url()
    if not url:
        _log(
            "Set CUTTLE_DEVICE_WORKERS_COORDINATOR_URL "
            "(or device_workers.coordinator_url in settings.json)"
        )
        return 2

    running = True

    def should_continue() -> bool:
        return running

    try:
        _log(f"starting package worker → {url}")
        run_remote_worker_loop(should_continue=should_continue, base_url=url)
    except KeyboardInterrupt:
        running = False
        _log("stopped")
    except Exception:
        _log("FATAL:\n" + traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
