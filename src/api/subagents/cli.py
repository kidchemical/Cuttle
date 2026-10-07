"""CLI: ``python -m api.subagents <verb> …``

Spawn real child chats from any parent chat, wait for results, message or
cancel them. See ``.cuttle_global/docs/subagents.md``.

Examples::

    .venv\\Scripts\\python.exe -m api.subagents spawn --parent CH-000535 --wait --json \\
        --child "{\\"title\\":\\"Chef A\\",\\"agent\\":\\"cursor\\",\\"message\\":\\"...\\"}" \\
        --child "{\\"title\\":\\"Chef B\\",\\"agent\\":\\"cursor\\",\\"model\\":\\"grok-4.6\\",\\"effort\\":\\"low\\",\\"message\\":\\"...\\"}"
    .venv\\Scripts\\python.exe -m api.subagents status --parent CH-000535 --json
    .venv\\Scripts\\python.exe -m api.subagents wait BATCH_ID --json
    .venv\\Scripts\\python.exe -m api.subagents message --session CH-000540 --text "follow up" --wait --json
    .venv\\Scripts\\python.exe -m api.subagents cancel --batch BATCH_ID --json
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
    from api.cuttle_ui_capabilities import numeric_chat_session_id

    sid = numeric_chat_session_id(str(raw).strip())
    if sid is None:
        raise SystemExit(f"error: invalid chat handle {raw!r}")
    return int(sid)


def _fail(payload: Dict[str, Any], as_json: bool, code: int = 2) -> int:
    _emit(payload, as_json=as_json, text=f"error: {payload.get('error') or 'failed'}")
    return code


def _require_wait(verb: str, args: argparse.Namespace) -> Optional[int]:
    """One-shot CLI processes cannot own detached work.

    Child turns run on daemon threads of this process; without ``--wait``
    the process exits immediately, the threads die with it, and the batch
    is left with pending/running rows no live owner can finish. Reject
    before any row exists or any worker starts. In-process service callers
    (which outlive the work) keep their own ``wait=False`` contract.
    """
    if args.wait:
        return None
    return _fail(
        {
            "ok": False,
            "error": (
                f"{verb} without --wait is not supported: this CLI process "
                "would exit immediately and its worker threads would die "
                "with it, leaving pending/running rows behind. Pass --wait "
                "so this process supervises the round to completion."
            ),
        },
        args.json,
    )


def _cmd_spawn(args: argparse.Namespace) -> int:
    from api.subagents.service import SubagentError, cancel_batch, spawn

    denied = _require_wait("spawn", args)
    if denied is not None:
        return denied
    parent = _parse_session(args.parent)
    children: List[Any] = list(args.child or [])
    if args.children_json:
        extra = json.loads(args.children_json)
        if isinstance(extra, list):
            children.extend(extra)
        else:
            children.append(extra)
    if not children:
        return _fail(
            {"ok": False, "error": "pass --child JSON (repeatable) or --children-json"},
            args.json,
        )
    db = _open_db(args.db)
    try:
        payload = spawn(
            parent_session_id=int(parent),
            children=children,
            collect=args.collect,
            lifetime=args.lifetime,
            wait=bool(args.wait),
            timeout=float(args.timeout),
            route=bool(args.route),
            watch=bool(args.watch),
            tasks=bool(args.tasks),
            db=db,
        )
    except SubagentError as exc:
        return _fail({"ok": False, "error": str(exc)}, args.json)
    except Exception as exc:
        return _fail({"ok": False, "error": str(exc)}, args.json, code=1)
    if payload.get("timed_out"):
        # This CLI created and supervised this round: a normal timeout must
        # not abandon it with active rows (the same defect as exiting without
        # --wait). Terminally cancel the unfinished work we own through the
        # existing cancellation API, then exit nonzero. The observation-only
        # `wait` verb never cancels another supervisor's batch.
        try:
            cancelled = cancel_batch(payload["id"], db=db)
        except SubagentError as exc:
            return _fail({"ok": False, "error": str(exc)}, args.json, code=1)
        out = dict(cancelled)
        out["ok"] = False
        out["timed_out"] = True
        out["error"] = (
            f"timed out after {float(args.timeout):g}s; "
            "unfinished work of this batch was cancelled"
        )
        _emit(
            out,
            as_json=args.json,
            text=f"batch {payload.get('id')}  timed out — unfinished work cancelled",
        )
        return 1
    lines = [
        f"batch {payload.get('id')}  collect={payload.get('collect')}  "
        f"lifetime={payload.get('lifetime')}  status={payload.get('status')}"
    ]
    for child in payload.get("children") or []:
        lines.append(
            f"  {child.get('handle')}  {child.get('label')}  "
            f"{child.get('agent')} {child.get('model') or ''}  {child.get('status')}"
        )
    _emit(payload, as_json=args.json, text="\n".join(lines))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from api.subagents.service import status_payload

    db = _open_db(args.db)
    payload = status_payload(
        batch_id=args.batch,
        parent_session_id=_parse_session(args.parent) if args.parent else None,
        db=db,
    )
    if not payload.get("ok"):
        return _fail(payload, args.json)
    if args.json:
        _emit(payload, as_json=True, text="")
        return 0
    if payload.get("children") is not None:
        lines = [f"batch {payload.get('id')}  {payload.get('status')}"]
        for child in payload.get("children") or []:
            lines.append(f"  {child.get('handle')}  {child.get('status')}  {child.get('label')}")
        _emit(payload, as_json=False, text="\n".join(lines))
        return 0
    lines = []
    for batch in payload.get("batches") or []:
        lines.append(f"{batch.get('id')}  {batch.get('status')}  {len(batch.get('children') or [])} children")
    _emit(payload, as_json=False, text="\n".join(lines) or "(none)")
    return 0


def _cmd_wait(args: argparse.Namespace) -> int:
    from api.subagents.service import SubagentError, wait_batch

    db = _open_db(args.db)
    try:
        # Observation only: poll rows another owner may be advancing. Never
        # start stale pending work and never cancel another owner's batch —
        # `advance=False` disables serial kick-ahead for this caller.
        payload = wait_batch(
            args.batch, timeout=float(args.timeout), db=db, advance=False
        )
    except SubagentError as exc:
        return _fail({"ok": False, "error": str(exc)}, args.json)
    _emit(payload, as_json=args.json, text=f"batch {payload.get('id')}  {payload.get('status')}")
    return 0


def _cmd_message(args: argparse.Namespace) -> int:
    from api.subagents.service import SubagentError, message_child

    denied = _require_wait("message", args)
    if denied is not None:
        return denied
    sid = _parse_session(args.session)
    db = _open_db(args.db)
    try:
        payload = message_child(
            int(sid),
            args.text,
            wait=bool(args.wait),
            timeout=float(args.timeout),
            db=db,
        )
    except SubagentError as exc:
        return _fail({"ok": False, "error": str(exc)}, args.json)
    child = payload.get("child") or {}
    _emit(
        payload,
        as_json=args.json,
        text=f"{child.get('handle')}  {child.get('status')}",
    )
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    from api.subagents.service import SubagentError, cancel_batch, cancel_session

    db = _open_db(args.db)
    try:
        if args.batch:
            payload = cancel_batch(args.batch, db=db)
        else:
            sid = _parse_session(args.session or args.parent)
            payload = cancel_session(int(sid), db=db)
    except SubagentError as exc:
        return _fail({"ok": False, "error": str(exc)}, args.json)
    _emit(payload, as_json=args.json, text="cancelled")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from api.subagents.service import status_payload

    db = _open_db(args.db)
    parent = _parse_session(args.parent)
    payload = status_payload(parent_session_id=int(parent), db=db)
    _emit(payload, as_json=args.json, text=json.dumps(payload.get("batches") or [], indent=2))
    return 0 if payload.get("ok") else 2


def _cmd_close(args: argparse.Namespace) -> int:
    from api.subagents.service import SubagentError, close_batch

    db = _open_db(args.db)
    try:
        payload = close_batch(args.batch, db=db)
    except SubagentError as exc:
        return _fail({"ok": False, "error": str(exc)}, args.json)
    _emit(payload, as_json=args.json, text=f"closed {payload.get('id')}")
    return 0


def _cmd_profiles(args: argparse.Namespace) -> int:
    from api.subagents import profiles as profiles_mod

    db = _open_db(args.db)
    action = str(args.action or "list").strip().lower()
    profile_id = (getattr(args, "profile_id_opt", None) or args.profile_id or "").strip()
    try:
        user_id = profiles_mod.infer_user_id(
            db,
            user_id=args.user_id,
            parent_session_id=_parse_session(args.parent) if args.parent else None,
        )
    except Exception as exc:
        return _fail({"ok": False, "error": str(exc)}, args.json)
    try:
        if action == "list":
            rows = profiles_mod.list_profiles(db, user_id)
            payload = {"ok": True, "user_id": user_id, "profiles": rows}
            lines = [
                f"{p['id']:12}  {p['name']:16}  agent={p['agent'] or 'none'}  "
                f"{'builtin' if p['builtin'] else 'custom'}  {p.get('avatar') or ''}"
                for p in rows
            ]
            _emit(payload, as_json=args.json, text="\n".join(lines) or "(none)")
            return 0
        if action == "get":
            if not profile_id:
                return _fail({"ok": False, "error": "profiles get requires an id"}, args.json)
            row = profiles_mod.resolve(db, user_id, profile_id)
            if not row:
                return _fail({"ok": False, "error": f"profile {profile_id!r} not found"}, args.json)
            _emit({"ok": True, "profile": row}, as_json=args.json, text=f"{row['id']}  {row['name']}")
            return 0
        if action == "save":
            if not profile_id:
                return _fail({"ok": False, "error": "profiles save requires --id"}, args.json)
            row = profiles_mod.save_profile(
                db,
                user_id,
                profile_id=profile_id,
                name=args.name or "",
                avatar=args.avatar or "",
                agent=args.agent if args.agent is not None else "none",
                model=args.model or "",
                effort=args.effort or "",
            )
            _emit({"ok": True, "profile": row}, as_json=args.json, text=f"saved {row['id']}")
            return 0
        if action == "delete":
            if not profile_id:
                return _fail({"ok": False, "error": "profiles delete requires an id"}, args.json)
            ok = profiles_mod.delete_profile(db, user_id, profile_id)
            if not ok:
                return _fail({"ok": False, "error": f"profile {profile_id!r} not found"}, args.json)
            _emit({"ok": True, "deleted": profile_id}, as_json=args.json, text=f"deleted {profile_id}")
            return 0
    except ValueError as exc:
        return _fail({"ok": False, "error": str(exc)}, args.json)
    return _fail({"ok": False, "error": f"unknown profiles action {action!r}"}, args.json)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON on stdout")
    common.add_argument("--db", default=None, help="Auth DB path (tests)")

    p = argparse.ArgumentParser(
        prog="python -m api.subagents",
        description="Spawn and manage Cuttle sub-agent child chats.",
        parents=[common],
    )
    sub = p.add_subparsers(dest="verb", required=True)

    sp = sub.add_parser("spawn", parents=[common], help="Create child chats and optionally wait")
    sp.add_argument("--parent", required=True, help="Parent CH- handle")
    sp.add_argument(
        "--child",
        action="append",
        default=[],
        help='Child JSON: {"title","agent","model","effort","message","profile","avatar"} (repeat)',
    )
    sp.add_argument("--children-json", default=None, help="JSON array of child objects")
    sp.add_argument(
        "--collect",
        default="all",
        help="all | first | serial",
    )
    sp.add_argument(
        "--lifetime",
        default="one_shot",
        help="one_shot | conversational",
    )
    sp.add_argument(
        "--wait",
        action="store_true",
        help="Required: supervise this round to completion in this process",
    )
    sp.add_argument("--timeout", type=float, default=900.0)
    sp.add_argument("--route", action="store_true", help="Ask the Cuttle router to pick each child's harness")
    sp.add_argument("--watch", action="store_true", help="Write a job_watch status JSON with per-child bars")
    sp.add_argument("--tasks", action="store_true", help="Create a Tasks gizmo on the parent chat")
    sp.set_defaults(func=_cmd_spawn)

    st = sub.add_parser("status", parents=[common], help="Show a batch or all batches for a parent")
    st.add_argument("--batch", default=None)
    st.add_argument("--parent", default=None)
    st.set_defaults(func=_cmd_status)

    wt = sub.add_parser(
        "wait",
        parents=[common],
        help="Poll a batch round (observation only; never starts or cancels work)",
    )
    wt.add_argument("batch")
    wt.add_argument("--timeout", type=float, default=900.0)
    wt.set_defaults(func=_cmd_wait)

    msg = sub.add_parser("message", parents=[common], help="Send a follow-up into a child chat")
    msg.add_argument("--session", required=True)
    msg.add_argument("--text", required=True)
    msg.add_argument(
        "--wait",
        action="store_true",
        help="Required: supervise this round to completion in this process",
    )
    msg.add_argument("--timeout", type=float, default=900.0)
    msg.set_defaults(func=_cmd_message)

    can = sub.add_parser("cancel", parents=[common], help="Cancel a batch or a child/parent session")
    can.add_argument("--batch", default=None)
    can.add_argument("--session", default=None)
    can.add_argument("--parent", default=None)
    can.set_defaults(func=_cmd_cancel)

    ls = sub.add_parser("list", parents=[common], help="List batches for a parent chat")
    ls.add_argument("--parent", required=True)
    ls.set_defaults(func=_cmd_list)

    cl = sub.add_parser("close", parents=[common], help="Mark a conversational batch finished")
    cl.add_argument("batch")
    cl.set_defaults(func=_cmd_close)

    pf = sub.add_parser("profiles", parents=[common], help="List, save, or delete agent profiles")
    pf.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=["list", "get", "save", "delete"],
    )
    pf.add_argument("profile_id", nargs="?", default=None, help="Profile id (get/delete)")
    pf.add_argument("--id", dest="profile_id_opt", default=None, help="Profile id (save)")
    pf.add_argument("--name", default=None)
    pf.add_argument("--avatar", default=None, help="Emoji, image URL, or 'cuttle'")
    pf.add_argument(
        "--agent",
        default=None,
        help="Harness preference: none | cursor | muse | codex | hermes | …",
    )
    pf.add_argument("--model", default="")
    pf.add_argument("--effort", default="")
    pf.add_argument("--parent", default=None, help="Infer user from this CH- session")
    pf.add_argument("--user-id", dest="user_id", type=int, default=None)
    pf.set_defaults(func=_cmd_profiles)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    _ensure_src_on_path()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
