"""Tasks item owners and in-progress flags through the service and item PATCH route."""
import pytest
from flask import Flask

from api.auth_db import AuthDatabase
from api.gizmos import service, tasks


@pytest.fixture
def task_db(tmp_path, monkeypatch):
    import api.auth_db as auth
    db = AuthDatabase(tmp_path / 'auth.db')
    monkeypatch.setattr(auth, '_db_instance', db)
    monkeypatch.setenv('CUTTLE_GIZMOS_DB', str(tmp_path / 'gizmos.db'))
    uid = db.create_user('owner-owner@local', 'Owner', 'local', password='x')
    other = db.create_user('owner-other@local', 'Other', 'local', password='x')
    sid = db.create_chat_session(uid, 'Planner')
    sid2 = db.create_chat_session(uid, 'Worker chat')
    other_sid = db.create_chat_session(other, 'Foreign')
    db.set_session_project(sid, uid, project_path='/projects/a')
    db.set_session_project(sid2, uid, project_path='/projects/a')
    return db, uid, sid, sid2, other, other_sid


def _create(sid, **kw):
    return tasks.create(gizmo_id='plan', session_id=sid,
                        items=[{'id': 'a', 'text': 'API'}, {'id': 'b', 'text': 'UI'}], **kw)


def test_chat_owner_is_canonicalized_and_labelled_from_the_chat_title(task_db):
    db, uid, sid, sid2, other, other_sid = task_db
    row = _create(sid, user_id=uid)
    assert 'owner' not in row['payload']['items'][0]
    out = tasks.patch('plan', {'set_owner': {'a': {'kind': 'chat', 'id': f'ch-{sid2:06d}'}},
                               'set_in_progress': ['a']}, user_id=uid, session_id=sid)
    owner = out['payload']['items'][0]['owner']
    assert owner == {'kind': 'chat', 'id': f'CH-{sid2:06d}', 'label': 'Worker chat'}
    assert out['payload']['items'][0]['in_progress'] is True


def test_chat_owner_must_be_one_of_the_same_users_chats(task_db):
    db, uid, sid, sid2, other, other_sid = task_db
    with pytest.raises(service.GizmoError) as exc:
        tasks.create(gizmo_id='plan', session_id=sid, user_id=uid,
                     items=[{'id': 'a', 'text': 'API',
                             'owner': {'kind': 'chat', 'id': f'CH-{other_sid:06d}'}}])
    assert exc.value.code == 'not_found'
    _create(sid, user_id=uid)
    with pytest.raises(service.GizmoError) as exc:
        tasks.patch('plan', {'set_owner': {'a': {'kind': 'chat', 'id': f'CH-{other_sid:06d}'}}},
                    user_id=uid, session_id=sid)
    assert exc.value.code == 'not_found'
    with pytest.raises(service.GizmoError):
        tasks.patch('plan', {'set_owner': {'a': {'kind': 'chat'}}}, user_id=uid, session_id=sid)


def test_non_chat_owners_and_user_owner_need_no_chat_lookup(task_db):
    db, uid, sid, *_ = task_db
    _create(sid, user_id=uid)
    out = tasks.patch('plan', {'set_owner': {'a': {'kind': 'user', 'label': 'You'},
                                             'b': {'kind': 'agent', 'agent': 'claude', 'label': 'Claude'}}},
                      user_id=uid, session_id=sid)
    items = out['payload']['items']
    assert items[0]['owner'] == {'kind': 'user', 'label': 'You'}
    assert items[1]['owner'] == {'kind': 'agent', 'agent': 'claude', 'label': 'Claude'}


