"""Collect a commit graph for Cuttle's git visualizer page."""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional

from scripts.utilities.git_pending_changes import (
    _count_hunk_lines,
    _filter_hunks_by_line_type,
    _looks_like_secret_path,
    _parse_numstat,
    _section_payload,
    _truncate_hunks,
    _validate_repo_rel,
    git_run,
    parse_unified_diff,
    resolve_git_workdir,
)

_REF_SPLIT = re.compile(r",\s*")

_NAME_STATUS_LABEL = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "modified",
}


def _name_status_to_label(code: str) -> str:
    c = (code or "").strip().upper()
    if not c:
        return "modified"
    return _NAME_STATUS_LABEL.get(c[0], "modified")


def _parse_decorate(raw: str) -> List[str]:
    """Turn `` (HEAD -> master, origin/master, tag: v1)`` into label list."""
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    labels: List[str] = []
    for part in _REF_SPLIT.split(s):
        part = part.strip()
        if not part:
            continue
        if part.startswith("tag: "):
            labels.append(part[5:].strip())
        elif part.startswith("HEAD -> "):
            labels.append("HEAD")
            rest = part[8:].strip()
            if rest:
                labels.append(rest)
        elif part == "HEAD":
            labels.append("HEAD")
        else:
            labels.append(part)
    # de-dupe preserve order
    seen = set()
    out: List[str] = []
    for lab in labels:
        if lab not in seen:
            seen.add(lab)
            out.append(lab)
    return out


def list_local_branches(cwd: str) -> List[Dict[str, Any]]:
    """Local branch tips for the sidebar (cheap for-each-ref)."""
    root = resolve_git_workdir(cwd)
    if not root:
        return []
    r = git_run(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)%00%(objectname:short)%00%(HEAD)%00%(subject)",
            "refs/heads",
        ],
        root,
        timeout=8.0,
    )
    if r.returncode != 0:
        return []
    out: List[Dict[str, Any]] = []
    for line in (r.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\x00")
        while len(parts) < 4:
            parts.append("")
        name = (parts[0] or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "short": (parts[1] or "").strip(),
                "current": (parts[2] or "").strip() == "*",
                "subject": (parts[3] or "").strip(),
            }
        )
    return out


def collect_git_graph(
    cwd: str,
    *,
    limit: int = 80,
    skip: int = 0,
    all_refs: bool = False,
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    """Return commits newest-first with parent hashes and ref labels.

    Shape::
      {
        repo_root, branch, all_refs, limit, skip, has_more, branches,
        commits: [{hash, short, parents, author, email, date, subject, refs}]
      }
    """
    root = resolve_git_workdir(cwd)
    if not root:
        raise ValueError("Not a git repository")

    # Keep this cheap: topo-order + --all can walk a huge DAG before emitting N
    # commits and has frozen the Electron shell (same-origin iframe).
    limit = max(1, min(int(limit or 80), 200))
    skip = max(0, min(int(skip or 0), 10000))

    branch_r = git_run(["branch", "--show-current"], root, timeout=8.0)
    current_branch = (branch_r.stdout or "").strip() or "HEAD"
    branches = list_local_branches(root)

    want_branch = (branch or "").strip()
    if want_branch in ("", "*", "HEAD", "(all)", "all"):
        want_branch = ""

    # Fetch one extra so we can set has_more without a second round-trip.
    args = ["log", f"-n{limit + 1}"]
    if skip:
        args.append(f"--skip={skip}")
    if want_branch:
        # Filter to one local branch tip (no --all).
        args.append(want_branch)
    elif all_refs:
        args.append("--all")
    # Date order (git default) stops after N commits; no full-history topo walk.
    args.extend(
        [
            "--pretty=format:%H%x00%P%x00%an%x00%ae%x00%ad%x00%d%x00%s",
            "--date=iso-strict",
            "--decorate=short",
        ]
    )
    try:
        log_r = git_run(args, root, timeout=12.0)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            "git log timed out. Uncheck All branches or pick a smaller repo."
        ) from e
    if log_r.returncode != 0:
        err = (log_r.stderr or log_r.stdout or "git log failed").strip()
        raise RuntimeError(err)

    commits: List[Dict[str, Any]] = []
    for raw in (log_r.stdout or "").split("\n"):
        if not raw.strip():
            continue
        parts = raw.split("\x00")
        if len(parts) < 7:
            # subject may contain nothing; pad
            while len(parts) < 7:
                parts.append("")
        full = parts[0].strip()
        if not full:
            continue
        parents = [p for p in (parts[1] or "").split() if p]
        commits.append(
            {
                "hash": full,
                "short": full[:7],
                "parents": parents,
                "author": parts[2] or "",
                "email": parts[3] or "",
                "date": parts[4] or "",
                "refs": _parse_decorate(parts[5] or ""),
                "subject": parts[6] or "",
            }
        )

    has_more = len(commits) > limit
    if has_more:
        commits = commits[:limit]

    return {
        "repo_root": root,
        "branch": current_branch,
        "filter_branch": want_branch or None,
        "all_refs": bool(all_refs) and not want_branch,
        "limit": limit,
        "skip": skip,
        "has_more": has_more,
        "branches": branches,
        "commits": commits,
        "count": len(commits),
    }


