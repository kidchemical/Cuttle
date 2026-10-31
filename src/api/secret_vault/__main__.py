"""Agent CLI for the secret vault — use secrets without reading them.

    python -m api.secret_vault status [--json]
    python -m api.secret_vault list [--json]
    python -m api.secret_vault request NAME [NAME …] [--label NAME=TEXT] [--reason TEXT]
    python -m api.secret_vault run [--env VAR=NAME] [--file VAR=NAME] [--stdin NAME]
                                   [--askpass NAME[=PROMPT_REGEX]] [--timeout SEC]
                                   [--no-redact] [--session CH-…] -- COMMAND [ARGS…]

There is deliberately no verb that prints a value. Exit codes for ``run``:
the child's own code, or 3 = approval needed (card printed), 4 = secret
missing (request card printed), 2 = usage / scope / disabled, 5 = backend error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List

from api.secret_vault import cards, service
from api.secret_vault.core import deliver
from api.secret_vault.core.names import require_name

EXIT_USAGE, EXIT_APPROVAL, EXIT_MISSING, EXIT_BACKEND = 2, 3, 4, 5

_EMIT_HINT = ("Emit the card below verbatim in your reply and end the turn. "
              "Rerun the same command after the user answers. Never ask for the value in chat.")


def _session(args) -> str:
    return service.normalize_session(args.session or os.getenv("CUTTLE_CHAT_SESSION_ID") or "")


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_status(args) -> int:
    data = service.status()
    if args.json:
        _print_json(data)
        return 0
    print(f"enabled: {data['enabled']}")
    print(f"approval: {data['config'].get('approval')}  default backend: "
          f"{data['config'].get('default_backend')}  redact: {data['config'].get('redact')}")
    for b in data["backends"]:
        mark = "ok " if b["available"] else "-- "
        print(f"  {mark}{b['id']:<14} {b['kind']:<8} {b['detail']}")
    print(f"secrets: {data['secret_count']}")
    return 0


def cmd_list(args) -> int:
    rows = service.list_secrets()
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print("No secrets yet. Ask with: python -m api.secret_vault request NAME")
    for r in rows:
        state = "stored" if r["stored"] else "no value"
        scope = f"  [{r['project']}]" if r["project"] else ""
        desc = f" — {r['description']}" if r["description"] else ""
        print(f"{r['name']:<24} {r['backend']:<12} {state}{scope}{desc}")
    return 0


def _labels(raw: List[str]) -> dict:
    out = {}
    for item in raw or []:
        name, sep, text = item.partition("=")
        if sep:
            out[name.strip()] = text.strip()
    return out


def cmd_request(args) -> int:
    for name in args.names:
        require_name(name)
    spec = cards.request_card(args.names, labels=_labels(args.label), reason=args.reason or "",
                              session=_session(args))
    print(_EMIT_HINT)
    print(cards.render(spec))
    return 0


def _deliveries(p: deliver.Plan) -> List[str]:
    out = [f"{var} env ← {n}" for var, n in p.env_names.items()]
    out += [f"{var} file ← {n}" for var, n in p.file_names.items()]
    if p.stdin_secret:
        out.append(f"stdin ← {p.stdin_secret}")
    out += [f"password prompts{(' matching ' + repr(r['match'])) if r['match'] else ''} ← {r['secret']}"
            for r in p.askpass]
    return out


def _confirm_tty(names: List[str], argv: List[str]) -> bool:
    if not (sys.stdin and sys.stdin.isatty()):
        return False
    sys.stderr.write(f"Allow `{cards.command_text(argv)}` to use {', '.join(names)}? [y/N] ")
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def cmd_run(args) -> int:
    argv = list(args.command or [])
    if argv and argv[0] == "--":
        argv = argv[1:]
    try:
        injections = ([deliver.parse_spec("env", s) for s in args.env]
                      + [deliver.parse_spec("file", s) for s in args.file]
                      + ([deliver.parse_spec("stdin", args.stdin)] if args.stdin else [])
                      + [deliver.parse_spec("askpass", s) for s in args.askpass])
        if not injections:
            raise ValueError("Nothing to inject: add --env, --file, --stdin or --askpass.")
        plan = deliver.plan(argv, injections)
    except ValueError as exc:
        print(f"cuttle secrets: {exc}", file=sys.stderr)
        return EXIT_USAGE
    names = plan.secrets
    session = _session(args)
    known = {r["name"]: r for r in service.list_secrets()}
    missing = [n for n in names if n not in known or not known[n]["stored"]]
    if missing:
        print(f"cuttle secrets: not in the vault yet: {', '.join(missing)}", file=sys.stderr)
        print(_EMIT_HINT)
        print(cards.render(cards.request_card(missing, session=session)))
        return EXIT_MISSING
    bad_scope = service.scope_violations(names, os.getcwd())
    if bad_scope:
        for name, project in bad_scope:
            print(f"cuttle secrets: '{name}' is limited to {project}", file=sys.stderr)
        return EXIT_USAGE
    need = service.missing_approvals(session, names)
    if need and not (not session and _confirm_tty(need, plan.argv)):
        if not session:
            print("cuttle secrets: approval needed and no chat session "
                  "(pass --session CH-… or run from a terminal).", file=sys.stderr)
        else:
            print(f"cuttle secrets: approval needed for {', '.join(need)}", file=sys.stderr)
        print(_EMIT_HINT)
        print(cards.render(cards.approval_card(need, plan.argv, session=session,
                                               deliveries=_deliveries(plan))))
        return EXIT_APPROVAL
    for note in plan.notes:
        print(f"cuttle secrets: {note}", file=sys.stderr)
    cfg = service.get_config()
    resolved = {}

    def resolve(name: str) -> str:
        if name not in resolved:
            resolved[name] = service.resolve_value(name)
        return resolved[name]

    try:
        for name in names:
            resolve(name)
    except service.VaultError as exc:
        print(f"cuttle secrets: {exc}", file=sys.stderr)
        return EXIT_BACKEND
    if session:
        service.consume_once(session, names)
    service.record_use(names)
    sys.stdout.flush()
    return deliver.run(plan, resolve, redact=bool(cfg.get("redact", True)) and not args.no_redact,
                       timeout=args.timeout, inherit_stdin=args.inherit_stdin,
                       askpass_max=int(cfg.get("askpass_max") or 3))


def cmd_grants(args) -> int:
    rows = service.list_grants(args.session or None)
    if args.json:
        _print_json(rows)
    else:
        for g in rows:
            print(f"CH-{int(g['session']):06d}  {g['secret']:<24} {g['mode']}"
                  if str(g.get("session", "")).isdigit()
                  else f"{g['session']}  {g['secret']:<24} {g['mode']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m api.secret_vault",
                                 description="Use vault secrets in commands without reading them.")
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("status", help="flag, backends, policy")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("list", help="secret names and metadata (never values)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("request", help="print a chat card asking the user to enter secrets")
    p.add_argument("names", nargs="+")
    p.add_argument("--label", action="append", default=[], help="NAME=prompt text")
    p.add_argument("--reason", default="")
    p.add_argument("--session", default="")
    p.set_defaults(func=cmd_request)
    p = sub.add_parser("run", help="run a command with secrets injected")
    p.add_argument("--env", action="append", default=[], metavar="VAR=NAME")
    p.add_argument("--file", action="append", default=[], metavar="VAR=NAME")
    p.add_argument("--stdin", default="", metavar="NAME")
    p.add_argument("--askpass", action="append", default=[], metavar="NAME[=REGEX]")
    p.add_argument("--timeout", type=float, default=None)
    p.add_argument("--no-redact", action="store_true")
    p.add_argument("--inherit-stdin", action="store_true")
    p.add_argument("--session", default="")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("grants", help="list live approvals")
    p.add_argument("--session", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_grants)
    return ap


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        service.require_enabled()
        return int(args.func(args) or 0)
    except (service.VaultError, ValueError) as exc:
        print(f"cuttle secrets: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
