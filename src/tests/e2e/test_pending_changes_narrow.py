"""Pending Changes strip adapts to pane width (container queries), not viewport.

Collapsed: header pieces never overlap the quick Commit button. Expanded: the
basename stays whole while the directory truncates, and per-row actions float
in on hover instead of eating path width. Wide panes keep the full chrome.
"""
import json

import pytest

from .test_chat_cost_profile import CostAPI, build_history, fresh_context
from .test_shared_diff_modal import browser, static_server  # noqa: F401

FILES = [
    {'path': 'src/web/js/a/deeply/nested/directory/panel.js', 'status': 'modified',
     'additions': 142, 'deletions': 37},
    {'path': 'README.md', 'status': 'added', 'additions': 1204, 'deletions': 0},
]


class NarrowAPI(CostAPI):
    def handle(self, route):
        from urllib.parse import urlparse
        if urlparse(route.request.url).path != '/api/git/pending-changes':
            return super().handle(route)
        data = {'success': True, 'project': {'id': 1, 'name': 'Cuttle', 'path': '/repo/Cuttle'},
                'repos': [{'repo_root': '/repo/Cuttle', 'branch': 'feature/a-long-branch-name',
                           'clean': False, 'total_files': 2, 'files': FILES,
                           'totals': {'files': 2, 'additions': 1346, 'deletions': 37}}]}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(data))


def _open(browser, static_server, width):
    context, _ = fresh_context(browser, static_server, NarrowAPI(build_history(4)))
    page = context.new_page()
    page.set_viewport_size({'width': width, 'height': 760})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(static_server + '/chat_page.html?chat=42', wait_until='domcontentloaded')
    page.locator('#pendingChangesHost [data-pending-panel]').wait_for(state='visible')
    return context, page, errors


HEADER_OVERLAP = """() => {
  const p = document.querySelector('#pendingChangesHost [data-pending-panel]');
  const box = (k) => p.querySelector('[data-pc="' + k + '"]').getBoundingClientRect();
  return box('stats').right <= box('commitQuick').left + 0.5;
}"""

ROW = '#pendingChangesHost .pending-changes-row[data-rel-path="src/web/js/a/deeply/nested/directory/panel.js"]'


@pytest.mark.parametrize('width', [300, 420])
def test_narrow_pane_compacts_collapsed_and_expanded(browser, static_server, width):
    context, page, errors = _open(browser, static_server, width)
    try:
        assert page.evaluate(HEADER_OVERLAP)
        assert not page.locator('#pendingChangesHost .pending-changes-title-lead').is_visible()
        assert '2 files' in page.locator('#pendingChangesHost [data-pc="meta"]').inner_text()

        page.locator('#pendingChangesHost [data-pc="toggle"]').click()
        assert not page.locator('#pendingChangesHost .pending-changes-select-hint').is_visible()
        assert not page.locator('#pendingChangesHost .pending-changes-branch-project').is_visible()
        name = page.locator(ROW + ' .pending-changes-file-name')
        assert name.evaluate('n => n.scrollWidth <= n.clientWidth')
        assert name.inner_text() == 'panel.js'

        page.mouse.move(0, 0)
        actions = page.locator(ROW + ' .pending-changes-row-actions')
        assert actions.evaluate('n => getComputedStyle(n).opacity') == '0'
        page.locator(ROW).hover()
        page.wait_for_function(
            'sel => getComputedStyle(document.querySelector(sel)).opacity === "1"',
            arg=ROW + ' .pending-changes-row-actions')
        assert errors == []
    finally:
        context.close()


