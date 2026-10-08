"""Tasks gizmos: tool writes, scope/privacy, durable attribution and live revision."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from api.auth_db import AuthDatabase
from api.gizmos import __main__ as cli, catalog, service, store, tasks
from core.agent_cli_env import agent_cli_env, agent_operation_context


@pytest.fixture
def task_db(tmp_path, monkeypatch):
    import api.auth_db as auth
    db = AuthDatabase(tmp_path / 'auth.db')
    monkeypatch.setattr(auth, '_db_instance', db)
    monkeypatch.setenv('CUTTLE_GIZMOS_DB', str(tmp_path / 'gizmos.db'))
    uid = db.create_user('task-owner@local', 'Owner', 'local', password='x')
    other = db.create_user('task-other@local', 'Other', 'local', password='x')
    sid = db.create_chat_session(uid, 'Tasks')
    sid2 = db.create_chat_session(uid, 'Same project')
    other_sid = db.create_chat_session(other, 'Other')
    db.set_session_project(sid, uid, project_path='/projects/a')
    db.set_session_project(sid2, uid, project_path='/projects/a')
    return db, uid, sid, sid2, other, other_sid


def _create(sid, **kw):
    return tasks.create(gizmo_id='plan', session_id=sid,
                        items=[{'id': 'a', 'text': 'API'}, {'id': 'b', 'text': 'UI'}], **kw)


def _run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_cli_tasks_work_with_usage_flag_off_and_inherit_attribution(task_db, capsys, monkeypatch):
    db, uid, sid, *_ = task_db
    monkeypatch.setenv('CUTTLE_EXPERIMENTAL', '0')
    # Simulate precisely the environment supplied to the guest CLI child.
    with agent_operation_context(session_id=sid, agent_id='codex', model='fixture-model',
                                 run_id='fixture-query', project_path='/projects/a'):
        for key, value in agent_cli_env({}).items():
            monkeypatch.setenv(key, value)
    code, body = _run(capsys, 'tasks', 'create', '--id', 'plan', '--items', '[{"id":"a","text":"API"}]')
    assert code == 0 and body['gizmo']['placement']['dock'] == 'composer'
    code, body = _run(capsys, 'tasks', 'patch', 'plan', '--set-done', 'a')
    assert code == 0 and body['gizmo']['status'] == 'archived'
    code, body = _run(capsys, 'tasks', 'list')
    assert code == 0 and body['gizmos'] == []
    code, body = _run(capsys, 'tasks', 'history', 'plan')
    changes = [e for e in body['events'] if e['after_state']]
    assert [e['operation'] for e in changes] == ['patch', 'create']
    assert changes[0]['before_state']['payload']['items'][0]['done'] is False
    actor = changes[0]['actor']
    assert actor['session_id'] == str(sid)
    assert actor['agent_id'] == 'codex' and actor['model'] == 'fixture-model'
    assert actor['run_id'] == 'fixture-query' and actor['user_id'] == uid
    # Opening a new database instance proves logs aren't only process-local.
    assert AuthDatabase(db.db_path).task_gizmo_history('plan', user_id=uid)


def test_existing_rows_are_visible_without_copy_or_migration(task_db):
    db, uid, sid, *_ = task_db
    db.upsert_chat_widget(widget_id='old-plan', user_id=uid, wtype='tasks', title='Existing',
        scope='session', session_id=sid, project_path='', payload={'items': ['Existing work']})
    assert tasks.list_tasks(user_id=uid, session_id=sid)[0]['id'] == 'old-plan'
    tasks.patch('old-plan', {'set_done': ['t1']}, session_id=sid)
    assert db.get_chat_widget('old-plan')['status'] == 'archived'
    assert store.count() == 0  # no parallel copy in the install-wide gizmo store


def test_project_scope_and_nested_archive_reopen(task_db):
    db, uid, sid, sid2, other, other_sid = task_db
    tasks.create(gizmo_id='nested', session_id=sid, scope='project', items=[
        {'id': 'a', 'text': 'Build', 'children': [{'id': 'b', 'text': 'Test'}]}])
    assert tasks.list_tasks(user_id=uid, session_id=sid2)[0]['id'] == 'nested'
    assert tasks.list_tasks(user_id=other, session_id=other_sid) == []
    tasks.patch('nested', {'set_done': ['a']}, session_id=sid2)
    assert tasks.get('nested')['status'] == 'active'
    tasks.patch('nested', {'set_done': ['b']}, session_id=sid2)
    assert tasks.list_tasks(user_id=uid, session_id=sid) == []
    row = tasks.patch('nested', {'add': [{'item': {'id': 'c', 'text': 'Followup'}}]}, session_id=sid)
    assert row['status'] == 'active'


def test_write_revision_observes_lower_revision_list_and_archive(task_db):
    db, uid, sid, *_ = task_db
    _create(sid)
    tasks.create(gizmo_id='other-plan', session_id=sid, items=['One'])
    for n in range(5):
        tasks.patch('other-plan', {'description': str(n)})
    before = db.widgets_revision_for(user_id=uid)
    tasks.patch('plan', {'set_done': ['a']})
    changed = db.widgets_revision_for(user_id=uid)
    assert changed > before
    tasks.patch('plan', {'set_done': ['b']})
    assert db.widgets_revision_for(user_id=uid) > changed
    tasks.record_interaction('other-plan', 'get')
    # Reads are logged but never trigger a polling feedback loop.
    current = db.widgets_revision_for(user_id=uid)
    tasks.record_interaction('other-plan', 'get')
    assert db.widgets_revision_for(user_id=uid) == current


def test_concurrent_task_patches_keep_both_edits(task_db):
    db, uid, sid, *_ = task_db
    _create(sid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda iid: tasks.patch('plan', {'set_done': [iid]}, db=db), ['a', 'b']))
    assert results
    row = tasks.get('plan')
    assert all(item['done'] for item in row['payload']['items'])
    assert len([e for e in tasks.history('plan') if e['operation'] == 'patch']) == 2


def test_invalid_tasks_and_foreign_owner_cannot_overwrite(task_db):
    db, uid, sid, sid2, other, other_sid = task_db
    with pytest.raises(service.GizmoError):
        tasks.create(session_id=sid, items=[])
    with pytest.raises(service.GizmoError):
        tasks.create(session_id=sid, items=[{'id': 'a'}, {'id': 'a'}])
    _create(sid)
    with pytest.raises(service.GizmoError) as exc:
        tasks.patch('plan', {'set_done': ['a']}, session_id=other_sid)
    assert exc.value.code == 'not_found'
    with pytest.raises(ValueError):
        db.upsert_chat_widget(widget_id='plan', user_id=other, wtype='tasks', title='Hijack',
                              scope='session', session_id=other_sid, project_path='', payload={})
    assert tasks.get('plan')['user_id'] == uid


def test_scoped_task_type_is_registered_but_cannot_use_unscoped_store(task_db):
    assert catalog.get('tasks').options()['docks'] == ['composer']
    with pytest.raises(service.GizmoError, match='chat-scoped'):
        service.create('tasks')


def test_task_http_scope_shared_editing_and_attribution(task_db):
    from flask import Flask
    from api.gizmos.routes import gizmos_bp
    db, uid, sid, sid2, other, other_sid = task_db
    app = Flask(__name__)
    app.register_blueprint(gizmos_bp)
    client = app.test_client()
    assert client.get(f'/api/gizmos/tasks?session_id={sid}').status_code == 401
    token = db.create_auth_session(uid)
    client.set_cookie('session_token', token)
    created = client.post('/api/gizmos/tasks', json={
        'id': 'plan', 'session_id': sid, 'items': [{'id': 'a', 'text': 'Work'}],
        'actor': {'agent_id': 'codex', 'model': 'fixture-model', 'run_id': 'query'}})
    assert created.status_code == 201
    row = created.get_json()['gizmo']
    assert row['placement']['dock'] == 'composer'
    # The session row beats a stale project chip passed by the browser.
    listed = client.get(f'/api/gizmos/tasks?session_id={sid}&project_path=/stale').get_json()
    assert listed['gizmos'][0]['id'] == 'plan'
    assert client.patch('/api/gizmos/tasks/plan/items/a', json={'done': True, 'session_id': sid}).status_code == 403
    assert client.patch('/api/gizmos/tasks/plan', json={'set_done': ['a'], 'session_id': sid}).status_code == 403
    client.patch('/api/gizmos/tasks/plan', json={'edit_mode': 'shared', 'session_id': sid})
    done = client.patch('/api/gizmos/tasks/plan/items/a', json={'done': True, 'session_id': sid})
    assert done.get_json()['gizmo']['status'] == 'archived'
    history = client.get('/api/gizmos/tasks/plan/history').get_json()['events']
    write = next(e for e in history if e['after_state'])
    assert write['actor']['source'] == 'ui' and write['actor']['session_id'] == str(sid)
    assert history[-1]['actor']['source'] == 'api_agent' and history[-1]['actor']['agent_id'] == 'codex'
    other_token = db.create_auth_session(other)
    client.set_cookie('session_token', other_token)
    assert client.get('/api/gizmos/tasks/plan/history').status_code == 404
    assert client.get(f'/api/gizmos/tasks?session_id={sid}').status_code == 404
    assert client.patch('/api/gizmos/tasks/plan', json={'description': 'No'}).status_code == 404


def test_generic_gizmo_audit_survives_removal(task_db):
    actor = {'source': 'cli', 'session_id': '12', 'agent_id': 'cursor', 'run_id': 'query'}
    g = service.create('usage_meter', actor=actor)
    service.move(g['id'], 'rail', actor=actor)
    service.update(g['id'], title='New title', actor=actor)
    service.remove(g['id'], actor=actor)
    events = store.history(g['id'])
    assert [e['operation'] for e in events] == ['remove', 'update', 'move', 'create']
    assert all(e['actor'] == actor for e in events)
    assert events[0]['before_state']['title'] == 'New title'
    assert events[0]['after_state'] is None


def test_audit_failure_rolls_back_task_change(task_db):
    import sqlite3
    db, uid, sid, *_ = task_db
    _create(sid)
    before = tasks.get('plan')
    conn = db._get_connection()
    with conn:
        conn.execute("CREATE TRIGGER fail_task_audit BEFORE INSERT ON task_gizmo_events "
                     "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
    conn.close()
    with pytest.raises(sqlite3.IntegrityError, match='audit unavailable'):
        tasks.patch('plan', {'set_done': ['a']})
    assert tasks.get('plan') == before
    assert len(tasks.history('plan')) == 1


def test_metadata_patch_preserves_manual_archive_and_project_binding(task_db):
    db, uid, sid, *_ = task_db
    row = _create(sid, scope='project')
    tasks.patch('plan', {'status': 'archived'}, session_id=sid)
    another = db.create_chat_session(uid, 'Another project')
    db.set_session_project(another, uid, project_path='/projects/b')
    updated = tasks.patch('plan', {'description': 'Keep this intent'}, session_id=another)
    assert updated['project_path'] == '/projects/a'
    assert updated['status'] == 'archived'
    reopened = tasks.patch('plan', {'add': [{'item': {'id': 'c', 'text': 'More work'}}]}, session_id=sid)
    assert reopened['status'] == 'active'
    with pytest.raises(service.GizmoError, match='array'):
        tasks.patch('plan', {'set_done': 'c'})
