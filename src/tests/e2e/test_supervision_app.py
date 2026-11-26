"""Supervision App in Chromium with a private static server and a fake chat-ops API."""
import copy
import json
from urllib.parse import urlparse

import pytest
from .test_shared_diff_modal import static_server, browser, apply_request_guard  # noqa: F401
from .control_helpers import low_contrast_text

BUILTIN = {'every': 900, 'agent': '', 'involve_user': 'blocked', 'nudge': 'agent',
           'system': {'processes': True, 'memory': False, 'display': False}, 'paused_alert_hours': 6}
ROWS = [
    {'id': 'op-game', 'state': 'pending', 'supervisor': 'CH-000012', 'supervisor_id': 12,
     'supervisor_name': 'Supervisor · CH-000011', 'target': 'CH-000011', 'target_id': 11,
     'target_name': 'Optimize the game', 'project_name': 'Galaxy', 'agent': 'claude', 'every': 900,
     'next_check': 4102444800, 'system': {'processes': True, 'memory': False, 'display': True},
     'last_seen': {'state': 'busy'}, 'violations': []},
    {'id': 'op-docs', 'state': 'paused', 'supervisor': 'CH-000014', 'supervisor_id': 14,
     'supervisor_name': 'Supervisor · CH-000013', 'target': 'CH-000013', 'target_id': 13,
     'target_name': 'Docs sweep', 'project_name': '', 'agent': '', 'every': 1800, 'next_check': 0,
     'system': {'processes': True, 'memory': False, 'display': False}, 'last_seen': {'state': 'idle_with_work'},
     'violations': [], 'paused_at': 1700000000, 'paused_until': None},
]


class FakeAPI:
    def __init__(self, *, disabled=False):
        self.rows = copy.deepcopy(ROWS)
        self.defaults = copy.deepcopy(BUILTIN)
        self.disabled = disabled
        self.calls = []

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path
        body = json.loads(req.post_data or '{}')
        self.calls.append((req.method, path, body))
        out, status = {'success': True}, 200
        if self.disabled and path.startswith('/api/chat-ops/'):
            out = {'success': False, 'disabled': True, 'error': 'Chat supervision is disabled'}
        elif path == '/api/chat-ops/supervisions':
            out.update(supervisions=self.rows, display={'held': True, 'method': 'fake', 'error': ''})
        elif path == '/api/chat-ops/defaults' and req.method == 'GET':
            out.update(defaults=self.defaults, builtin=BUILTIN)
        elif path == '/api/chat-ops/defaults' and req.method == 'PUT':
            if body.get('paused_alert_hours') == 13:
                out, status = {'success': False, 'error': 'Owner access required'}, 403
            else:
                self.defaults = body
                out.update(defaults=self.defaults, builtin=BUILTIN)
        elif path.startswith('/api/chat-ops/operations/'):
            _, _, _, _, oid, verb = path.split('/')
            row = next(r for r in self.rows if r['id'] == oid)
            if verb == 'cancel':
                self.rows.remove(row)
            else:
                row['state'] = 'paused' if verb == 'pause' else 'pending'
        elif path == '/api/agents':
            out.update(agents=[{'id': 'cursor', 'label': 'Cursor'}, {'id': 'claude', 'label': 'Claude'},
                               {'id': 'none', 'label': 'Router', 'sticky': False}])
        elif path == '/api/auth/me':
            out.update(authenticated=True, user={'id': 1})
        route.fulfill(status=status, content_type='application/json', body=json.dumps(out))


@pytest.fixture
def page_ctx(browser, static_server):
    context = browser.new_context(viewport={'width': 1240, 'height': 1000})
    apply_request_guard(context, static_server)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    yield page, errors
    context.close()


def _open(page, static_server, api):
    page.route('**/api/**', api.handle)
    page.goto(static_server + '/supervision_page.html')


def _puts(api):
    return [c[2] for c in api.calls if c[0] == 'PUT']


