"""Agent verbs: ``python -m api.orchestrator <verb>``.

Inside a Cuttle turn ``--session`` defaults to ``CUTTLE_CHAT_SESSION_ID``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional


def _emit(payload: Dict[str, Any], as_json: bool, text: str = "") -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(text or json.dumps(payload, indent=2, ensure_ascii=False))


def _fail(message: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, indent=2))
    else:
        print(f"error: {message}", file=sys.stderr)
    return 2


def _session(args) -> Optional[str]:
    return getattr(args, "session", None) or os.environ.get("CUTTLE_CHAT_SESSION_ID") or None


def _user(args, db) -> int:
    from api.subagents.profiles import infer_user_id

    sid = None
    session = _session(args)
    if session:
        from api.orchestrator.service import parse_session

        sid = parse_session(session)
    return infer_user_id(db, user_id=getattr(args, "user_id", None), parent_session_id=sid)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON on stdout")
    common.add_argument("--session", default=None, help="CH- handle (default: this chat)")
    common.add_argument("--user-id", type=int, default=None)
    p = argparse.ArgumentParser(prog="python -m api.orchestrator",
                                description="Orchestrator playbooks and chat bindings.")
    sub = p.add_subparsers(dest="verb", required=True)
    sub.add_parser("list", parents=[common], help="List playbooks")
    g = sub.add_parser("get", parents=[common], help="Show one playbook")
    g.add_argument("playbook", nargs="?", default="", help="Playbook id (default: your default playbook)")
    s = sub.add_parser("save", parents=[common], help="Create/replace a playbook from JSON")
    s.add_argument("--file", default=None, help="JSON file (default: stdin)")
    d = sub.add_parser("delete", parents=[common], help="Delete a playbook")
    d.add_argument("playbook")
    st = sub.add_parser("start", parents=[common], help="Bind this chat to a playbook and print its brief")
    st.add_argument("--playbook", default="", help="Playbook id (default: the one set in Apps → Orchestrator)")
    st.add_argument("--goal", default="")
    sub.add_parser("stop", parents=[common], help="Unbind this chat")
    b = sub.add_parser("brief", parents=[common], help="Print the brief for this chat (or --playbook preview)")
    b.add_argument("--playbook", default="")
    sv = sub.add_parser("supervise-command", parents=[common],
                        help="Print the chat_cli supervise command for an orchestrator chat")
    sv.add_argument("target", help="Orchestrator CH- handle")
    sv.add_argument("--playbook", default="")
    sub.add_parser("bindings", parents=[common], help="List chats bound to playbooks")
    df = sub.add_parser("default", parents=[common], help="Show or set the default playbook")
    df.add_argument("playbook", nargs="?", default="")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from api.auth_db import get_auth_db
    from api.orchestrator import service

    as_json = args.json
    if not service.enabled():
        return _fail("Orchestrator is off. Enable it in Settings → Experimental "
                     "(python -m api.experimental set orchestrator on).", as_json)
    db = get_auth_db()
    try:
        if args.verb == "list":
            docs = service.list_playbooks(_user(args, db), db=db)
            _emit({"ok": True, "playbooks": docs}, as_json,
                  "\n".join(f"{d['id']}  {d['name']}" + ("  (seed)" if d.get("seed") else "")
                            + ("  (default)" if d.get("is_default") else "") for d in docs))
        elif args.verb == "get":
            doc = service.get_playbook(_user(args, db), args.playbook, db=db)
            _emit({"ok": True, "playbook": doc}, as_json)
        elif args.verb == "save":
            raw = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
            try:
                body = json.loads(raw)
            except ValueError as exc:
                return _fail(f"invalid JSON: {exc}", as_json)
            doc = service.save_playbook(_user(args, db), body, db=db)
            _emit({"ok": True, "playbook": doc}, as_json, f"saved {doc['id']}")
        elif args.verb == "delete":
            if not service.delete_playbook(_user(args, db), args.playbook):
                return _fail(f"playbook {args.playbook!r} not found", as_json)
            _emit({"ok": True, "deleted": args.playbook}, as_json, f"deleted {args.playbook}")
        elif args.verb == "start":
            session = _session(args)
            if not session:
                return _fail("start needs --session outside a Cuttle turn", as_json)
            out = service.bind(session, args.playbook, args.goal, db=db)
            _emit({"ok": True, **out}, as_json, out["brief"])
        elif args.verb == "stop":
            session = _session(args)
            if not session:
                return _fail("stop needs --session outside a Cuttle turn", as_json)
            _emit({"ok": True, "unbound": service.unbind(session, db=db)}, as_json, "unbound")
        elif args.verb == "brief":
            session = _session(args)
            out = service.brief(session, playbook_id=args.playbook,
                                user_id=None if session else _user(args, db), db=db)
            _emit({"ok": True, **out}, as_json, out["brief"])
        elif args.verb == "supervise-command":
            target = service.parse_session(args.target)
            out = service.brief(target, playbook_id=args.playbook, db=db)
            _emit({"ok": True, "argv": out["supervisor_argv"], "command": out["supervisor_command"]},
                  as_json, out["supervisor_command"])
        elif args.verb == "default":
            uid = _user(args, db)
            pid = service.set_default(uid, args.playbook, db=db) if args.playbook else service.default_id(uid)
            _emit({"ok": True, "default_id": pid}, as_json, pid)
        elif args.verb == "bindings":
            rows = service.list_bindings(_user(args, db), db=db)
            _emit({"ok": True, "bindings": rows}, as_json,
                  "\n".join(f"{r['session']}  {r['playbook_id']}  {r['title']}" for r in rows) or "(none)")
    except (service.OrchestratorError, ValueError) as exc:
        return _fail(str(exc), as_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
