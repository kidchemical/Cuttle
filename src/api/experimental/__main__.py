"""``python -m api.experimental list|get|set|reset``.

Agent/human toolkit for the generic flag surface. One-shot JSON output; no
wait loops (see ``.cuttle_global/docs/headless-turns.md``).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional


def _emit(payload) -> int:
    print(json.dumps(payload, indent=2, default=str))
    return 0 if payload.get("success", True) else 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.experimental")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="show every registered flag and its resolved value")

    p_get = sub.add_parser("get", help="show one flag")
    p_get.add_argument("flag_id")

    p_set = sub.add_parser("set", help="enable or disable one flag")
    p_set.add_argument("flag_id")
    p_set.add_argument("value", choices=["on", "off"])

    sub.add_parser("reset", help="drop all stored overrides")

    args = parser.parse_args(argv)

    # Imported after parsing so --help never touches settings.json.
    import api.experimental.flags as flags

    if args.cmd in (None, "list"):
        return _emit({"success": True, **flags.flags_payload()})

    if args.cmd == "get":
        spec = flags.get_flag(args.flag_id)
        if spec is None:
            return _emit({"success": False, "error": f"unknown flag: {args.flag_id}"})
        return _emit(
            {
                "success": True,
                "flag": spec.to_dict(flags.is_enabled(spec.id)),
                "kill_switch": flags.kill_switch_active(),
            }
        )

    if args.cmd == "set":
        try:
            spec = flags.set_enabled(args.flag_id, args.value == "on")
        except (ValueError, OSError) as e:
            return _emit({"success": False, "error": str(e)})
        return _emit({"success": True, "flag": spec, "kill_switch": flags.kill_switch_active()})

    if args.cmd == "reset":
        try:
            return _emit({"success": True, "flags": flags.reset_all(), "kill_switch": flags.kill_switch_active()})
        except OSError as e:
            return _emit({"success": False, "error": str(e)})

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())