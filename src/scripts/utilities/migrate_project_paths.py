#!/usr/bin/env python3
"""
Remap stored Cuttle project paths after a move (drive letter, UNC share, folder rename).

Updates (in the per-user Cuttle home, ``core.runtime_paths.cuttle_home``):
  - db/projects.db  (registered projects)
  - db/cuttle_auth.db  (chat_sessions.project_path, chat_messages.project_path)
  - config/settings.json  (starred_project.path, when set)

Examples:
  # Preview prefix swap (default is dry-run):
  python migrate_project_paths.py --from "D:\\Server" --to "E:\\Server"

  # Apply:
  python migrate_project_paths.py --from "D:\\Server" --to "E:\\Server" --apply

  # Explicit mappings (repeat --map):
  python migrate_project_paths.py \\
    --map "D:\\Server\\www\\ExampleProject=E:\\Server\\www\\ExampleProject" \\
    --map "\\\\host\\share\\repos=/path/to/repos" \\
    --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.runtime_paths import cuttle_home, settings_path  # noqa: E402

PROJECTS_DB = cuttle_home() / "db" / "projects.db"
AUTH_DB = cuttle_home() / "db" / "cuttle_auth.db"
SETTINGS_JSON = settings_path()


def _norm_key(path: str) -> str:
    """Case-insensitive path key with forward slashes."""
    p = path.strip().replace("/", "\\")
    while p.endswith("\\") and len(p) > 3:
        p = p[:-1]
    return p.lower()


@dataclass(frozen=True)
class PathRule:
    old: str
    new: str
    old_key: str
    new_value: str

    @classmethod
    def from_pair(cls, old: str, new: str) -> PathRule:
        return cls(old=old, new=new, old_key=_norm_key(old), new_value=new.strip())

    @classmethod
    def prefix(cls, old_prefix: str, new_prefix: str) -> PathRule:
        return cls.from_pair(old_prefix, new_prefix)


def build_rules(args: argparse.Namespace) -> list[PathRule]:
    rules: list[PathRule] = []
    if args.from_prefix and args.to_prefix:
        rules.append(PathRule.prefix(args.from_prefix, args.to_prefix))
    for item in args.map or []:
        if "=" not in item:
            raise SystemExit(f"--map values must be OLD=NEW (got {item!r})")
        old, new = item.split("=", 1)
        rules.append(PathRule.from_pair(old, new))
    if not rules:
        raise SystemExit("Provide --from/--to and/or one or more --map OLD=NEW rules.")
    return rules


def remap_path(raw: str, rules: list[PathRule]) -> str | None:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()

    # Exact match first (longest old prefix wins among exact keys).
    sk = _norm_key(s)
    exact = [r for r in rules if sk == r.old_key]
    if exact:
        return exact[-1].new_value

    # Prefix replace: longest matching old prefix wins.
    best: PathRule | None = None
    best_len = -1
    for rule in rules:
        ok = rule.old_key
        if sk == ok or sk.startswith(ok + "\\"):
            if len(ok) > best_len:
                best = rule
                best_len = len(ok)
    if not best:
        return None

    # Preserve the user's slash style where possible.
    sep = "\\" if "\\" in s else "/"
    old_tail = s[len(best.old) :] if s.lower().startswith(best.old.lower()) else ""
    if not old_tail and _norm_key(s) != best.old_key:
        # Matched via normalized prefix; rebuild tail from normalized keys.
        old_tail = s[len(best.old.replace("/", sep)) :] if len(s) >= len(best.old) else ""
    new_base = best.new_value.replace("/", sep)
    if old_tail.startswith(sep) or old_tail.startswith("\\") or old_tail.startswith("/"):
        return new_base + old_tail
    if old_tail:
        return new_base + sep + old_tail.lstrip("\\/")
    return new_base


@dataclass
class Change:
    store: str
    label: str
    old: str
    new: str


def scan_projects(rules: list[PathRule]) -> list[Change]:
    if not PROJECTS_DB.exists():
        return []
    conn = sqlite3.connect(PROJECTS_DB)
    conn.row_factory = sqlite3.Row
    out: list[Change] = []
    for row in conn.execute("select id, name, path from projects order by id"):
        old = row["path"] or ""
        new = remap_path(old, rules)
        if new and new != old:
            out.append(Change("projects.db", f"project {row['id']} {row['name']!r}", old, new))
    conn.close()
    return out


def scan_auth(rules: list[PathRule]) -> list[Change]:
    if not AUTH_DB.exists():
        return []
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    out: list[Change] = []
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    for table in ("chat_sessions", "chat_messages"):
        if table not in tables:
            continue
        cols = {r[1] for r in conn.execute(f"pragma table_info({table})")}
        if "project_path" not in cols:
            continue
        pk = "id" if "id" in cols else "rowid"
        for row in conn.execute(
            f"select {pk} as _pk, project_path from {table} where coalesce(project_path,'') != ''"
        ):
            old = row["project_path"] or ""
            new = remap_path(old, rules)
            if new and new != old:
                out.append(Change("cuttle_auth.db", f"{table} {pk}={row['_pk']}", old, new))
    conn.close()
    return out


def scan_settings(rules: list[PathRule]) -> list[Change]:
    if not SETTINGS_JSON.exists():
        return []
    try:
        data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    starred = data.get("starred_project")
    if not isinstance(starred, dict):
        return []
    old = starred.get("path") or ""
    new = remap_path(old, rules)
    if new and new != old:
        return [Change("settings.json", "starred_project.path", old, new)]
    return []


def apply_projects(changes: list[Change], dry_run: bool) -> int:
    touched = [c for c in changes if c.store == "projects.db"]
    if not touched or dry_run:
        return len(touched)
    conn = sqlite3.connect(PROJECTS_DB)
    for c in touched:
        pid = c.label.split()[1]
        conn.execute(
            "update projects set path = ?, updated_at = CURRENT_TIMESTAMP where id = ?",
            (c.new, int(pid)),
        )
    conn.commit()
    conn.close()
    return len(touched)


def apply_auth(changes: list[Change], dry_run: bool) -> int:
    touched = [c for c in changes if c.store == "cuttle_auth.db"]
    if not touched or dry_run:
        return len(touched)
    conn = sqlite3.connect(AUTH_DB)
    for c in touched:
        # label: "chat_sessions id=238"
        table, _, pk_part = c.label.partition(" ")
        pk_name, _, pk_val = pk_part.partition("=")
        conn.execute(
            f"update {table} set project_path = ? where {pk_name} = ?",
            (c.new, pk_val),
        )
    conn.commit()
    conn.close()
    return len(touched)


def apply_settings(changes: list[Change], dry_run: bool) -> int:
    touched = [c for c in changes if c.store == "settings.json"]
    if not touched or dry_run:
        return len(touched)
    data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    data.setdefault("starred_project", {})["path"] = touched[0].new
    SETTINGS_JSON.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return len(touched)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="from_prefix", metavar="OLD_PREFIX", help="Old path prefix to replace")
    p.add_argument("--to", dest="to_prefix", metavar="NEW_PREFIX", help="New path prefix")
    p.add_argument(
        "--map",
        action="append",
        metavar="OLD=NEW",
        help="Explicit old=new mapping (repeatable; checked before prefix rules for exact matches)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run preview only)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rules = build_rules(args)
    dry_run = not args.apply

    print("Rules:")
    for r in rules:
        print(f"  {r.old!r}  ->  {r.new_value!r}")
    print()

    changes = scan_projects(rules) + scan_auth(rules) + scan_settings(rules)
    if not changes:
        print("No matching stored paths — nothing to do.")
        return 0

    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"=== {mode} ({len(changes)} change(s)) ===")
    for c in changes:
        print(f"[{c.store}] {c.label}")
        print(f"  {c.old!r}")
        print(f"  -> {c.new!r}")
        print()

    if dry_run:
        print("Re-run with --apply to write these updates.")
        return 0

    n = apply_projects(changes, dry_run=False)
    n += apply_auth(changes, dry_run=False)
    n += apply_settings(changes, dry_run=False)
    print(f"Applied {n} update(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
