"""Update channels for this install: which exist and what each would change.

Read-only (an explicit check may ``git fetch`` the upstream remote, which only
moves remote-tracking refs). Published releases stay ``api.releases``-owned and
applying a checkout update stays ``device_workers`` (``cuttle_self_update``)
owned. This module answers the questions the update modal needs before the
user picks a channel:

* what kind of install this is — a ``release`` package or a ``source`` checkout;
* whether Developer mode is on (source checkouts only; it reveals the branch
  and Host channels, which fast-forward a working repository);
* how far this checkout is behind its upstream, and whether anything would
  block a fast-forward (local changes, local commits, detached HEAD).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

SETTINGS_KEY = 'updates'
_COMMIT_PREVIEW = 8


def repo_root() -> Path:
    env_root = (os.environ.get('CUTTLE_REPO_ROOT') or '').strip()
    # src/api/updates.py -> repository (or packaged app) root
    return Path(env_root) if env_root else Path(__file__).resolve().parents[2]


def install_kind(root: Optional[Path] = None) -> str:
    """``source`` for a git checkout, else ``release`` (packaged install)."""
    # A linked worktree has a .git *file*; both are source checkouts.
    return 'source' if ((root or repo_root()) / '.git').exists() else 'release'


def _stored_developer_mode() -> Optional[bool]:
    try:
        from managers.settings_manager import get_settings_manager

        block = get_settings_manager().get_setting(SETTINGS_KEY) or {}
    except Exception:
        return None
    value = block.get('developer_mode') if isinstance(block, dict) else None
    return value if isinstance(value, bool) else None


def developer_mode(kind: Optional[str] = None) -> Dict[str, bool]:
    """Resolved Developer mode. Unset follows the install (source -> on).

    A release install has no checkout to pull, so it is never available there.
    """
    available = (kind or install_kind()) == 'source'
    stored = _stored_developer_mode()
    return {
        'enabled': bool(available and (True if stored is None else stored)),
        'available': available,
        'explicit': stored is not None,
    }


def set_developer_mode(enabled: bool) -> Dict[str, bool]:
    if not isinstance(enabled, bool):
        raise ValueError('developer_mode must be true or false')
    if install_kind() != 'source':
        raise ValueError('Developer mode needs a source checkout; release installs update from releases.')
    from managers.settings_manager import get_settings_manager

    get_settings_manager().update_setting(
        SETTINGS_KEY, lambda current: {**(current or {}), 'developer_mode': enabled}
    )
    return developer_mode('source')


def _git(root: Path, *args: str, timeout: float = 8) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ['git', '-C', str(root), *args],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return completed.returncode, (completed.stdout or completed.stderr or '').strip()


def checkout_status(root: Optional[Path] = None, *, fetch: bool = False) -> Dict[str, Any]:
    """Branch/upstream position of the checkout and what would block a pull.

    ``fetch`` refreshes only the upstream's remote first; without it, counts
    are as of the last fetch (``fetched`` says which).
    """
    root = root or repo_root()
    _, head = _git(root, 'rev-parse', '--short=12', 'HEAD')
    code, branch = _git(root, 'symbolic-ref', '--short', '-q', 'HEAD')
    branch = branch if code == 0 else ''
    code, upstream = _git(root, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    upstream = upstream if code == 0 and branch else ''
    status: Dict[str, Any] = {
        'path': str(root), 'branch': branch, 'head': head, 'upstream': upstream,
        'ahead': 0, 'behind': 0, 'commits': [], 'dirty': False,
        'fetched': False, 'error': '', 'blocked': '',
    }
    code, porcelain = _git(root, 'status', '--porcelain', '--untracked-files=normal')
    status['dirty'] = bool(code == 0 and porcelain)
    if fetch and upstream:
        remote = upstream.split('/', 1)[0]
        code, out = _git(root, 'fetch', '--prune', remote, timeout=60)
        status['fetched'] = code == 0
        if code:
            status['error'] = 'Could not fetch ' + remote + ': ' + out[-300:]
    if upstream:
        code, counts = _git(root, 'rev-list', '--left-right', '--count', 'HEAD...@{u}')
        parts = counts.split() if code == 0 else []
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            status['ahead'], status['behind'] = int(parts[0]), int(parts[1])
        if status['behind']:
            code, log = _git(root, 'log', '--format=%h%x09%s', '-n', str(_COMMIT_PREVIEW), 'HEAD..@{u}')
            commits: List[Dict[str, str]] = []
            for line in (log.splitlines() if code == 0 else []):
                rev, _, subject = line.partition('\t')
                commits.append({'rev': rev, 'subject': subject})
            status['commits'] = commits
    # Same refusals as the preservation helper (client-update-checkout.py),
    # surfaced before the user confirms instead of after the job fails.
    if not branch:
        status['blocked'] = 'Detached HEAD — check out a branch to update.'
    elif not upstream:
        status['blocked'] = 'Branch ' + branch + ' has no upstream to update from.'
    elif status['ahead'] and status['behind']:
        status['blocked'] = 'Local commits diverge from ' + upstream + ' — reconcile them first.'
    elif status['dirty'] and status['behind']:
        status['blocked'] = 'Local changes or untracked files — commit or stash them before updating.'
    return status


def snapshot(*, check: bool = False, include_release: bool = True) -> Dict[str, Any]:
    """Everything the update modal shows. ``check`` forces both remote checks."""
    from api.device_workers.capabilities import cuttle_git_rev, cuttle_version, update_source

    kind = install_kind()
    mode = developer_mode(kind)
    release = None
    if include_release:
        from api.releases import check_releases

        release = check_releases(force=check)
    return {
        'success': True,
        'install_kind': kind,
        'developer_mode': mode,
        'current_version': cuttle_version(),
        'git_rev': cuttle_git_rev(),
        'update_source': update_source(),
        'release': release,
        'checkout': checkout_status(fetch=check) if mode['enabled'] else None,
    }
