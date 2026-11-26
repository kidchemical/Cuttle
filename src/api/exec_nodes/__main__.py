"""Agent verbs: ``python -m api.exec_nodes <verb>``.

Inside a Cuttle turn ``--session`` defaults to ``CUTTLE_CHAT_SESSION_ID``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List


def _row(node: Dict[str, Any]) -> str:
    where = "this Cuttle host" if node.get("host") else node.get("id")
    state = node.get("state") + (f" ({node['reason']})" if node.get("reason") else "")
    extra = f"  dir {node['default_cwd']}" if node.get("default_cwd") else ""
    return f"{node.get('label')} — {node.get('os') or '?'}  [{where}]  {state}{extra}"


def _binding_text(binding: Dict[str, Any]) -> str:
    node = binding["node"]
    text = f"runs on {_row(node)}"
    return text + (f"\nworking directory: {binding['cwd']}" if binding.get("cwd") else "")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON on stdout")
    p = argparse.ArgumentParser(prog="python -m api.exec_nodes",
                                description="Execution nodes: which machine runs a chat's agent CLI.")
    sub = p.add_subparsers(dest="verb", required=True)
    ls = sub.add_parser("list", parents=[common], help="The host and every paired machine, with state")
    ls.add_argument("--agent", default="", help="Check that this agent's CLI is installed on each node")
    show = sub.add_parser("show", parents=[common], help="The node a chat runs on")
    show.add_argument("--session", default=None, help="CH- handle (default: this chat)")
    st = sub.add_parser("set", parents=[common], help="Run a chat on a node from its next turn")
    st.add_argument("node", help="Node id from `list`, or `host`")
    st.add_argument("--cwd", default="", help="Working directory on the node for this chat")
    st.add_argument("--session", default=None, help="CH- handle (default: this chat)")
    nd = sub.add_parser("node", parents=[common], help="Set a node's label, default directory and path map")
    nd.add_argument("node")
    nd.add_argument("--label", default=None)
    nd.add_argument("--default-cwd", default=None, help="Directory chats use when their project is not mapped")
    nd.add_argument("--map", action="append", default=None, metavar="HOST_PATH=NODE_PATH",
                    help="Where a host project lives on the node (repeatable; replaces the map)")
    return p


def main(argv: List[str] | None = None) -> int:
    from api.exec_nodes import service

    args = build_parser().parse_args(argv)

    def emit(payload: Dict[str, Any], text: str) -> int:
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else text)
        return 0

    try:
        if not service.enabled():
            raise service.ExecNodeError("Execution nodes are off (Settings → Experimental, "
                                        "or `python -m api.experimental set exec_nodes on`).")
        if args.verb == "list":
            nodes = service.list_nodes(args.agent)
            return emit({"ok": True, "nodes": nodes}, "\n".join(_row(n) for n in nodes))
        if args.verb == "node":
            paths = None
            if args.map is not None:
                paths = dict(item.split("=", 1) for item in args.map if "=" in item)
            node = service.set_node_prefs(args.node, label=args.label, default_cwd=args.default_cwd, paths=paths)
            return emit({"ok": True, "node": node},
                        _row(node) + "".join(f"\n  {h} → {n}" for h, n in (node.get("paths") or {}).items()))
        session = args.session or os.environ.get("CUTTLE_CHAT_SESSION_ID") or ""
        if service.session_number(session) is None:
            raise service.ExecNodeError("no chat: pass --session CH-…")
        if args.verb == "set":
            binding = service.set_binding(session, args.node, args.cwd)
            return emit({"ok": True, "binding": binding},
                        f"From its next turn this chat {_binding_text(binding)}")
        binding = service.binding(session)
        return emit({"ok": True, "binding": binding}, f"This chat {_binding_text(binding)}")
    except service.ExecNodeError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
