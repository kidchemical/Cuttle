"""Studio playbook view (orchestrator Org chart tab) in Chromium.

Studio: tiers as columns in escalation order, identity dot + harness name,
rule chips from stored fields, members jump to their ladder row. Classic: the
chart host stays hidden and the ladder/list is unchanged. The fake API answers
the agent catalog (with colours), the experimental flag and the playbook.
Screenshots for the side-by-side go to temp/studio_shots/L9/ (gitignored).
"""
import copy
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.e2e.test_orchestrator_apps import FakeAPI, PROFILES
from tests.e2e.test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401

SHOTS = Path(__file__).resolve().parents[3] / "temp" / "studio_shots" / "L9"

# Coloured catalog rows (the real rows carry `color` from each harness manifest).
AGENTS = [
    {'id': 'codex', 'label': 'Codex', 'color': '#19c37d', 'supports_model_pin': True, 'supports_effort': True,
     'models': ['gpt-5.6'], 'efforts': ['low', 'medium', 'high'], 'model_efforts': {}},
    {'id': 'cursor', 'label': 'Cursor Agent', 'color': '#4c8dff', 'supports_model_pin': True, 'supports_effort': False,
     'models': ['auto', 'grok-4.6'], 'efforts': [], 'model_efforts': {}},
    {'id': 'muse', 'label': 'Muse Code', 'color': '#a855f7', 'supports_model_pin': True, 'supports_effort': False,
     'models': ['muse-code'], 'efforts': [], 'model_efforts': {}},
    {'id': 'claude', 'label': 'Claude Code', 'color': '#d97757', 'supports_model_pin': True, 'supports_effort': True,
     'models': ['sonnet', 'claude-opus-5-5'], 'efforts': ['low', 'medium', 'high'],
     'model_efforts': {'claude-opus-5-5': ['low', 'medium', 'high', 'xhigh', 'max']}},
]

PROFILE_ROWS = [
    {'id': 'codex-bugs', 'name': 'Bug hunter', 'avatar': '🐜', 'agent': 'codex', 'model': 'gpt-5.6', 'effort': 'low', 'builtin': False},
    {'id': 'cursor-grunt', 'name': 'Grunt', 'avatar': '🛠', 'agent': 'cursor', 'model': 'auto', 'effort': '', 'builtin': False},
    {'id': 'muse-log', 'name': 'Changelog', 'avatar': '📜', 'agent': 'muse', 'model': 'muse-code', 'effort': '', 'builtin': False},
    {'id': 'claude-sonnet', 'name': 'Sonnet fixer', 'avatar': '🦊', 'agent': 'claude', 'model': 'sonnet', 'effort': 'medium', 'builtin': False},
    {'id': 'claude-opus', 'name': 'Expert (Opus)', 'avatar': '🦉', 'agent': 'claude', 'model': 'claude-opus-5-5', 'effort': 'high', 'builtin': False},
]

# Four tiers, escalation order: workers → escalation → expert → final review.
CREW = {
    'schema': 2, 'id': 'release-crew', 'name': 'Release crew', 'description': 'Ship a release.', 'seed': False,
    'missing_profiles': [], 'updated_at': 1,
    'tiers': [
        {'id': 'workers', 'label': 'Workers', 'role': 'worker', 'use_for': 'Bugs, tests, changelog.',
         'members': [{'profile': 'codex-bugs', 'on': True}, {'profile': 'cursor-grunt', 'on': True},
                     {'profile': 'muse-log', 'on': True}]},
        {'id': 'escalation', 'label': 'Escalation', 'role': 'escalation', 'use_for': 'Repeat failures.',
         'members': [{'profile': 'claude-sonnet', 'on': True}]},
        {'id': 'expert', 'label': 'Expert', 'role': 'expert', 'use_for': 'Plans and reviews.',
         'members': [{'profile': 'claude-opus', 'on': True}]},
        {'id': 'final', 'label': 'Final review', 'role': 'expert', 'use_for': '',
         'members': [{'name': 'Codex high', 'agent': 'codex', 'model': 'gpt-5.6', 'effort': 'high', 'on': False}]},
    ],
    'limits': {'solo_attempts': 2, 'solo_minutes': 40, 'child_retries': 1, 'setup_retries': 2},
    'supervisor': {'every_sec': 900, 'agent': '', 'instructions': '', 'involve_user': 'blocked', 'nudge': 'agent',
                   'require_branch': '', 'require_path': '', 'forbid_branches': []},
    'notes': '',
}


