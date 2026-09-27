"""``python -m api.jev`` — debug / batch verbs for the Jev judgment layer."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional


def _print(payload: dict, *, ok: bool = True) -> int:
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    return 0 if ok else 1


def _cmd_status(_args: argparse.Namespace) -> int:
    from api.jev.client import jev_available
    from api.jev.config import load_jev_config

    cfg = load_jev_config()
    d = cfg.to_dict()
    d["available"] = jev_available()
    return _print(d)


def _cmd_eval(args: argparse.Namespace) -> int:
    from api.jev.client import get_client
    from api.jev.types import noul

    questions = {}
    for spec in args.noul or []:
        if ":" not in spec:
            return _print({"success": False, "error": "noul must be id:instructions"}, ok=False)
        kid, _, instr = spec.partition(":")
        questions[kid.strip()] = noul(instr.strip())
    if not questions:
        questions["yes"] = noul(args.question or "Is this true?")
    client = get_client()
    result = client.system_one(args.state or "", questions)
    return _print({"success": True, **result.to_dict()})


def _cmd_route(args: argparse.Namespace) -> int:
    from api.agent_router.config import load_router_config
    from api.agent_router.engine import build_context
    from api.jev.routing import decide_routing

    cfg = load_router_config()
    ctx = build_context(args.prompt or "", project_name=args.project or "")
    decision = decide_routing(ctx, cfg)
    return _print({"success": True, "decision": decision.to_dict(), "raw": decision.raw})


def _cmd_rank(args: argparse.Namespace) -> int:
    from api.cuttle_brain.context_compiler import project_inventory
    from api.jev.rank import rank_context

    inv = project_inventory(args.project)
    ranked = rank_context(args.prompt or "", project_path=args.project, inventory=inv)
    return _print({"success": True, **ranked})


def _cmd_label(args: argparse.Namespace) -> int:
    from api.agent_router.outcomes import list_outcomes
    from api.jev.labels import label_pending_async, label_rows

    if args.all:
        status = label_pending_async(wait=True)
        return _print({"success": not status.get("last_error"), **status})
    rows = list_outcomes(limit=max(1, int(args.limit)))
    labeled = label_rows(rows, force=bool(args.refresh), limit=max(1, int(args.limit)))
    slim = []
    for r in labeled:
        slim.append(
            {
                "decision_id": r.get("decision_id"),
                "target": f"{r.get('target_agent')}/{r.get('target_model')}",
                "success": r.get("success"),
                "feedback": r.get("user_feedback"),
                "jev": r.get("jev"),
            }
        )
    return _print({"success": True, "rows": slim})


def _cmd_regress(args: argparse.Namespace) -> int:
    from api.jev.regress import run_regress_once

    report = run_regress_once(
        lookback_s=args.lookback,
        run_tests=not args.no_pytest,
        notify=not args.quiet,
    )
    failed = int((report.get("pytest") or {}).get("returncode") or 0) != 0 and (
        report.get("pytest") or {}
    ).get("ran")
    return _print(report, ok=not failed)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m api.jev")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show Jev config + whether a key is available")

    ev = sub.add_parser("eval", help="One-off System One call")
    ev.add_argument("--state", default="")
    ev.add_argument("--question", default="")
    ev.add_argument("--noul", action="append", help="id:instructions (repeatable)")

    rt = sub.add_parser("route", help="Run the routing-brain recipe (no tentacle)")
    rt.add_argument("--prompt", required=True)
    rt.add_argument("--project", default="")

    rk = sub.add_parser("rank", help="Rank extra skills/docs for a prompt")
    rk.add_argument("--prompt", required=True)
    rk.add_argument("--project", default=".")

    lb = sub.add_parser("label", help="Label recent outcomes (batched Jev calls)")
    lb.add_argument("--limit", type=int, default=40)
    lb.add_argument("--refresh", action="store_true")
    lb.add_argument("--all", action="store_true", help="Label every unlabeled outcome; prints totals only")

    rg = sub.add_parser("regress", help="Scan recent logs and maybe run pytest")
    rg.add_argument("--lookback", type=int, default=None)
    rg.add_argument("--no-pytest", action="store_true")
    rg.add_argument("--quiet", action="store_true", help="Do not toast on failure")

    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = {
        "status": _cmd_status,
        "eval": _cmd_eval,
        "route": _cmd_route,
        "rank": _cmd_rank,
        "label": _cmd_label,
        "regress": _cmd_regress,
    }[args.cmd]
    try:
        return int(cmd(args))
    except Exception as e:
        return _print({"success": False, "error": str(e)[:400]}, ok=False)


if __name__ == "__main__":
    raise SystemExit(main())
