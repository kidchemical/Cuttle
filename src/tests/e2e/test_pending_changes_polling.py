"""Periodic pending changes have one owner per project in the real shell.

Native timers and fresh guarded contexts; no Git writes or live API. Keep
standalone polling, restored project registration, and distinct-path routing.
"""
import json

import pytest

from .test_chat_cost_profile import CostAPI, build_history, fresh_context
from .test_shared_diff_modal import browser, static_server  # noqa: F401


@pytest.mark.parametrize('distinct', [False, True])
def test_restored_panes_share_periodic_project_poll(browser, static_server, distinct):
    other = {'id': 2, 'name': 'Other', 'path': '/repo/Other', 'type': 'local'}
    projects = {103: other} if distinct else {}
    api = CostAPI(build_history(20), session_projects=projects)
    context, _ = fresh_context(browser, static_server, api)
    sids = [100, 101, 102, 103]
    layout = {'version': 2, 'orientation': 'horizontal', 'root': {
        'type': 'group', 'id': 'poll-root', 'orientation': 'horizontal',
        'children': [{'type': 'leaf', 'id': f'p-{sid}',
                      'page': f'/chat_page.html?chat={sid}', 'flex': '1 1 0%'}
                     for sid in sids]}}
    context.add_init_script('localStorage.setItem("shell_split_layout", '
                            + json.dumps(json.dumps(layout)) + ')')
    try:
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(static_server + '/app_shell.html', wait_until='domcontentloaded')
        page.wait_for_function('() => typeof getOpenPaneSessions === "function" '
                               '&& getOpenPaneSessions().length === 4')
        frames = [f for f in page.frames if 'chat_page.html?chat=' in f.url]
        assert len(frames) == 4
        for f in frames:
            f.locator('#pendingChangesHost [data-pending-panel]').wait_for(state='visible')
            assert '1 file' in f.locator('#pendingChangesHost [data-pc="meta"]').inner_text()
        page.wait_for_timeout(1000)  # drain initial registrations/refreshes
        start = len(api.requests)
        page.wait_for_timeout(13000)  # one native 12s period + boundary margin
        paths = [(r['query'].get('path') or [''])[0] for r in api.requests[start:]
                 if r['path'] == '/api/git/pending-changes']
        assert paths.count('/repo/Cuttle') == 1, paths
        assert paths.count('/repo/Other') == int(distinct), paths
        for f in frames:
            f.locator('#pendingChangesHost [data-pending-panel]').wait_for(state='visible')
        assert errors == []
    finally:
        context.close()


def test_standalone_retains_periodic_pending_scan(browser, static_server):
    api = CostAPI(build_history(20))
    context, _ = fresh_context(browser, static_server, api)
    try:
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(static_server + '/chat_page.html?chat=42', wait_until='domcontentloaded')
        page.locator('#pendingChangesHost [data-pending-panel]').wait_for(state='visible')
        page.wait_for_timeout(1000)
        start = len(api.requests)
        page.wait_for_timeout(13000)
        polls = [r for r in api.requests[start:] if r['path'] == '/api/git/pending-changes']
        assert len(polls) == 1, polls
        assert errors == []
    finally:
        context.close()