def test_model_errors_surface_as_gizmo_errors(task_db):
    db, uid, sid, *_ = task_db
    _create(sid, user_id=uid)
    tasks.patch('plan', {'set_done': ['a']}, user_id=uid, session_id=sid)
    with pytest.raises(service.GizmoError, match='done and cannot be in progress'):
        tasks.patch('plan', {'set_in_progress': ['a']}, user_id=uid, session_id=sid)
    with pytest.raises(service.GizmoError, match='owner.kind'):
        tasks.patch('plan', {'set_owner': {'b': {'kind': 'robot', 'label': 'x'}}}, user_id=uid, session_id=sid)
    with pytest.raises(service.GizmoError, match='set_owner must be an object'):
        tasks.patch('plan', {'set_owner': ['b']}, user_id=uid, session_id=sid)
    with pytest.raises(service.GizmoError, match='clear_in_progress must be an array'):
        tasks.patch('plan', {'clear_in_progress': 'b'}, user_id=uid, session_id=sid)


def test_owner_only_edit_does_not_reactivate_an_archived_list(task_db):
    db, uid, sid, *_ = task_db
    _create(sid, user_id=uid)
    tasks.patch('plan', {'status': 'archived'}, user_id=uid, session_id=sid)
    out = tasks.patch('plan', {'set_owner': {'b': {'kind': 'user', 'label': 'You'}}},
                      user_id=uid, session_id=sid)
    assert out['status'] == 'archived'


@pytest.fixture
def client(task_db):
    from api.gizmos.routes import gizmos_bp
    db, uid, sid, sid2, other, other_sid = task_db
    app = Flask(__name__)
    app.register_blueprint(gizmos_bp)
    test_client = app.test_client()
    test_client.set_cookie('session_token', db.create_auth_session(uid))
    created = test_client.post('/api/gizmos/tasks', json={
        'id': 'plan', 'session_id': sid, 'items': [{'id': 'a', 'text': 'API'}, {'id': 'b', 'text': 'UI'}]})
    assert created.status_code == 201
    assert test_client.patch('/api/gizmos/tasks/plan', json={'edit_mode': 'shared', 'session_id': sid}).status_code == 200
    return test_client


def test_item_patch_route_sets_owner_and_in_progress(client, task_db):
    _, uid, sid, sid2, *_ = task_db
    res = client.patch('/api/gizmos/tasks/plan/items/a', json={
        'owner': {'kind': 'chat', 'id': f'CH-{sid2:06d}', 'label': ''}, 'in_progress': True, 'session_id': sid})
    assert res.status_code == 200, res.get_json()
    item = res.get_json()['gizmo']['payload']['items'][0]
    assert item['owner']['id'] == f'CH-{sid2:06d}' and item['owner']['label'] == 'Worker chat'
    assert item['in_progress'] is True
    # Owner-only body does not need done; clearing removes the flag and keeps the owner.
    res = client.patch('/api/gizmos/tasks/plan/items/a', json={'in_progress': False, 'session_id': sid})
    item = res.get_json()['gizmo']['payload']['items'][0]
    assert 'in_progress' not in item and item['owner']['kind'] == 'chat'


def test_item_patch_route_keeps_the_done_contract(client, task_db):
    _, uid, sid, *_ = task_db
    res = client.patch('/api/gizmos/tasks/plan/items/a', json={'session_id': sid})
    assert res.status_code == 400 and 'done must be a boolean' in res.get_json()['error']
    res = client.patch('/api/gizmos/tasks/plan/items/a', json={'in_progress': 'yes', 'session_id': sid})
    assert res.status_code == 400 and 'in_progress must be a boolean' in res.get_json()['error']
    res = client.patch('/api/gizmos/tasks/plan/items/a', json={'done': True, 'in_progress': True,
                                                              'session_id': sid})
    assert res.status_code == 400 and 'done and cannot be in progress' in res.get_json()['error']
    res = client.patch('/api/gizmos/tasks/plan/items/a', json={'done': True, 'session_id': sid})
    assert res.status_code == 200 and res.get_json()['gizmo']['payload']['items'][0]['done'] is True


def test_item_patch_route_rejects_a_foreign_chat_owner(client, task_db):
    _, uid, sid, sid2, other, other_sid = task_db
    res = client.patch('/api/gizmos/tasks/plan/items/a', json={
        'owner': {'kind': 'chat', 'id': f'CH-{other_sid:06d}', 'label': 'x'}, 'session_id': sid})
    assert res.status_code == 404
