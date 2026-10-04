"""Guest CLIs must not inherit host provider authentication."""
import json
import os
import subprocess
import sys

from core.agent_cli_env import agent_cli_env


def test_real_child_isolated_while_parent_keeps_credentials(monkeypatch):
    keys = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'META_API_KEY',
            'DEEPSEEK_API_KEY', 'CURSOR_API_KEY', 'OPENROUTER_API_KEY',
            'GEMINI_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
            'AZURE_OPENAI_API_KEY', 'AWS_SECRET_ACCESS_KEY')
    for key in keys:
        monkeypatch.setenv(key, 'host-only-test-secret')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://host-provider.invalid')
    child = subprocess.run(
        [sys.executable, '-c', 'import os,json; print(json.dumps(dict(os.environ)))'],
        env=agent_cli_env(), capture_output=True, text=True, check=True,
    )
    env = json.loads(child.stdout)
    for key in keys:
        assert key not in env
        assert os.environ[key] == 'host-only-test-secret'
    assert 'ANTHROPIC_BASE_URL' not in env
    assert env['PATH'] == os.environ['PATH']


def test_preserves_native_configuration_and_runtime():
    source = {'HOME': '/native/home', 'CODEX_HOME': '/native/codex',
              'HERMES_HOME': '/native/hermes', 'PATH': '/bin',
              'HTTPS_PROXY': 'http://proxy', 'CUTTLE_INTERNAL_API_BASE': 'https://localhost',
              'anthropic_api_key': 'host-key'}
    env = agent_cli_env(source)
    assert env == {k: v for k, v in source.items() if k != 'anthropic_api_key'}
    assert source['anthropic_api_key'] == 'host-key'


def test_bundled_catalog_does_not_request_host_api_credentials():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1] / 'api/agent_harness/agents'
    for path in root.glob('*/manifest.yaml'):
        manifest = yaml.safe_load(path.read_text())
        assert not manifest.get('credential_env'), path
