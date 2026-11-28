"""Per-item ``hidden`` field: model, CLI, HTTP (including agent-managed lists)."""
from __future__ import annotations

import copy
import io
import json
from contextlib import redirect_stdout
import pytest
from flask import Flask

from api.auth_db import AuthDatabase
from api.gizmos import tasks, tasks_model as model
from api.gizmos.__main__ import main
from api.gizmos.routes import gizmos_bp


def _base():
    return {'items': [
        {'id': 'a', 'text': 'API', 'done': False, 'children': []},
        {'id': 'b', 'text': 'UI', 'done': False, 'children': [
            {'id': 'b.1', 'text': 'Button', 'done': False, 'children': []},
        ]},
    ]}


def test_hidden_round_trips_and_is_omitted_when_false():
    out = model.apply_tasks_patch(_base(), {'set_hidden': ['a', 'b.1']})
    assert out['items'][0]['hidden'] is True
    assert out['items'][1]['children'][0]['hidden'] is True
    assert 'hidden' not in out['items'][1]
    cleared = model.apply_tasks_patch(out, {'clear_hidden': ['a']})
    assert 'hidden' not in cleared['items'][0]
    assert cleared['items'][1]['children'][0]['hidden'] is True


def test_set_done_does_not_hide_or_clear_hidden():
    seeded = model.apply_tasks_patch(_base(), {'set_hidden': ['a']})
    done = model.apply_tasks_patch(seeded, {'set_done': ['a']})
    assert done['items'][0]['done'] is True
    assert done['items'][0]['hidden'] is True
    plain = model.apply_tasks_patch(_base(), {'set_done': ['a']})
    assert plain['items'][0]['done'] is True
    assert 'hidden' not in plain['items'][0]


def test_add_and_items_replace_keep_hidden():
    out = model.apply_tasks_patch(_base(), {'add': [
        {'item': {'id': 'c', 'text': 'Docs', 'hidden': True}},
    ]})
    assert out['items'][-1]['hidden'] is True
    out = model.apply_tasks_patch(_base(), {'items': [
        {'id': 'x', 'text': 'New', 'hidden': True},
    ]})
    assert out['items'] == [{'id': 'x', 'text': 'New', 'done': False, 'children': [], 'hidden': True}]


def test_hidden_must_be_boolean_on_write():
    with pytest.raises(ValueError, match='hidden must be a boolean'):
        model.apply_tasks_patch(_base(), {'add': [{'id': 'c', 'text': 'x', 'hidden': 'yes'}]})


def test_legacy_payload_unchanged_without_hidden():
    legacy = _base()
    before = copy.deepcopy(legacy)
    assert model.normalize_tasks_payload(legacy) == before


def test_hidden_items_remain_in_digest_and_counts():
    payload = model.apply_tasks_patch(_base(), {'set_hidden': ['a']})
    done, total = model.count_tasks(payload['items'])
    assert (done, total) == (0, 3)
    digest = model.format_tasks_digest([{
        'type': 'tasks', 'status': 'active', 'title': 'Plan', 'id': 'plan',
        'scope': 'session', 'payload': payload,
    }])
    assert '`a`' in digest and 'API' in digest


@pytest.fixture
def task_db(tmp_path, monkeypatch):
    import api.auth_db as auth
    db = AuthDatabase(tmp_path / 'auth.db')
    monkeypatch.setattr(auth, '_db_instance', db)
    monkeypatch.setenv('CUTTLE_GIZMOS_DB', str(tmp_path / 'gizmos.db'))
    uid = db.create_user('hidden-owner@local', 'Owner', 'local', password='x')
    sid = db.create_chat_session(uid, 'Planner')
    return db, uid, sid


def _run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(['tasks', *argv])
    return code, json.loads(buf.getvalue())


def test_cli_hide_unhide_round_trip(tmp_path: Path, monkeypatch):
    monkeypatch.delenv('CUTTLE_CHAT_SESSION_ID', raising=False)
    monkeypatch.setattr(
        tasks, 'database',
        lambda db=None: db or AuthDatabase(tmp_path / 'cli_auth.db'))
    db = AuthDatabase(tmp_path / 'cli_auth.db')
    owner = db.create_user('cli-owner@local', 'Owner', 'local', password='x')
    sid = db.create_chat_session(owner, 'planner')
    db.upsert_chat_widget(
        widget_id='plan', user_id=owner, wtype='tasks', title='Tasks', scope='session',
        session_id=sid, project_path='',
        payload={'items': [
            {'id': '1', 'text': 'Scaffold', 'done': False, 'children': []},
            {'id': '2', 'text': 'Tests', 'done': False, 'children': []},
        ]},
    )
    code, body = _run_cli(['patch', 'plan', '--hide', '1,2'])
    assert code == 0, body
    items = {i['id']: i for i in body['gizmo']['payload']['items']}
    assert items['1']['hidden'] is True and items['2']['hidden'] is True
    code, body = _run_cli(['patch', 'plan', '--unhide', '1', '--set-done', '1'])
    assert code == 0, body
    item = {i['id']: i for i in body['gizmo']['payload']['items']}['1']
    assert 'hidden' not in item and item['done'] is True
    code, body = _run_cli(['get', 'plan'])
    assert code == 0
    assert {i['id']: i.get('hidden') for i in body['gizmo']['payload']['items']} == {
        '1': None, '2': True}


@pytest.fixture
def client(task_db):
    db, uid, sid = task_db
    app = Flask(__name__)
    app.register_blueprint(gizmos_bp)
    test_client = app.test_client()
    test_client.set_cookie('session_token', db.create_auth_session(uid))
    created = test_client.post('/api/gizmos/tasks', json={
        'id': 'plan', 'session_id': sid,
        'items': [{'id': 'a', 'text': 'API'}, {'id': 'b', 'text': 'UI'}]})
    assert created.status_code == 201, created.get_json()
    return test_client, sid


def test_item_patch_hidden_works_on_agent_managed_list(client):
    test_client, sid = client
    # Default create is agent edit_mode; hide must still work.
    res = test_client.patch('/api/gizmos/tasks/plan/items/a',
                            json={'hidden': True, 'session_id': sid})
    assert res.status_code == 200, res.get_json()
    item = res.get_json()['gizmo']['payload']['items'][0]
    assert item['hidden'] is True and item['done'] is False
    # Done still forbidden on agent lists.
    res = test_client.patch('/api/gizmos/tasks/plan/items/a',
                            json={'done': True, 'session_id': sid})
    assert res.status_code == 403
    # Unhide + checking done does not auto-hide.
    assert test_client.patch('/api/gizmos/tasks/plan',
                             json={'edit_mode': 'shared', 'session_id': sid}).status_code == 200
    res = test_client.patch('/api/gizmos/tasks/plan/items/a',
                            json={'hidden': False, 'done': True, 'session_id': sid})
    assert res.status_code == 200
    item = res.get_json()['gizmo']['payload']['items'][0]
    assert item['done'] is True and 'hidden' not in item


def test_item_patch_hidden_validation(client):
    test_client, sid = client
    res = test_client.patch('/api/gizmos/tasks/plan/items/a',
                            json={'hidden': 'yes', 'session_id': sid})
    assert res.status_code == 400 and 'hidden must be a boolean' in res.get_json()['error']
    res = test_client.patch('/api/gizmos/tasks/plan/items/a', json={'session_id': sid})
    assert res.status_code == 400 and 'done must be a boolean' in res.get_json()['error']
