"""Run the shipped checkout updater against disposable local Git repositories."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == 'win32' or shutil.which('bash') is None, reason='POSIX lifecycle launcher test')

SCRIPT = Path(__file__).resolve().parents[3] / '.cuttle_global/scripts/client-self-update.sh'


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], text=True, encoding="utf-8", capture_output=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    seed = tmp_path / 'seed'
    seed.mkdir()
    git(seed, 'init', '-b', 'main')
    git(seed, 'config', 'user.name', 'Fixture')
    git(seed, 'config', 'user.email', 'fixture@example.invalid')
    (seed / 'file.txt').write_text('original\n', encoding="utf-8")
    (seed / '.gitignore').write_text('personal/\n', encoding="utf-8")
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'original')
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'clone', '--bare', str(seed), str(remote))
    client = tmp_path / 'client'
    git(tmp_path, 'clone', str(remote), str(client))
    git(client, 'config', 'user.name', 'Fixture')
    git(client, 'config', 'user.email', 'fixture@example.invalid')
    (seed / 'file.txt').write_text('upstream\n', encoding="utf-8")
    git(seed, 'commit', '-am', 'upstream')
    # Populate fixture remote through a local fetch, never any public forge.
    git(remote, 'fetch', str(seed), 'main:main')
    return client, remote


def run_update(tmp_path, client):
    # No process discovery, stops, launchers, sleeps, or model calls occur.
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'python3').symlink_to(sys.executable)
    for command in ('pgrep', 'sleep'):
        stub = bin_dir / command
        stub.write_text('#!/bin/sh\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)
    env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', XDG_STATE_HOME=str(tmp_path / 'state'))
    return subprocess.run(['bash', str(SCRIPT), '--repo', str(client), '--no-electron', '--no-daemon'], env=env, text=True, encoding="utf-8", capture_output=True, timeout=20)


@pytest.mark.skipif(shutil.which('bash') is None, reason='POSIX updater needs bash')
def test_clean_client_fast_forwards_and_keeps_ignored_personal(checkout, tmp_path):
    client, remote = checkout
    personal = client / 'personal'
    personal.mkdir()
    (personal / 'local.md').write_text('preserved', encoding="utf-8")
    result = run_update(tmp_path, client)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(client, 'rev-parse', 'HEAD') == git(remote, 'rev-parse', 'main')
    assert (personal / 'local.md').read_text(encoding="utf-8") == 'preserved'


@pytest.mark.parametrize('state', ['modified', 'staged', 'untracked', 'local_commit', 'diverged', 'detached', 'no_upstream'])
def test_update_refuses_local_work_and_keeps_head(checkout, tmp_path, state):
    client, _ = checkout
    local = client / 'file.txt'
    if state in ('modified', 'staged', 'diverged'):
        local.write_text('my work\n', encoding="utf-8")
    if state == 'staged':
        git(client, 'add', '.')
    if state in ('local_commit', 'diverged'):
        if state == 'local_commit':
            git(client, 'fetch', 'origin')
            git(client, 'merge', '--ff-only', 'origin/main')
            local.write_text('my commit\n', encoding="utf-8")
        git(client, 'commit', '-am', 'local work')
    if state == 'untracked':
        (client / 'new.txt').write_text('untracked work', encoding="utf-8")
    if state == 'detached':
        git(client, 'checkout', '--detach')
    if state == 'no_upstream':
        git(client, 'branch', '--unset-upstream')
    head = git(client, 'rev-parse', 'HEAD')
    contents = local.read_text(encoding="utf-8")
    result = run_update(tmp_path, client)
    assert result.returncode != 0
    assert 'ERROR' in result.stdout
    assert git(client, 'rev-parse', 'HEAD') == head
    assert local.read_text(encoding="utf-8") == contents
    if state == 'untracked':
        assert (client / 'new.txt').read_text(encoding="utf-8") == 'untracked work'


def test_incoming_file_does_not_overwrite_ignored_personal(checkout, tmp_path):
    client, remote = checkout
    personal = client / 'personal'
    personal.mkdir()
    (personal / 'local.md').write_text('my ignored content', encoding="utf-8")
    seed = tmp_path / 'seed'
    upstream_personal = seed / 'personal'
    upstream_personal.mkdir()
    (upstream_personal / 'local.md').write_text('incoming tracked content', encoding="utf-8")
    git(seed, 'add', '-f', 'personal/local.md')
    git(seed, 'commit', '-m', 'incoming collision')
    git(remote, 'fetch', str(seed), 'main:main')
    before = git(client, 'rev-parse', 'HEAD')
    result = run_update(tmp_path, client)
    assert result.returncode != 0
    assert git(client, 'rev-parse', 'HEAD') == before
    assert (personal / 'local.md').read_text(encoding="utf-8") == 'my ignored content'


@pytest.mark.parametrize('skip_pull', [False, True])
def test_refusal_happens_before_process_discovery_even_with_skip_pull(checkout, tmp_path, skip_pull):
    client, _ = checkout
    (client / 'file.txt').write_text('late user edit\n', encoding='utf-8')
    marker = tmp_path / 'lifecycle-called'
    bin_dir = tmp_path / 'guard-bin'
    bin_dir.mkdir()
    (bin_dir / 'python3').symlink_to(sys.executable)
    # Refusal must precede even process discovery. All lifecycle commands are
    # inert spies; an accidental call never reaches the real OS command.
    for command in ('pgrep', 'ps', 'kill', 'npm', 'electron'):
        stub = bin_dir / command
        stub.write_text('#!/bin/sh\n: > "$LIFECYCLE_MARKER"\nexit 0\n')
        stub.chmod(0o755)
    (bin_dir / 'sleep').write_text('#!/bin/sh\nexit 0\n')
    (bin_dir / 'sleep').chmod(0o755)
    env = dict(os.environ, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
               XDG_STATE_HOME=str(tmp_path / 'state'), LIFECYCLE_MARKER=str(marker))
    argv = ['bash', str(SCRIPT), '--repo', str(client), '--no-electron', '--no-daemon']
    if skip_pull:
        argv.append('--skip-pull')
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert not marker.exists()
    assert (client / 'file.txt').read_text() == 'late user edit\n'


@pytest.mark.parametrize('flags,expected', [
    (['--no-electron', '--no-daemon'], []),
    (['--no-electron'], ['101', '102']),
    (['--no-daemon'], ['103']),
    ([], ['101', '102', '103']),
])
def test_process_stops_respect_checkout_and_requested_components(checkout, tmp_path, flags, expected):
    client, _ = checkout
    marker = tmp_path / 'stopped'
    startup = tmp_path / 'bash-env'
    # Shell functions replace lifecycle commands, including Bash's kill builtin.
    # No test PID can reach a real OS signal or launch a real application.
    startup.write_text('''
pgrep() { printf '%s\\n' 101 102 103 104 105 106 107; }
ps() {
    case "$2" in
        101) echo "python $TEST_REPO/src/scripts/cuttle_client_daemon.py" ;;
        102) echo "python $TEST_REPO/src/scripts/cuttle_device_worker.py" ;;
        103) echo "$TEST_REPO/electron/node_modules/electron/dist/electron ." ;;
        104) echo "python /another/cuttle/src/scripts/cuttle_client_daemon.py" ;;
        105) echo "/another/cuttle/electron/node_modules/electron/dist/electron ." ;;
        106) echo "python $TEST_REPO/src/scripts/cuttle_daemon.py" ;;
        107) echo "python $TEST_REPO/src/api/web_chat_api.py" ;;
    esac
}
kill() { echo "$1" >> "$TEST_STOPPED"; }
sleep() { :; }
nohup() { :; }
npm() { :; }
''', encoding='utf-8')
    # An empty Electron directory permits the inert npm launch fallback.
    (client / 'electron').mkdir()
    env = dict(os.environ, BASH_ENV=str(startup), TEST_REPO=str(client),
               TEST_STOPPED=str(marker), XDG_STATE_HOME=str(tmp_path / 'state'))
    result = subprocess.run(['bash', str(SCRIPT), '--repo', str(client), *flags],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (marker.read_text().splitlines() if marker.exists() else []) == expected
