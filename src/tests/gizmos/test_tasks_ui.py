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