def collect_commit_detail(cwd: str, commit_hash: str) -> Dict[str, Any]:
    """Metadata + file list + capped patch for one commit."""
    root = resolve_git_workdir(cwd)
    if not root:
        raise ValueError("Not a git repository")

    h = (commit_hash or "").strip()
    if not h or not re.match(r"^[0-9a-fA-F]{4,40}$", h):
        raise ValueError("Invalid commit hash")

    # Verify object exists
    ver = git_run(["cat-file", "-t", h], root)
    if ver.returncode != 0 or (ver.stdout or "").strip() != "commit":
        raise ValueError(f"Commit not found: {h}")

    meta_r = git_run(
        [
            "show",
            "-s",
            "--pretty=format:%H%x00%P%x00%an%x00%ae%x00%ad%x00%d%x00%s%x00%b",
            "--date=iso-strict",
            h,
        ],
        root,
        timeout=30.0,
    )
    if meta_r.returncode != 0:
        err = (meta_r.stderr or meta_r.stdout or "git show failed").strip()
        raise RuntimeError(err)

    parts = (meta_r.stdout or "").split("\x00")
    while len(parts) < 8:
        parts.append("")
    full = parts[0].strip()
    parents = [p for p in (parts[1] or "").split() if p]
    subject = parts[6] or ""
    body = (parts[7] or "").strip()

    ns_r = git_run(
        ["show", "--name-status", "--pretty=format:", "--no-renames", h],
        root,
        timeout=12.0,
    )
    files: List[Dict[str, str]] = []
    for line in (ns_r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        bits = line.split("\t", 1)
        if len(bits) != 2:
            continue
        files.append({"status": bits[0].strip(), "path": bits[1].strip()})
        if len(files) >= 80:
            break

    stat_r = git_run(["show", "--stat", "--pretty=format:", h], root, timeout=12.0)
    stat_text = (stat_r.stdout or "").strip()
    if len(stat_text) > 4000:
        stat_text = stat_text[:4000] + "\n… [stat truncated]"

    # Never dump a full patch into the Electron iframe — large commits freeze
    # the shared renderer. Stat + file list is enough for the drawer.
    return {
        "repo_root": root,
        "hash": full,
        "short": full[:7],
        "parents": parents,
        "author": parts[2] or "",
        "email": parts[3] or "",
        "date": parts[4] or "",
        "refs": _parse_decorate(parts[5] or ""),
        "subject": subject,
        "body": body[:4000] if body else "",
        "files": files,
        "stat": stat_text,
        "patch": "",
        "patch_truncated": True,
    }


def collect_file_commit_diff(
    cwd: str,
    commit_hash: str,
    rel_path: str,
    *,
    context_lines: int = 3,
    max_lines: int = 300,
    full: bool = False,
) -> Dict[str, Any]:
    """Structured unified-diff hunks for one file in a commit (in-app preview).

    Same response shape as ``collect_file_pending_diff`` so the UI can reuse
    the pending-changes hunk renderer (side-separated added/removed sections).
    """
    root = resolve_git_workdir(cwd)
    if not root:
        raise ValueError("Not a git repository")

    h = (commit_hash or "").strip()
    if not h or not re.match(r"^[0-9a-fA-F]{4,40}$", h):
        raise ValueError("Invalid commit hash")

    ver = git_run(["cat-file", "-t", h], root)
    if ver.returncode != 0 or (ver.stdout or "").strip() != "commit":
        raise ValueError(f"Commit not found: {h}")

    rel = _validate_repo_rel(rel_path)
    ctx = max(0, min(int(context_lines), 20))
    per_side = 100_000 if full else max(1, min(int(max_lines or 300), 20_000))
    empty_sections = {
        "added": {"hunks": [], "truncated": False, "shown": 0, "total": 0},
        "removed": {"hunks": [], "truncated": False, "shown": 0, "total": 0},
    }

    status_code = "M"
    ns_r = git_run(
        ["show", "--name-status", "--pretty=format:", "--no-renames", h, "--", rel],
        root,
        timeout=20.0,
    )
    if ns_r.returncode == 0:
        for line in (ns_r.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            bits = line.split("\t", 1)
            if len(bits) == 2 and bits[1].strip() == rel:
                status_code = bits[0].strip() or "M"
                break
    status = _name_status_to_label(status_code)

    if _looks_like_secret_path(rel):
        return {
            "repo_root": root,
            "commit": h,
            "path": rel,
            "status": status,
            "binary": False,
            "secret_redacted": True,
            "additions": 0,
            "deletions": 0,
            "hunks": [],
            "sections": empty_sections,
            "truncated": False,
            "truncated_added": False,
            "truncated_removed": False,
            "max_lines_per_side": per_side,
            "full": bool(full),
            "message": "Diff hidden for credential-like paths",
        }

    diff_r = git_run(
        ["show", h, f"-U{ctx}", "--format=", "--", rel],
        root,
        timeout=45.0,
    )
    diff_text = (diff_r.stdout or "") if diff_r.returncode == 0 else ""
    if "Binary files" in diff_text and " differ" in diff_text:
        return {
            "repo_root": root,
            "commit": h,
            "path": rel,
            "status": status,
            "binary": True,
            "secret_redacted": False,
            "additions": 0,
            "deletions": 0,
            "hunks": [],
            "sections": empty_sections,
            "truncated": False,
            "truncated_added": False,
            "truncated_removed": False,
            "max_lines_per_side": per_side,
            "full": bool(full),
            "message": "Binary file (no text preview)",
        }

    full_hunks = parse_unified_diff(diff_text)
    added_section = _section_payload(
        _filter_hunks_by_line_type(full_hunks, "add"), max_lines=per_side
    )
    removed_section = _section_payload(
        _filter_hunks_by_line_type(full_hunks, "del"), max_lines=per_side
    )
    sections = {"added": added_section, "removed": removed_section}
    truncated = bool(added_section["truncated"] or removed_section["truncated"])
    hunks, _ = _truncate_hunks(full_hunks, max(per_side * 2, 100))

    adds = _count_hunk_lines(full_hunks, "add")
    dels = _count_hunk_lines(full_hunks, "del")

    num_r = git_run(
        ["show", h, "--numstat", "--pretty=format:", "--", rel],
        root,
        timeout=20.0,
    )
    numstat = _parse_numstat(num_r.stdout or "") if num_r.returncode == 0 else {}
    ns_adds, ns_dels = numstat.get(rel, (adds, dels))
    if ns_adds or ns_dels:
        adds, dels = int(ns_adds), int(ns_dels)

    message = ""
    if not full_hunks and not diff_text.strip():
        message = "No line changes to show"

    return {
        "repo_root": root,
        "commit": h,
        "path": rel,
        "status": status,
        "binary": False,
        "secret_redacted": False,
        "additions": adds,
        "deletions": dels,
        "hunks": hunks,
        "sections": sections,
        "truncated": truncated,
        "truncated_added": bool(added_section["truncated"]),
        "truncated_removed": bool(removed_section["truncated"]),
        "max_lines_per_side": per_side,
        "full": bool(full),
        "message": message,
    }
