"""Supervision guard rules: where the target must work and which branches stay untouched.

Pure checks over a checkout's live state; the interval check-in (``worker``) and
the event-driven re-check (``triggers``) both evaluate them.
"""
from __future__ import annotations

import os
import re
import subprocess


def has_guards(cfg):
    return bool(cfg.get('required_branch') or cfg.get('required_path') or cfg.get('forbidden_branches'))


def branch_tips(path, branches):
    """``{branch: sha}`` for the local branches that exist in ``path``'s repository.

    Worktrees share refs, so this sees commits made from any checkout of the repo."""
    tips = {}
    if not path or not branches:
        return tips
    for branch in branches:
        try:
            out = subprocess.run(['git', '-C', path, 'rev-parse', '--verify', '-q', f'refs/heads/{branch}^{{commit}}'],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            tips[branch] = out.stdout.strip()
    return tips


def state_violations(cfg, branch, path):
    found = []
    if cfg.get('required_branch') and branch != cfg['required_branch']:
        found.append(f"branch is {branch!r}, required {cfg['required_branch']!r}")
    if branch and branch in (cfg.get('forbidden_branches') or []):
        found.append(f'target is on forbidden branch {branch!r}')
    if cfg.get('required_path') and os.path.realpath(path or '') != cfg['required_path']:
        found.append(f"checkout is {path!r}, required {cfg['required_path']!r}")
    return found


# ── Conditions on a moved forbidden branch ────────────────────────────────────
# A forbidden branch moving is only a violation when the move is tied to the
# supervised chats or carries protected work; anyone else's commit is a note.

GIT_WRITE = re.compile(r'\bgit\b[^\n;&|]*?\s(commit|merge|rebase|cherry-pick|revert|reset|push|pull|am|'
                       r'update-ref|checkout|switch|branch\s+-[fFdDmM])\b')
MAX_COMMITS = 50


def is_git_write(command):
    return bool(GIT_WRITE.search(command or ''))


def _git(path, *args):
    try:
        out = subprocess.run(['git', '-C', path, *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def worktree_branches(path):
    """``{realpath: branch}`` for every worktree of ``path``'s repository."""
    found, current = {}, None
    for line in (_git(path, 'worktree', 'list', '--porcelain') or '').splitlines():
        if line.startswith('worktree '):
            current = os.path.realpath(line[9:])
        elif line.startswith('branch refs/heads/') and current:
            found[current] = line[18:]
    return found


def forbidden_command(command, forbidden, worktrees):
    """A git write naming a forbidden branch, or run inside a worktree that has one checked out."""
    if not forbidden or not is_git_write(command):
        return False
    for branch in forbidden:
        if re.search(rf'(?<![\w/.-]){re.escape(branch)}(?![\w/.-])', command):
            return True
    return any(branch in forbidden and wt in command for wt, branch in worktrees.items())


def new_commits(path, old, new):
    out = _git(path, 'rev-list', f'--max-count={MAX_COMMITS}', f'{old}..{new}')
    return [line for line in (out or '').split() if line]


def commit_query_ids(path, sha):
    out = _git(path, 'log', '-1', '--format=%(trailers:key=Cuttle-Query,valueonly,separator=%x2C)', sha) or ''
    return {q.strip() for q in out.replace('\n', ',').split(',') if q.strip()}


def commit_files(path, sha):
    out = _git(path, 'show', '--name-only', '--format=', sha) or ''
    return [line for line in out.splitlines() if line.strip()]


def is_ancestor(path, sha, tip):
    try:
        out = subprocess.run(['git', '-C', path, 'merge-base', '--is-ancestor', sha, tip],
                             capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def classify_move(path, branch, old, new, *, own_queries, protected_tip='', protected_name='',
                  path_pattern=''):
    """``(violations, notes)`` for one forbidden branch that moved ``old`` → ``new``.

    Violations: a commit attributed (``Cuttle-Query`` trailer) to the supervised
    chats; commits brought in from the protected branch (merge/rebase/cherry-pick
    of its history); an unattributed commit touching ``path_pattern`` files.
    Anything else is someone else's work and only noted."""
    commits = new_commits(path, old, new)
    head = f'forbidden branch {branch!r} moved {old[:10]} → {new[:10]}'
    violations = []
    queries = {sha: commit_query_ids(path, sha) for sha in commits}
    mine = [sha for sha, q in queries.items() if q & own_queries]
    if mine:
        violations.append(f'{head}: {len(mine)} commit(s) made by the supervised chats ({mine[0][:10]})')
    if protected_tip:
        brought = [sha for sha in commits if is_ancestor(path, sha, protected_tip)]
        if brought:
            violations.append(f'{head}: brings in {len(brought)} commit(s) from {protected_name!r}')
    if path_pattern:
        try:
            pattern = re.compile(path_pattern)
        except re.error:
            pattern = None
        hits = [sha for sha, q in queries.items() if not q and pattern
                and any(pattern.search(f) for f in commit_files(path, sha))]
        if hits:
            violations.append(f'{head}: unattributed commit {hits[0][:10]} touches protected paths')
    notes = [] if violations else [f'{head} by someone else ({len(commits)} commit(s)); not a violation']
    return violations, notes
