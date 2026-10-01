"""``python -m api.dashboards`` — list / dump dashboard JSON for agents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import api.dashboards.catalog as catalog
import api.dashboards.service as service
import api.dashboards.usage as usage


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.dashboards")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Print dashboard catalog")

    p_get = sub.add_parser("get", help="Dump one dashboard payload")
    p_get.add_argument("id", help="Dashboard id, e.g. model-benchmarks")
    p_get.add_argument("--refresh", action="store_true", help="Bypass selected benchmark source caches")
    p_get.add_argument("--cache-dir", type=Path, default=None)
    p_get.add_argument("--no-discovery", action="store_true")
    p_get.add_argument(
        "--source",
        choices=("deepswe", "swebench", "aider", "aggregate", "all", "pinned", "router"),
        default=None,
        help="Benchmark source (model-benchmarks) or turn source (cuttle-performance)",
    )
    p_get.add_argument("--days", type=int, default=30, help="cuttle-performance window; 0 = all time")
    p_get.add_argument("--range", dest="range_id", default="30d",
                       help="cuttle-usage range: 1d, 3d, 7d, 14d, 30d, 90d, 1y, all")
    p_get.add_argument("--start", default=None, help="cuttle-usage custom start (YYYY-MM-DD)")
    p_get.add_argument("--end", default=None, help="cuttle-usage custom end (YYYY-MM-DD)")
    p_get.add_argument("--group", choices=("model", "harness", "none"), default="model")
    p_get.add_argument("--interval", choices=("auto", "day", "week", "month"), default="auto")

    p_backfill = sub.add_parser(
        "backfill-performance",
        help="Create My Cuttle Performance rows from past chat replies (idempotent)",
    )
    p_backfill.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        print(json.dumps(service.hub(), indent=2))
        return 0
    if args.cmd == "backfill-performance":
        from api.agent_router.pinned_outcomes import backfill_from_history

        print(json.dumps(backfill_from_history(dry_run=args.dry_run), indent=2))
        return 0
    if args.cmd == "get":
        if args.id == "model-benchmarks":
            payload = service.model_benchmarks(
                cache_dir=args.cache_dir,
                force=args.refresh,
                include_discovery=not args.no_discovery,
                source_id=args.source or "deepswe",
            )
            print(json.dumps(payload, indent=2))
            return 0 if payload.get("success") else 1
        if args.id == "cuttle-performance":
            print(json.dumps(service.cuttle_performance(
                label=args.refresh, source_id=args.source or "all", days=args.days,
            ), indent=2))
            return 0
        if args.id == usage.USAGE_ID:
            print(json.dumps(usage.cuttle_usage(
                range_id=args.range_id, start=args.start, end=args.end, group_by=args.group,
                interval=args.interval, source_id=args.source or "all",
            ), indent=2))
            return 0
        if catalog.get_dashboard(args.id):
            print(json.dumps({"success": True, **catalog.get_dashboard(args.id)}, indent=2))
            return 0
        print(json.dumps({"success": False, "error": "unknown dashboard"}), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
