"""Orchestrator + Agents Apps in Chromium with a private static server and fake API."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest
from .test_shared_diff_modal import static_server, browser, apply_request_guard

PROFILES = [
    {'id': 'grunt', 'name': 'Grunt', 'avatar': '🐜', 'agent': 'cursor', 'model': 'auto', 'effort': '', 'builtin': False},
    {'id': 'expert-claude', 'name': 'Expert (Opus)', 'avatar': '🦉', 'agent': 'claude', 'model': 'claude-opus-5-5', 'effort': 'high', 'builtin': False},
    {'id': 'scout', 'name': 'Scout', 'avatar': '🔭', 'agent': 'cursor', 'model': '', 'effort': '', 'builtin': True},
]
AGENTS = [
    {'id': 'cursor', 'label': 'Cursor Agent', 'supports_model_pin': True, 'supports_effort': False,
     'models': ['auto', 'grok-4.6'], 'efforts': [], 'model_efforts': {}},
    {'id': 'claude', 'label': 'Claude Code', 'supports_model_pin': True, 'supports_effort': True,
     'models': ['opus', 'claude-opus-5-5'], 'efforts': ['low', 'medium', 'high'],
     'model_efforts': {'claude-opus-5-5': ['low', 'medium', 'high', 'xhigh', 'max']}},
]
DEFAULT = {
    'schema': 1, 'id': 'default', 'name': 'Default org chart', 'description': '', 'seed': True,
    'missing_profiles': [],
    'tiers': [{'id': 'worker', 'label': 'Grunt', 'role': 'worker', 'profiles': ['grunt'], 'use_for': 'grunt work'},
              {'id': 'expert', 'label': 'Expert', 'role': 'expert', 'profiles': ['expert-claude'], 'use_for': 'review'}],
    'limits': {'solo_attempts': 2, 'solo_minutes': 40, 'child_retries': 1, 'setup_retries': 2},
    'supervisor': {'every_sec': 900, 'agent': '', 'instructions': 'nudge', 'require_branch': '', 'require_path': '', 'forbid_branches': []},
    'notes': '',
}


class FakeAPI:
    def __init__(self):
        self.profiles = copy.deepcopy(PROFILES)
        self.playbooks = [copy.deepcopy(DEFAULT)]
        self.calls = []

    def handle(self, route):
        req = route.request
        url = urlparse(req.url)
        path = url.path
        body = json.loads(req.post_data or '{}')
        self.calls.append((req.method, path, body, parse_qs(url.query)))
        out, status = {'success': True}, 200
        if path == '/api/agent-profiles' and req.method == 'GET':
            out['profiles'] = self.profiles
        elif path == '/api/agent-profiles/check':
            ok = not (body.get('agent') == 'cursor' and body.get('effort'))
            out.update(ok=ok, error=None if ok else 'Unsupported effort for cursor')
        elif path.startswith('/api/agent-profiles/') and req.method == 'PUT':
            pid = path.rsplit('/', 1)[1]
            row = {'id': pid, 'builtin': False, **body}
            self.profiles = [p for p in self.profiles if p['id'] != pid] + [row]
            out['profile'] = row
        elif path == '/api/agents':
            out['agents'] = AGENTS
        elif path == '/api/orchestrator/playbooks':
            out['playbooks'] = self.playbooks
        elif path == '/api/orchestrator/bindings':
            out['bindings'] = [{'session': 'CH-001548', 'playbook_id': 'default', 'title': 'Galaxy', 'goal': '', 'bound_at': 1}]
        elif path.endswith('/preview'):
            session = parse_qs(url.query).get('session', ['CH-<orchestrator>'])[0]
            out.update(brief='## Orchestrator playbook: Default org chart\n', supervisor_command=f'python -m api.chat_cli supervise {session} --every 900')
        elif path.startswith('/api/orchestrator/playbooks/') and req.method == 'PUT':
            doc = {**body, 'missing_profiles': []}
            self.playbooks = [p for p in self.playbooks if p['id'] != doc['id']] + [doc]
            out['playbook'] = doc
        elif path == '/api/auth/me':
            out.update(authenticated=True, user={'id': 1})
        route.fulfill(status=status, content_type='application/json', body=json.dumps(out))


@pytest.fixture
def page_ctx(browser, static_server):
    context = browser.new_context(viewport={'width': 1200, 'height': 950})
    apply_request_guard(context, static_server)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    yield page, errors
    context.close()


def test_agents_app_edits_profile_with_catalog_pins(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    page.route('**/api/**', api.handle)
    page.goto(static_server + '/agent_profiles_page.html')
    page.locator('#agentsCustom .orch-row').first.wait_for()
    assert page.locator('#agentsCustom .orch-row').count() == 2
    assert page.locator('#agentsBuiltin [data-del]').count() == 0  # built-ins read-only
    page.click('[data-edit="expert-claude"]')
    form = page.locator('#agentsForm')
    assert form.locator('[name=id]').input_value() == 'expert-claude'
    assert form.locator('[name=agent]').input_value() == 'claude'
    efforts = form.locator('[name=effort] option').all_inner_texts()
    assert 'max' in efforts and form.locator('[name=effort]').input_value() == 'high'
    page.wait_for_function("document.getElementById('agentsCheck').textContent === 'Pin is runnable'")
    form.locator('[name=agent]').select_option('cursor')
    assert form.locator('[name=effort]').is_disabled()
    form.locator('[name=model]').fill('grok-4.6')
    form.locator('[name=name]').fill('Opus Reviewer')
    form.locator('button[type=submit]').click()
    page.wait_for_function("document.getElementById('agentsEditor').hidden")
    put = [c for c in api.calls if c[0] == 'PUT'][-1]
    assert put[1] == '/api/agent-profiles/expert-claude'
    assert put[2] == {'name': 'Opus Reviewer', 'avatar': '🦉', 'agent': 'cursor', 'model': 'grok-4.6', 'effort': ''}
    assert errors == []


def test_orchestrator_app_edits_tiers_and_previews_supervise(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    page.route('**/api/**', api.handle)
    page.goto(static_server + '/orchestrator_page.html')
    page.locator('#orchTiers .orch-tier').first.wait_for()
    assert page.locator('#orchTiers .orch-tier').count() == 2
    assert 'Grunt' in page.locator('#orchTiers .orch-tier').first.inner_text()
    page.wait_for_function("document.getElementById('orchBrief').textContent.includes('Orchestrator playbook')")
    page.click('#orchAddTier')
    assert page.locator('#orchTiers .orch-tier').count() == 3
    assert not page.locator('#orchProblems').is_hidden() and page.locator('#orchSave').is_disabled()
    third = page.locator('#orchTiers .orch-tier').nth(2)
    third.locator('[data-field=role]').select_option('escalation')
    third.locator('[data-add-profile]').select_option('grunt')
    assert page.locator('#orchProblems').is_hidden() and not page.locator('#orchSave').is_disabled()
    page.locator('#orchTiers .orch-tier').nth(2).locator('[data-move="-1"]').click()
    page.fill('#orchSupervisor [name=forbid_branches]', 'master, dev/core')
    page.click('#orchSave')
    page.wait_for_function("document.getElementById('orchSaveState').textContent === ''")
    put = [c for c in api.calls if c[0] == 'PUT'][-1]
    assert put[1] == '/api/orchestrator/playbooks/default'
    assert [t['role'] for t in put[2]['tiers']] == ['worker', 'escalation', 'expert']
    assert put[2]['supervisor']['forbid_branches'] == ['master', 'dev/core']
    assert 'seed' not in put[2] and 'missing_profiles' not in put[2]
    page.click('[data-target="CH-001548"]')
    page.wait_for_function("document.getElementById('orchSupervise').textContent.includes('CH-001548')")
    assert errors == []
