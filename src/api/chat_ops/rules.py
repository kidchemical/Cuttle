"""Supervision guard rules: where the target must work and which branches stay untouched.

Pure checks over a checkout's live state; the interval check-in (``worker``) and
the event-driven re-check (``triggers``) both evaluate them.
"""
from __future__ import annotations

import os
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


def moved_tips(baseline, tips):
    """Forbidden branches whose tip changed since the baseline (a commit landed on them)."""
    return [f'forbidden branch {b!r} moved {old[:10]} → {tips[b][:10]} (a commit landed on it)'
            for b, old in sorted(baseline.items()) if tips.get(b) and tips[b] != old]
