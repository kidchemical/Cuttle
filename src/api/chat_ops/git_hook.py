"""Block agent updates of protected branches with a plain git hook.

Git's ``reference-transaction`` hook (git >= 2.28) sees every ref update (commit,
merge, rebase, reset, ``branch -f``, ``update-ref``) and aborts the transaction
by exiting non-zero in the ``prepared`` phase. Protected branches are standard
per-repository git config (``git config --add cuttle.protectBranch <branch>``),
shared by every worktree and never in the tree. Only supervised agent turns are
refused: processes carrying ``CUTTLE_SUPERVISED`` (set by the turn launcher for
sub-agent children, orchestrator lanes and chats under a live, unpaused
supervise schedule; see :func:`supervised_reason`) whose own checkout
(``CUTTLE_AGENT_PROJECT_PATH``) is not on that branch. A top-level chat the user
drives directly, an agent working in the branch's own checkout, the user's
terminal, editors and the Git UI pass. The hook never calls Cuttle, so it works
while Cuttle is down and cannot hang a commit.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

HOOK_NAME = 'reference-transaction'
MARKER_PREFIX = '# cuttle-protect-branch-hook '
MARKER = MARKER_PREFIX + 'v2'
ENV_SUPERVISED = 'CUTTLE_SUPERVISED'

SCRIPT = f"""#!/bin/sh
{MARKER}
# Installed by `python -m api.chat_cli install-hook`. Refuses updates of branches
# listed in `git config cuttle.protectBranch` by supervised Cuttle agents
# ({ENV_SUPERVISED} set: sub-agents, orchestrator lanes, supervised chats) whose own
# checkout is not on that branch; everyone else passes.
[ "$1" = prepared ] || exit 0
[ -n "${ENV_SUPERVISED}" ] || exit 0
protected=$(git config --get-all cuttle.protectBranch 2>/dev/null) || {{ cat >/dev/null; exit 0; }}
own=""
if [ -n "$CUTTLE_AGENT_PROJECT_PATH" ]; then
  own=$(unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_PREFIX; \
        git -C "$CUTTLE_AGENT_PROJECT_PATH" symbolic-ref --quiet --short HEAD 2>/dev/null)
fi
status=0
while read -r old new ref; do
  for b in $protected; do
    if [ "$ref" = "refs/heads/$b" ] && [ "$b" != "$own" ]; then
      echo "cuttle: supervised agent chat CH-$CUTTLE_CHAT_SESSION_ID (supervised: ${ENV_SUPERVISED}) may not update protected branch '$b' (git config cuttle.protectBranch)" >&2
      status=1
    fi
  done
done
exit $status
"""


def hooks_dir(repo):
    """The repository's hooks directory (honors ``core.hooksPath``; shared by worktrees)."""
    out = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute', '--git-path', 'hooks'],
                         capture_output=True, text=True, timeout=15)
    if out.returncode != 0 or not out.stdout.strip():
        raise ValueError(f'{repo} is not a git repository')
    return Path(out.stdout.strip())


def _ours(path):
    """Any version of Cuttle's hook, so ``install`` upgrades older installs in place."""
    try:
        return MARKER_PREFIX in path.read_text(errors='replace')
    except OSError:
        return False


def supervised_reason(session_id, db=None):
    """Why a chat's agent turns run unattended, for ``CUTTLE_SUPERVISED``; '' when user-driven.

    ``subagent`` covers child chats (orchestrator lanes are spawned as children);
    ``supervised`` covers targets of a supervise schedule that is live; a paused schedule
    does not count (the chat is user-driven while its supervision is paused).
    """
    try:
        sid = int(session_id)
    except (TypeError, ValueError):
        return ''
    from api.chat_ops import store
    if store.actively_supervised(sid):
        return 'supervised'
    if db is None:
        from api.auth_db import get_auth_db
        db = get_auth_db()
    return 'subagent' if db.session_nesting_depth(sid) > 0 else ''


def install(repo, branches=()):
    """Install the hook and add ``branches`` to ``cuttle.protectBranch``. Never overwrites a foreign hook."""
    target = hooks_dir(repo) / HOOK_NAME
    if target.exists() and not _ours(target):
        raise ValueError(f'{target} already exists and is not Cuttle\'s; add a line to it that runs '
                         f'this script instead of replacing it (see api/chat_ops/git_hook.py)')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SCRIPT)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    have = protected(repo)
    for branch in branches:
        if branch and branch not in have:
            subprocess.run(['git', '-C', str(repo), 'config', '--add', 'cuttle.protectBranch', branch],
                           check=True, capture_output=True, timeout=15)
    return {'hook': str(target), 'protected': protected(repo)}


def uninstall(repo):
    """Remove Cuttle's hook (only if it is ours). The branch list stays in git config."""
    target = hooks_dir(repo) / HOOK_NAME
    removed = target.exists() and _ours(target)
    if removed:
        os.remove(target)
    return {'hook': str(target), 'removed': bool(removed), 'protected': protected(repo)}


def protected(repo):
    out = subprocess.run(['git', '-C', str(repo), 'config', '--get-all', 'cuttle.protectBranch'],
                         capture_output=True, text=True, timeout=15)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]
