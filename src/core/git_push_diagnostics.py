"""Redacted pre-push findings and transport-independent failure reports."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

REPORT_PREFIX = 'CUTTLE_PUSH_REPORT='


def finding(hook, rule, commit='', file='', line=None, **details):
    return dict(hook=hook, rule=rule, commit=commit, file=file, line=line, **details)


def scan_commits(commits, patterns, blocked_path):
    """Scan each introduced version, including secrets removed by a later commit."""
    hits = []
    compiled = [(name, re.compile(pattern)) for name, pattern in patterns]
    for commit in commits:
        proc = subprocess.run(['git', 'diff-tree', '--root', '--no-commit-id', '--first-parent', '-m', '-r', '--diff-filter=AM', '--name-only', '-z', commit], capture_output=True, check=True)
        paths = proc.stdout.decode('utf-8', 'replace').split('\0')
        for path in filter(None, paths):
            reason = blocked_path(path)
            if reason:
                hits.append(finding('path-policy', reason, commit, path))
            if Path(path).suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.db3') or path.endswith(('-wal', '-shm')):
                hits.extend(scan_database(commit, path, compiled))
        diff = subprocess.run(['git', 'show', '--format=', '--root', '--diff-merges=first-parent', '-U0', commit, '--'], capture_output=True, text=True, check=True).stdout
        path, line = '', 0
        for text in diff.splitlines():
            if text.startswith('+++ b/'):
                path = text[6:]
            elif text.startswith('@@ '):
                match = re.search(r'\+(\d+)', text)
                line = int(match[1]) if match else 0
            elif text.startswith('+') and not text.startswith('+++'):
                for rule, regex in compiled:
                    if regex.search(text[1:]):
                        hits.append(finding('secret-patterns', rule, commit, path, line, preview='[Flagged line redacted]'))
                        break
                line += 1
            elif text.startswith(' '):
                line += 1
    return hits


def scan_database(commit, path, patterns):
    """Read SQLite commit blobs in memory; never open the live runtime database.

    Unsupported/oversized databases block with an inspection error. Values are
    never included in the report. SQLite extension loading stays disabled.
    """
    def hit(rule, **detail):
        return finding('sqlite-secrets', rule, commit, path, **detail)
    conn = None
    try:
        size = int(subprocess.check_output(['git', 'cat-file', '-s', f'{commit}:{path}']))
        if size > 16 * 1024 * 1024:
            return [hit('Database exceeds the 16 MiB inspection limit')]
        blob = subprocess.check_output(['git', 'show', f'{commit}:{path}'])
        if not blob.startswith(b'SQLite format 3\x00'):
            return [hit('Not a standalone SQLite database; automatic inspection unavailable')]
        conn = sqlite3.connect(':memory:')
        conn.deserialize(blob)
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 3
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        hits, count = [], 0
        for (table,) in tables:
            quote = '"' + table.replace('"', '""') + '"'
            cursor = conn.execute('SELECT * FROM ' + quote)
            columns = [col[0] for col in cursor.description]
            for row_number, row in enumerate(cursor, 1):
                count += 1
                if count > 100000 or time.monotonic() > deadline:
                    return hits + [hit('Database inspection exceeded its row/time limit')]
                for column, value in zip(columns, row):
                    if not isinstance(value, (str, bytes)):
                        continue
                    text = value.decode('utf-8', 'replace') if isinstance(value, bytes) else value
                    if text.strip() and re.search(r'(?:password|private[_-]?key|api[_-]?key|auth[_-]?token|access[_-]?token|secret)', column, re.I):
                        hits.append(hit('Nonempty credential column', table=table, column=column, row=row_number, preview='[Database value redacted]'))
                    for rule, regex in patterns:
                        if regex.search(text):
                            hits.append(hit(rule, table=table, column=column, row=row_number, preview='[Database value redacted]'))
                            break
                    if len(hits) >= 1000:
                        return hits + [hit('Database finding limit reached')]
        return hits
    except (sqlite3.Error, AttributeError, ValueError, subprocess.SubprocessError):
        return [hit('Database could not be inspected safely')]
    finally:
        if conn is not None:
            conn.close()


def scan_gitleaks(binary, base, head):
    scratch = Path.cwd() / 'temp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='prepush-', dir=scratch) as directory:
        report = Path(directory) / 'report.json'
        try:
            proc = subprocess.run([binary, 'detect', '--no-banner', '--redact', '--report-format', 'json', '--report-path', str(report), '--log-opts', f'{base}..{head}' if base else head], capture_output=True, text=True, timeout=300)
            if proc.returncode not in (0, 1):
                return [], ['gitleaks could not complete; built-in checks still ran']
            records = json.loads(report.read_text()) if report.exists() else []
            if proc.returncode == 1 and not records:
                return [finding('gitleaks', 'Scanner reported findings without a detailed report')], []
            return [finding('gitleaks', r.get('RuleID', 'secret'), r.get('Commit', ''), r.get('File', ''), r.get('StartLine'), end_line=r.get('EndLine'), preview='[Secret redacted]') for r in records], []
        except (OSError, ValueError, subprocess.SubprocessError):
            return [], ['gitleaks could not complete; built-in checks still ran']



def apply_scanner_exceptions(findings, policy_path):
    """Approve only exact historical source lines; never path/SQLite findings.

    Changing a commit, blob, line, rule, or scanner invalidates the exception.
    The committed source is verified rather than trusting scanner previews.
    """
    import hashlib
    policy = json.loads(Path(policy_path).read_text())
    if not isinstance(policy, dict) or policy.get('version') != 1 or not isinstance(policy.get('exceptions'), list):
        raise ValueError('Invalid scanner exception policy')
    exceptions = policy['exceptions']
    for entry in exceptions:
        if (not isinstance(entry, dict) or
            not re.fullmatch(r'[0-9a-f]{40}', str(entry.get('commit', ''))) or
            not re.fullmatch(r'[0-9a-f]{40}', str(entry.get('blob', ''))) or
            not re.fullmatch(r'[0-9a-f]{64}', str(entry.get('line_sha256', ''))) or
            type(entry.get('line')) is not int or entry['line'] < 1 or
            not entry.get('reason') or not entry.get('file') or
            not isinstance(entry.get('checks'), dict) or not entry['checks'] or
            any(check not in ('secret-patterns', 'gitleaks') or not isinstance(rule, str) or not rule for check, rule in entry['checks'].items()) or
            not isinstance(entry.get('end_lines', {}), dict) or
            any(type(end) is not int or end < entry['line'] for end in entry.get('end_lines', {}).values())):
            raise ValueError('Scanner exceptions must identify exact source and checks')
    retained, approved, cache = [], [], {}
    for hit in findings:
        allowed = False
        for entry in exceptions:
            if (hit.get('commit') != entry['commit'] or hit.get('file') != entry['file'] or
                hit.get('line') != entry['line'] or
                hit.get('end_line') not in (None, (entry.get('end_lines') or {}).get(hit.get('hook'), entry['line'])) or
                entry['checks'].get(hit.get('hook')) != hit.get('rule')):
                continue
            key = entry['commit'] + ':' + entry['file']
            try:
                if key not in cache:
                    blob = subprocess.check_output(['git', 'rev-parse', key], text=True).strip()
                    lines = subprocess.check_output(['git', 'show', key]).splitlines()
                    cache[key] = blob, lines
                blob, lines = cache[key]
                allowed = blob == entry['blob'] and hashlib.sha256(lines[entry['line'] - 1]).hexdigest() == entry['line_sha256']
            except (subprocess.SubprocessError, IndexError):
                allowed = False
            if allowed:
                break
        (approved if allowed else retained).append(hit)
    return retained, approved

def push_failure(stdout, stderr):
    """Prefer the hook's structured reason over Git's generic stderr footer."""
    reports, lines = [], []
    for line in ((stdout or '') + '\n' + (stderr or '')).splitlines():
        if line.startswith(REPORT_PREFIX):
            try:
                report = json.loads(line[len(REPORT_PREFIX):])
                if report.get('version') == 1 and isinstance(report.get('findings'), list):
                    reports.append(report)
            except (ValueError, AttributeError):
                pass
        else:
            lines.append(line)
    if reports:
        hits = [f for report in reports for f in report['findings']]
        hooks = sorted({str(f.get('hook', 'pre-push')) for f in hits})
        rules = sorted({str(f.get('rule', 'flagged content')) for f in hits})
        summary = 'Pre-push secret hook blocked the push: ' + ', '.join(rules[:2])
        if len(rules) > 2:
            summary += ' (and more)'
        summary += ' [' + ', '.join(hooks) + ']'
        return {'error': summary, 'push_report': {'version': 1, 'summary': summary, 'findings': hits, 'hooks': hooks, 'warnings': [w for r in reports for w in r.get('warnings', [])]}, 'output': '\n'.join(lines).strip()}
    text = '\n'.join(lines).strip() or 'Git push failed'
    if 'pre-push hook: push blocked' in text:
        text = 'Pre-push secret hook blocked the push\n' + text
    return {'error': text, 'output': text}
