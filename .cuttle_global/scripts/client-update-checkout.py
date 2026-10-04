#!/usr/bin/env python3
"""Preserve client work while updating a clean tracking checkout (no lifecycle)."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


class UpdateRefused(RuntimeError):
    pass


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if result.returncode:
        raise UpdateRefused(f'git {args[0]} failed: {result.stderr.decode(errors="replace").strip()}')
    return result.stdout


def require_clean(repo: Path) -> None:
    if git(repo, 'status', '--porcelain', '--untracked-files=all'):
        raise UpdateRefused('checkout has local changes or untracked files; preserve/commit them before updating')


def update_checkout(repo: Path) -> None:
    require_clean(repo)
    git(repo, 'symbolic-ref', '-q', 'HEAD')  # Detached checkouts are never updated.
    upstream = git(repo, 'rev-parse', '--abbrev-ref', '@{u}').decode().strip()
    print('git fetch --all --prune', flush=True)
    git(repo, 'fetch', '--all', '--prune')
    try:
        git(repo, 'merge-base', '--is-ancestor', 'HEAD', upstream)
    except UpdateRefused:
        raise UpdateRefused('local commits or diverged upstream; reconcile before updating') from None
    require_clean(repo)  # Work created during fetch must be refused too.
    incoming = set(git(repo, 'ls-tree', '-r', '-z', '--name-only', upstream).split(b'\0')) - {b''}
    ignored = set(git(repo, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').split(b'\0')) - {b''}
    # Byte paths support spaces/newlines/non-UTF8 names. Check file/directory
    # conflicts too (new tracked file "personal" vs ignored "personal/x").
    incoming_ancestors = set(incoming)
    for path in incoming:
        parts = path.split(b'/')
        incoming_ancestors.update(b'/'.join(parts[:n]) for n in range(1, len(parts)))
    for local in ignored:
        ancestors = {local}
        parts = local.split(b'/')
        ancestors.update(b'/'.join(parts[:n]) for n in range(1, len(parts)))
        if ancestors & incoming or local in incoming_ancestors:
            raise UpdateRefused('incoming tracked paths would overwrite ignored local files; preserve them before updating')
    print(f'git merge --ff-only --no-overwrite-ignore {upstream}', flush=True)
    git(repo, 'merge', '--ff-only', '--no-overwrite-ignore', upstream)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    args = parser.parse_args()
    try:
        update_checkout(args.repo)
    except (UpdateRefused, OSError) as exc:
        print(f'ERROR update refused: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
