"""Projects App: host path ordering, registry identity, and authenticated workflows."""
import os
import json
from pathlib import Path

import pytest
from flask import Flask


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from managers import project_manager as module
    monkeypatch.setattr(module.ProjectManager, 'ensure_default_project', lambda self: None)
    pm = module.ProjectManager(str(tmp_path / 'projects.db'))
    folder = tmp_path / 'project'
    folder.mkdir()
    pid = pm.register_project('Demo', str(folder), tags=['game'])
    return pm, pid, folder


def test_ordered_paths_are_host_resolved_and_survive_restart(registry, tmp_path, monkeypatch):
    from managers.project_manager import ProjectManager
    pm, pid, folder = registry
    alternate = tmp_path / 'alternate'
    alternate.mkdir()
    foreign = '/home/demo/Demo' if os.name == 'nt' else 'Z:\\Windows\\Demo'
    paths = [foreign, str(tmp_path / 'missing'), str(alternate), str(folder)]
    pm.update_project(pid, paths=paths)
    project = pm.get_project(pid)
    assert project['path'] == str(alternate)
    assert project['paths'] == paths
    assert [c['selected'] for c in project['path_checks']] == [False, False, True, False]
    assert project['path_checks'][0]['status'] == 'different_os'
    restarted = ProjectManager(pm.db_path)
    restarted.update_project(pid, paths=[str(folder), str(alternate)])
    assert restarted.get_project(pid)['path'] == str(folder)
    assert restarted.get_project(pid)['id'] == pid


def test_unavailable_paths_never_choose_an_unrelated_folder(registry, tmp_path):
    from managers.project_locations import ProjectUnavailable, require_project_path
    pm, pid, _ = registry
    pm.update_project(pid, paths=[str(tmp_path / 'missing')])
    project = pm.get_project(pid)
    assert not project['available']
    assert project['resolved_path'] is None
    with pytest.raises(ProjectUnavailable):
        require_project_path(project)


@pytest.mark.parametrize('paths', [[], ['relative/path'], ['/a', '/a'], [123], ['/a\x00b']])
def test_invalid_paths_do_not_mutate_registry(registry, paths):
    pm, pid, folder = registry
    with pytest.raises(ValueError):
        pm.update_project(pid, paths=paths)
    assert pm.get_project(pid)['path'] == str(folder)


def test_missing_folder_can_be_saved_for_later_and_archive_is_reversible(registry, tmp_path):
    pm, pid, folder = registry
    pm.switch_to_project(pid)
    pm.update_project(pid, paths=[str(tmp_path / 'later'), str(folder)], archived=True)
    assert not pm.get_projects()
    assert pm.get_projects(include_archived=True)[0]['id'] == pid
    assert pm.get_current_project()['archived']
    pm.update_project(pid, archived=False)
    assert pm.get_projects()[0]['id'] == pid
    assert pm.get_current_project()['paths'][0].endswith('later')


def test_unknown_configuration_preserved_and_remove_never_deletes_files(registry):
    pm, pid, folder = registry
    with pm.get_db_connection() as conn:
        conn.execute('UPDATE projects SET config = ? WHERE id = ?', (json.dumps({'custom': {'keep': True}}), pid))
        conn.commit()
    pm.update_project(pid, description='Updated')
    assert pm.get_project(pid)['config']['custom'] == {'keep': True}
    assert (folder / '.cuttle').is_dir()
    pm.switch_to_project(pid)
    assert pm.delete_project(pid)
    assert folder.is_dir() and (folder / '.cuttle').is_dir()
    assert pm.get_current_project() is None
    assert any(h['action'] == 'deleted' for h in pm.get_project_history(pid))


def test_registration_validates_and_scaffolds(registry, tmp_path):
    pm, _, folder = registry
    with pytest.raises(ValueError):
        pm.register_project('Dead', str(tmp_path / 'dead'))
    with pytest.raises(ValueError):
        pm.register_project('Demo', str(folder))
    assert len(pm.get_projects()) == 1
    assert (folder / '.cuttle' / 'commands').is_dir()


@pytest.fixture
def app_context(registry, tmp_path, monkeypatch):
    from api import auth_db, project_routes
    pm, pid, folder = registry
    db = auth_db.AuthDatabase(tmp_path / 'auth.db')
    monkeypatch.setattr(auth_db, 'get_auth_db', lambda: db)
    monkeypatch.setattr(project_routes, 'project_manager', pm)
    monkeypatch.setenv('OWNER_USER_EMAIL', 'owner@test')
    owner = db.create_user('owner@test', 'Owner', 'local', password='x')
    other = db.create_user('other@test', 'Other', 'local', password='x')
    app = Flask(__name__)
    app.register_blueprint(project_routes.projects_bp)
    app.register_blueprint(project_routes.projects_pages_bp)
    client = app.test_client()
    return client, db, pm, pid, folder, owner, other


def login(client, db, uid):
    client.set_cookie('session_token', db.create_auth_session(uid))


def test_new_routes_auth_owner_validation_and_typed_removal(app_context, tmp_path):
    c, db, pm, pid, folder, owner, other = app_context
    assert c.get('/projects_page.html').status_code == 401
    login(c, db, other)
    assert c.get('/projects_page.html').status_code == 200
    assert c.get('/api/projects/app/access').get_json()['can_edit'] is False
    for route, payload in [('/api/projects/paths/check', {'paths':[str(folder)]}),
                           ('/api/projects/register', {'name':'Other','path':str(folder)}),
                           (f'/api/projects/{pid}/remove', {'confirm_name':'Demo'})]:
        assert c.post(route, json=payload).status_code == 403
    login(c, db, owner)
    assert c.post('/api/projects/paths/check', json={'paths':['relative']}).status_code == 400
    assert c.put(f'/api/projects/{pid}', json={'paths':[]}).status_code == 400
    assert c.post(f'/api/projects/{pid}/remove', json={'confirm_name':'Wrong'}).status_code == 400
    assert pm.get_project(pid)
    overview = c.get(f'/api/projects/{pid}/overview').get_json()['data']
    assert overview['project']['available']
    assert any(layer['label'] == 'Project' for layer in overview['configuration'])
    assert c.post(f'/api/projects/{pid}/remove', json={'confirm_name':'Demo'}).get_json()['success']
    assert folder.is_dir()


