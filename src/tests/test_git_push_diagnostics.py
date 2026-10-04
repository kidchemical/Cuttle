import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.git_push_diagnostics import REPORT_PREFIX, push_failure, scan_gitleaks


def test_hook_report_wins_over_generic_git_stderr():
    report = {'version': 1, 'findings': [{'hook': 'gitleaks', 'commit': 'abc', 'file': 'config.py', 'line': 12, 'rule': 'private-key'}], 'warnings': []}
    result = push_failure(REPORT_PREFIX + json.dumps(report), 'error: failed to push some refs')
    assert result['error'] == 'Pre-push secret hook blocked the push: private-key [gitleaks]'
    assert result['push_report']['findings'] == report['findings']
    assert REPORT_PREFIX not in result['output']


def test_other_push_errors_keep_both_output_streams():
    result = push_failure('remote: protected branch', 'error: failed to push some refs')
    assert 'protected branch' in result['error']
    assert 'failed to push' in result['error']
    assert 'push_report' not in result


def test_gitleaks_json_is_reduced_to_redacted_locations(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    def run(args, **kwargs):
        report = Path(args[args.index('--report-path') + 1])
        report.write_text(json.dumps([{'RuleID': 'private-key', 'File': 'fixture.py', 'Commit': 'abc', 'StartLine': 4, 'Secret': 'must not appear', 'Match': 'must not appear'}]))
        return subprocess.CompletedProcess(args, 1, '', '')
    monkeypatch.setattr(subprocess, 'run', run)
    hits, warnings = scan_gitleaks('gitleaks', 'base', 'head')
    assert not warnings
    assert hits[0]['line'] == 4
    assert 'must not appear' not in json.dumps(hits)
    assert list((tmp_path / 'temp').iterdir()) == []


@pytest.mark.skipif(not shutil.which('node'), reason='node unavailable')
def test_popup_escapes_locations_and_displays_commit_and_hooks():
    script = Path(__file__).resolve().parents[1] / 'web/js/git_push_report.js'
    code = '''const A = require(process.argv[1]);
const html = A.render({summary:'blocked', findings:[{hook:'sqlite-secrets',rule:'AWS access key',file:'<script>x</script>.db',commit:'abc123',table:'config',column:'value',row:2}]});
if (html.includes('<script>x') || !html.includes('&lt;script&gt;') || !html.includes('abc123') || !html.includes('sqlite-secrets') || !html.includes('column:')) process.exit(1);
'''
    subprocess.run(['node', '-e', code, str(script)], check=True)


def test_reviewed_exceptions_match_only_exact_historical_finding():
    from core.git_push_diagnostics import apply_scanner_exceptions
    repo = Path(__file__).resolve().parents[2]
    policy = repo / '.cuttle/scripts/git-hooks/scanner-exceptions.json'
    entry = json.loads(policy.read_text())['exceptions'][0]
    hit = {'hook': 'gitleaks', 'rule': 'private-key', 'commit': entry['commit'], 'file': entry['file'], 'line': entry['line'], 'end_line': 423}
    retained, approved = apply_scanner_exceptions([hit], policy)
    assert retained == [] and approved == [hit]
    for altered in [dict(hit, commit='0' * 40), dict(hit, file='other.py'), dict(hit, line=373), dict(hit, end_line=424), dict(hit, rule='another-secret'), dict(hit, hook='sqlite-secrets')]:
        retained, approved = apply_scanner_exceptions([altered], policy)
        assert retained == [altered] and not approved


def test_exception_rejects_wrong_content_digest(tmp_path):
    from core.git_push_diagnostics import apply_scanner_exceptions
    repo = Path(__file__).resolve().parents[2]
    data = json.loads((repo / '.cuttle/scripts/git-hooks/scanner-exceptions.json').read_text())
    entry = data['exceptions'][0]
    entry['line_sha256'] = '0' * 64
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps(data))
    hit = {'hook': 'secret-patterns', 'rule': 'private key', 'commit': entry['commit'], 'file': entry['file'], 'line': entry['line']}
    assert apply_scanner_exceptions([hit], policy) == ([hit], [])
