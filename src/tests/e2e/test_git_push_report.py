"""Isolated popup/browser checks; no live push or hosting-runtime changes."""
import json
from tests.e2e.test_shared_diff_modal import browser, static_server, surface  # noqa: F401

REPORT = {
    'version': 1,
    'summary': 'Pre-push secret hook blocked the push: secret-patterns, sqlite-secrets',
    'findings': [
        {'hook': 'secret-patterns', 'rule': 'private key', 'file': 'src/tests/test_github_app.py', 'commit': 'dc85768c', 'line': 372, 'preview': '[Flagged line redacted]'},
        {'hook': 'sqlite-secrets', 'rule': 'AWS access key', 'file': 'fixture.db', 'commit': 'abcdef', 'table': 'config', 'column': 'value', 'row': 2},
    ],
    'warnings': [],
}


def test_chat_action_report_opens_and_escapes_untrusted_filename(surface):
    page, api, errors = surface('chat_page.html')
    report = dict(REPORT, findings=[dict(REPORT['findings'][0], file='<img src=x onerror=alert(1)>')])
    response = {'results': [{'action': 'git.push', 'response': 'CUTTLE_PUSH_REPORT=' + json.dumps(report)}]}
    summary = page.evaluate('(data) => CuttleGitPushReport.fromAction(data)', response)
    assert 'secret-patterns' in summary
    dialog = page.locator('#cuttle-push-report')
    assert dialog.is_visible()
    assert dialog.locator('img').count() == 0
    assert '<img src=x' in dialog.inner_text()
    dialog.get_by_role('button', name='Close').click()
    assert not errors
