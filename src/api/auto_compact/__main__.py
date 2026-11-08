"""``python -m api.auto_compact`` — inspect and tune auto-compaction.

    settings [--set JSON]                      global settings (owner verb)
    status CH-… [--json]                       policy, state, history, dry-run decision
    set CH-… [--enabled on|off|inherit] [--percent N|inherit] [--milestone on|off|inherit]
    history CH-… [--limit N]

Compacting now is ``python -m api.chat_cli compact CH-…`` (the host checks
the chat is idle); this CLI never runs a compact itself.
"""
from __future__ import annotations

import argparse
import json
import sys


def _tri(value, name):
    if value is None:
        return ...
    v = str(value).strip().lower()
    if v == "inherit":
        return None
    if v in ("on", "true", "yes", "1"):
        return True
    if v in ("off", "false", "no", "0"):
        return False
    raise SystemExit(f"--{name} must be on, off or inherit")


def _dump(obj):
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.auto_compact")
    sub = parser.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("settings")
    p.add_argument("--set", dest="patch", default=None, help='JSON patch, e.g. {"managed":{"percent":55}}')
    p = sub.add_parser("status")
    p.add_argument("chat")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("set")
    p.add_argument("chat")
    p.add_argument("--enabled", default=None)
    p.add_argument("--percent", default=None)
    p.add_argument("--milestone", default=None)
    p = sub.add_parser("history")
    p.add_argument("chat")
    p.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    from api.auto_compact import policy, service, store

    if not service.available() and args.verb != "settings":
        print("Auto-compaction is off (experimental flag auto_compact). "
              "Enable: python -m api.experimental set auto_compact on", file=sys.stderr)
        return 2
    try:
        if args.verb == "settings":
            if args.patch:
                _dump({"available": service.available(), "settings": service.save_settings(json.loads(args.patch))})
            else:
                _dump({"available": service.available(), "settings": service.settings()})
            return 0
        sid = service._sid(args.chat)
        if args.verb == "history":
            _dump(store.history(sid, args.limit))
            return 0
        if args.verb == "set":
            current = dict(store.get_override(sid))
            for key, raw in (("enabled", _tri(args.enabled, "enabled")),
                             ("milestone", _tri(args.milestone, "milestone"))):
                if raw is not ...:
                    current[key] = raw
            if args.percent is not None:
                current["percent"] = None if args.percent == "inherit" else int(args.percent)
            current = {k: v for k, v in current.items() if v is not None}
            _dump(service.set_chat_override(sid, current))
            return 0
        out = service.describe(sid)
        out["check"] = service.evaluate(sid, trigger="manual", dry_run=True)
        if args.json:
            _dump(out)
            return 0
        p, check = out["policy"], out["check"]
        snap = check.get("snapshot") or {}
        scope = f"managed ({out['managed_reason']})" if out["managed"] else "ordinary chat"
        print(f"CH-{sid:06d} · {scope} · auto-compact {'on' if p['enabled'] else 'off'}")
        print(f"  threshold {p['percent']}%" + (f" or {p['max_tokens']:,} tokens" if p["max_tokens"] else "")
              + (f"; milestone {p['milestone_percent']}% / {p['milestone_max_tokens']:,}" if p["milestone"] else "")
              + f"; mode {p['mode']}")
        if snap:
            print(f"  {check.get('agent')}: {round(snap.get('percent') or 0)}% · {snap.get('used_tokens', 0):,} tokens")
        print(f"  now: {'would compact (' + check['reason'] + ')' if check.get('would_compact') else 'no — ' + str(check.get('reason'))}")
        for h in out["history"][:5]:
            print(f"  - {h['outcome']}: {service.notice_detail(h['detail'])}")
        return 0
    except (policy.PolicyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
