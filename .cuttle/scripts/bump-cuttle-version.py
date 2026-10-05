#!/usr/bin/env python3
"""Bump Cuttle desktop/mesh version (electron/package.json).

Workers advertise cuttle_version. Bump SemVer only at release time, never per push.
Git revision mismatch and stale boot revision also flag workers for self-update.
Release procedure: .cuttle/docs/cuttle-release.md.

Usage (any OS, from the repo root):
  python .cuttle/scripts/bump-cuttle-version.py            # patch 0.2.5 -> 0.2.6
  python .cuttle/scripts/bump-cuttle-version.py --minor    # 0.2.5 -> 0.3.0
  python .cuttle/scripts/bump-cuttle-version.py --major    # 0.2.5 -> 1.0.0
  python .cuttle/scripts/bump-cuttle-version.py --set 0.2.7
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "electron" / "package.json"
_VERSION = re.compile(r'("version"\s*:\s*")([^"]+)(")')


def bumped(old: str, *, major: bool = False, minor: bool = False, set_to: str = "") -> str:
    if set_to.strip():
        return set_to.strip()
    parts = [int(p) for p in (old.split(".") + ["0", "0"])[:3]]
    if major:
        return f"{parts[0] + 1}.0.0"
    if minor:
        return f"{parts[0]}.{parts[1] + 1}.0"
    return f"{parts[0]}.{parts[1]}.{parts[2] + 1}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--major", action="store_true")
    group.add_argument("--minor", action="store_true")
    group.add_argument("--set", dest="set_to", default="")
    args = ap.parse_args(argv)
    if not PKG.is_file():
        print(f"missing {PKG}", file=sys.stderr)
        return 1
    raw = PKG.read_text(encoding="utf-8")
    old = str(json.loads(raw).get("version") or "")
    if not old:
        print("no version in package.json", file=sys.stderr)
        return 1
    new = bumped(old, major=args.major, minor=args.minor, set_to=args.set_to)
    if new == old:
        print(json.dumps({"success": True, "unchanged": True, "version": old}))
        return 0
    # Preserve formatting: replace the first version field only.
    PKG.write_text(_VERSION.sub(lambda m: m.group(1) + new + m.group(3), raw, count=1), encoding="utf-8")
    print(json.dumps({"success": True, "old": old, "version": new, "path": "electron/package.json"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
