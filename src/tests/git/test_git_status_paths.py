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
