"""Tray + in-app toast notifications (daemon pystray queue, app shell poll).

Writers call :func:`notify_tray`; the daemon drains the notify queue for its
tray balloon and ``GET /api/ui-toasts`` drains the ui toast file for
Electron/app_shell toasts. Both files live in the per-user instance state
dir (``core.runtime_paths.instance_state_dir``) — never the checkout root,
which may be read-only once installed.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import List

from core.runtime_paths import notify_queue_path, ui_toast_path

_lock = threading.Lock()

_NOTIFY_PATH = notify_queue_path()
_UI_TOAST_PATH = ui_toast_path()


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
            _NOTIFY_PATH.parent.mkdir(parents=True, exist_ok=True)
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
