"""Isolated git workspace helpers for Gitea-triggered Cuttle jobs.

``E:\\CuttleWorkspaces\\…`` (or whatever ``CUTTLE_JOBS_WORKSPACES`` points at) is a
**machine-owned** tree: never use it for manual Unity/editor work. The worker is
serialized (one job at a time), so a single persistent copy is enough — no need
to duplicate a huge LFS clone per issue.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from api.cuttle_jobs.commands import cuttle_issue_branch


class WorkspaceError(RuntimeError):
    """Unsafe or ambiguous git state — stop and report to Gitea (infrastructure)."""

    def __init__(self, message: str, *, code: str = "workspace_error") -> None:
        super().__init__(message)
        self.code = code


def _read_env_file() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if not env_path.is_file():
            return out
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _env(key: str, default: str = "") -> str:
    file_env = _read_env_file()
    return (os.getenv(key) or file_env.get(key) or default).strip()


def parse_workspace_map() -> Dict[str, Path]:
    """
    ``CUTTLE_JOBS_WORKSPACES=owner/repo=/path/to/workspace,...``
    plus optional ``CUTTLE_JOBS_WORKSPACE_owner_repo=...``
    """
    out: Dict[str, Path] = {}
    raw = _env("CUTTLE_JOBS_WORKSPACES")
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        repo, _, path = part.partition("=")
        repo = repo.strip()
        path = path.strip().strip('"')
        if repo and path:
            out[repo] = Path(path)
    file_env = _read_env_file()
    combined = {**file_env, **dict(os.environ)}
    for key, val in combined.items():
        if not key.startswith("CUTTLE_JOBS_WORKSPACE_"):
            continue
        if key in ("CUTTLE_JOBS_WORKSPACE_ROOT", "CUTTLE_JOBS_WORKSPACES"):
            continue
        suffix = key[len("CUTTLE_JOBS_WORKSPACE_") :]
        if val.strip():
            repo_guess = suffix.replace("_", "/", 1)
            out[repo_guess] = Path(val.strip().strip('"'))
    return out


def resolve_workspace(repository: str) -> Path:
    mapped = parse_workspace_map()
    if repository in mapped:
        path = mapped[repository]
    else:
        root = _env("CUTTLE_JOBS_WORKSPACE_ROOT")
        if not root:
            raise WorkspaceError(
                f"No isolated workspace configured for {repository}. "
                "Set CUTTLE_JOBS_WORKSPACES or CUTTLE_JOBS_WORKSPACE_ROOT in src/.env",
                code="workspace_not_configured",
            )
        owner, _, repo = repository.partition("/")
        path = Path(root) / (repo or owner)
    if not path.is_dir():
        raise WorkspaceError(
            f"Workspace path does not exist: {path}",
            code="workspace_missing",
        )
    if not (path / ".git").exists() and not (path / "source" / ".git").exists():
        raise WorkspaceError(
            f"Workspace is not a git repository: {path}",
            code="workspace_not_git",
        )
    return path


def git_workdir(workspace: Path) -> Path:
    """Prefer nested Unity ``source/`` git dir when present."""
    nested = workspace / "source"
    if (nested / ".git").exists():
        return nested
    return workspace


def cuttle_commit_env() -> Dict[str, str]:
    """Author/committer identity for job commits (does not touch global gitconfig).

    Gitea links commit avatars to users by email — use an address on the Cuttle
    Gitea account (``GITEA_COMMIT_AUTHOR_EMAIL``).
    """
    name = (
        _env("GITEA_COMMIT_AUTHOR_NAME")
        or _env("GITEA_AGENT_USERNAME")
        or "Cuttle"
    )
    email = _env("GITEA_COMMIT_AUTHOR_EMAIL") or "cuttle@localhost"
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


def run_git(
    workdir: Path,
    *args: str,
    check: bool = True,
    timeout: int = 600,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(workdir), *args]
    run_env = None
    if env is not None:
        run_env = {**os.environ, **env}
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        env=run_env,
    )
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:800]
        raise WorkspaceError(f"git {' '.join(args)} failed: {err}", code="git_error")
    return proc


def ensure_clean(workdir: Path) -> None:
    proc = run_git(workdir, "status", "--porcelain")
    dirty = (proc.stdout or "").strip()
    if dirty:
        raise WorkspaceError(
            "Cuttle workspace is dirty; refusing to start. "
            f"This tree is machine-owned — do not edit it manually. "
            f"Path: {workdir}\n{dirty[:500]}",
            code="workspace_dirty",
        )


def current_branch(workdir: Path) -> str:
    proc = run_git(workdir, "rev-parse", "--abbrev-ref", "HEAD")
    return (proc.stdout or "").strip()


def remote_branch_exists(workdir: Path, branch: str) -> bool:
    proc = run_git(
        workdir,
        "ls-remote",
        "--heads",
        "origin",
        branch,
        check=False,
    )
    return bool((proc.stdout or "").strip())


def local_branch_exists(workdir: Path, branch: str) -> bool:
    proc = run_git(
        workdir,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}",
        check=False,
    )
    return proc.returncode == 0


def fetch_origin(workdir: Path) -> None:
    try:
        run_git(workdir, "fetch", "origin", "--prune", timeout=1200)
    except WorkspaceError as e:
        raise RuntimeError(f"git fetch failed (will retry): {e}") from e


def prepare_issue_branch(
    workdir: Path,
    *,
    issue_number: int,
    base_branch: str,
) -> Tuple[str, bool]:
    """
    Check out ``cuttle/issue-N``.

    - **New issue branch:** always created from current ``origin/<base>`` after fetch
      (never from a stale local ``dev/core``).
    - **Follow-up:** prefer ``origin/cuttle/issue-N`` after fetch; do not delete remotes.
    - Local ``cuttle/issue-*`` branches from prior issues are left alone.
    """
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", base_branch):
        raise WorkspaceError(
            f"Unsafe base branch name: {base_branch!r}",
            code="unsafe_base_branch",
        )
    branch = cuttle_issue_branch(issue_number)
    if not branch.startswith("cuttle/issue-"):
        raise WorkspaceError(
            f"Refusing non-Cuttle branch: {branch}",
            code="unsafe_branch",
        )

    ensure_clean(workdir)
    fetch_origin(workdir)

    origin_base = f"origin/{base_branch}"
    probe = run_git(workdir, "rev-parse", "--verify", origin_base, check=False)
    if probe.returncode != 0:
        raise WorkspaceError(
            f"Missing {origin_base} after fetch; cannot base Cuttle work",
            code="missing_base_branch",
        )

    created = False
    if remote_branch_exists(workdir, branch):
        # Follow-up or resume: sync local to remote tip (never recreate from base).
        run_git(workdir, "checkout", "-B", branch, f"origin/{branch}")
    elif local_branch_exists(workdir, branch):
        # Remote not published yet (interrupted before first push).
        run_git(workdir, "checkout", branch)
    else:
        # Brand-new issue branch from *fresh* origin/<base>, not local base.
        run_git(workdir, "checkout", "-B", branch, origin_base)
        created = True

    if current_branch(workdir) != branch:
        raise WorkspaceError(
            "Failed to check out Cuttle issue branch",
            code="checkout_failed",
        )
    return branch, created


def park_workspace(workdir: Path, *, base_branch: str = "dev/core") -> None:
    """
    After a job: hard-reset/clean and detach onto ``origin/<base>``.

    Keeps all ``cuttle/issue-*`` refs (local + remote). Next job's
    ``prepare_issue_branch`` will switch to the needed issue branch.
    """
    reset_hard_clean(workdir)
    fetch_origin(workdir)
    origin_base = f"origin/{base_branch}"
    probe = run_git(workdir, "rev-parse", "--verify", origin_base, check=False)
    if probe.returncode == 0:
        # Detached HEAD on base tip — not a human branch checkout.
        run_git(workdir, "checkout", "--detach", origin_base)
    # If base missing, leave reset-on-current; next job will fail clearly.


def list_changed_files(workdir: Path, base_ref: str) -> List[str]:
    proc = run_git(workdir, "diff", "--name-only", f"{base_ref}...HEAD", check=False)
    if proc.returncode != 0:
        proc = run_git(workdir, "status", "--porcelain")
        files = []
        for line in (proc.stdout or "").splitlines():
            if len(line) >= 4:
                files.append(line[3:].strip())
        return files
    return [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]


def suggest_job_commit_message(
    workdir: Path,
    *,
    issue_number: int,
    issue_title: str = "",
    triggering_user: str = "",
) -> str:
    """Same subject pipeline as Pending Changes (OpenAI → … → heuristic).

    Body always includes the Gitea issue trailer so PRs stay linkable.
    """
    title = (issue_title or "").strip() or "Cuttle solve"
    fallback_subject = f"fix(#{issue_number}): {title}"
    if len(fallback_subject) > 72:
        fallback_subject = fallback_subject[:71].rstrip() + "…"

    subject = fallback_subject
    try:
        from scripts.utilities.git_pending_changes import collect_commit_suggest_context
        from api.commit_message_suggester import (
            sanitize_commit_message,
            suggest_commit_message,
        )

        ctx = collect_commit_suggest_context(str(workdir))
        prompts = [f"Gitea issue #{issue_number}: {title}"]
        if triggering_user:
            prompts.append(f"Requested via @cuttle by @{triggering_user}")
        result = suggest_commit_message(ctx, user_prompts=prompts)
        cleaned = sanitize_commit_message(str(result.get("message") or ""))
        if cleaned:
            subject = cleaned
    except Exception as e:
        # Never block a fix on message suggestion — fall back to issue-tied subject.
        print(f"[CUTTLE-JOBS] commit message suggest failed: {e}", flush=True)

    body_lines = [f"Fixes #{issue_number}"]
    if triggering_user:
        body_lines.append(f"Triggered by @{triggering_user}")
    return f"{subject}\n\n" + "\n".join(body_lines)


def commit_all(workdir: Path, message: str) -> Optional[str]:
    """Stage tracked+untracked (respecting .gitignore) and commit. Returns sha or None if empty.

    Commits as the Cuttle bot identity (see ``cuttle_commit_env``), not the host
    user.name / user.email.
    """
    run_git(workdir, "add", "-A")
    staged = run_git(workdir, "diff", "--cached", "--name-only")
    if not (staged.stdout or "").strip():
        return None
    run_git(workdir, "commit", "-m", message, env=cuttle_commit_env())
    sha = run_git(workdir, "rev-parse", "HEAD").stdout.strip()
    return sha


def push_branch(workdir: Path, branch: str) -> None:
    if not branch.startswith("cuttle/issue-"):
        raise WorkspaceError(
            f"Refusing to push non-Cuttle branch: {branch}",
            code="unsafe_push",
        )
    try:
        run_git(workdir, "push", "-u", "origin", branch, timeout=1200)
    except WorkspaceError as e:
        raise RuntimeError(f"push rejected or failed for `{branch}`: {e}") from e


def reset_hard_clean(workdir: Path) -> None:
    """Discard uncommitted work in the machine-owned workspace only."""
    run_git(workdir, "reset", "--hard", "HEAD")
    run_git(workdir, "clean", "-fd")
