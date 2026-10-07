"""``python -m api.gizmos types|list|get|create|update|move|remove|data|usage``.

One-shot JSON output with nonzero exit codes on error. Every verb requires
the ``gizmos`` flag, matching the HTTP surface. Writes land in the same
SQLite store the shell polls, so an open Cuttle window picks them up within
seconds — no Flask restart, no chat message.

Examples::

    PYTHONPATH=src .venv/bin/python -m api.gizmos create usage_meter --agent codex --dock titlebar
    PYTHONPATH=src .venv/bin/python -m api.gizmos move usage-meter-1a2b3c4d --dock float --x 0.8 --y 0.1
    PYTHONPATH=src .venv/bin/python -m api.gizmos usage claude
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional


def _emit(payload: Dict[str, Any], code: int = 0) -> int:
    print(json.dumps(payload, indent=2, default=str))
    return code


def _json_arg(raw: Optional[str], name: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--{name} is not JSON ({exc})") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"--{name} must be a JSON object")
    return value


def _config_from(args: argparse.Namespace) -> Dict[str, Any]:
    config = _json_arg(getattr(args, "config", None), "config")
    for key in ("agent", "window", "show"):
        value = getattr(args, key, None)
        if value is not None:
            config[key] = value
    notify = getattr(args, "notify_on_unblock", None)
    if notify is not None:
        config["notify_on_unblock"] = bool(notify)
    return config


def _placement_from(args: argparse.Namespace) -> Dict[str, Any]:
    placement: Dict[str, Any] = {}
    for key in ("dock", "order", "x", "y"):
        value = getattr(args, key, None)
        if value is not None:
            placement[key] = value
    return placement


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.gizmos",
        description="Cuttle gizmos (experimental flag `gizmos`): shell-docked live UI objects.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("types", help="registered gizmo types and their options")
    sub.add_parser("list", help="every gizmo + the store revision")
    p_get = sub.add_parser("get", help="one gizmo")
    p_get.add_argument("gizmo_id")

    def placement_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dock", choices=["titlebar", "rail", "float", "popout"])
        p.add_argument("--order", type=float)
        p.add_argument("--x", type=float, help="float position, 0..1 of viewport width")
        p.add_argument("--y", type=float, help="float position, 0..1 of viewport height")

    def config_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--agent", help="usage_meter: codex | claude | cursor | …")
        p.add_argument("--window", help="usage_meter: 'tightest' or a window id (five_hour, weekly, …)")
        p.add_argument("--show", choices=["remaining", "used"])
        p.add_argument("--notify-on-unblock", dest="notify_on_unblock",
                       action="store_true", default=None,
                       help="notify (tray + UI toast) when a blocked account unblocks")
        p.add_argument("--no-notify-on-unblock", dest="notify_on_unblock",
                       action="store_false", default=None,
                       help="stop notifying when a blocked account unblocks")
        p.add_argument("--config", help="JSON object merged into the config")

    p_create = sub.add_parser("create", help="spawn a gizmo")
    p_create.add_argument("type", help="e.g. usage_meter")
    p_create.add_argument("--id", dest="gizmo_id")
    p_create.add_argument("--title")
    config_args(p_create)
    placement_args(p_create)

    p_update = sub.add_parser("update", help="edit title/config/placement")
    p_update.add_argument("gizmo_id")
    p_update.add_argument("--title")
    config_args(p_update)
    placement_args(p_update)

    p_move = sub.add_parser("move", help="re-dock a gizmo")
    p_move.add_argument("gizmo_id")
    placement_args(p_move)

    p_remove = sub.add_parser("remove", help="delete a gizmo")
    p_remove.add_argument("gizmo_id")

    p_data = sub.add_parser("data", help="live data a gizmo renders")
    p_data.add_argument("gizmo_id")
    p_data.add_argument("--refresh", action="store_true")

    p_usage = sub.add_parser("usage", help="normalized plan usage for one agent")
    p_usage.add_argument("agent", nargs="?", help="omit to list providers")
    p_usage.add_argument("--refresh", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 2

    from api.gizmos import FLAG_ID, is_enabled, service, usage

    if not is_enabled():
        return _emit({"success": False, "disabled": True,
                      "error": f"gizmos flag is off (python -m api.experimental set {FLAG_ID} on)"}, 1)
    try:
        if args.cmd == "types":
            return _emit({"success": True, "types": service.types()})
        if args.cmd == "list":
            return _emit({"success": True, **service.list_payload()})
        if args.cmd == "get":
            return _emit({"success": True, "gizmo": service.get(args.gizmo_id)})
        if args.cmd == "create":
            gizmo = service.create(args.type, config=_config_from(args),
                                   placement=_placement_from(args) or None,
                                   title=args.title, gizmo_id=args.gizmo_id, created_by="agent")
            return _emit({"success": True, "gizmo": gizmo})
        if args.cmd == "update":
            config = _config_from(args)
            placement = _placement_from(args)
            gizmo = service.update(args.gizmo_id, title=args.title,
                                   config=config or None, placement=placement or None)
            return _emit({"success": True, "gizmo": gizmo})
        if args.cmd == "move":
            placement = _placement_from(args)
            if not placement:
                return _emit({"success": False, "error": "pass --dock and/or --order/--x/--y"}, 2)
            return _emit({"success": True, "gizmo": service.update(args.gizmo_id, placement=placement)})
        if args.cmd == "remove":
            service.remove(args.gizmo_id)
            return _emit({"success": True, "id": args.gizmo_id})
        if args.cmd == "data":
            return _emit({"success": True, "id": args.gizmo_id,
                          "data": service.resolve_data(args.gizmo_id, refresh=args.refresh)})
        if args.cmd == "usage":
            if not args.agent:
                return _emit({"success": True, "providers": usage.providers()})
            return _emit({"success": True, "usage": usage.snapshot(args.agent, force=args.refresh)})
    except service.GizmoError as exc:
        return _emit({"success": False, "error": str(exc), "code": exc.code}, 2)
    except ValueError as exc:
        return _emit({"success": False, "error": str(exc)}, 2)
    except SystemExit as exc:
        return _emit({"success": False, "error": str(exc)}, 2)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