def test_feed_is_user_scoped_id_based_and_paginates(app_context, tmp_path):
    c, db, pm, pid, folder, owner, other = app_context
    owned = db.create_chat_session(owner, 'My Demo')
    theirs = db.create_chat_session(other, 'Private Demo')
    for sid, uid in [(owned, owner), (theirs, other)]:
        db.set_session_project(sid, uid, project_id=pid, project_name='Demo', project_path=str(folder))
    db.add_message(owned, 'user', 'My visible message')
    last = db.add_message(owned, 'assistant', 'Visible reply')
    db.add_message(owned, 'system', 'Hidden system notice')
    db.add_message(theirs, 'user', 'PRIVATE secret')
    pm.update_project(pid, paths=[str(tmp_path / 'moved')], name='Renamed')
    login(c, db, owner)
    feed = c.get(f'/api/projects/{pid}/activity').get_json()['data']
    assert feed['stats']['chats'] == 1 and feed['stats']['messages'] == 2
    assert len(feed['messages']) == 2
    assert 'PRIVATE' not in str(feed)
    older = c.get(f'/api/projects/{pid}/activity?before_id={last}').get_json()['data']
    assert len(older['messages']) == 1
    assert c.get(f'/api/projects/{pid}/activity?before_id=oops').status_code == 400
    assert c.post(f'/api/projects/{pid}/chat', json={}).status_code == 409
    pm.update_project(pid, paths=[str(folder)])
    sid = c.post(f'/api/projects/{pid}/chat', json={}).get_json()['session_id']
    assert db.get_session_project(sid)['project_id'] == pid


def test_resolver_uses_host_list_over_stale_client_and_pins_turn(registry, tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from managers.project_locations import ProjectUnavailable
    from flask import g
    pm, pid, folder = registry
    alternate = tmp_path / 'alternate'
    alternate.mkdir()
    monkeypatch.setattr(wca, 'project_manager', pm)
    with wca.app.test_request_context('/api/chat'):
        assert wca._resolve_request_project_path({'project_id':pid, 'project_path':'Z:/old'}) == str(folder)
        g.project_turn_path = wca._resolve_request_project_path({'project_id':pid})
        pm.update_project(pid, paths=[str(alternate)])
        assert wca._resolve_request_project_path({'project_id':pid}) == str(folder)
    with wca.app.test_request_context('/api/chat'):
        assert wca._resolve_request_project_path({'project_id':pid}) == str(alternate)
        pm.update_project(pid, paths=[str(tmp_path / 'missing')])
        with pytest.raises(ProjectUnavailable):
            wca._resolve_request_project_path({'project_id':pid, 'project_path':str(folder)})
        with pytest.raises(ProjectUnavailable):
            wca._resolve_request_project_path({'project_name':'Demo', 'project_path':str(folder)})


def test_chat_ingress_rejects_unavailable_project_before_dispatch(app_context, tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from types import SimpleNamespace
    c, db, pm, pid, folder, owner, _ = app_context
    pm.update_project(pid, paths=[str(tmp_path / 'missing')])
    monkeypatch.setattr(wca, 'project_manager', pm)
    monkeypatch.setattr(wca, 'get_auth_db', lambda: db)
    monkeypatch.setattr(wca, 'PAIRING_AVAILABLE', True)
    monkeypatch.setattr(wca, 'get_pairing_manager', lambda: SimpleNamespace(check_access=lambda *a, **k: {'allowed':True}))
    def forbidden(*args, **kwargs):
        pytest.fail('Unavailable project reached the agent executor.')
    monkeypatch.setattr(wca, '_run_harness_web_command', forbidden)
    client = wca.app.test_client()
    login(client, db, owner)
    response = client.post('/api/chat', json={'message':'/codex do work', 'project_id':pid, 'project_path':str(folder)})
    assert response.status_code == 409, response.get_json()
    assert response.get_json()['type'] == 'project_unavailable'
    assert 'Projects' in response.get_json()['response']


def test_register_http_workflow_and_cli_share_registry(app_context, tmp_path, monkeypatch, capsys):
    from api import projects_cli
    from managers import project_manager as module
    client, db, pm, pid, _, owner, _ = app_context
    folder = tmp_path / 'second'; folder.mkdir()
    login(client, db, owner)
    response = client.post('/api/projects/register', json={'name':'Second', 'path':str(folder), 'repo_url':'https://gitea.example/you/second'})
    assert response.status_code == 200
    new_id = response.get_json()['project_id']
    assert pm.get_project(new_id)['config']['repo_url'].startswith('https://gitea')
    monkeypatch.setattr(module, 'project_manager', pm)
    assert projects_cli.main(['get', str(new_id)]) == 0
    assert json.loads(capsys.readouterr().out)['data']['name'] == 'Second'
    assert projects_cli.main(['update', str(new_id), '--json', '{"description":"CLI edit"}']) == 0
    capsys.readouterr()
    assert pm.get_project(new_id)['description'] == 'CLI edit'
