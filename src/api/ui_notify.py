"""Tray + in-app toast notifications (daemon pystray queue, app shell poll).

Writers call :func:`notify_tray`; the daemon drains ``cuttle_notify_queue.jsonl``
for its tray balloon and ``GET /api/ui-toasts`` drains ``cuttle_ui_toasts.jsonl``
for Electron/app_shell toasts. Both files sit at the checkout root, where the
daemon reads them.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List

_lock = threading.Lock()

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NOTIFY_PATH = _PROJECT_ROOT / "cuttle_notify_queue.jsonl"
_UI_TOAST_PATH = _PROJECT_ROOT / "cuttle_ui_toasts.jsonl"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def notify_tray(message: str, *, variant: str = "info") -> None:
    """Notify Electron/web UI (toast+chirp poll) and daemon pystray queue."""
    title = "Cuttle"
    if variant == "error":
        title = "Cuttle — Error"
    elif variant == "success":
        title = "Cuttle — Success"
    msg = (message or "")[:500]
    tray_entry = json.dumps({"title": title, "message": msg}, ensure_ascii=False) + "\n"
    ui_entry = (
        json.dumps(
            {"title": title, "message": msg, "variant": variant or "info", "ts": _utc_iso()},
            ensure_ascii=False,
        )
        + "\n"
    )
    try:
        with _lock:
            with open(_NOTIFY_PATH, "a", encoding="utf-8") as f:
                f.write(tray_entry)
            with open(_UI_TOAST_PATH, "a", encoding="utf-8") as f:
                f.write(ui_entry)
    except OSError:
        pass


def pull_ui_toasts() -> List[dict]:
    """Drain pending UI toasts (Flask → Electron/app_shell)."""
    with _lock:
        if not _UI_TOAST_PATH.is_file() or _UI_TOAST_PATH.stat().st_size == 0:
            return []
        try:
            raw = _UI_TOAST_PATH.read_text(encoding="utf-8")
            _UI_TOAST_PATH.write_text("", encoding="utf-8")
        except OSError:
            return []
    out: List[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and entry.get("message"):
                out.append(entry)
        except json.JSONDecodeError:
            continue
    return out
