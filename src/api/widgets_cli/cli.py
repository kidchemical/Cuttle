"""CLI: ``python -m api.widgets_cli <verb> …``

Inspect and patch Tasks widgets stored in ``cuttle_auth.db``. Semantics match
``apply_tasks_patch`` / ``/api/widgets``.

Compatibility alias. New agents use ``python -m api.gizmos tasks`` during
planning and work; markdown tags are deprecated.

Examples::

    .venv\\Scripts\\python.exe -m api.widgets_cli list --session CH-000465 --json
    .venv\\Scripts\\python.exe -m api.widgets_cli get auth-refactor --json
    .venv\\Scripts\\python.exe -m api.widgets_cli patch auth-refactor --set-done 1,1a --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _ensure_src_on_path() -> None:
    src = Path(__file__).resolve().parents[2]
    src_s = str(src)
    if src_s not in sys.path:
        sys.path.insert(0, src_s)


def _open_db(db_path: Optional[str] = None):
    from api.auth_db import AuthDatabase, get_auth_db

    if db_path:
        return AuthDatabase(Path(db_path))
    return get_auth_db()


def _emit(payload: Dict[str, Any], *, as_json: bool, text: str) -> None:
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _parse_session(raw: Optional[str]) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return None
    from api.cuttle_ui_capabilities import parse_chat_handle

    parsed = parse_chat_handle(str(raw).strip())
    if not parsed:
        raise SystemExit(f"error: invalid --session handle {raw!r}")
    return int(parsed["session_id"])


def _resolve_list_scope(db, args: argparse.Namespace) -> Dict[str, Any]:
    """Return user_id, session_id, project_path for list."""
    sid = _parse_session(getattr(args, "session", None))
    user_id = args.user_id
    project = (args.project or "").strip() or None
    if sid is not None:
        row = db.get_chat_session_by_id(sid)
        if not row:
            raise SystemExit(f"error: session {sid} not found")
        if user_id is None:
            user_id = int(row["user_id"])
        if not project:
            project = (row.get("project_path") or "").strip() or None
    if user_id is None:
        raise SystemExit("error: pass --session CH-… or --user-id N")
    return {
        "user_id": int(user_id),
        "session_id": sid,
        "project_path": project,
    }


def _csv_ids(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def _load_ops(args: argparse.Namespace) -> Dict[str, Any]:
    ops: Dict[str, Any] = {}
    raw = (getattr(args, "ops", None) or "").strip()
    if raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError as e:
            raise SystemExit(f"error: --ops is not JSON ({e})") from e
        if not isinstance(loaded, dict):
            raise SystemExit("error: --ops must be a JSON object")
        ops.update(loaded)
    done = _csv_ids(getattr(args, "set_done", None))
    undone = _csv_ids(getattr(args, "set_undone", None))
    remove = _csv_ids(getattr(args, "remove", None))
    if done:
        ops["set_done"] = list(ops.get("set_done") or []) + done
    if undone:
        ops["set_undone"] = list(ops.get("set_undone") or []) + undone
    if remove:
        ops["remove"] = list(ops.get("remove") or []) + remove
    if getattr(args, "description", None) is not None:
        ops["description"] = args.description
    return ops


def _public_widget(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    return {
        "id": row.get("id"),
        "type": row.get("type"),
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "scope": row.get("scope"),
        "session_id": row.get("session_id"),
        "project_path": row.get("project_path") or "",
        "status": row.get("status"),
        "edit_mode": row.get("edit_mode"),
        "revision": row.get("revision"),
        "payload": payload,
        "updated_at": row.get("updated_at"),
    }


def cmd_list(args: argparse.Namespace) -> int:
    from api.chat_widgets import format_tasks_digest

    db = _open_db(args.db)
    try:
        scope = _resolve_list_scope(db, args)
    except SystemExit as e:
        msg = str(e)
        if args.json:
            _emit({"ok": False, "error": "bad_args", "detail": msg}, as_json=True, text="")
        else:
            sys.stderr.write(msg + "\n")
        return 2
    rows = db.list_chat_widgets(
        user_id=scope["user_id"],
        session_id=scope["session_id"],
        project_path=scope["project_path"],
        status=str(args.status or "active"),
    )
    widgets = [_public_widget(r) for r in rows if r]
    payload = {
        "ok": True,
        "session_id": scope["session_id"],
        "user_id": scope["user_id"],
        "project_path": scope["project_path"] or "",
        "count": len(widgets),
        "widgets": widgets,
    }
    text = format_tasks_digest(widgets) or "(no widgets)\n"
    _emit(payload, as_json=args.json, text=text)
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    from api.chat_widgets import format_tasks_digest

    db = _open_db(args.db)
    row = db.get_chat_widget(args.widget_id)
    if not row:
        _emit(
            {"ok": False, "error": "not_found", "id": args.widget_id},
            as_json=args.json,
            text=f"not found: {args.widget_id}\n",
        )
        return 2
    widget = _public_widget(row)
    text = format_tasks_digest([widget]) or json.dumps(widget, indent=2)
    _emit({"ok": True, "widget": widget}, as_json=args.json, text=text)
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    from api.chat_widgets import (
        _description_from_sources,
        _norm_edit_mode,
        _norm_scope,
        apply_tasks_patch,
        resolve_tasks_widget_status,
    )

    try:
        ops = _load_ops(args)
    except SystemExit as e:
        msg = str(e)
        if args.json:
            _emit({"ok": False, "error": "bad_args", "detail": msg}, as_json=True, text="")
        else:
            sys.stderr.write(msg + "\n")
        return 2
    if not ops:
        msg = "error: pass --ops '{...}' and/or --set-done/--set-undone/--remove/--description"
        if args.json:
            _emit({"ok": False, "error": "bad_args", "detail": msg}, as_json=True, text="")
        else:
            sys.stderr.write(msg + "\n")
        return 2

    db = _open_db(args.db)
    existing = db.get_chat_widget(args.widget_id)
    if not existing:
        _emit(
            {"ok": False, "error": "not_found", "id": args.widget_id},
            as_json=args.json,
            text=f"not found: {args.widget_id}\n",
        )
        return 2

    from api.gizmos import tasks
    from core.agent_cli_env import operation_actor
    if args.title is not None:
        ops["title"] = args.title
    row = tasks.patch(args.widget_id, ops, db=db, actor=operation_actor(source="legacy_cli"))
    widget = _public_widget(row or {})
    _emit({"ok": True, "widget": widget}, as_json=args.json, text=f"patched {widget.get('id')} rev={widget.get('revision')}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=None, help="Override cuttle_auth.db path")
    common.add_argument("--json", action="store_true", help="Emit JSON")

    p = argparse.ArgumentParser(
        prog="python -m api.widgets_cli",
        description=(
            "Cuttle Tasks widgets — agent ops CLI. "
            "In a chat reply, prefer <cuttle_widget> tags. "
            "Use this to list, read, or patch the store."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    lst = sub.add_parser("list", parents=[common], help="List widgets for a chat")
    lst.add_argument("--session", default=None, help="CH-000182 or numeric session id")
    lst.add_argument("--user-id", type=int, default=None)
    lst.add_argument("--project", default=None, help="Project path (defaults from the session)")
    lst.add_argument("--status", default="active")
    lst.set_defaults(func=cmd_list)

    get_p = sub.add_parser("get", parents=[common], help="Show one widget")
    get_p.add_argument("widget_id")
    get_p.set_defaults(func=cmd_get)

    pat = sub.add_parser(
        "patch",
        parents=[common],
        help="Apply tasks patch ops (same JSON as a cuttle_widget patch body)",
    )
    pat.add_argument("widget_id")
    pat.add_argument("--ops", default="", help="JSON object: set_done, add, description, …")
    pat.add_argument("--set-done", default=None, help="Comma-separated item ids")
    pat.add_argument("--set-undone", default=None, help="Comma-separated item ids")
    pat.add_argument("--remove", default=None, help="Comma-separated item ids")
    pat.add_argument("--description", default=None, help="Replace the intent blurb (empty clears)")
    pat.add_argument("--title", default=None)
    pat.set_defaults(func=cmd_patch)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    _ensure_src_on_path()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except BrokenPipeError:
        return 0
    except SystemExit as e:
        code = e.code
        if isinstance(code, int):
            return code
        return 1
    except Exception as e:
        sys.stderr.write(f"error: {e}\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
