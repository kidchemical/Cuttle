"""Regression tests against real Git: paths must survive observation intact."""
import os
import subprocess

import pytest

from core.git_status import parse_status_z
from api.agent_router.supervised.evidence import snapshot_worktree


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)


@pytest.mark.skipif(os.name == "nt", reason='these names use " and > and trailing spaces, which Windows forbids')
def test_literal_paths_and_rename(tmp_path):
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.email', 'test@example.test')
    git(tmp_path, 'config', 'user.name', 'Test')
    (tmp_path / 'first.txt').write_text('before')
    (tmp_path / 'old.txt').write_text('rename me')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'initial')
    (tmp_path / 'first.txt').write_text('after')
    git(tmp_path, 'mv', 'old.txt', ' new -> ü.txt ')
    for name in [' spaces.txt ', 'quote".txt', 'ü.txt', 'arrow -> path.txt']:
        (tmp_path / name).write_text(name)
    snap = snapshot_worktree(str(tmp_path))
    assert snap['ok']
    assert set(snap['paths']) == {'first.txt', ' new -> ü.txt ', ' spaces.txt ', 'quote".txt', 'ü.txt', 'arrow -> path.txt'}
    assert snap['paths']['first.txt']['status'] == ' M'
    assert snap['paths']['first.txt']['digest']


def test_rename_source_not_mistaken_for_destination():
    assert parse_status_z(' M first\0R  destination\0source\0?? a\nb\0') == {
        'first': ' M', 'destination': 'R ', 'a\nb': '??',
    }


def test_oversized_file_digest_is_bounded(tmp_path, monkeypatch):
    import io
    from pathlib import Path
    from api.agent_router.supervised.evidence import _file_content_digest

    path = tmp_path / 'big.bin'
    path.write_bytes(b'a' * 1000)
    class CountingReader(io.BytesIO):
        consumed = 0

        def read(self, size=-1):
            data = super().read(size)
            self.consumed += len(data)
            return data

    reader = CountingReader(b'a' * 1000)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', lambda *a, **k: reader)
        assert _file_content_digest(path, limit=100)
    assert reader.consumed == 100
    first = _file_content_digest(path, limit=100)
    path.write_bytes(b'a' * 1001)
    second = _file_content_digest(path, limit=100)
    assert first and second and first != second


def test_oversized_file_digest_detects_same_size_tail_edits(tmp_path):
    import os
    from api.agent_router.supervised.evidence import _file_content_digest

    path = tmp_path / 'big.bin'
    path.write_bytes(b'a' * 1000)
    first = _file_content_digest(path, limit=100)
    stamp = path.stat().st_mtime_ns
    with path.open('r+b') as f:
        f.seek(999)
        f.write(b'b')
    os.utime(path, ns=(stamp, stamp + 1_000_000_000))
    assert _file_content_digest(path, limit=100) != first
