"""CLI: ``python -m api.gitea <verb> …``

Thin argparse front over ``api.gitea_client``. Formerly
``.cuttle/scripts/gitea_cli.py`` (now ``.cuttle_global/scripts/gitea_cli.py``;
that path still delegates here).

Examples::

    .venv\\Scripts\\python.exe -m api.gitea list owner/repo --state open --json
    .venv\\Scripts\\python.exe -m api.gitea show owner/repo 12 --json
    .venv\\Scripts\\python.exe -m api.gitea comments owner/repo 12 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence


def _ensure_src_on_path() -> None:
    src = Path(__file__).resolve().parents[2]
    src_s = str(src)
    if src_s not in sys.path:
        sys.path.insert(0, src_s)


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_list(args: argparse.Namespace) -> int:
    from api.gitea_client import issue_web_url, list_issues, parse_owner_repo

    owner, repo = parse_owner_repo(args.repo)
    issues = list_issues(owner, repo, state=args.state, limit=args.limit)
    if args.json:
        _print_json({"ok": True, "count": len(issues), "issues": issues})
        return 0
    for it in issues:
        labels = ", ".join(l.get("name", "") for l in (it.get("labels") or []))
        print(
            f"#{it.get('number')} | {it.get('title')} | "
            f"labels={labels or '-'} | {issue_web_url(owner, repo, int(it.get('number') or 0))}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from api.gitea_client import get_issue, issue_web_url, parse_owner_repo

    owner, repo = parse_owner_repo(args.repo)
    issue = get_issue(owner, repo, args.issue)
    if args.json:
        _print_json({"ok": True, "issue": issue})
        return 0
    labels = ", ".join(l.get("name", "") for l in (issue.get("labels") or []))
    print(f"#{issue.get('number')} {issue.get('title')}")
    print(f"URL: {issue_web_url(owner, repo, int(issue.get('number') or 0))}")
    print(f"State: {issue.get('state')} | Labels: {labels or '-'}")
    print()
    print(issue.get("body") or "")
    return 0


def cmd_comments(args: argparse.Namespace) -> int:
    from api.gitea_client import list_issue_comments, parse_owner_repo

    owner, repo = parse_owner_repo(args.repo)
    comments = list_issue_comments(owner, repo, args.issue)
    if args.json:
        _print_json({"ok": True, "count": len(comments), "comments": comments})
        return 0
    for c in comments:
        user = (c.get("user") or {}).get("login", "?")
        print(f"--- {user} @ {c.get('created_at', '')}")
        print(c.get("body") or "")
        print()
    return 0


def cmd_labels(args: argparse.Namespace) -> int:
    from api.gitea_client import list_repo_labels, parse_owner_repo

    owner, repo = parse_owner_repo(args.repo)
    labels = list_repo_labels(owner, repo)
    if args.json:
        _print_json({"ok": True, "count": len(labels), "labels": labels})
        return 0
    for lb in labels:
        print(f"{lb.get('id')}: {lb.get('name')}")
    return 0


def _parse_csv_labels(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def cmd_update(args: argparse.Namespace) -> int:
    from api.gitea_client import (
        add_issue_comment,
        add_issue_labels,
        default_agent_username,
        delete_issue_label,
        get_issue,
        issue_web_url,
        parse_owner_repo,
        patch_issue,
    )

    owner, repo = parse_owner_repo(args.repo)
    issue_index = int(args.issue)
    labels_add = _parse_csv_labels(args.labels_add)
    labels_remove = _parse_csv_labels(args.labels_remove)
    comment = (args.comment or "").strip()
    assign_to = (args.assign or "").strip()
    if args.assign_self:
        assign_to = default_agent_username()

    if (
        not comment
        and not labels_add
        and not labels_remove
        and not args.close
        and not args.reopen
        and not assign_to
    ):
        print(
            "Error: provide --comment, --labels-add, --labels-remove, "
            "--assign/--assign-self, --reopen, and/or --close",
            file=sys.stderr,
        )
        return 1

    steps: list[str] = []
    if comment:
        add_issue_comment(owner, repo, issue_index, comment)
        steps.append("comment posted")
    if labels_add:
        add_issue_labels(owner, repo, issue_index, labels_add)
        steps.append(f"labels added: {', '.join(labels_add)}")
    if labels_remove:
        issue = get_issue(owner, repo, issue_index)
        name_to_id = {
            str(lb.get("name") or ""): int(lb.get("id") or 0)
            for lb in (issue.get("labels") or [])
        }
        for name in labels_remove:
            lid = name_to_id.get(name)
            if lid:
                delete_issue_label(owner, repo, issue_index, lid)
        steps.append(f"labels removed: {', '.join(labels_remove)}")
    if assign_to:
        patch_issue(owner, repo, issue_index, assignees=[assign_to])
        steps.append(f"assigned to {assign_to}")
    if args.close:
        patch_issue(owner, repo, issue_index, state="closed")
        steps.append("issue closed")
    if args.reopen:
        patch_issue(owner, repo, issue_index, state="open")
        steps.append("issue reopened")

    if args.json:
        _print_json(
            {
                "ok": True,
                "issue": issue_index,
                "steps": steps,
                "url": issue_web_url(owner, repo, issue_index),
            }
        )
        return 0
    print(f"#{issue_index} updated: {', '.join(steps)}")
    print(issue_web_url(owner, repo, issue_index))
    return 0


def cmd_create_label(args: argparse.Namespace) -> int:
    from api.gitea_client import create_repo_label, parse_owner_repo

    owner, repo = parse_owner_repo(args.repo)
    label = create_repo_label(owner, repo, args.name, description=args.description or "")
    if args.json:
        _print_json({"ok": True, "label": label})
        return 0
    print(f"Created label {label.get('id')}: {label.get('name')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.gitea",
        description="Gitea issue CLI for Cuttle agents",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List issues")
    p_list.add_argument("repo", help="owner/repo")
    p_list.add_argument("--state", default="open", choices=("open", "closed", "all"))
    p_list.add_argument("--limit", type=int, default=30)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show one issue")
    p_show.add_argument("repo", help="owner/repo")
    p_show.add_argument("issue", type=int)
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_comments = sub.add_parser("comments", help="List issue comments")
    p_comments.add_argument("repo", help="owner/repo")
    p_comments.add_argument("issue", type=int)
    p_comments.add_argument("--json", action="store_true")
    p_comments.set_defaults(func=cmd_comments)

    p_labels = sub.add_parser("labels", help="List repo labels")
    p_labels.add_argument("repo", help="owner/repo")
    p_labels.add_argument("--json", action="store_true")
    p_labels.set_defaults(func=cmd_labels)

    p_update = sub.add_parser("update", help="Comment, label, and/or close an issue")
    p_update.add_argument("repo", help="owner/repo")
    p_update.add_argument("issue", type=int)
    p_update.add_argument("--comment", help="Comment body")
    p_update.add_argument("--labels-add", help="Comma-separated labels to add")
    p_update.add_argument("--labels-remove", help="Comma-separated labels to remove")
    p_update.add_argument("--assign", help="Assign issue to this Gitea username")
    p_update.add_argument(
        "--assign-self",
        action="store_true",
        help="Assign issue to the Cuttle bot user (GITEA_AGENT_USERNAME or cuttle)",
    )
    p_update.add_argument("--close", action="store_true", help="Close the issue")
    p_update.add_argument("--reopen", action="store_true", help="Reopen the issue")
    p_update.add_argument("--json", action="store_true")
    p_update.set_defaults(func=cmd_update)

    p_create_label = sub.add_parser("create-label", help="Create a repo label")
    p_create_label.add_argument("repo", help="owner/repo")
    p_create_label.add_argument("name", help="Label name (e.g. Status/Needs Review)")
    p_create_label.add_argument("--description", default="")
    p_create_label.add_argument("--json", action="store_true")
    p_create_label.set_defaults(func=cmd_create_label)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    _ensure_src_on_path()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
