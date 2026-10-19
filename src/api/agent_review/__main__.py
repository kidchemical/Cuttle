"""``python -m api.agent_review list|show|keep|undo`` — agent-ops verbs."""

from __future__ import annotations

import argparse
import json
import sys

from api.agent_review import service


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.agent_review", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, text in (("list", "Open reviews under a workspace"), ("show", "One file's hunks"),
                       ("keep", "Accept hunks (disk untouched)"), ("undo", "Revert hunks on disk")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--root", required=True, help="Registered workspace (project) path")
        if name != "list":
            p.add_argument("--path", action="append", required=name == "show",
                           help="File relative to --root (repeatable; omit for every file)")
        if name in ("keep", "undo"):
            p.add_argument("--hunk", action="append", help="Hunk id from `show` (repeatable; omit for whole files)")
        p.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)
    from api.ide.service import IdeError, resolve_workspace

    try:
        root = resolve_workspace(args.root)
        if args.cmd == "list":
            out = service.list_reviews(root)
        elif args.cmd == "show":
            out = service.file_review(root, args.path[0])
        elif args.cmd == "keep":
            out = service.keep(root, args.path, args.hunk)
        else:
            out = service.undo(root, args.path, args.hunk)
    except (service.ReviewError, IdeError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}) if args.json else f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    elif args.cmd == "list":
        for f in out["files"]:
            print(f"{f['change'][0].upper()} {f['path']}  +{f['additions']} -{f['deletions']}  "
                  f"{f['hunks']} hunk(s){'  [live]' if f.get('live') else ''}")
        if not out["files"]:
            print("No agent changes awaiting review.")
    elif args.cmd == "show":
        review = out["review"]
        if not review:
            print("No agent changes awaiting review in that file.")
        for h in (review or {}).get("hunk_list", []):
            print(f"@@ {h['id']}  -{h['old_start']},{h['old_count']} +{h['new_start']},{h['new_count']}")
            sys.stdout.write("".join("-" + line for line in h["removed"]))
            sys.stdout.write("".join("+" + line for line in h["added"]))
    else:
        print(f"{args.cmd}: {len(out['files'])} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
