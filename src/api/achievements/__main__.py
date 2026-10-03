"""``python -m api.achievements list|progress|scan|grant|ack|reset``.

One-shot JSON output (see ``.cuttle_global/docs/headless-turns.md`` — no wait
loops). Every verb requires the ``achievements`` flag on, matching the HTTP
surface.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional


def _emit(payload) -> int:
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.achievements")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="the catalog with resolved progress")

    p_prog = sub.add_parser("progress", help="only the progress lines")
    p_prog.add_argument("--unlocked-only", action="store_true")
    p_prog.add_argument("--limit", type=int, default=0)

    p_grant = sub.add_parser("grant", help="manually unlock one achievement")
    p_grant.add_argument("achievement_id")

    p_ack = sub.add_parser("ack", help="acknowledge an unlock's celebration")
    p_ack.add_argument("achievement_id")

    sub.add_parser("pending", help="unseen unlocks")
    sub.add_parser("scan", help="re-evaluate all metrics now")
    sub.add_parser("reset", help="wipe progress and events")

    args = parser.parse_args(argv)

    import api.achievements as achievements

    if args.cmd in (None, "list"):
        if not achievements.is_enabled():
            return _emit({"success": False, "error": "achievements flag is off"})
        return _emit({"success": True, **achievements.status()})

    if args.cmd == "progress":
        if not achievements.is_enabled():
            return _emit({"success": False, "error": "achievements flag is off"})
        st = achievements.status()
        rows = [i for i in st["items"] if i["unlocked"]] if args.unlocked_only else st["items"]
        rows.sort(key=lambda i: (-i.get("progress", 0.0), i["id"]))
        if args.limit:
            rows = rows[: args.limit]
        return _emit({"success": True, "count": len(rows),
                      "unlocked": st["unlocked"], "total": st["total"], "rows": rows})

    if args.cmd == "scan":
        return _emit({"success": True, **achievements.evaluate(force=True)})

    if args.cmd == "grant":
        from api.achievements import store

        if not achievements.is_enabled():
            return _emit({"success": False, "error": "achievements flag is off"})
        ach = achievements.catalog.get(args.achievement_id)
        if ach is None:
            return _emit({"success": False, "error": "unknown achievement"})
        return _emit({"success": True, "id": ach.id,
                      "unlocked": store.grant(ach.id, {"granted": True})})

    if args.cmd == "ack":
        from api.achievements import store

        if not achievements.is_enabled():
            return _emit({"success": False, "error": "achievements flag is off"})
        return _emit({"success": True, "changed": store.mark_seen(args.achievement_id)})

    if args.cmd == "pending":
        from api.achievements import catalog, store

        if not achievements.is_enabled():
            return _emit({"success": False, "error": "achievements flag is off"})
        pending = store.pending_unlocks(catalog.catalog_payload())
        return _emit({"success": True, "count": len(pending), "pending": pending})

    if args.cmd == "reset":
        from api.achievements import store

        if not achievements.is_enabled():
            return _emit({"success": False, "error": "achievements flag is off"})
        return _emit({"success": True, "cleared": store.reset()})

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())