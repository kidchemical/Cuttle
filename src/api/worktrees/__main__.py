"""``python -m api.worktrees`` — agent-ops verbs for Cuttle-managed git worktrees.

Owner-run / agent-run CLI (no HTTP, no owner gate — filesystem access is the
gate, matching the other ``python -m api.*`` verbs). Decisions live in
``api.worktrees.service`` / ``cleanup``; this module only parses args and prints
JSON.

    python -m api.worktrees list [--project-id N]
    python -m api.worktrees create --project-id N [--branch B] [--base-branch B] [--bind [CH-…]]
    python -m api.worktrees adopt PATH --project-id N [--bind [CH-…]]
    python -m api.worktrees bind (CHECKOUT_UID | --local) [--chat CH-…] [--as CH-…]
    python -m api.worktrees show [--chat CH-…]
    python -m api.worktrees remove CHECKOUT_UID [--force]
    python -m api.worktrees move CHECKOUT_UID [--to DIR]
    python -m api.worktrees root [--project-id N] [--set DIR | --clear]
    python -m api.worktrees orphans --project-id N
    python -m api.worktrees reconcile [--project-id N]

``--chat`` / ``--bind`` default to the calling chat (``CUTTLE_CHAT_SESSION_ID``,
set for every agent turn); ``--as`` defaults to it too. Binding a chat that is
neither the caller's nor one of its sub-agent chats succeeds with a ``warning``.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from typing import Any, Dict, Optional, Tuple

from api.worktrees import chat_binding, cleanup, registry, roots, service


def _resolve_project(project_id) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """``(project_row, primary_path, error)`` for a project id."""
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        return None, None, "project_id is required"
    try:
        from managers.project_manager import project_manager
        from managers.project_locations import require_project_path
    except Exception as exc:  # pragma: no cover - import guard
        return None, None, f"project registry unavailable: {exc}"
    proj = project_manager.get_project(pid) if project_manager is not None else None
    if not proj:
        return None, None, "Unknown project"
    try:
        return proj, require_project_path(proj), None
    except Exception as exc:
        return proj, None, str(exc)


def _settings_block() -> Dict[str, Any]:
    try:
        from managers.settings_manager import get_settings_manager
        value = get_settings_manager().get_setting("worktrees", {})
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _default_branch(prefix: Optional[str]) -> str:
    stem = str(prefix or "cuttle").strip("/") or "cuttle"
    return f"{stem}/{secrets.token_hex(3)}"


def _checkout_json(checkout) -> Dict[str, Any]:
    return dict(checkout.__dict__)


def _cmd_list(args) -> Dict[str, Any]:
    pid = int(args.project_id) if args.project_id not in (None, "") else None
    return {"success": True, "worktrees": [_checkout_json(c) for c in service.list_worktrees(pid)]}


def _cmd_create(args) -> Dict[str, Any]:
    proj, primary, err = _resolve_project(args.project_id)
    if err:
        return {"success": False, "error": err}
    settings = _settings_block()
    branch = str(args.branch or "").strip() or _default_branch(settings.get("branch_prefix"))
    base_branch = str(args.base_branch or proj.get("default_branch") or "").strip()
    try:
        checkout = service.create_worktree(
            project_id=int(args.project_id),
            project_name=str(proj.get("name") or ""),
            primary_path=primary,
            base_branch=base_branch,
            branch=branch,
            settings=settings,
        )
    except service.WorktreeError as exc:
        return {"success": False, "error": str(exc)}
    return _with_bind(args, {"success": True, "worktree": _checkout_json(checkout)}, checkout)


def _calling_chat() -> Optional[str]:
    return str(os.getenv("CUTTLE_CHAT_SESSION_ID") or "").strip() or None


def _bind(chat, checkout_uid, actor) -> Dict[str, Any]:
    if not chat:
        return {"success": False, "error": "no chat: pass --chat CH-… (no CUTTLE_CHAT_SESSION_ID)"}
    try:
        result = chat_binding.bind_chat(chat, checkout_uid, actor=actor or _calling_chat())
    except chat_binding.ChatBindingError as exc:
        return {"success": False, "error": str(exc)}
    if result.get("warning"):
        print(f"WARNING: {result['warning']}", file=sys.stderr)
    return {"success": True, "binding": result}


def _with_bind(args, payload: Dict[str, Any], checkout) -> Dict[str, Any]:
    """Apply ``--bind`` after create/adopt; the checkout survives a bind failure."""
    if args.bind is None:
        return payload
    chat = _calling_chat() if args.bind == "self" else args.bind
    bound = _bind(chat, checkout.checkout_uid, getattr(args, "actor", None))
    if not bound.get("success"):
        return {**payload, "success": False, "error": f"created but not bound: {bound['error']}"}
    return {**payload, **bound}


def _cmd_adopt(args) -> Dict[str, Any]:
    _proj, primary, err = _resolve_project(args.project_id)
    if err:
        return {"success": False, "error": err}
    try:
        checkout = service.adopt_worktree(
            project_id=int(args.project_id), primary_path=primary, path=args.path,
        )
    except service.WorktreeError as exc:
        return {"success": False, "error": str(exc)}
    return _with_bind(args, {"success": True, "worktree": _checkout_json(checkout)}, checkout)


def _cmd_bind(args) -> Dict[str, Any]:
    if bool(args.local) == bool(args.checkout_uid):
        return {"success": False, "error": "pass exactly one of CHECKOUT_UID or --local"}
    return _bind(args.chat or _calling_chat(), None if args.local else args.checkout_uid, args.actor)


def _cmd_show(args) -> Dict[str, Any]:
    chat = args.chat or _calling_chat()
    if not chat:
        return {"success": False, "error": "no chat: pass --chat CH-… (no CUTTLE_CHAT_SESSION_ID)"}
    try:
        return {"success": True, **chat_binding.get_chat_binding(chat)}
    except chat_binding.ChatBindingError as exc:
        return {"success": False, "error": str(exc)}


def _cmd_remove(args) -> Dict[str, Any]:
    checkout = registry.get_checkout(args.checkout_uid)
    if checkout is None:
        return {"success": False, "error": "Unknown checkout"}
    _proj, primary, err = _resolve_project(checkout.project_id)
    if err:
        return {"success": False, "error": err}
    try:
        result = service.remove_worktree(args.checkout_uid, primary_path=primary, force=bool(args.force))
    except service.WorktreeError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, **result}


def _cmd_move(args) -> Dict[str, Any]:
    checkout = registry.get_checkout(args.checkout_uid)
    if checkout is None:
        return {"success": False, "error": "Unknown checkout"}
    proj, primary, err = _resolve_project(checkout.project_id)
    if err:
        return {"success": False, "error": err}
    try:
        result = service.move_worktree(
            args.checkout_uid, project_name=str(proj.get("name") or ""),
            primary_path=primary, to_path=args.to or None, settings=_settings_block(),
        )
    except service.WorktreeError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, **result}


def _root_report(settings: Dict[str, Any], project_id: Optional[str]) -> Dict[str, Any]:
    active = roots.resolve_worktrees_root(settings, project_id=project_id)
    table = settings.get("project_roots")
    return {
        "success": True,
        "project_id": project_id,
        "root": active.root if active else None,
        "is_default": bool(active and active.is_default),
        "usable": active is not None,
        "global_root": str(settings.get("root") or ""),
        "project_roots": dict(table) if isinstance(table, dict) else {},
    }


def _cmd_root(args) -> Dict[str, Any]:
    """Show or set where new worktrees go: one project's folder or the global one."""
    pid = str(args.project_id).strip() if args.project_id not in (None, "") else None
    if args.set is None and not args.clear:
        return _root_report(_settings_block(), pid)
    new_root = ""
    if args.set is not None:
        new_root = roots.validate_root(args.set) or ""
        if not new_root:
            return {"success": False,
                    "error": "The folder must be an absolute path on this machine (not a drive root)."}
    if pid is not None:
        _proj, _primary, err = _resolve_project(pid)
        if err:
            return {"success": False, "error": err}

    def _apply(block):
        block = dict(block) if isinstance(block, dict) else {}
        previous = [str(p) for p in (block.get("previous_roots") or []) if str(p or "").strip()]
        if pid is not None:
            table = dict(block.get("project_roots") or {})
            old = str(table.get(pid) or "").strip()
            if new_root:
                table[pid] = new_root
            else:
                table.pop(pid, None)
            block["project_roots"] = table
        else:
            old = str(block.get("root") or "").strip()
            block["root"] = new_root
        # Worktrees already under the old folder stay managed.
        if old and old != new_root and old not in previous:
            previous.append(old)
        block["previous_roots"] = previous
        return block

    from managers.settings_manager import get_settings_manager
    if not get_settings_manager().update_setting("worktrees", _apply):
        return {"success": False, "error": "Could not save the setting."}
    return _root_report(_settings_block(), pid)


