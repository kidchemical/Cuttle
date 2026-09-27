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
    if args.from_agent and args.agent:
        handoff = build_handoff(
            args.session_id,
            to_agent=args.agent,
            from_agent=args.from_agent,
            limit=args.handoff_limit,
        )

    compiled = compile_context(
        args.prompt or "",
        project_path=args.project,
        profile=args.profile,
        inject_capabilities=not args.no_capabilities,
        handoff=handoff,
        include_chat_store_hint=args.chat_store_hint,
        wsl=args.wsl,
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
    c.add_argument("--handoff-limit", type=int, default=12)
    c.add_argument("--no-capabilities", action="store_true")
    c.add_argument("--chat-store-hint", action="store_true")
    c.add_argument("--wsl", action="store_true")
    c.add_argument("--json", action="store_true", help="Emit structured JSON")
    c.set_defaults(func=_cmd_compile)

    i = sub.add_parser("inventory", help="List .cuttle commands/docs/actions/rules")
    i.add_argument("--project", default=".")
    i.set_defaults(func=_cmd_inventory)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