def _flags_body(enabled, kill_switch=False):
    return json.dumps({
        'success': True, 'kill_switch': kill_switch,
        'flags': [{'id': 'chat_studio_display', 'label': 'Studio chat display', 'description': '', 'default': False,
                   'category': 'chat', 'risk': 'low', 'enabled': enabled, 'configured_enabled': enabled,
                   'needs_restart': False, 'blocked_by': []}],
    })


def _route(api, enabled, kill_switch=False):
    def handle(route):
        path = urlparse(route.request.url).path
        if path == '/api/experimental/flags':
            route.fulfill(status=200, content_type='application/json', body=_flags_body(enabled, kill_switch))
        elif path == '/api/agents':
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'success': True, 'agents': AGENTS}))
        else:
            api.handle(route)
    return handle


def _api():
    api = FakeAPI()
    api.profiles = copy.deepcopy(PROFILE_ROWS)
    api.playbooks = [copy.deepcopy(CREW)]
    return api


def _open(browser, static_server, *, studio, dark=False, width=1200, server=None, kill_switch=False):  # noqa: F811
    """studio = the browser's pref and cached flag; server = the flag the server reports (default: same)."""
    context = browser.new_context(viewport={'width': width, 'height': 950})
    apply_request_guard(context, static_server)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    api = _api()
    page.route('**/api/**', _route(api, studio if server is None else server, kill_switch))
    page.goto(static_server + '/orchestrator_page.html')
    page.evaluate("""([studio, dark]) => {
        localStorage.setItem('cuttleChatDisplay', studio ? 'studio' : 'classic');
        localStorage.setItem('cuttleChatStudioFlag', studio ? '1' : '0');
        localStorage.setItem('theme', dark ? 'dark' : 'light');
    }""", [studio, dark])
    page.reload()
    page.locator('#orchTiers .orch-tier').first.wait_for()
    return context, page, errors


def _chart_text(page):
    return page.evaluate("""() => [...document.querySelectorAll('#orchChart .sp-tier')].map(t => ({
        name: t.querySelector('.sp-tier-name').textContent.trim(),
        members: [...t.querySelectorAll('.sp-member')].map(m => m.className.replace('sp-member ', '') + '|' + m.innerText.trim().replace(/\\n+/g, ' / ')),
    }))""")


def test_studio_chart_shows_tiers_in_escalation_order(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=True)
    try:
        page.wait_for_selector('#orchChart .sp-tier')
        assert page.is_visible('#orchChart')
        assert page.is_visible('.orch-intro-studio') and page.is_hidden('.orch-intro-classic')
        names = page.eval_on_selector_all('#orchChart .sp-tier-name', 'els => els.map(e => e.textContent.trim())')
        assert names == ['Workers', 'Escalation', 'Expert', 'Final review']
        # Columns sit left to right in that order.
        lefts = page.eval_on_selector_all('#orchChart .sp-tier', 'els => els.map(e => e.getBoundingClientRect().left)')
        assert lefts == sorted(lefts)
        # Identity: the dot takes the harness colour; the harness name is the label.
        dot = page.locator('#orchChart .sp-tier').first.locator('.sp-member').first.locator('.agent-dot')
        assert dot.evaluate("el => getComputedStyle(el).backgroundColor") == 'rgb(25, 195, 125)'
        assert 'Codex' in page.locator('#orchChart .sp-tier').first.inner_text()
        # Profile name and model · effort are the quiet line under the harness.
        assert 'Bug hunter · gpt-5.6 · low' in page.locator('#orchChart .sp-member').first.inner_text()
        # Default is the first switched-on member; the off temporary agent says so.
        assert page.locator('#orchChart .sp-member.is-default').count() == 3  # one per tier that has an agent on
        off = page.locator('#orchChart .sp-member.is-off')
        assert off.count() == 1 and 'Off' in off.inner_text() and 'gpt-5.6 · high' in off.inner_text()
        # Rule chips come only from stored fields.
        chips = page.eval_on_selector_all('#orchChart .sp-chip', 'els => els.map(e => e.textContent.trim())')
        assert chips == ['Grind limit · 2 tries', 'No progress · 40 min',
                         'Escalate after 1 same-tier retry', 'Supervisor · every 15 min']
        assert page.locator('#orchChart').get_attribute('aria-label') == 'Playbook org chart'
        assert errors == []
    finally:
        context.close()


