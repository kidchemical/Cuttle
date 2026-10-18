"""Executable overrides must not bypass a wrapper or launch real vendor tools."""
import importlib
import os
import shutil

import pytest

from api.agent_harness import cli_paths
from managers.settings_manager import SettingsManager


RESOLVERS = [
    ('codex', 'scripts.utilities.codex_cli_tool', 'codex_executable', False),
    ('claude', 'scripts.utilities.claude_cli_tool', 'claude_executable', False),
    ('hermes', 'scripts.utilities.hermes_cli_tool', 'hermes_executable', False),
    ('muse', 'scripts.utilities.muse_cli_tool', '_which_muse_native', False),
    ('cursor', 'scripts.utilities.cursor_cli_tool', '_resolve_cursor_agent_argv', True),
    ('cursor', 'scripts.utilities.cursor_cli_tool', '_resolve_cursor_agent_cli', False),
    ('deepseek', 'api.agent_harness.agents.deepseek.adapter', 'dsh_argv', True),
    ('opencode', 'api.agent_harness.agents.opencode.adapter', 'opencode_executable', False),
    ('antigravity', 'api.agent_harness.agents.antigravity.adapter', 'antigravity_executable', False),
]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    manager = SettingsManager(str(tmp_path / 'settings.json'))
    monkeypatch.setattr(cli_paths, 'get_settings_manager', lambda: manager)
    return manager


@pytest.mark.parametrize('agent,module,function,argv', RESOLVERS)
def test_exact_wrapper_wins_and_missing_wrapper_never_falls_back(
    agent, module, function, argv, manager, tmp_path, monkeypatch,
):
    resolve = getattr(importlib.import_module(module), function)
    wrapper = tmp_path / 'wrapper with spaces'
    wrapper.write_text('#!/bin/sh\nexit 0\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv(cli_paths.CLI_PATH_ENV[agent], '/environment/vendor-cli')
    before_env = dict(os.environ)
    cli_paths.save_cli_paths({agent: str(wrapper)})
    monkeypatch.setattr(shutil, 'which', lambda *a, **kw: pytest.fail('Must not use PATH'))
    assert resolve() == ([str(wrapper)] if argv else str(wrapper))
    # After a saved wrapper is removed, do not run the host CLI in its place.
    wrapper.unlink()
    assert resolve() is None
    assert dict(os.environ) == before_env


def test_merge_reset_and_storage_failure(manager, tmp_path, monkeypatch):
    cli_paths.save_cli_paths({'codex': '/one', 'claude': '/two'})
    cli_paths.save_cli_paths({'codex': ''})
    assert manager.get_setting('agent_cli_paths') == {'claude': '/two'}
    monkeypatch.setattr(manager, 'update_setting', lambda *a: False)
    with pytest.raises(OSError, match='Failed to save'):
        cli_paths.save_cli_paths({'muse': '/three'})


def test_reset_restores_existing_environment_discovery(manager, tmp_path, monkeypatch):
    from scripts.utilities.codex_cli_tool import codex_executable

    wrapper = tmp_path / 'wrapper'
    native = tmp_path / 'native'
    for path in (wrapper, native):
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    monkeypatch.setenv('CODEX_CLI_PATH', str(native))
    cli_paths.save_cli_paths({'codex': str(wrapper)})
    assert codex_executable() == str(wrapper)
    cli_paths.save_cli_paths({'codex': ''})
    assert codex_executable() == str(native)


def test_probes_and_usage_do_not_bypass_selected_wrappers(manager, tmp_path, monkeypatch):
    import subprocess
    from scripts.utilities.claude_cli_tool import claude_cli_health
    from api.agent_usage import _hermes_argv
    from api.agent_context import _which_opencode

    wrapper = tmp_path / 'probe-wrapper'
    wrapper.write_text('#!/bin/sh\nexit 0\n')
    wrapper.chmod(0o755)
    cli_paths.save_cli_paths({agent: str(wrapper) for agent in ('claude', 'hermes', 'opencode')})
    calls = []

    def failed_probe(args, **kwargs):
        calls.append(args)
        raise OSError('wrapper failed')

    monkeypatch.setattr(subprocess, 'run', failed_probe)
    assert claude_cli_health() is False
    assert calls == [[str(wrapper), '--version']]
    assert _hermes_argv() == [str(wrapper)]
    assert _which_opencode() == str(wrapper)
    wrapper.unlink()
    assert claude_cli_health() is False
    assert _hermes_argv() is None
    assert _which_opencode() is None
    assert len(calls) == 1


def test_every_bundled_agent_has_an_override_integration():
    from pathlib import Path
    manifests = Path(__file__).resolve().parents[2] / 'api/agent_harness/agents'
    assert set(cli_paths.CLI_PATH_ENV) == {p.parent.name for p in manifests.glob('*/manifest.yaml')}