def test_wide_pane_keeps_full_chrome(browser, static_server):
    context, page, errors = _open(browser, static_server, 900)
    try:
        assert page.locator('#pendingChangesHost .pending-changes-title').inner_text() == 'Pending changes'
        page.locator('#pendingChangesHost [data-pc="toggle"]').click()
        assert page.locator('#pendingChangesHost .pending-changes-select-hint').is_visible()
        assert page.locator(ROW + ' .pending-changes-ignore-btn').is_visible()
        assert page.locator(ROW + ' .pending-changes-row-actions').evaluate(
            'n => getComputedStyle(n).position') == 'static'
        assert 'Cuttle' in page.locator('#pendingChangesHost [data-pc="branch"]').inner_text()
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize('expanded', [False, True])
@pytest.mark.parametrize('fail_second', [False, True])
def test_shift_hover_batch_commits_and_partial_failure(browser, static_server, expanded, fail_second):
    context, page, errors = _open(browser, static_server, 900)
    plans, commits = [], []
    page.evaluate('window.batchToasts = []; window.showToast = message => window.batchToasts.push(message)')
    def plan(route):
        plans.append(route.request.post_data_json)
        route.fulfill(json={'success': True, 'commits': [
            {'message': 'Fix panel', 'files': [FILES[0]['path']]},
            {'message': 'Update guide', 'files': ['README.md']},
        ]})
    def commit(route):
        commits.append(route.request.post_data_json)
        route.fulfill(json={'success': not fail_second or len(commits) == 1, 'commit': 'abc123', 'error': 'Hook rejected'})
    page.route('**/api/git/suggest-commit-message', plan)
    page.route('**/api/git/commit', commit)
    button = page.locator('#pendingChangesHost [data-pc="commitQuick"]')
    panel = page.locator('#pendingChangesHost [data-pending-panel]')
    try:
        button.hover()
        page.keyboard.down('Shift')
        page.wait_for_function("document.querySelector('[data-pc=commitQuick]').textContent === 'Batch Commits'")
        page.keyboard.up('Shift')
        assert button.inner_text() == 'Commit'
        page.keyboard.down('Shift')
        page.mouse.move(0, 0)
        assert button.inner_text() == 'Commit'
        panel.hover()
        assert button.inner_text() == 'Batch Commits'
        page.keyboard.up('Shift')
        if expanded:
            panel.locator('[data-pc=toggle]').click()
            button = panel.locator('[data-pc=commitBtn]')
        button.hover()
        page.keyboard.down('Shift')
        assert button.inner_text() == 'Batch Commits'
        button.click(modifiers=['Shift'])
        page.wait_for_function("window.batchToasts.some(t => typeof t === 'string' && (t.startsWith('Batch stopped') || t === '2 commits created'))")
        toast = page.evaluate("window.batchToasts.find(t => typeof t === 'string' && (t.startsWith('Batch stopped') || t === '2 commits created'))")
        assert ('Batch stopped after 1' in toast) if fail_second else toast == '2 commits created'
        assert plans[0]['batch'] is True
        assert plans[0]['files'] == [f['path'] for f in FILES]
        assert [c['files'] for c in commits] == [[FILES[0]['path']], ['README.md']]
        assert [c['message'] for c in commits] == ['Fix panel', 'Update guide']
        assert errors == []
    finally:
        context.close()


def test_plain_commit_still_suggests_one_message(browser, static_server):
    context, page, errors = _open(browser, static_server, 900)
    committed = []
    def suggestion(route):
        assert not route.request.post_data_json.get('batch')
        route.fulfill(json={'success': True, 'message': 'Fix pending panel'})
    def commit(route):
        committed.append(route.request.post_data_json)
        route.fulfill(json={'success': True, 'commit': 'abc123'})
    page.route('**/api/git/suggest-commit-message', suggestion)
    page.route('**/api/git/commit', commit)
    try:
        with page.expect_response('**/api/git/commit'):
            page.locator('#pendingChangesHost [data-pc=commitQuick]').click()
        assert len(committed) == 1
        assert committed[0]['message'] == 'Fix pending panel'
        assert committed[0]['files'] == [f['path'] for f in FILES]
        assert errors == []
    finally:
        context.close()