def test_stale_pref_or_kill_switch_renders_classic(browser, static_server):  # noqa: F811
    """A Studio pref with the flag off on the server (or the kill switch) is Classic; the pref is kept."""
    for kwargs in ({'server': False}, {'kill_switch': True}):
        context, page, errors = _open(browser, static_server, studio=True, **kwargs)
        try:
            page.wait_for_function("localStorage.getItem('cuttleChatStudioFlag') === '0'")
            assert page.is_hidden('#orchChart')
            assert page.locator('#orchChart .sp-tier').count() == 0
            assert page.evaluate("document.documentElement.classList.contains('cuttle-studio')") is False
            assert page.evaluate("localStorage.getItem('cuttleChatDisplay')") == 'studio'
            assert page.is_visible('.orch-intro-classic')
            assert errors == []
        finally:
            context.close()


def test_classic_keeps_the_ladder_and_no_chart(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=False)
    try:
        assert page.is_hidden('#orchChart')
        assert page.locator('#orchChart .sp-tier').count() == 0
        assert page.evaluate("document.documentElement.classList.contains('cuttle-studio')") is False
        assert page.is_visible('.orch-intro-classic') and page.is_hidden('.orch-intro-studio')
        assert 'Tiers run top to bottom' in page.locator('.orch-intro-classic').inner_text()
        assert page.locator('#orchTiers .orch-tier').count() == 4
        assert page.locator('#orchTiers .orch-tier').first.locator('.orch-tier-name').input_value() == 'Workers'
        assert errors == []
    finally:
        context.close()


def test_member_opens_its_ladder_row_from_keyboard(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=True)
    try:
        page.wait_for_selector('#orchChart .sp-tier')
        target = page.locator('#orchChart .sp-member-btn[data-jump-tier="1"][data-jump-member="0"]')
        target.focus()
        page.keyboard.press('Enter')
        active = page.evaluate("""() => { const el = document.activeElement;
            const row = el.closest('[data-member]'); const box = el.closest('.orch-tier');
            return [box ? box.dataset.index : null, row ? row.dataset.member : null]; }""")
        assert active == ['1', '0']
        assert errors == []
    finally:
        context.close()


def test_chart_follows_unsaved_edits_and_stacks_when_narrow(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=True)
    try:
        page.wait_for_selector('#orchChart .sp-tier')
        assert page.evaluate("getComputedStyle(document.querySelector('#orchChart .sp-tiers')).flexDirection") == 'row'
        page.fill('#orchTiers .orch-tier:nth-child(1) .orch-tier-name', 'Grunts')
        assert page.locator('#orchChart .sp-tier-name').first.text_content().strip() == 'Grunts'
        page.locator('#orchTiers .orch-tier:nth-child(1) .orch-switch').first.click()
        assert page.locator('#orchChart .sp-tier').first.locator('.sp-member.is-default').count() == 1  # next one takes over
        assert 'Off' in page.locator('#orchChart .sp-tier').first.locator('.sp-member.is-off').first.inner_text()
        page.set_viewport_size({'width': 640, 'height': 950})
        page.wait_for_function("getComputedStyle(document.querySelector('#orchChart .sp-tiers')).flexDirection === 'column'")
        assert errors == []
    finally:
        context.close()


def test_reduced_motion_stops_the_arrow_draw(browser, static_server):  # noqa: F811
    context, page, errors = _open(browser, static_server, studio=True)
    try:
        page.wait_for_selector('#orchChart .sp-arrow path')
        assert page.evaluate("getComputedStyle(document.querySelector('#orchChart .sp-arrow path')).animationName") == 'sp-draw'
        page.emulate_media(reduced_motion='reduce')
        page.reload()
        page.wait_for_selector('#orchChart .sp-arrow path')
        assert page.evaluate("getComputedStyle(document.querySelector('#orchChart .sp-arrow path')).animationName") == 'none'
        assert errors == []
    finally:
        context.close()


def test_side_by_side_screenshots(browser, static_server):  # noqa: F811
    SHOTS.mkdir(parents=True, exist_ok=True)
    for name, kwargs in {
        'playbook-studio': dict(studio=True),
        'playbook-studio-dark': dict(studio=True, dark=True),
        'playbook-studio-narrow': dict(studio=True, width=640),
        'playbook-classic': dict(studio=False),
    }.items():
        context, page, errors = _open(browser, static_server, **kwargs)
        try:
            page.locator('[data-tab="chart"]').click()
            page.wait_for_timeout(600)
            page.screenshot(path=str(SHOTS / f'{name}.png'), full_page=False)
            assert errors == []
        finally:
            context.close()
