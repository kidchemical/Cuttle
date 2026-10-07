"""Tray + in-app toast notifications (daemon pystray queue, app shell poll).

Writers call :func:`notify_tray`; the daemon drains the notify queue for its
tray balloon and ``GET /api/ui-toasts`` drains the ui toast file for
Electron/app_shell toasts. Both files live in the per-user instance state
dir (``core.runtime_paths.instance_state_dir``) — never the checkout root,
which may be read-only once installed.

Agent CLI: ``python -m api.ui_notify send "Build done" --variant success``
(JSON out, nonzero failures).
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


def build_parser():
    """Argparse front for ``python -m api.ui_notify`` (agent ops CLI)."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m api.ui_notify",
        description="Cuttle tray + in-app toast notifications.",
    )
    sub = parser.add_subparsers(dest="cmd")
    p_send = sub.add_parser("send", help="queue a tray + UI toast")
    p_send.add_argument("message", help="toast text (max 500 chars)")
    p_send.add_argument("--variant", default="info",
                        help="toast variant (info, success, warning, error)")
    p_pending = sub.add_parser("pending", help="drain pending UI toasts")
    p_pending.add_argument("--limit", type=int, default=50,
                           help="max toasts to return")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "send":
        message = str(args.message or "").strip()
        if not message:
            print(json.dumps({"success": False, "error": "message required"}))
            return 2
        variant = str(args.variant or "info").strip().lower() or "info"
        if variant not in ("info", "success", "warning", "error"):
            print(json.dumps({"success": False,
                              "error": "variant must be info, success, warning, or error"}))
            return 2
        notify_tray(message, variant=variant)
        print(json.dumps({"success": True, "message": message[:500], "variant": variant}))
        return 0
    if args.cmd == "pending":
        try:
            limit = max(1, min(500, int(args.limit)))
        except (TypeError, ValueError):
            print(json.dumps({"success": False, "error": "limit must be a number"}))
            return 2
        print(json.dumps({"success": True, "toasts": pull_ui_toasts()[:limit]}))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
