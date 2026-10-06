"""Retired apps stay unreachable; old saved pane URLs recover to Chat."""
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[3]
PAGES = ('task_management', 'media_player', 'git_ui', 'wizard_page')


def test_retired_routes_are_absent(tmp_path, monkeypatch):
    from tests.auth.test_http_authz import _auth_client
    ctx = _auth_client(tmp_path, monkeypatch)
    client = ctx['client']
    client.set_cookie('session_token', ctx['token'])
    for path in [*(f'/{name}.html' for name in PAGES), '/api/tasks',
                 '/api/tasks/1', '/api/wizard/status']:
        assert client.get(path).status_code == 404, path


def test_saved_retired_page_urls_recover_to_chat():
    source = (REPO / 'src/web/js/shell/app_shell.js').read_text()
    start = source.index('function canonicalizeShellPage(page)')
    end = source.index('function pageWithPaneSession', start)
    script = "const assert = require('assert'); const window = {location: {origin: 'http://localhost'}};\n"
    script += source[start:end]
    script += 'const retired = ' + json.dumps(PAGES) + ';\n'
    script += """
for (const name of retired) {
    assert.equal(canonicalizeShellPage('/' + name + '.html?old=1#panel'), '/chat_page.html');
}
assert.equal(canonicalizeShellPage('/git_graph_page.html'), '/git_graph_page.html');
assert.equal(canonicalizeShellPage('/chat_page.html?chat=7'), '/chat_page.html?chat=7');
"""
    subprocess.run(['node', '-e', script], check=True)
