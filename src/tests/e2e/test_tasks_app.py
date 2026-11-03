"""Tasks App in Chromium with a private static server and a fake Tasks API."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest
from .test_shared_diff_modal import static_server, browser, apply_request_guard

CHATS = [
    {'id': 11, 'handle': 'CH-000011', 'name': 'Galaxy orchestrator', 'project_path': '/p/galaxy', 'last_activity': '2026-11-03 11:00:00', 'archived': False},
    {'id': 12, 'handle': 'CH-000012', 'name': 'Galaxy supervisor', 'project_path': '', 'last_activity': '2026-11-03 10:00:00', 'archived': False},
    {'id': 13, 'handle': 'CH-000013', 'name': 'Shader sub-agent', 'project_path': '/p/galaxy', 'last_activity': '2026-11-02 10:00:00', 'archived': False},
]
LISTS = [
    {'id': 'plan', 'type': 'tasks', 'title': 'Galaxy plan', 'description': 'Ship the demo', 'scope': 'session',
     'session_id': 11, 'project_path': '', 'status': 'active', 'edit_mode': 'shared', 'revision': 3,
     'linked_sessions': [12], 'updated_at': '2026-11-03 11:30:00',
     'payload': {'items': [
         {'id': 'a', 'text': 'Engine', 'done': True, 'children': []},
         {'id': 'b', 'text': 'Shaders', 'done': False, 'children': [{'id': 'b1', 'text': 'Bloom', 'done': False, 'children': []}]},
     ]}},
    {'id': 'agent-only', 'type': 'tasks', 'title': 'Sub-agents', 'description': '', 'scope': 'session',
     'session_id': 13, 'project_path': '', 'status': 'active', 'edit_mode': 'agent', 'revision': 1,
     'linked_sessions': [], 'updated_at': '2026-11-03 09:00:00',
     'payload': {'items': [{'id': 'x', 'text': 'Profile', 'done': False, 'children': []}]}},
    {'id': 'old', 'type': 'tasks', 'title': 'Old release', 'description': '', 'scope': 'project',
     'session_id': None, 'project_path': '/p/galaxy', 'status': 'archived', 'edit_mode': 'agent', 'revision': 5,
     'linked_sessions': [], 'updated_at': '2026-10-01 09:00:00',
     'payload': {'items': [{'id': 'r', 'text': 'Tag', 'done': True, 'children': []}]}},
]


def _count(items):
    done = total = 0
    for n in items:
        total += 1
        done += 1 if n.get('done') else 0
        d, t = _count(n.get('children') or [])
        done, total = done + d, total + t
    return done, total


def _mark(items, ids, value):
    for n in items:
        if n['id'] in ids:
            n['done'] = value
        _mark(n.get('children') or [], ids, value)


class FakeAPI:
    def __init__(self):
        self.lists = copy.deepcopy(LISTS)
        self.revision = 100
        self.calls = []

    def chat(self, sid):
        return next((c for c in CHATS if c['id'] == int(sid)), None)

    def summary(self, row):
        done, total = _count(row['payload']['items'])
        return {**row, 'done': done, 'total': total,
                'home_chat': self.chat(row['session_id']) if row['scope'] == 'session' else None,
                'linked_chats': [self.chat(s) for s in row['linked_sessions']]}

    def find(self, gid):
        return next((l for l in self.lists if l['id'] == gid), None)

    def handle(self, route):
        req = route.request
        url = urlparse(req.url)
        path, query = url.path, parse_qs(url.query)
        body = json.loads(req.post_data or '{}')
        self.calls.append((req.method, path, body, query))
        out, status = {'success': True}, 200
        parts = path.split('/')
        gid = parts[4] if len(parts) > 4 else ''
        if path == '/api/gizmos/tasks' and req.method == 'GET':
            out.update(lists=[self.summary(l) for l in self.lists], chats=CHATS, revision=self.revision)
        elif path == '/api/gizmos/tasks' and req.method == 'POST':
            row = {'id': 'new-1', 'type': 'tasks', 'title': body['title'], 'description': body['description'],
                   'scope': body['scope'], 'session_id': int(body['session_id']), 'project_path': '',
                   'status': 'active', 'edit_mode': body['edit_mode'], 'revision': 1,
                   'linked_sessions': [int(s) for s in body['linked_sessions']], 'updated_at': '2026-11-03 12:00:00',
                   'payload': {'items': body['items']}}
            self.lists.insert(0, row)
            self.revision += 1
            out['gizmo'], status = self.summary(row), 201
        elif path.endswith('/history'):
            out['events'] = [{'sequence': 2, 'operation': 'patch', 'created_at': '2026-11-03 11:30:00',
                              'actor': {'source': 'cli', 'agent_id': 'codex', 'model': 'gpt-5'},
                              'before_state': {'title': 'Galaxy plan', 'status': 'active', 'payload': {'items': [{'id': 'a', 'done': False}]}},
                              'after_state': {'title': 'Galaxy plan', 'status': 'active', 'payload': {'items': [{'id': 'a', 'done': True}]}}},
                             {'sequence': 1, 'operation': 'create', 'created_at': '2026-11-03 10:00:00',
                              'actor': {'source': 'ui'}, 'after_state': {'payload': {'items': [{'id': 'a'}, {'id': 'b'}]}}}]
        elif path.endswith('/related'):
            out['chats'] = [{**CHATS[1], 'relation': 'supervisor'}, {**CHATS[2], 'relation': 'sub-agent'}]
        elif req.method == 'DELETE':
            self.lists = [l for l in self.lists if l['id'] != gid]
            self.revision += 1
        elif req.method == 'PATCH':
            row = self.find(gid)
            if 'expected_revision' in body and body['expected_revision'] != row['revision']:
                out, status = {'success': False, 'error': 'changed', 'code': 'conflict'}, 409
            else:
                for key in ('title', 'description', 'status', 'edit_mode', 'scope'):
                    if key in body:
                        row[key] = body[key]
                if 'items' in body:
                    row['payload']['items'] = body['items']
                _mark(row['payload']['items'], set(body.get('set_done') or []), True)
                _mark(row['payload']['items'], set(body.get('set_undone') or []), False)
                for sid in body.get('link') or []:
                    if int(sid) not in row['linked_sessions']:
                        row['linked_sessions'].append(int(sid))
                row['linked_sessions'] = [s for s in row['linked_sessions'] if str(s) not in map(str, body.get('unlink') or [])]
                row['revision'] += 1
                self.revision += 1
                out['gizmo'] = self.summary(row)
        elif path == '/api/auth/me':
            out.update(authenticated=True, user={'id': 1})
        route.fulfill(status=status, content_type='application/json', body=json.dumps(out))


@pytest.fixture
def page_ctx(browser, static_server):
    context = browser.new_context(viewport={'width': 1240, 'height': 950})
    apply_request_guard(context, static_server)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    yield page, errors
    context.close()


def _open(page, static_server, api, query=''):
    page.route('**/api/**', api.handle)
    page.goto(static_server + '/tasks_page.html' + query)
    page.locator('#tasksList .tasks-card').first.wait_for()


def _patches(api):
    return [c for c in api.calls if c[0] == 'PATCH']


def test_lists_filter_and_shared_list_edits_save_with_revision_guard(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    _open(page, static_server, api)
    assert page.locator('#tasksList .tasks-card').count() == 2  # active only
    page.click('.tasks-filter [data-status="archived"]')
    assert page.locator('#tasksList .tasks-card').count() == 1
    page.click('.tasks-filter [data-status="all"]')
    page.fill('#tasksSearch', 'bloom')
    assert page.locator('#tasksList .tasks-card').count() == 1
    page.fill('#tasksSearch', '')
    page.click('[data-open="plan"]')
    assert page.locator('#tasksTitle').input_value() == 'Galaxy plan'
    assert page.locator('.tasks-item').count() == 3
    assert '1 of 3 done' in page.locator('.tasks-progress-text').inner_text()
    # Checkbox applies immediately (tree clean).
    page.click('.tasks-item[data-id="b1"] > .tasks-row .tasks-check')
    page.wait_for_function("document.querySelector('.tasks-progress-text').textContent.includes('2 of 3')")
    assert _patches(api)[-1][2] == {'set_done': ['b1']}
    # Text edit + Enter adds a row → save bar → PATCH with expected_revision.
    page.click('.tasks-item[data-id="b"] > .tasks-row textarea')
    page.keyboard.press('End')
    page.keyboard.type(' pass')
    page.keyboard.press('Enter')
    page.keyboard.type('Fog')
    page.keyboard.press('Tab')
    assert not page.locator('#tasksSavebar').is_hidden()
    page.click('#tasksSave')
    page.wait_for_function("document.getElementById('tasksSavebar').hidden")
    body = _patches(api)[-1][2]
    assert body['expected_revision'] == 4
    shaders = body['items'][1]
    assert shaders['text'] == 'Shaders pass' and [c['text'] for c in shaders['children']] == ['Bloom', 'Fog']
    assert errors == []


def test_conflict_offers_reload_or_overwrite(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    _open(page, static_server, api, '?list=plan')
    page.fill('#tasksTitle', 'Renamed')
    api.find('plan')['revision'] = 9  # an agent wrote meanwhile
    page.click('#tasksSave')
    page.wait_for_function("document.getElementById('tasksSave').textContent === 'Overwrite'")
    assert 'Changed elsewhere' in page.locator('#tasksSaveState').inner_text()
    page.click('#tasksSave')
    page.wait_for_function("document.getElementById('tasksSavebar').hidden")
    assert 'expected_revision' not in _patches(api)[-1][2] and _patches(api)[-1][2]['title'] == 'Renamed'
    assert errors == []


def test_agent_only_list_is_read_only_until_shared(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    _open(page, static_server, api, '?list=agent-only')
    assert page.locator('.tasks-lock').is_visible()
    assert page.locator('.tasks-item input[type=checkbox]').first.is_disabled()
    assert page.locator('.tasks-add').count() == 0
    page.click('[data-act="share"]')
    page.wait_for_function("!document.querySelector('.tasks-lock')")
    assert _patches(api)[-1][2] == {'edit_mode': 'shared'}
    assert page.locator('.tasks-add').count() == 1
    assert errors == []


def test_chats_tab_links_family_and_opens_chat(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    _open(page, static_server, api, '?list=plan')
    page.click('.tasks-tabs [data-tab="chats"]')
    page.wait_for_selector('[data-link="13"]')
    assert 'Supervisor' not in page.locator('[data-link]').all_inner_texts()  # 12 is already linked
    page.click('[data-link="13"]')
    page.wait_for_selector('[data-unlink="13"]')
    assert _patches(api)[-1][2] == {'link': ['13']}
    page.click('[data-unlink="12"]')
    page.wait_for_function("!document.querySelector('[data-unlink=\"12\"]')")
    assert _patches(api)[-1][2] == {'unlink': ['12']}
    page.click('.tasks-tabs [data-tab="history"]')
    page.wait_for_selector('.tasks-event')
    assert 'codex · gpt-5' in page.locator('.tasks-event').first.inner_text()
    assert errors == []
    page.route('**/chat_page.html*', lambda route: route.fulfill(status=200, content_type='text/html', body='<p>chat</p>'))
    with page.expect_navigation():
        page.click('.tasks-identity [data-chat="11"]')
    assert page.url.endswith('/chat_page.html?chat=11')


def test_new_list_with_home_scope_mode_and_links(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    _open(page, static_server, api)
    page.click('#tasksNew')
    page.fill('#tasksTitle', 'Release')
    page.evaluate("""() => { const s = document.getElementById('tasksNewChat'); s.value = '11';
        s.dispatchEvent(new Event('change', { bubbles: true })); }""")
    page.click('.tasks-seg[data-seg="scope"] [data-value="project"]')
    page.evaluate("""() => { const s = document.getElementById('tasksNewLink'); s.value = '12';
        s.dispatchEvent(new Event('change', { bubbles: true })); }""")
    page.wait_for_selector('[data-unlink-new="12"]')
    page.click('textarea.tasks-text')
    page.keyboard.type('Changelog')
    page.keyboard.press('Enter')
    page.keyboard.type('Tag build')
    page.click('[data-act="create"]')
    page.wait_for_function("document.getElementById('tasksTitle') && document.getElementById('tasksTitle').value === 'Release' && document.querySelector('.tasks-tabs')")
    post = [c for c in api.calls if c[0] == 'POST'][-1][2]
    assert post['session_id'] == '11' and post['scope'] == 'project' and post['edit_mode'] == 'shared'
    assert post['linked_sessions'] == ['12']
    assert [i['text'] for i in post['items']] == ['Changelog', 'Tag build']
    assert errors == []


def test_archive_and_delete(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    _open(page, static_server, api, '?list=plan')
    page.click('[data-act="archive"]')
    page.wait_for_selector('[data-act="restore"]')
    assert _patches(api)[-1][2] == {'status': 'archived'}
    page.on('dialog', lambda d: d.accept())
    page.click('[data-act="delete"]')
    page.wait_for_selector('.orch-empty')
    assert any(c[0] == 'DELETE' and c[1] == '/api/gizmos/tasks/plan' for c in api.calls)
    assert errors == []
