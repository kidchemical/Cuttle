"""python -m api.orchestration_telemetry events|report|backfill — agent ops CLI."""

from __future__ import annotations

import argparse
import json
import sys
import time


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m api.orchestration_telemetry",
                                description="Orchestration + supervision event log (read-only except backfill).")
    sub = p.add_subparsers(dest="verb", required=True)
    ev = sub.add_parser("events", help="Raw events, newest last")
    ev.add_argument("--kind", action="append", default=[], help="child_spawn, child_round_end, sup_check, ...")
    ev.add_argument("--days", type=float, default=7)
    ev.add_argument("--limit", type=int, default=200)
    rp = sub.add_parser("report", help="The dashboard payload as JSON")
    rp.add_argument("--range", default="30d", choices=["7d", "30d", "90d", "all"])
    rp.add_argument("--project", default="")
    bf = sub.add_parser("backfill", help="Import sub-agent + supervision history recorded before the log (once)")
    bf.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from api.orchestration_telemetry import report, store
    if args.verb == "events":
        rows = store.events(kinds=args.kind, since=time.time() - args.days * 86400)[-args.limit:]
        print(json.dumps(rows, indent=2, default=str))
        return 0
    from api.auth_db import get_auth_db
    db = get_auth_db()
    if args.verb == "report":
        print(json.dumps(report.report(range_id=args.range, project=args.project, db=db), indent=2, default=str))
        return 0
    print(json.dumps(report.backfill(db, dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
