"""Tasks App: linked chats, manager listing, delete, stale-edit guard, related chats.

Owner: api.gizmos.tasks (writes) + api.gizmos.tasks_manager (read model);
client model is covered in test_tasks_app_js.py.
"""
import json

import pytest

from api.auth_db import AuthDatabase
from api.gizmos import __main__ as cli, tasks, tasks_manager
from api.gizmos.service import GizmoError


@pytest.fixture
def world(tmp_path, monkeypatch):
    import api.auth_db as auth
    db = AuthDatabase(tmp_path / 'auth.db')
    monkeypatch.setattr(auth, '_db_instance', db)
    monkeypatch.setenv('CUTTLE_GIZMOS_DB', str(tmp_path / 'gizmos.db'))
    uid = db.create_user('app-owner@local', 'Owner', 'local', password='x')
    other = db.create_user('app-other@local', 'Other', 'local', password='x')
    orch = db.create_chat_session(uid, 'Orchestrator')
    sup = db.create_chat_session(uid, 'Supervisor')
    child = db.create_chat_session(uid, 'Sub-agent A')
    foreign = db.create_chat_session(other, 'Not yours')
    db.set_session_project(orch, uid, project_path='/projects/game')
    for sid in (orch, sup, child):
        db.add_message(sid, 'user', 'hello')
    return db, uid, other, orch, sup, child, foreign


def _plan(orch, **kw):
    return tasks.create(gizmo_id='plan', session_id=orch, edit_mode='shared',
                        items=[{'id': 'a', 'text': 'Engine'}, {'id': 'b', 'text': 'Art'}], **kw)


def _ids(rows):
    return [r['id'] for r in rows]


def test_linked_chats_see_the_list_and_its_context(world):
    db, uid, other, orch, sup, child, foreign = world
    _plan(orch, linked_sessions=[f'CH-{sup:06d}', str(child), orch])  # home is dropped from links
    row = tasks.get('plan')
    assert row['linked_sessions'] == [sup, child]
    assert row['config']['linked_sessions'] == [sup, child]
    for sid in (orch, sup, child):
        assert _ids(tasks.list_tasks(user_id=uid, session_id=sid)) == ['plan']
    # The Context Compiler reads the same listing, so linked agents get the digest.
    from api.gizmos.tasks_model import format_tasks_digest
    assert '`plan`' in format_tasks_digest(tasks.list_tasks(user_id=uid, session_id=child))
    tasks.patch('plan', {'unlink': [f'CH-{child:06d}']})
    assert tasks.list_tasks(user_id=uid, session_id=child) == []
    tasks.patch('plan', {'link': [child]})
    assert tasks.get('plan')['linked_sessions'] == [sup, child]
    tasks.patch('plan', {'linked_sessions': []})
    assert tasks.get('plan')['linked_sessions'] == []


def test_links_are_owner_checked_and_survive_unrelated_patches(world):
    db, uid, other, orch, sup, child, foreign = world
    with pytest.raises(GizmoError):
        _plan(orch, linked_sessions=[foreign])
    _plan(orch, linked_sessions=[sup])
    with pytest.raises(GizmoError) as err:
        tasks.patch('plan', {'link': [foreign]})
    assert err.value.code == 'not_found'
    tasks.patch('plan', {'set_done': ['a'], 'title': 'Game plan'})
    assert tasks.get('plan')['linked_sessions'] == [sup]
    # Scope changes keep links; project lists still reach linked chats outside it.
    tasks.patch('plan', {'scope': 'project'}, session_id=orch)
    assert _ids(tasks.list_tasks(user_id=uid, session_id=sup)) == ['plan']


def test_expected_revision_rejects_stale_editor_saves(world):
    db, uid, other, orch, *_ = world
    row = _plan(orch)
    tasks.patch('plan', {'set_done': ['a']})  # an agent moves first
    with pytest.raises(GizmoError) as err:
        tasks.patch('plan', {'items': [{'id': 'a', 'text': 'Mine'}], 'expected_revision': row['revision']})
    assert err.value.code == 'conflict'
    fresh = tasks.get('plan')
    saved = tasks.patch('plan', {'title': 'Renamed', 'expected_revision': fresh['revision']})
    assert saved['title'] == 'Renamed'


def test_delete_removes_the_list_audits_and_bumps_revision(world):
    db, uid, other, orch, *_ = world
    _plan(orch)
    before = db.widgets_revision_for(user_id=uid)
    with pytest.raises(GizmoError):
        tasks.delete('plan', user_id=other)
    tasks.delete('plan', user_id=uid)
    assert db.get_chat_widget('plan') is None
    assert db.widgets_revision_for(user_id=uid) > before
    events = db.task_gizmo_history('plan', user_id=uid)
    assert events[0]['operation'] == 'delete' and events[0]['before_state']['title'] == 'Tasks'
    with pytest.raises(GizmoError):
        tasks.delete('plan', user_id=uid)


