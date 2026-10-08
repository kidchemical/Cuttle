"""Git domain service — the single owner for Git system behavior.

Ownership contract (Phase 2 Slice 3A):
- Pure Git operations over explicit ``cwd`` arguments: repo resolution,
  subprocess execution, output parsing, status/diff/commit/history/pull/
  push/branch behavior. No Flask, no ``request``/``jsonify``, no
  ``api.web_chat_api`` import, no HTTP globals.
- Transport (auth, request parsing/validation, response shaping, status
  codes) stays in the HTTP handlers, which must keep calling these
  operations with the same arguments and mapping the same errors.
- Error semantics mirror the legacy inline handlers exactly:
  ``GitError`` carries the fully formatted message
  (``Git command failed: …``, ``Git commit failed: …`` …);
  ``NotARepositoryError`` marks the one case the status route answers
  with 400 instead of 500.
- Parsing preserves legacy quirks verbatim (notably the
  ``stdout.strip()`` leading-space behavior in status/files counts);
  quirks are pinned by ``test_git_service.py`` and must not be
  "fixed" here — fix callers, not the shared parser.

Subprocess execution reuses ``git_run`` from
``scripts.utilities.git_pending_changes`` (dual import path, mirroring
HTTP handlers); a minimal local runner covers the helper-unavailable
case. ``timeout=None`` preserves the legacy wait-forever semantics.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

try:
    from scripts.utilities.git_pending_changes import (
        git_push_command,
        git_run,
    )
except ImportError:  # pragma: no cover - sys.path variant
    try:
        from utilities.git_pending_changes import (  # type: ignore
            git_push_command,
            git_run,
        )
    except ImportError:
        git_push_command = None  # type: ignore[assignment]
        git_run = None  # type: ignore[assignment]


class GitError(Exception):
    """Git invocation failed. ``message`` is the fully formatted,
    handler-compatible error string."""

    def __init__(self, message: str, *, returncode: Optional[int] = None,
                 stderr: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.returncode = returncode
        self.stderr = stderr

    def __str__(self) -> str:
        return self.message


class NotARepositoryError(GitError):
    """``cwd`` is not inside a Git work tree (status route answers 400)."""


def _run(args: List[str], cwd: str) -> subprocess.CompletedProcess:
    if git_run is not None:
        return git_run(list(args), cwd, timeout=None)
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=None, check=False,
        )
    except OSError as e:
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=127, stdout="", stderr=str(e))


def _check(proc: subprocess.CompletedProcess, prefix: str) -> subprocess.CompletedProcess:
    if proc.returncode != 0:
        raise GitError(f"{prefix}: {proc.stderr}", returncode=proc.returncode,
                       stderr=proc.stderr)
    return proc


def resolve_repo_cwd(current_project: Optional[Dict[str, Any]],
                     fallback: Optional[str] = None) -> str:
    """Working directory for Git operations: the active project path when it
    is a local-style project, else ``fallback`` or the process cwd."""
    if (current_project
            and current_project.get('type') in ('local', 'github', 'gitlab')
            and current_project.get('path')):
        return str(current_project['path'])
    if fallback:
        return str(fallback)
    return os.getcwd()


def repo_work_tree(cwd: str) -> str:
    """Top-level work tree for ``cwd``; raises NotARepositoryError outside
    a repository (the status route maps this to 400)."""
    proc = _run(['rev-parse', '--is-inside-work-tree'], cwd)
    if proc.returncode != 0 or (proc.stdout or '').strip() != 'true':
        raise NotARepositoryError('Not in a Git repository',
                                  returncode=proc.returncode, stderr=proc.stderr)
    top = _check(_run(['rev-parse', '--show-toplevel'], cwd), 'Git command failed')
    return (top.stdout or '').strip()


def repo_status(cwd: str) -> Dict[str, Any]:
    """Repository summary for the status route (counts parsed exactly like
    the legacy inline handler, including its ``strip()`` behavior)."""
    repo_work_tree(cwd)
    repo_name = os.path.basename(repo_work_tree(cwd))
    branch = _check(_run(['branch', '--show-current'], cwd), 'Git command failed')
    current_branch = (branch.stdout or '').strip()
    status = _check(_run(['status', '--porcelain'], cwd), 'Git command failed')
    status_lines = (status.stdout or '').strip().split('\n') if (status.stdout or '').strip() else []

    modified = len([line for line in status_lines if line.startswith(' M') or line.startswith('M ')])
    added = len([line for line in status_lines if line.startswith('A ') or line.startswith('A')])
    deleted = len([line for line in status_lines if line.startswith(' D') or line.startswith('D ')])
    untracked = len([line for line in status_lines if line.startswith('??')])
    staged = len([line for line in status_lines if line[0] != ' ' and line[0] != '?'])
    working_clean = len(status_lines) == 0

    return {
        'repository': repo_name,
        'currentBranch': current_branch,
        'workingDirectory': {
            'clean': working_clean,
            'modified': modified,
            'added': added,
            'deleted': deleted,
            'untracked': untracked,
        },
        'stagingArea': {
            'staged': staged,
        },
    }


def list_branches(cwd: str) -> Dict[str, Any]:
    """All branches (local + remote) with the current branch marked."""
    result = _check(_run(['branch', '-a'], cwd), 'Git command failed')
    branch_lines = (result.stdout or '').strip().split('\n')

    branches = []
    current_branch = None

    for line in branch_lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith('*'):
            current_branch = line[2:].strip()
            branches.append({
                'name': current_branch,
                'current': True,
                'remote': False
            })
        else:
            branch_name = line
            is_remote = False
            if line.startswith('remotes/'):
                branch_name = line.split('/', 2)[-1]
                is_remote = True

            branches.append({
                'name': branch_name,
                'current': False,
                'remote': is_remote
            })

    return {'branches': branches, 'current': current_branch}


def list_commits(cwd: str, page: int, per_page: int) -> Dict[str, Any]:
    """Recent commits with pagination (callers validate/clamp paging)."""
    skip = (page - 1) * per_page
    count_result = _check(_run(['rev-list', '--count', 'HEAD'], cwd), 'Git command failed')
    total_commits = int((count_result.stdout or '').strip())

    result = _check(_run(
        ['log', f'--max-count={per_page}', f'--skip={skip}',
         '--pretty=format:%H|%an|%ae|%ad|%s', '--date=iso'],
        cwd), 'Git command failed')

    commits = []
    for line in (result.stdout or '').strip().split('\n'):
        if not line:
            continue

        parts = line.split('|', 4)
        if len(parts) >= 5:
            commits.append({
                'hash': parts[0],
                'author': parts[1],
                'email': parts[2],
                'date': parts[3],
                'message': parts[4]
            })

    return {
        'commits': commits,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total_commits,
            'pages': (total_commits + per_page - 1) // per_page,
            'has_next': skip + per_page < total_commits,
            'has_prev': page > 1
        }
    }


def list_files(cwd: str, page: int, per_page: int,
               status_filter: Optional[str] = None) -> Dict[str, Any]:
    """Tracked files annotated with working-tree status (parsed exactly like
    the legacy handler, including its ``strip()`` behavior)."""
    tracked_result = _check(_run(['ls-files'], cwd), 'Git command failed')
    status_result = _check(_run(['status', '--porcelain'], cwd), 'Git command failed')

    status_map = {}
    for line in (status_result.stdout or '').strip().split('\n'):
        if not line:
            continue
        status_code = line[:2]
        filename = line[3:]

        if status_code == '??':
            status = 'untracked'
        elif status_code[0] == 'A':
            status = 'added'
        elif status_code[0] == 'M':
            status = 'modified'
        elif status_code[0] == 'D':
            status = 'deleted'
        elif status_code[1] == 'M':
            status = 'modified'
        elif status_code[1] == 'D':
            status = 'deleted'
        else:
            status = 'clean'

        status_map[filename] = {
            'status': status,
            'staged': status_code[0] != ' ' and status_code[0] != '?'
        }

    all_files = []
    for filename in (tracked_result.stdout or '').strip().split('\n'):
        if not filename:
            continue

        if filename in status_map:
            file_data = {
                'name': filename,
                'status': status_map[filename]['status'],
                'staged': status_map[filename]['staged']
            }
        else:
            file_data = {
                'name': filename,
                'status': 'clean',
                'staged': False
            }

        if status_filter is None or file_data['status'] == status_filter:
            all_files.append(file_data)

    total_files = len(all_files)
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    files = all_files[start_idx:end_idx]

    return {
        'files': files,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total_files,
            'pages': (total_files + per_page - 1) // per_page,
            'has_next': end_idx < total_files,
            'has_prev': page > 1
        }
    }


def commit_diff(cwd: str, commit_hash: str, page: int, per_page: int,
                file_filter: Optional[str] = None) -> Dict[str, Any]:
    """Commit metadata + changed files (with diff content) + paginated diff."""
    commit_info_result = _check(_run(
        ['show', '--format=%H|%an|%ae|%ad|%s', '--no-patch', commit_hash],
        cwd), 'Git command failed')

    commit_info_line = (commit_info_result.stdout or '').strip()
    if commit_info_line:
        parts = commit_info_line.split('|', 4)
        commit_info = {
            'hash': parts[0],
            'author': parts[1],
            'email': parts[2],
            'date': parts[3],
            'message': parts[4]
        }
    else:
        commit_info = {}

    files_result = _check(_run(['show', '--name-status', commit_hash], cwd),
                          'Git command failed')

    files_changed_raw = []
    for line in (files_result.stdout or '').strip().split('\n'):
        if not line or '\t' not in line:
            continue
        status, filename = line.split('\t', 1)
        files_changed_raw.append({
            'status': status,
            'filename': filename
        })

    if file_filter:
        full_diff_result = _check(_run(
            ['show', '--format=', commit_hash, '--', file_filter],
            cwd), 'Git command failed')
    else:
        full_diff_result = _check(_run(['show', '--format=', commit_hash], cwd),
                                  'Git command failed')

    full_diff_text = full_diff_result.stdout or ''
    files_changed = []

    for file_info in files_changed_raw:
        filename = file_info['filename']
        has_diff = False

        try:
            file_diff_result = _run(['show', '--format=', commit_hash, '--', filename], cwd)
            if file_diff_result.returncode != 0:
                raise GitError(f'Git command failed: {file_diff_result.stderr}',
                               returncode=file_diff_result.returncode,
                               stderr=file_diff_result.stderr)

            file_diff_text = file_diff_result.stdout or ''

            for line in file_diff_text.split('\n'):
                if line.startswith('+') and not line.startswith('+++'):
                    has_diff = True
                    break
                elif line.startswith('-') and not line.startswith('---'):
                    has_diff = True
                    break

        except GitError:
            has_diff = False

        if has_diff:
            files_changed.append(file_info)

    diff_lines = full_diff_text.split('\n')
    total_lines = len(diff_lines)

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_diff_lines = diff_lines[start_idx:end_idx]
    paginated_diff = '\n'.join(paginated_diff_lines)

    return {
        'commit': commit_info,
        'files': files_changed,
        'diff': paginated_diff,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total_lines': total_lines,
            'pages': (total_lines + per_page - 1) // per_page,
            'has_next': end_idx < total_lines,
            'has_prev': page > 1
        }
    }


def pull_repo(cwd: str, remote: str, branch: str) -> str:
    """Pull ``remote``/``branch``; returns stdout. Errors raise
    GitError('Git pull failed: …')."""
    proc = _run(['pull', remote, branch], cwd)
    if proc.returncode != 0:
        raise GitError(f'Git pull failed: {proc.stderr}',
                       returncode=proc.returncode, stderr=proc.stderr)
    return proc.stdout or ''


def current_branch_name(cwd: str) -> str:
    """Current branch or '' (never raises; mirrors the push fallback)."""
    try:
        proc = _run(['branch', '--show-current'], cwd)
    except Exception:
        return ''
    if proc.returncode != 0:
        return ''
    return (proc.stdout or '').strip()


def push_repo(cwd: str, remote: str, branch: str) -> subprocess.CompletedProcess:
    """Push; returns the raw result for the handler to shape (auth hints,
    sanitize, rich payload stay transport-side). Uses the shared
    ``git_push_command`` argv (upstream/HEAD fallbacks, credential-helper
    flags) exactly like the legacy handler did."""
    argv: List[str] = ['push', remote, branch]
    env = None
    if git_push_command is not None:
        try:
            full, env = git_push_command(remote, branch)
            argv = list(full[1:]) if full and full[0] == 'git' else list(full)
        except Exception:
            env = None
    if git_run is not None and env is None:
        return git_run(argv, cwd, timeout=None)
    import os as _os
    try:
        return subprocess.run(
            ['git', *argv], cwd=cwd, capture_output=True, text=True,
            timeout=None, check=False, env=env if env is not None else _os.environ.copy(),
        )
    except OSError as e:
        return subprocess.CompletedProcess(
            args=['git', *argv], returncode=127, stdout="", stderr=str(e))


def stage_and_commit(cwd: str, message: str, files: Any = '.') -> str:
    """Task close-via-commit, sharing GitHub App attribution with the Git UI.

    Existing staging/message/error semantics remain unchanged. Verified app
    identity overrides this commit's environment without changing gitconfig.
    """
    import subprocess as _sp
    from api.github_app import commit_env

    try:
        app_env = commit_env(cwd, os.environ.copy())
    except ValueError as exc:
        raise GitError(str(exc)) from exc

    try:
        if files != '.':
            for file in files.split():
                _sp.run(['git', 'add', file], check=True, cwd=cwd)
        else:
            _sp.run(['git', 'add', '.'], check=True, cwd=cwd)
        result = _sp.run(['git', 'commit', '-m', message], check=True, cwd=cwd,
                         capture_output=True, text=True,
                         **({"env": app_env} if app_env is not None else {}))
    except _sp.CalledProcessError as e:
        raise GitError(f'Git command failed: {str(e)}',
                       returncode=e.returncode, stderr=e.stderr)
    return (result.stdout or '')


def use_working_branch(cwd: str, branch: str) -> Dict[str, str]:
    """Explicit checkout action: refuse dirty worktrees and never overwrite a branch."""
    from core.git_refs import validate_branch_name
    branch = validate_branch_name(branch)
    current = _check(_run(['branch', '--show-current'], cwd), 'Cannot read branch').stdout.strip()
    if current == branch:
        return {'message': f'Already on branch: {branch}', 'branch': branch}
    status = _check(_run(['status', '--porcelain', '--untracked-files=all'], cwd), 'Cannot read worktree')
    if status.stdout.strip():
        raise GitError('Commit or stash pending changes before switching the working branch.')
    exists = _run(['show-ref', '--verify', '--quiet', 'refs/heads/' + branch], cwd)
    remote = _run(['show-ref', '--verify', '--quiet', 'refs/remotes/origin/' + branch], cwd)
    if exists.returncode == 0:
        args = ['switch', '--', branch]
    elif remote.returncode == 0:
        args = ['switch', '--track', '-c', branch, 'refs/remotes/origin/' + branch]
    else:
        args = ['switch', '-c', branch]
    _check(_run(args, cwd), 'Cannot switch working branch')
    return {'message': f'Switched to branch: {branch}', 'branch': branch}


def branch_operation(cwd: str, action: str, branch: str) -> Dict[str, str]:
    """create / switch / delete a branch. Unknown actions raise ValueError
    (the route maps this to 400); git failures raise GitError."""
    if action == 'create':
        cmd = ['checkout', '-b', branch]
        message = f'Created and switched to branch: {branch}'
    elif action == 'switch':
        cmd = ['checkout', branch]
        message = f'Switched to branch: {branch}'
    elif action == 'delete':
        cmd = ['branch', '-d', branch]
        message = f'Deleted branch: {branch}'
    else:
        raise ValueError(f'Invalid action: {action}')

    proc = _run(cmd, cwd)
    if proc.returncode != 0:
        raise GitError(f'Git branch operation failed: {proc.stderr}',
                       returncode=proc.returncode, stderr=proc.stderr)
    return {'message': message, 'output': proc.stdout or ''}
