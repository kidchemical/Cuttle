"""Browser acceptance for tool-driven Tasks and attributed history, offline."""
import json
from pathlib import Path
from urllib.parse import urlparse
import pytest
from tests.browser_guard import launch_chromium

SCRIPT = Path(__file__).resolve().parents[2] / 'web/js/gizmos/tasks_gizmo.js'


def test_tasks_render_tool_progress_history_and_shared_edits():
    playwright = pytest.importorskip('playwright.sync_api')
    row = {'id': 'plan', 'type': 'tasks', 'title': 'Tool plan', 'scope': 'session',
           'status': 'active', 'edit_mode': 'shared', 'revision': 1,
           'payload': {'items': [{'id': 'a', 'text': 'Verify', 'done': False}]}}
    state = {'revision': 1, 'patches': [], 'rows': [row]}
    fixture = '''<div id="chatWidgetsStrip" hidden></div>
      <script src="/tasks.js"></script><script>
      window.ctrl = CuttleTaskGizmos.create({getSessionId: () => 7, getProjectPath: () => ''});
      ctrl.refresh();</script>'''
    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        def route(route):
            path = urlparse(route.request.url).path
            if path == '/fixture':
                return route.fulfill(body=fixture, content_type='text/html')
            if path == '/tasks.js':
                return route.fulfill(body=SCRIPT.read_text(), content_type='text/javascript')
            if path == '/api/gizmos/tasks':
                return route.fulfill(json={'success': True, 'gizmos': state['rows'], 'revision': state['revision']})
            if path.endswith('/history'):
                return route.fulfill(json={'success': True, 'events': [{
                    'operation': 'create', 'created_at': '2026-10-07 12:00',
                    'actor': {'agent_id': 'codex <script>', 'session_id': '7',
                              'model': 'fixture-model', 'run_id': 'fixture-query'}}]})
            if path.endswith('/items/a'):
                body = route.request.post_data_json
                state['patches'].append(body)
                row['payload']['items'][0]['done'] = body['done']
                row['status'] = 'archived'
                state['rows'] = []
                state['revision'] += 1
                return route.fulfill(json={'success': True, 'gizmo': row})
            return route.abort()
        page.route('**/*', route)
        page.goto('http://tasks.test/fixture')
        page.wait_for_selector('[data-widget-id="plan"]')
        # Simulate a tool patch during a live turn; no assistant bubble is added.
        row['title'] = 'Progress from tool'
        state['revision'] = 2
        page.evaluate('ctrl.onRevision(2)')
        page.wait_for_function("document.getElementById('chatWidgetsStrip').textContent.includes('Progress from tool')")
        page.locator('[data-widget-history]').click()
        page.wait_for_function("document.getElementById('chatWidgetsStrip').textContent.includes('fixture-query')")
        text = page.locator('[aria-label="Task interaction history"]').inner_text()
        assert 'CH-000007' in text and 'codex <script>' in text and 'fixture-model' in text
        assert page.locator('[aria-label="Task interaction history"] script').count() == 0
        page.locator('input[type=checkbox]').check()
        page.wait_for_function("document.getElementById('chatWidgetsStrip').hidden")
        assert state['patches'] == [{'done': True, 'session_id': '7'}]
        assert not errors
        browser.close()


@pytest.mark.parametrize('width', [390, 1024])
def test_tasks_refresh_preserves_each_list_scroll_and_skips_unchanged_paint(width):
    playwright = pytest.importorskip('playwright.sync_api')
    def task_row(wid):
        return {'id': wid, 'type': 'tasks', 'title': wid, 'status': 'active',
                'payload': {'items': [{'id': str(i), 'text': f'Task {i}', 'done': False}
                                      for i in range(40)]}}
    rows = [task_row('first'), task_row('second')]
    state = {'revision': 1, 'rows': rows}
    fixture = '''<style>
      #chatWidgetsStrip { display:flex; width:280px; overflow:auto; }
      .chat-widget { min-width:260px; }
      .chat-widget-body { max-height:160px; overflow:auto; }
      .chat-widget-task { height:30px; }
      </style><div id="chatWidgetsStrip" hidden></div>
      <script src="/tasks.js"></script><script>
      window.sid = 7;
      window.ctrl = CuttleTaskGizmos.create({getSessionId: () => sid});
      ctrl.refresh();</script>'''
    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        page = browser.new_page(viewport={'width': width, 'height': 768})
        def route(route):
            path = urlparse(route.request.url).path
            if path == '/fixture':
                return route.fulfill(body=fixture, content_type='text/html')
            if path == '/tasks.js':
                return route.fulfill(body=SCRIPT.read_text(), content_type='text/javascript')
            if path == '/api/gizmos/tasks':
                return route.fulfill(json={'success': True, 'gizmos': state['rows'],
                                           'revision': state['revision']})
            return route.abort()
        page.route('**/*', route)
        try:
            page.goto('http://tasks.test/fixture')
            page.locator('[data-widget-id="first"] [data-widget-toggle]').click()
            page.locator('[data-widget-id="second"] [data-widget-toggle]').click()
            page.evaluate('''() => {
                const strip = document.getElementById('chatWidgetsStrip');
                const bodies = strip.querySelectorAll('.chat-widget-body');
                bodies[0].scrollTop = 180;
                bodies[1].scrollTop = 360;
                strip.scrollLeft = 120;
                window.originalBody = bodies[0];
            }''')
            page.evaluate('ctrl.refresh()')
            assert page.locator('.chat-widget-body').first.evaluate(
                '(el) => el === window.originalBody')
            rows[1]['payload']['items'][2]['done'] = True
            state['revision'] += 1
            page.evaluate('ctrl.refresh()')
            assert page.locator('.chat-widget-body').evaluate_all(
                '(els) => els.map(el => el.scrollTop)') == [180, 360]
            assert page.locator('#chatWidgetsStrip').evaluate('(el) => el.scrollLeft') == 120
            assert page.locator('[data-widget-id="second"] [data-item-id="2"] input').is_checked()
            # A different chat must not inherit the old list's scroll offset.
            page.evaluate('sid = 8; ctrl.refresh()')
            assert page.locator('.chat-widget-body').first.evaluate('(el) => el.scrollTop') == 0
        finally:
            browser.close()
