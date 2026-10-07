"""Exercise source and bundled executors with real Git and blocked lifecycle."""
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from api.device_workers import executor, platform
from tests.runtime.test_client_update_checkout import checkout, git

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / '.cuttle_global/scripts'


def load_sidecar():
    spec = importlib.util.spec_from_file_location('update_test_sidecar', ROOT / 'electron/device-worker/cuttle_device_worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=['source-posix', 'source-windows', 'sidecar-posix', 'sidecar-windows'])
def worker(request, tmp_path, monkeypatch):
    module = executor if request.param.startswith('source') else load_sidecar()
    # Simulate dispatch selection without changing pathlib's host OS.
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt' if request.param.endswith('windows') else 'posix', environ=os.environ, path=os.path))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'state with spaces'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state with spaces'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('CUTTLE_FLASK_HOST', 'fixture.invalid')
    from core import runtime_paths
    monkeypatch.setattr(runtime_paths, 'desktop_state_dir', lambda: tmp_path / 'state with spaces')
    calls = []
    real_popen = subprocess.Popen

    def guarded_popen(argv, **kwargs):
        # Only isolated Git + Python helper processes may execute. Lifecycle
        # scripts are recorded, never executed (including Windows dispatch).
        if Path(argv[0]).name in ('bash', 'powershell.exe'):
            calls.append((argv, kwargs))
            return SimpleNamespace(pid=123)
        assert Path(argv[0]).name.startswith(('git', 'python')), argv
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', guarded_popen)
    return module, calls


def params(client, embedded):
    result = {'repo': str(client), 'restart_electron': False, 'restart_daemon': False}
    if embedded:
        result.update(script_text=(SCRIPTS / 'client-self-update.ps1').read_text(encoding='utf-8-sig'),
                      script_text_posix=(SCRIPTS / 'client-self-update.sh').read_text(),
                      checkout_helper_text=(SCRIPTS / 'client-update-checkout.py').read_text())
    return result


def install_scripts(client):
    git(client, 'fetch', 'origin')
    git(client, 'merge', '--ff-only', 'origin/main')
    dest = client / '.cuttle_global/scripts'
    dest.mkdir(parents=True)
    for name in ('client-update-checkout.py', 'client-self-update.sh', 'client-self-update.ps1'):
        (dest / name).write_bytes((SCRIPTS / name).read_bytes())
    git(client, 'add', '.')
    git(client, 'commit', '-m', 'install updater')
    # Make this commit part of upstream, using a local fetch only.
    remote = client.parent / 'remote.git'
    git(remote, 'fetch', str(client), 'main:main')
    git(client, 'fetch', 'origin')
    seed = client.parent / 'seed'
    git(seed, 'fetch', str(remote), 'main')
    git(seed, 'merge', '--ff-only', 'FETCH_HEAD')


@pytest.mark.parametrize('embedded', [False, True])
def test_executor_fast_forward_preserves_personal_and_delivers_helper(worker, checkout, tmp_path, embedded):
    module, calls = worker
    client, remote = checkout
    if not embedded:
        install_scripts(client)
        seed = tmp_path / 'seed'
        git(seed, 'fetch', str(remote), 'main')
        git(seed, 'merge', '--ff-only', 'FETCH_HEAD')
        (seed / 'file.txt').write_text('next update\n')
        git(seed, 'commit', '-am', 'next')
        git(remote, 'fetch', str(seed), 'main:main')
    personal = client / 'personal/local.md'
    personal.parent.mkdir()
    personal.write_text('keep me')
    before_scripts = {p.name: p.read_bytes() for p in (client / '.cuttle_global/scripts').glob('*')} if not embedded else {}
    result = module.execute_job({'type': 'cuttle_self_update', 'params': params(client, embedded)})
    assert result['scheduled']
    assert len(calls) == 1
    assert git(client, 'rev-parse', 'HEAD') == git(remote, 'rev-parse', 'main')
    assert git(client, 'status', '--porcelain') == ''
    assert personal.read_text() == 'keep me'
    for name, content in before_scripts.items():
        assert (client / '.cuttle_global/scripts' / name).read_bytes() == content
    if module.os.name == 'nt':
        command = calls[0][0][-1]
        assert "-ArgumentList '" in command
        if embedded:
            script = next((tmp_path / 'state with spaces').rglob('client-self-update.ps1'))
            # Verify the actual dispatched native command retains quoted paths.
            assert f'"{script}"' in command
    if embedded:
        staged = list((tmp_path / 'state with spaces').rglob('client-update-checkout.py'))
        assert len(staged) == 1
        assert staged[0].read_bytes() == (SCRIPTS / 'client-update-checkout.py').read_bytes()


@pytest.mark.parametrize('embedded', [False, True])
@pytest.mark.parametrize('state', ['modified', 'staged', 'untracked', 'local_commit', 'diverged', 'ignored_collision', 'ignored_directory_collision', 'detached', 'no_upstream'])
def test_executor_refusal_preserves_work_and_never_schedules(worker, checkout, tmp_path, state, embedded):
    module, calls = worker
    client, remote = checkout
    if not embedded:
        install_scripts(client)
    file = client / 'file.txt'
    if state in ('modified', 'staged', 'diverged', 'local_commit'):
        file.write_text('my work\n')
    if state == 'staged':
        git(client, 'add', '.')
    if state == 'local_commit':
        git(client, 'fetch', 'origin')
        git(client, 'reset', '--soft', 'origin/main')
        git(client, 'add', '.')
    if state in ('local_commit', 'diverged'):
        git(client, 'commit', '-am', 'my work')
    if state == 'untracked':
        (client / 'new.txt').write_text('keep untracked')
    if state.startswith('ignored_'):
        personal = client / 'personal/local.md'
        personal.parent.mkdir()
        personal.write_text('keep ignored')
        seed = tmp_path / 'seed'
        incoming = seed / ('personal' if state == 'ignored_directory_collision' else 'personal/local.md')
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_text('incoming')
        git(seed, 'add', '-f', str(incoming.relative_to(seed)))
        git(seed, 'commit', '-m', 'collision')
        git(remote, 'fetch', str(seed), 'main:main')
    if state == 'detached':
        git(client, 'checkout', '--detach')
    if state == 'no_upstream':
        git(client, 'branch', '--unset-upstream')
    before = git(client, 'rev-parse', 'HEAD')
    status = git(client, 'status', '--porcelain')
    files = {p.relative_to(client): p.read_bytes() for p in client.rglob('*') if p.is_file() and '.git' not in p.relative_to(client).parts}
    with pytest.raises(RuntimeError, match="update refused"):
        module.execute_job({'type': 'cuttle_self_update', 'params': params(client, embedded)})
    assert calls == []
    assert git(client, 'rev-parse', 'HEAD') == before
    assert git(client, 'status', '--porcelain') == status
    assert files == {p.relative_to(client): p.read_bytes() for p in client.rglob('*') if p.is_file() and '.git' not in p.relative_to(client).parts}


def test_coordinator_embeds_preservation_owner(monkeypatch):
    monkeypatch.setattr(platform, 'submit_job', lambda **kwargs: kwargs)
    job = platform.submit_self_update(target_worker_id='fixture')
    assert job['params']['checkout_helper_text'] == (SCRIPTS / 'client-update-checkout.py').read_text()


@pytest.mark.parametrize('missing', ['client-self-update.ps1', 'client-self-update.sh', 'client-update-checkout.py'])
def test_coordinator_refuses_incomplete_delivery(monkeypatch, missing):
    real_read = Path.read_text
    submitted = []

    def read(path, *args, **kwargs):
        if path.name == missing:
            raise FileNotFoundError(missing)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(platform, 'submit_job', lambda **kwargs: submitted.append(kwargs))
    with pytest.raises(FileNotFoundError):
        platform.submit_self_update(target_worker_id='fixture')
    assert submitted == []