def test_live_list_shows_state_care_and_controls(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    _open(page, static_server, api)
    page.locator('.sup-row').first.wait_for()
    assert page.locator('.sup-row').count() == 2
    game = page.locator('[data-row="op-game"]')
    assert 'Optimize the game' in game.inner_text() and 'Chat working' in game.inner_text()
    assert [t.strip() for t in game.locator('.orch-tag').all_inner_texts()] == ['Processes', 'Display']
    assert 'Keeping the display awake' in page.locator('#supDisplayNote').inner_text()
    docs = page.locator('[data-row="op-docs"]')
    assert 'no auto-resume' in docs.inner_text() and docs.get_by_role('button', name='Resume now').count() == 1
    # Pausing asks for how long, then posts that choice.
    game.get_by_role('button', name='Pause…').click()
    game.get_by_role('button', name='4 hours').click()
    page.wait_for_function("document.querySelectorAll('.sup-row.is-paused').length === 2")
    assert ('POST', '/api/chat-ops/operations/op-game/pause', {'hours': 4}) in api.calls
    docs.get_by_role('button', name='Resume now').click()
    page.wait_for_function("!document.querySelector('[data-row=\"op-docs\"]').classList.contains('is-paused')")
    # Stop asks first; declining changes nothing.
    page.once('dialog', lambda d: d.dismiss())
    docs.get_by_role('button', name='Stop').click()
    assert page.locator('.sup-row').count() == 2
    page.once('dialog', lambda d: d.accept())
    docs.get_by_role('button', name='Stop').click()
    page.wait_for_function("document.querySelectorAll('.sup-row').length === 1")
    assert not errors


def test_defaults_save_discard_reset_and_rejected_save(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    _open(page, static_server, api)
    form = page.locator('#supDefaults')
    form.locator('[name=sys_display]').wait_for(state='attached')
    assert page.locator('#supSavebar').is_hidden() and page.locator('#supReset').is_disabled()
    assert not form.locator('[name=sys_display]').is_checked() and form.locator('[name=sys_processes]').is_checked()
    form.locator('label.sup-care', has_text='Keep the display awake').click()
    assert form.locator('[name=sys_display]').is_checked()
    assert 'Unsaved changes' in page.locator('#supSaveState').inner_text()
    page.click('#supDiscard')
    assert not form.locator('[name=sys_display]').is_checked() and page.locator('#supSavebar').is_hidden()
    form.locator('label.sup-care', has_text='Keep the display awake').click()
    form.locator('[name=paused_alert_hours]').fill('2')
    page.click('#supSave')
    page.wait_for_function("document.getElementById('supSavebar').hidden")
    expected = {k: v for k, v in BUILTIN.items() if k != 'agent'}
    assert _puts(api)[-1] == {**expected, 'paused_alert_hours': 2,
                              'system': {'processes': True, 'memory': False, 'display': True}}
    # An invalid value never leaves the page; a server refusal is shown, not swallowed.
    form.locator('[name=paused_alert_hours]').fill('')
    page.click('#supSave')
    assert 'Paused alert' in page.locator('#supSaveState').inner_text() and len(_puts(api)) == 1
    form.locator('[name=paused_alert_hours]').fill('13')
    page.click('#supSave')
    page.wait_for_function("document.getElementById('supSaveState').textContent.includes('Owner access required')")
    page.click('#supReset')
    assert not form.locator('[name=sys_display]').is_checked()
    assert form.locator('[name=paused_alert_hours]').input_value() == '6'
    assert not errors


def test_empty_list_and_disabled_feature_explain_themselves(page_ctx, static_server):
    page, errors = page_ctx
    api = FakeAPI()
    api.rows = []
    _open(page, static_server, api)
    page.locator('#supList .orch-empty-line').wait_for()
    assert 'Supervise this chat' in page.locator('#supList').inner_text()
    assert page.locator('#supDisplayNote').is_visible()  # the fake host still holds the display
    off = FakeAPI(disabled=True)
    page.unroute('**/api/**')
    _open(page, static_server, off)
    page.wait_for_function("document.getElementById('supNotice').textContent.includes('turned off')")
    assert page.locator('#supContent').is_hidden()
    assert not errors



@pytest.mark.parametrize('theme', ['dark', 'midnight', 'light', 'graphite', 'paper'])
def test_text_is_readable_in_each_theme(page_ctx, static_server, theme):
    """Status tones are border colours in the themes (greys on Midnight): every
    line, tag and button must still read, with a pause menu open."""
    page, errors = page_ctx
    page.add_init_script("localStorage.setItem('theme', %s)" % json.dumps(theme))
    _open(page, static_server, FakeAPI())
    page.locator('[data-row="op-game"]').get_by_role('button', name='Pause…').click()
    page.locator('.sup-pause-menu').wait_for()
    classes = page.evaluate('document.documentElement.className').split()
    assert theme + '-mode' in classes or 'theme-' + theme in classes
    assert low_contrast_text(page) == []
    assert not errors
