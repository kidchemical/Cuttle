"""Host-served git bundle (Scenario B) — manifest + artifact."""
from pathlib import Path
import subprocess

from api import desktop_git


def _git(repo: Path, *args: str) -> str:
    env = {
        **__import__('os').environ,
        'GIT_AUTHOR_NAME': 'Fixture',
        'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
        'GIT_COMMITTER_NAME': 'Fixture',
        'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
    }
    return subprocess.run(
        ['git', '-C', str(repo), *args], capture_output=True, text=True, env=env, check=True
    ).stdout.strip()


def _make_repo(root: Path, branch: str = 'dev') -> Path:
    repo = root / 'host'
    repo.mkdir()
    _git(repo, 'init', '-b', branch)
    (repo / 'file.txt').write_text('one\n', encoding='utf-8')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', 'one')
    return repo


def test_manifest_release_when_no_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_git, 'repo_root', lambda: tmp_path / 'missing')
    man = desktop_git.git_manifest()
    assert man['updateSource'] == 'release'
    assert man['artifact'] is False
    assert man['downloadPath'] is None
    assert 'reason' in man


def test_manifest_and_bundle_on_checkout(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, 'dev')
    cache = tmp_path / 'cache'
    monkeypatch.setattr(desktop_git, 'repo_root', lambda: repo)
    monkeypatch.setattr(desktop_git, 'bundle_dir', lambda: cache)

    man = desktop_git.git_manifest()
    assert man['ok'] is True
    assert man['updateSource'] == 'host'
    assert man['branch'] == 'dev'
    assert man['artifact'] is True
    assert man['downloadPath'] == '/api/desktop/git/bundle'
    assert man['artifactSize'] > 0
    assert len(man['artifactSha256']) == 64
    bundles = list(cache.glob('host-*.bundle'))
    assert len(bundles) == 1

    # A new commit advances the rev and re-caches (pruning the old bundle).
    (repo / 'file.txt').write_text('two\n', encoding='utf-8')
    _git(repo, 'commit', '-am', 'two')
    man2 = desktop_git.git_manifest()
    assert man2['rev'] != man['rev']
    assert len(list(cache.glob('host-*.bundle'))) == 1
