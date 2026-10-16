"""``python -m api.service_ops <verb>`` — list registered external action handlers.

Read-only. Service ops are dispatched by ``project_actions`` when a project
action's ``type`` matches a registered op (e.g. ``discord.post``).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.service_ops")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="registered service ops (id, action types)")
    sub.add_parser("status", help="alias of list")
    args = parser.parse_args(argv)

    from api.service_ops import catalog

    if args.cmd in (None, "list", "status"):
        print(json.dumps({"service_ops": catalog.ops_status()}, indent=2))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
