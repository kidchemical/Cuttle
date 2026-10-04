"""Optional thin CLI for the Context Compiler.

Agents and humans can inspect the compiled envelope without going through chat::

    .venv\\Scripts\\python.exe -m api.cuttle_brain compile --project C:\\Projects\\Cuttle --prompt "hi"
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional


def _cmd_compile(args: argparse.Namespace) -> int:
    from api.cuttle_brain.context_compiler import compile_context
    from api.cuttle_brain.handoff import build_handoff

    handoff = None
    if args.agent and args.session_id:
        # Same selection the kernel makes; --from-agent forces a switch view.
        handoff = build_handoff(
            args.session_id,
            to_agent=args.agent,
            from_agent=args.from_agent or None,
            limit=args.handoff_limit,
            current_prompt=args.prompt or None,
        )

    compiled = compile_context(
        args.prompt or "",
        project_path=args.project,
        profile=args.profile,
        inject_capabilities=not args.no_capabilities,
        handoff=handoff,
        include_chat_store_hint=args.chat_store_hint,
        chat_session_id=args.session_id,
    )

    if args.json:
        payload = {
            "schema_version": compiled.schema_version,
            "layers_used": compiled.layers_used,
            "meta": compiled.meta,
            "envelope": compiled.envelope,
            "prompt": compiled.prompt,
        }
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(compiled.prompt + ("\n" if compiled.prompt else ""))
    return 0


def _cmd_inventory(args: argparse.Namespace) -> int:
    from api.cuttle_brain.context_compiler import project_inventory

    inv = project_inventory(args.project)
    sys.stdout.write(json.dumps(inv, indent=2) + "\n")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    from api.cuttle_brain.state import prune

    sys.stdout.write(json.dumps(prune(dry_run=args.dry_run), indent=2) + "\n")
    return 0


def _cmd_metrics(args: argparse.Namespace) -> int:
    from api.cuttle_brain import metrics

    if args.action == "backfill":
        out = metrics.backfill_from_query_logs()
    else:
        import time

        rows = metrics.fetch_turns(since_ts=time.time() - args.days * 86400)
        out = {"db": str(metrics.database_path()), "turns": len(rows), "recent": rows[-args.limit:]}
    sys.stdout.write(json.dumps(out, indent=2, default=str) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.cuttle_brain",
        description="Cuttle Brain — Context Compiler CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compile", help="Compile a context envelope + user prompt")
    c.add_argument("--project", default=".", help="Project root (finds .cuttle/)")
    c.add_argument("--prompt", default="", help="User request text")
    c.add_argument("--profile", default="standard", help="standard|coordination|supervision")
    c.add_argument("--agent", default="", help="Target agent id (for handoff)")
    c.add_argument("--from-agent", default="", help="Previous agent id (force handoff)")
    c.add_argument("--session-id", default=None, help="Cuttle chat session id")
    c.add_argument("--handoff-limit", type=int, default=40)
    c.add_argument("--no-capabilities", action="store_true")
    c.add_argument("--chat-store-hint", action="store_true")
    c.add_argument("--json", action="store_true", help="Emit structured JSON")
    c.set_defaults(func=_cmd_compile)

    i = sub.add_parser("inventory", help="List .cuttle commands/docs/actions/rules")
    i.add_argument("--project", default=".")
    i.set_defaults(func=_cmd_inventory)

    pr = sub.add_parser(
        "prune",
        help="Drop briefing/handoff state for deleted chats and pytest leftovers",
    )
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=_cmd_prune)

    m = sub.add_parser("metrics", help="Per-turn context metrics (Context dashboard)")
    m.add_argument("action", choices=("list", "backfill"))
    m.add_argument("--days", type=int, default=7)
    m.add_argument("--limit", type=int, default=10)
    m.set_defaults(func=_cmd_metrics)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
