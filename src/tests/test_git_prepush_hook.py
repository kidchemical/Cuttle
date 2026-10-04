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


def _report(result):
    import json
    from core.git_push_diagnostics import REPORT_PREFIX
    return json.loads(next(line[len(REPORT_PREFIX):] for line in result.stdout.splitlines() if line.startswith(REPORT_PREFIX)))


def test_report_names_commit_file_line_and_redacts_value(tmp_path):
    repo = _init_repo(tmp_path / 'report')
    base = _head(repo)
    secret = _synthetic_aws_key()
    (repo / 'config.py').write_text('KEY = "' + secret + '"\n')
    _git('add', '.', cwd=repo)
    _git('commit', '-m', 'introduce credential', cwd=repo)
    sha = _head(repo)
    result = _run_hook(repo, sha, base)
    report = _report(result)
    hit = report['findings'][0]
    assert (hit['hook'], hit['file'], hit['commit'], hit['line']) == ('secret-patterns', 'config.py', sha, 1)
    assert secret not in result.stdout + result.stderr
    # A later cleanup does not make the introduced secret safe to publish.
    (repo / 'config.py').write_text('KEY = None\n')
    _git('add', '.', cwd=repo)
    _git('commit', '-m', 'remove credential', cwd=repo)
    assert _run_hook(repo, _head(repo), base).returncode == 1


@pytest.mark.parametrize('secret', [False, True])
def test_sqlite_committed_cells_checked_without_exposing_values(tmp_path, secret):
    import sqlite3
    repo = _init_repo(tmp_path / 'sqlite')
    base = _head(repo)
    path = repo / 'fixture.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE config (value TEXT)')
        db.execute('INSERT INTO config VALUES (?)', (_synthetic_aws_key() if secret else 'ordinary data',))
    _git('add', '.', cwd=repo)
    _git('commit', '-m', 'database', cwd=repo)
    # The committed blob is authoritative, regardless of subsequent local edits.
    path.unlink()
    result = _run_hook(repo, _head(repo), base)
    assert result.returncode == int(secret)
    if secret:
        hit = next(f for f in _report(result)['findings'] if f['hook'] == 'sqlite-secrets')
        assert (hit['file'], hit['table'], hit['column'], hit['row']) == ('fixture.db', 'config', 'value', 1)
        assert _synthetic_aws_key() not in result.stdout


def test_unknown_database_blocks_with_explicit_inspection_reason(tmp_path):
    repo = _init_repo(tmp_path / 'unknown-db')
    base = _head(repo)
    (repo / 'opaque.db').write_bytes(b'unsupported format')
    _git('add', '.', cwd=repo)
    _git('commit', '-m', 'opaque database', cwd=repo)
    report = _report(_run_hook(repo, _head(repo), base))
    assert report['findings'][0]['hook'] == 'sqlite-secrets'
    assert 'inspection unavailable' in report['findings'][0]['rule']


def test_sqlite_credential_column_blocks_unrecognizable_tokens(tmp_path):
    import sqlite3
    repo = _init_repo(tmp_path / 'sqlite-column')
    base = _head(repo)
    with sqlite3.connect(repo / 'config.db') as db:
        db.execute('CREATE TABLE config (api_key TEXT)')
        db.execute('INSERT INTO config VALUES (?)', ('opaque credential value',))
    _git('add', '.', cwd=repo)
    _git('commit', '-m', 'credential column', cwd=repo)
    result = _run_hook(repo, _head(repo), base)
    assert result.returncode == 1
    hit = _report(result)['findings'][0]
    assert hit['rule'] == 'Nonempty credential column'
    assert 'opaque credential value' not in result.stdout


def test_real_private_key_in_same_test_filename_is_not_excepted(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    repo = _init_repo(tmp_path / 'real-key')
    base = _head(repo)
    path = repo / 'src/tests/test_github_app.py'
    path.parent.mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    _git('add', '.', cwd=repo)
    _git('commit', '-m', 'credential accidentally committed', cwd=repo)
    result = _run_hook(repo, _head(repo), base)
    assert result.returncode == 1
    assert any(hit['hook'] == 'secret-patterns' and hit['file'] == 'src/tests/test_github_app.py' for hit in _report(result)['findings'])