def _cmd_orphans(args) -> Dict[str, Any]:
    _proj, primary, err = _resolve_project(args.project_id)
    if err:
        return {"success": False, "error": err}
    return {
        "success": True,
        "orphans": cleanup.list_orphans(primary_path=primary, settings=_settings_block()),
    }


def _cmd_reconcile(args) -> Dict[str, Any]:
    primary = None
    if args.project_id not in (None, ""):
        _proj, primary, err = _resolve_project(args.project_id)
        if err:
            return {"success": False, "error": err}
    return {"success": True, **cleanup.reconcile(primary_path=primary)}


def _add_bind_args(p) -> None:
    p.add_argument("--bind", nargs="?", const="self", default=None, metavar="CH",
                   help="also bind a chat to it (default: the calling chat)")
    p.add_argument("--as", dest="actor", default="", help="acting CH-… (default: the calling chat)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.worktrees",
        description="Cuttle-managed git worktrees (agent ops).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="registered worktrees")
    p.add_argument("--project-id")
    p.set_defaults(fn=_cmd_list)

    p = sub.add_parser("create", help="create a worktree on a fresh branch")
    p.add_argument("--project-id", required=True)
    p.add_argument("--branch", default="", help="new branch name (default <prefix>/<hex>)")
    p.add_argument("--base-branch", default="", help="base ref (default: project default branch)")
    _add_bind_args(p)
    p.set_defaults(fn=_cmd_create)

    p = sub.add_parser("adopt", help="register an existing `git worktree add` checkout")
    p.add_argument("path")
    p.add_argument("--project-id", required=True)
    _add_bind_args(p)
    p.set_defaults(fn=_cmd_adopt)

    p = sub.add_parser("bind", help="set the worktree a chat runs in (its chip)")
    p.add_argument("checkout_uid", nargs="?", default="")
    p.add_argument("--local", action="store_true", help="unbind: run in the primary checkout")
    p.add_argument("--chat", default="", help="target CH-… (default: the calling chat)")
    p.add_argument("--as", dest="actor", default="", help="acting CH-… (default: the calling chat)")
    p.set_defaults(fn=_cmd_bind)

    p = sub.add_parser("show", help="a chat's project and bound worktree")
    p.add_argument("--chat", default="", help="CH-… (default: the calling chat)")
    p.set_defaults(fn=_cmd_show)

    p = sub.add_parser("remove", help="remove a registered worktree (dirty-guarded)")
    p.add_argument("checkout_uid")
    p.add_argument("--force", action="store_true", help="remove even if dirty")
    p.set_defaults(fn=_cmd_remove)

    p = sub.add_parser("move", help="move a registered worktree (default: to the configured root)")
    p.add_argument("checkout_uid")
    p.add_argument("--to", default="", help="new directory (absolute); default <root>/<repo-id>/<worktree-id>")
    p.set_defaults(fn=_cmd_move)

    p = sub.add_parser("root", help="show or set the folder new worktrees go in")
    p.add_argument("--project-id", help="one project's folder (omit for the global one)")
    p.add_argument("--set", default=None, metavar="DIR", help="absolute folder on this machine")
    p.add_argument("--clear", action="store_true", help="back to the global / built-in folder")
    p.set_defaults(fn=_cmd_root)

    p = sub.add_parser("orphans", help="managed-root git worktrees missing from the registry")
    p.add_argument("--project-id", required=True)
    p.set_defaults(fn=_cmd_orphans)

    p = sub.add_parser("reconcile", help="mark missing checkouts and prune stale admin entries")
    p.add_argument("--project-id")
    p.set_defaults(fn=_cmd_reconcile)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    result = args.fn(args)
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
