"""Per-store retention and maintenance dispatch through owning services."""
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from api.storage.service import (
    ALLOWED, NEVER, TYPED_CONFIRM,
    list_stores, operate, policy, set_policy,
)
from managers.settings_manager import SettingsManager


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / 'home'
    monkeypatch.setenv('CUTTLE_HOME', str(root))
    monkeypatch.setenv('CUTTLE_QUERY_LOG_DIR', str(root / 'logs' / 'queries'))
    monkeypatch.setenv('CUTTLE_ROUTER_DB', str(root / 'db' / 'router_outcomes.db'))
    return root


@pytest.fixture()
def manager(tmp_path):
    return SettingsManager(str(tmp_path / 'settings.json'))


def test_unknown_store_is_rejected(manager):
    with pytest.raises(ValueError, match='Unknown store'):
        set_policy('nope', {}, manager)
    with pytest.raises(ValueError, match='Unknown store'):
        operate('nope', 'prune')


def test_unknown_operation_is_rejected():
    with pytest.raises(ValueError, match='Unknown maintenance operation'):
        operate('query_logs', 'defrag')


def test_non_configurable_stores_reject_policy(manager):
    for ident in ('chats', 'device_workers', 'edit_journal', 'brain_metrics'):
        with pytest.raises(ValueError, match='editable retention'):
            set_policy(ident, {'retention_days': 30}, manager)


def test_simple_stores_accept_only_retention_days(manager):
    selected = set_policy('query_logs', {'retention_days': 30}, manager)
    assert selected == {'retention_days': 30}
    assert policy('query_logs', manager) == {'retention_days': 30}
    with pytest.raises(ValueError):
        set_policy('query_logs', {'retention_days': 0}, manager)
    with pytest.raises(ValueError):
        set_policy('query_logs', {'quota_mb': 10}, manager)
    with pytest.raises(ValueError):
        set_policy('query_logs', {'thinking_mode': 'summary_only'}, manager)


def test_outputs_defaults_to_owner_ttl(manager):
    assert policy('outputs', manager) == {'retention_days': 7}


def test_never_reset_stores_refuse(home):
    for ident in ('chats', 'device_workers'):
        with pytest.raises(ValueError, match='cannot be reset'):
            operate(ident, 'reset', confirm=ident)
        with pytest.raises(ValueError, match='not support pruning'):
            operate(ident, 'prune')


def test_typed_confirm_is_required(home):
    with pytest.raises(ValueError, match='Type query_logs'):
        operate('query_logs', 'reset')
    with pytest.raises(ValueError, match='Type query_logs'):
        operate('query_logs', 'reset', confirm='query_logs ')


def test_query_log_prune_keeps_new_files(home):
    from api.query_events import logs_dir
    root = logs_dir()
    root.mkdir(parents=True, exist_ok=True)
    old = root / 'query_data_old.json'
    new = root / 'query_data_new.json'
    old.write_text('{}')
    new.write_text('{}')
    aged = time.time() - 100 * 86400
    os.utime(old, (aged, aged))
    assert operate('query_logs', 'prune') == {'deleted': 1}
    assert not old.exists()
    assert new.exists()


def test_query_log_reset_needs_confirm_and_clears(home):
    from api.query_events import logs_dir
    root = logs_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / 'query_data_x.json').write_text('{}')
    assert operate('query_logs', 'reset', confirm='query_logs') == {'deleted': 1}
    assert list(root.iterdir()) == []


def test_router_outcomes_prune_uses_recorded_at(home):
    from api.agent_router.outcomes import database_path, record_turn
    now = time.time()
    assert record_turn(decision_id='old', target_agent='a', target_model='m',
                       source='test', failure_kind='none', query_id='old',
                       recorded_at=now - 100 * 86400)
    assert record_turn(decision_id='new', target_agent='a', target_model='m',
                       source='test', failure_kind='none', query_id='new',
                       recorded_at=now)
    path = database_path()
    assert operate('router_outcomes', 'prune') == {'deleted': 1}
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute('SELECT query_id FROM router_outcomes').fetchall()
        assert [row[0] for row in rows] == ['new']
    finally:
        conn.close()


def test_outputs_prune_uses_retention_as_ttl(home, manager):
    from api.shared_media import shared_media_root
    root = shared_media_root()
    root.mkdir(parents=True, exist_ok=True)
    stale = root / 'stale.png'
    stale.write_text('x')
    aged = time.time() - 10 * 86400
    os.utime(stale, (aged, aged))
    set_policy('outputs', {'retention_days': 7}, manager)
    result = operate('outputs', 'prune', manager=manager)
    assert result['deleted'] >= 1
    assert not stale.exists()


def test_brain_prune_runs_without_seed_data(home):
    result = operate('brain_metrics', 'prune')
    assert result['snapshots_removed'] == 0


def test_list_stores_exposes_policies_and_reset_paths(home, manager):
    set_policy('router_outcomes', {'retention_days': 45}, manager)
    rows = {row['id']: row for row in list_stores(manager)}
    assert rows['query_logs']['policy'] == {'retention_days': 90}
    assert rows['router_outcomes']['policy'] == {'retention_days': 45}
    assert rows['chats']['policy'] is None
    assert rows['chats']['reset_policy'] == NEVER
    assert rows['query_logs']['reset_policy'] == TYPED_CONFIRM
    assert rows['outputs']['reset_policy'] == ALLOWED
    assert rows['device_workers']['supports_prune'] is False
    assert rows['outputs']['supports_prune'] is True
