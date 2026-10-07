import json
import sqlite3

import pytest

from api.dashboards import cost_repair as cr


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, 'lookup_model_rates', lambda m: {'cache_read': .2, 'cache_write': 5})
    monkeypatch.setattr(cr, 'estimate_cost_usd', lambda *a, **kw: 4.539049)
    auth = tmp_path / 'auth.db'
    outcomes = tmp_path / 'outcomes.db'
    logs = tmp_path / 'logs'
    logs.mkdir()
    usage = {'prompt_tokens': 48, 'completion_tokens': 16644, 'cache_read_tokens': 20323108,
             'cache_write_tokens': 28271, 'cost': 83.985179, 'model': 'opus'}
    meta = {'query_id': 'test123', 'usage': usage, 'slash_command': {'chips': [
        {'category': 'claude', 'label': 'Claude Code - Opus 5.5 · medium'}]}, 'unrelated': 'keep'}
    with sqlite3.connect(auth) as db:
        db.execute('CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, chat_session_id INTEGER, role TEXT, metadata TEXT)')
        db.execute('INSERT INTO chat_messages VALUES (1, 1023, ?, ?)', ('assistant', json.dumps(meta)))
    with sqlite3.connect(outcomes) as db:
        db.execute('CREATE TABLE router_outcomes (id INTEGER PRIMARY KEY, query_id TEXT, session_id TEXT, target_agent TEXT, cost REAL)')
        db.execute('INSERT INTO router_outcomes VALUES (1, ?, ?, ?, ?)', ('test123', '1023', 'claude', usage['cost']))
    log = {'total_cost': usage['cost'] + 1, 'tool_calls': [dict(tool_name='Claude Code', model='opus', tokens=usage, cost=usage['cost'], start_time=1, end_time=2)],
           'llm_calls': [dict(model='opus', cost=usage['cost'], start_time=1, end_time=2)]}
    (logs / 'query_data_test123.json').write_text(json.dumps(log))
    return dict(auth_path=auth, outcomes_path=outcomes, logs_path=logs), meta


def test_repair_backup_consistency_and_idempotency(saved, tmp_path):
    paths, meta = saved
    dry = cr.repair(**paths)
    assert dry['messages'] == dry['outcomes'] == dry['log_files'] == 1
    assert json.loads((paths['logs_path'] / 'query_data_test123.json').read_text())['total_cost'] == pytest.approx(84.985179)
    backup = tmp_path / 'backup'
    result = cr.repair(**paths, apply=True, backup_dir=backup)
    assert result['messages'] == 1
    with sqlite3.connect(paths['auth_path']) as db:
        repaired = json.loads(db.execute('SELECT metadata FROM chat_messages').fetchone()[0])
    assert repaired['unrelated'] == 'keep'
    assert repaired['usage']['reported_cost'] == meta['usage']['cost']
    assert repaired['usage']['cost_estimated']
    assert repaired['usage']['pricing_model'] == 'claude-opus-5-5'
    with sqlite3.connect(paths['outcomes_path']) as db:
        assert db.execute('SELECT cost FROM router_outcomes').fetchone()[0] == repaired['usage']['cost']
    log = json.loads((paths['logs_path'] / 'query_data_test123.json').read_text())
    assert log['total_cost'] == pytest.approx(5.539049)
    assert log['tool_calls'][0]['cost'] == log['llm_calls'][0]['cost'] == repaired['usage']['cost']
    with sqlite3.connect(backup / 'auth.db') as db:
        assert json.loads(db.execute('SELECT metadata FROM chat_messages').fetchone()[0]) == meta
    assert cr.repair(**paths)['messages'] == 0


def test_ambiguous_alias_is_not_guessed(saved):
    paths, meta = saved
    meta['slash_command']['chips'][0]['label'] = 'Claude Code - Opus'
    assert cr.estimate_saved(meta)[1] == 'ambiguous_model'


def test_unmatched_logs_skip_entire_turn(saved):
    paths, _ = saved
    p = paths['logs_path'] / 'query_data_test123.json'
    log = json.loads(p.read_text())
    log['tool_calls'][0]['tokens']['cache_read_tokens'] = 1
    p.write_text(json.dumps(log))
    assert cr.repair(**paths)['messages'] == 0


def test_multiple_attempts_skip(saved):
    paths, _ = saved
    with sqlite3.connect(paths['outcomes_path']) as db:
        db.execute('INSERT INTO router_outcomes SELECT 2, query_id, session_id, target_agent, cost FROM router_outcomes')
    assert cr.repair(**paths)['messages'] == 0


def test_duplicate_chat_records_and_resume(saved, tmp_path):
    paths, _ = saved
    with sqlite3.connect(paths['auth_path']) as db:
        db.execute('INSERT INTO chat_messages SELECT 2, chat_session_id, role, metadata FROM chat_messages')
    # Historical duplicate bubbles can share a query, without an outcome row.
    with sqlite3.connect(paths['outcomes_path']) as db:
        db.execute('DELETE FROM router_outcomes')
    backup = tmp_path / 'backup'
    assert cr.repair(**paths, apply=True, backup_dir=backup)['messages'] == 2
    summary = cr.resume_logs(backup_dir=backup, logs_path=paths['logs_path'])
    assert summary['unique_log_files'] == 1
    assert summary['resumed_logs'] == 0
    target = paths['logs_path'] / 'query_data_test123.json'
    target.write_bytes((backup / target.name).read_bytes())
    assert cr.resume_logs(backup_dir=backup, logs_path=paths['logs_path'])['resumed_logs'] == 1
