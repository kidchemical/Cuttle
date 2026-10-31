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
OPENCODE = {'id': 'opencode', 'label': 'OpenCode', 'supports_model_pin': True, 'supports_effort': False,
            'models': [f'provider/model-{i:04d}' for i in range(2000)], 'efforts': [], 'model_efforts': {}}
AGENTS = [
    {'id': 'cursor', 'label': 'Cursor Agent', 'supports_model_pin': True, 'supports_effort': False,
     'models': ['auto', 'grok-4.6'], 'efforts': [], 'model_efforts': {}},
    {'id': 'claude', 'label': 'Claude Code', 'supports_model_pin': True, 'supports_effort': True,
     'models': ['opus', 'claude-opus-5-5'], 'efforts': ['low', 'medium', 'high'],
     'model_efforts': {'claude-opus-5-5': ['low', 'medium', 'high', 'xhigh', 'max']}},
    OPENCODE,
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


def _set_select(page, selector, value):
    page.evaluate("""([sel, v]) => { const el = document.querySelector(sel); el.value = v;
        el.dispatchEvent(new Event('change', { bubbles: true })); }""", [selector, value])


def test_agents_app_edits_profile_with_composer_picker_and_avatar(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    page.route('**/api/**', api.handle)
    page.goto(static_server + '/agent_profiles_page.html')
    page.locator('#agentsCustom .orch-card').first.wait_for()
    assert page.locator('#agentsCustom .orch-card').count() == 2
    page.click('[data-open="expert-claude"]')
    assert page.locator('#agentsName').input_value() == 'Expert (Opus)'
    assert 'Claude Code · claude-opus-5-5 · high' in page.locator('#agentsPin').inner_text()
    page.wait_for_function("document.querySelector('.orch-status.is-ok')")
    # Built-ins are read-only.
    page.click('[data-open="scout"]')
    assert page.locator('#agentsPin').is_disabled() and page.locator('#agentsAvatar').is_disabled()
    page.click('[data-open="expert-claude"]')
    # Runs on → the composer's chip-config modal.
    page.click('#agentsPin')
    page.wait_for_selector('#chipConfigModal.active #chipConfigAgent', state='attached')
    assert page.locator('#chipConfigTitle').inner_text() == 'Runs on'
    effort_values = page.eval_on_selector_all('#chipConfigEffort option', 'os => os.map(o => o.value)')
    assert 'max' in effort_values
    _set_select(page, '#chipConfigAgent', 'cursor')
    page.wait_for_function("!document.querySelector('#chipConfigEffort') && document.querySelector('#chipConfigModel')")
    _set_select(page, '#chipConfigModel', 'grok-4.6')
    page.click('#chipConfigDone')
    assert 'Cursor Agent · grok-4.6' in page.locator('#agentsPin').inner_text()
    # Avatar → emoji.
    page.click('#agentsAvatar')
    page.click('.orch-avp [data-emoji="🦊"]')
    assert '🦊' in page.locator('#agentsPreview').inner_text()
    page.fill('#agentsName', 'Opus Reviewer')
    page.click('[data-act="save"]')
    page.wait_for_function("!document.querySelector('[data-act=\"save\"]') || document.getElementById('agentsName').value === 'Opus Reviewer'")
    put = [c for c in api.calls if c[0] == 'PUT'][-1]
    assert put[1] == '/api/agent-profiles/expert-claude'
    assert put[2] == {'name': 'Opus Reviewer', 'avatar': '🦊', 'agent': 'cursor', 'model': 'grok-4.6', 'effort': ''}
    assert errors == []


def test_agent_picker_pages_long_model_catalogs(page_ctx, static_server):
    page, errors = page_ctx
    page.route('**/api/**', FakeAPI().handle)
    page.goto(static_server + '/agent_profiles_page.html')
    page.locator('#agentsCustom .orch-card').first.wait_for()
    page.click('#agentsNew')
    page.click('#agentsPin')
    page.wait_for_selector('#chipConfigModal.active #chipConfigAgent', state='attached')
    _set_select(page, '#chipConfigAgent', 'opencode')
    page.wait_for_function("document.querySelectorAll('#chipConfigModel option').length === 2001")
    page.locator('#chipConfigModal .chip-config-field').nth(1).locator('.cuttle-combo-trigger').click()
    page.wait_for_selector('.cuttle-combo-popup .cuttle-combo-option')
    rendered = page.eval_on_selector_all('.cuttle-combo-popup .cuttle-combo-option', 'os => os.length')
    assert 0 < rendered <= 120, rendered  # lazily paged, never 2000 rows at once
    assert page.locator('datalist').count() == 0
    assert errors == []


def test_orchestrator_app_edits_ladder_and_previews_supervise(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    page.route('**/api/**', api.handle)
    page.goto(static_server + '/orchestrator_page.html')
    page.locator('#orchTiers .orch-tier').first.wait_for()
    assert page.locator('#orchTiers .orch-tier').count() == 2
    assert 'Default' in page.locator('#orchTiers .orch-tier').first.inner_text()
    assert page.locator('#orchStartCmd code').inner_text() == '/orchestrate <goal>'
    page.click('#orchAddTier')
    assert page.locator('#orchTiers .orch-tier').count() == 3
    assert 'to fix' in page.locator('#orchSaveState').inner_text() and page.locator('#orchSave').is_disabled()
    _set_select(page, '#orchTiers .orch-tier:nth-child(3) select[data-field=role]', 'escalation')
    _set_select(page, '#orchTiers .orch-tier:nth-child(3) select[data-add-profile]', 'grunt')
    assert not page.locator('#orchSave').is_disabled()
    page.locator('#orchTiers .orch-tier').nth(2).locator('[data-move="-1"]').click()
    page.click('[data-tab="limits"]')
    page.locator('.orch-stepper[data-key="solo_minutes"] [data-step="-1"]').click()
    page.click('[data-tab="supervisor"]')
    page.fill('#orchForbid input', 'master')
    page.keyboard.press('Enter')
    page.fill('#orchForbid input', 'dev/core')
    page.keyboard.press('Enter')
    assert page.locator('#orchForbid .orch-tag-chip').count() == 2
    page.click('#orchSave')
    page.wait_for_function("document.getElementById('orchSaveState').textContent === 'All changes saved'")
    put = [c for c in api.calls if c[0] == 'PUT'][-1]
    assert put[1] == '/api/orchestrator/playbooks/default'
    assert [t['role'] for t in put[2]['tiers']] == ['worker', 'escalation', 'expert']
    assert put[2]['limits']['solo_minutes'] == 39
    assert put[2]['supervisor']['forbid_branches'] == ['master', 'dev/core']
    assert 'seed' not in put[2] and 'missing_profiles' not in put[2]
    page.click('[data-target="CH-001548"]')
    page.wait_for_function("document.getElementById('orchSupervise').textContent.includes('CH-001548')")
    assert errors == []


NO_STRAY_SCROLLERS = """() => [...document.querySelectorAll('.orch-av, .orch-av *, .orch-tier-step, .orch-icon-btn, .orch-role-badge')]
    .filter(e => ['auto', 'scroll'].includes(getComputedStyle(e).overflowY)).length"""


def test_small_glyph_boxes_never_become_scroll_containers(page_ctx, static_server):
    """landing_page.css `* { overflow-x: hidden }` turns overflow-y into auto;
    Electron then drew scrollbars on avatars and clipped tier numbers."""
    page, errors = page_ctx
    page.route('**/api/**', FakeAPI().handle)
    page.goto(static_server + '/agent_profiles_page.html')
    page.locator('#agentsCustom .orch-card').first.wait_for()
    page.click('[data-open="expert-claude"]')
    assert page.evaluate(NO_STRAY_SCROLLERS) == 0
    page.goto(static_server + '/orchestrator_page.html')
    page.locator('#orchTiers .orch-tier').first.wait_for()
    assert page.evaluate(NO_STRAY_SCROLLERS) == 0
    assert errors == []
