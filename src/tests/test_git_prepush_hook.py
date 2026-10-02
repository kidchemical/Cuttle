"""Pre-push secret gate: hook blocks secrets/blocked files, passes clean pushes.

Exercises scripts/git-hooks/pre-push against real git repos in tmp_path.
Gitleaks is force-disabled (CUTTLE_PREPUSH_NO_GITLEAKS=1) so the suite is
hermetic; the gitleaks path is an extra layer on dev machines, not the
thing under test.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".cuttle" / "scripts" / "git-hooks" / "pre-push"
ZERO_SHA = "0" * 40


def _git(*args, cwd, **kw):
    base = ["git", "-c", "user.name=T", "-c", "user.email=t@t",
            "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"]
    env = dict(os.environ)
    env["CUTTLE_PREPUSH_NO_GITLEAKS"] = "1"
    env.update(kw.pop("env", {}))
    return subprocess.run(base + list(args), cwd=cwd, capture_output=True,
                          text=True, timeout=60, env=env, **kw)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    (path / "seed.txt").write_text("one\n", encoding="utf-8")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "seed", cwd=path)
    return path


def _run_hook(repo: Path, local_sha: str, remote_sha: str = ZERO_SHA) -> subprocess.CompletedProcess:
    stdin = f"refs/heads/main {local_sha} refs/heads/main {remote_sha}\n"
    env = dict(os.environ)
    env["CUTTLE_PREPUSH_NO_GITLEAKS"] = "1"
    return subprocess.run([str(HOOK)], input=stdin, cwd=repo, capture_output=True,
                          text=True, timeout=120, env=env)


def _head(repo: Path) -> str:
    proc = _git("rev-parse", "HEAD", cwd=repo)
    assert proc.returncode == 0
    return proc.stdout.strip()


def _synthetic_aws_key() -> str:
    """Assemble an AWS-shaped synthetic key at runtime.

    Neither part below is token-shaped on its own, so this file never
    contains a scannable key literal — but the assembled value still
    trips the hook's AWS access-key pattern, preserving the regression.
    """
    return "AKIA" + "0" * 16


def test_hook_file_exists_and_executable():
    assert HOOK.is_file()
    assert os.access(HOOK, os.X_OK)


def test_clean_push_passes(tmp_path):
    repo = _init_repo(tmp_path / "clean")
    result = _run_hook(repo, _head(repo))
    assert result.returncode == 0, result.stdout + result.stderr


def test_env_file_push_blocked(tmp_path):
    repo = _init_repo(tmp_path / "env")
    (repo / ".env").write_text("DISCORD_TOKEN=abc123\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "oops env", cwd=repo)
    result = _run_hook(repo, _head(repo))
    assert result.returncode != 0
    assert "pre-push hook: push blocked" in result.stdout
    assert ".env" in result.stdout


def test_aws_key_push_blocked(tmp_path):
    repo = _init_repo(tmp_path / "aws")
    (repo / "config.py").write_text(f'KEY = "{_synthetic_aws_key()}"\n', encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "oops key", cwd=repo)
    result = _run_hook(repo, _head(repo))
    assert result.returncode != 0
    assert "pre-push hook: push blocked" in result.stdout


def test_branch_deletion_skipped(tmp_path):
    repo = _init_repo(tmp_path / "del")
    result = _run_hook(repo, ZERO_SHA, remote_sha=_head(repo))
    assert result.returncode == 0, result.stdout + result.stderr