def test_manager_listing_covers_archived_lists_and_names_chats(world):
    db, uid, other, orch, sup, child, foreign = world
    _plan(orch, linked_sessions=[sup])
    tasks.create(gizmo_id='done', session_id=child, items=[{'id': 'x', 'text': 'Ship', 'done': True}])
    tasks.create(gizmo_id='proj', session_id=orch, scope='project', items=['Docs'])
    tasks.create(gizmo_id='theirs', session_id=foreign, items=['Hidden'])
    out = tasks_manager.listing(user_id=uid)
    by_id = {r['id']: r for r in out['lists']}
    assert set(by_id) == {'plan', 'done', 'proj'}
    assert by_id['done']['status'] == 'archived' and (by_id['done']['done'], by_id['done']['total']) == (1, 1)
    assert by_id['plan']['home_chat']['name'] == 'Orchestrator'
    assert by_id['plan']['home_chat']['handle'] == f'CH-{orch:06d}'
    assert [c['name'] for c in by_id['plan']['linked_chats']] == ['Supervisor']
    assert by_id['proj']['home_chat'] is None and by_id['proj']['project_path'] == '/projects/game'
    assert {c['id'] for c in out['chats']} == {orch, sup, child}
    assert out['revision'] == db.widgets_revision_for(user_id=uid)


def test_related_chats_walk_the_orchestration_family(world, monkeypatch):
    db, uid, other, orch, sup, child, foreign = world
    sibling = db.create_chat_session(uid, 'Sub-agent B')
    parents = {child: orch, sibling: orch}
    monkeypatch.setattr(tasks_manager, '_subagent_parent', lambda _db, sid: parents.get(sid))
    monkeypatch.setattr(tasks_manager, '_subagent_children',
                        lambda _db, sid: [k for k, p in parents.items() if p == sid])
    monkeypatch.setattr(tasks_manager, '_supervision_links', lambda: {orch: sup})
    from_child = {c['id']: c['relation'] for c in tasks_manager.related_chats(child, user_id=uid)}
    assert from_child == {orch: 'parent', sibling: 'sibling', sup: 'supervisor'}
    from_sup = {c['id']: c['relation'] for c in tasks_manager.related_chats(sup, user_id=uid)}
    assert from_sup == {orch: 'supervised', child: 'sub-agent', sibling: 'sub-agent'}
    with pytest.raises(GizmoError):
        tasks_manager.related_chats(foreign, user_id=uid)


def test_related_chats_reads_real_subagent_and_supervision_stores(world, monkeypatch):
    """No fakes: the deferred owners answer for a chat with no family."""
    db, uid, other, orch, *_ = world
    monkeypatch.setattr(tasks_manager, '_supervision_links', lambda: {})
    assert tasks_manager.related_chats(orch, user_id=uid) == []


def test_cli_link_flags(world, capsys, monkeypatch):
    db, uid, other, orch, sup, child, foreign = world
    monkeypatch.setenv('CUTTLE_CHAT_SESSION_ID', str(orch))
    code = cli.main(['tasks', 'create', '--id', 'cli-plan', '--items', '["One"]',
                     '--link', f'CH-{sup:06d},{child}'])
    assert code == 0 and json.loads(capsys.readouterr().out)['gizmo']['linked_sessions'] == [sup, child]
    code = cli.main(['tasks', 'patch', 'cli-plan', '--unlink', str(sup)])
    assert code == 0 and json.loads(capsys.readouterr().out)['gizmo']['linked_sessions'] == [child]


def test_http_manager_delete_related_and_page(world):
    from flask import Flask
    from api.gizmos.routes import gizmos_bp, gizmos_pages_bp
    db, uid, other, orch, sup, child, foreign = world
    app = Flask(__name__)
    app.register_blueprint(gizmos_bp)
    app.register_blueprint(gizmos_pages_bp)
    client = app.test_client()
    assert client.get('/api/gizmos/tasks?all=1').status_code == 401
    client.set_cookie('session_token', db.create_auth_session(uid))
    created = client.post('/api/gizmos/tasks', json={
        'id': 'web', 'title': 'Web plan', 'items': [{'id': 'a', 'text': 'A'}], 'session_id': str(orch),
        'edit_mode': 'shared', 'linked_sessions': [str(sup)]})
    assert created.status_code == 201 and created.get_json()['gizmo']['linked_sessions'] == [sup]
    listed = client.get('/api/gizmos/tasks?all=1').get_json()
    assert [l['id'] for l in listed['lists']] == ['web'] and listed['lists'][0]['linked_chats'][0]['id'] == sup
    rev = listed['lists'][0]['revision']
    patched = client.patch('/api/gizmos/tasks/web?summary=1', json={'link': [str(child)], 'expected_revision': rev})
    assert [c['id'] for c in patched.get_json()['gizmo']['linked_chats']] == [sup, child]
    stale = client.patch('/api/gizmos/tasks/web', json={'title': 'X', 'expected_revision': rev})
    assert stale.status_code == 409
    assert client.get('/api/gizmos/tasks/web/related').get_json()['success'] is True
    client.set_cookie('session_token', db.create_auth_session(other))
    assert client.delete('/api/gizmos/tasks/web').status_code == 404
    assert client.get('/api/gizmos/tasks?all=1').get_json()['lists'] == []
    client.set_cookie('session_token', db.create_auth_session(uid))
    assert client.delete('/api/gizmos/tasks/web').get_json()['success'] is True
    assert client.get('/api/gizmos/tasks/web').status_code == 404
    page = client.get('/tasks_page.html')
    assert page.status_code == 200 and b'tasks_page.js' in page.data
