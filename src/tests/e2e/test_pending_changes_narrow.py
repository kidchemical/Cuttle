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
