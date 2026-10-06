"""Real-Chromium rendering of production action cards (plan C1).

Drives the actual chat page with an isolated API (no Flask, provider,
subprocess, model, or login): the fake ``/api/chat`` fulfills immediately
with production-shaped SSE whose response text carries card markup, and the
test asserts the painted card DOM. Covers choice structure, escaping,
locked state, and watch bars through the real formatMessage ->
placeholder -> owner-renderer path.
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import pytest

from .test_shared_diff_modal import (  # noqa: F401
    IsolatedAPI,
    apply_request_guard,
    browser,
    static_server,
)


def _sse(*events):
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events)


def _fulfill_card(route, sid, text):
    route.fulfill(status=200, content_type="text/event-stream",
                  body=_sse({"type": "session", "session_id": sid},
                            {"type": "response", "success": True,
                             "response": text, "session_id": sid},
                            {"type": "done"}))


CARD_CHOICE = (
    "Here:\n<cuttle_action_form>"
    '{"mode":"choice","title":"Pick <b>one</b> & \\"two\\"",'
    '"options":[{"id":"a","label":"A&P"},{"id":"cancel","label":"No"}]}'
    "</cuttle_action_form>"
)

CARD_LOCKED = (
    "<cuttle_action_form>"
    '{"mode":"choice","title":"Done","locked":true,'
    '"options":[{"id":"a","label":"A"}]}'
    "</cuttle_action_form>"
)

CARD_WATCH = (
    "<cuttle_action_form>"
    '{"mode":"choice","title":"Bake",'
    '"watch":{"id":"job","url":"/output/x.json",'
    '"snapshot":{"state":"running","percent":42,"label":"Half",'
    '"bars":[{"id":"all","percent":42},{"label":"w1","percent":10}]}},'
    '"options":[{"id":"a","label":"A","action":"__watch_resume__"}]}'
    "</cuttle_action_form>"
)


class CardWorld:
    """Fake server: scripted card replies, persisted-row history model."""

    def __init__(self, replies):
        now = time.time()
        self.history = [
            {"id": 1, "role": "user", "content": "seed",
             "timestamp": (now - 30) * 1000},
        ]
        self.next_id = 2
        self.replies = list(replies)

    def _append(self, role, content):
        row = {"id": self.next_id, "role": role, "content": content,
               "timestamp": time.time() * 1000}
        self.next_id += 1
        self.history.append(row)

    def handle(self, route):
        req = route.request
        parsed = urlparse(req.url)
        path = parsed.path
        if req.method == "POST" and path == "/api/chat":
            try:
                body = json.loads(req.post_data or "{}")
            except ValueError:
                body = {}
            self._append("user", body.get("message", ""))
            text = self.replies.pop(0) if self.replies else "no card"
            self._append("assistant", text)
            _fulfill_card(route, 42, text)
            return
        if path.endswith("/messages"):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True,
                                           "session_name": "Cards",
                                           "messages": list(self.history)}))
            return
        if path == "/api/chat-live-status":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"active": False,
                                           "generating": False,
                                           "status": None}))
            return
        if path == "/api/auth/sessions":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(
                              {"success": True,
                               "sessions": [{"id": 42,
                                             "session_name": "Cards"}]}))
            return
        IsolatedAPI().handle(route)


def _open_card_chat(browser, static_server, world):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    apply_request_guard(page.context, static_server)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", world.handle)
    page.route(f"{static_server}/qa-host.html", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             " src=\"/chat_page.html?chat=42\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator("#chatMessages .message.user").first.wait_for(
        state="visible")
    return page, frame, errors


def _send_and_wait_card(frame, text):
    frame.locator("#chatInput").fill(text)
    frame.locator("#sendButton").click()
    card = frame.locator(".cuttle-action-form").last
    card.wait_for(state="visible")
    return card


def test_choice_card_renders_escaped(browser, static_server):
    world = CardWorld([CARD_CHOICE])
    page, frame, _errors = _open_card_chat(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show choice")
        assert card.locator(".cuttle-action-form-title").inner_text() == \
            'Pick <b>one</b> & "two"'
        assert card.locator("b").count() == 0  # title text is escaped
        assert card.locator('[data-action-form-option="a"]').inner_text() == "A&P"
        cancel = card.locator('[data-action-form-option="cancel"]')
        assert cancel.get_attribute("data-action-form-cancel") == "1"
        assert "cuttle-button--primary" not in (cancel.get_attribute("class") or "")
    finally:
        page.close()


def test_locked_card_disabled_with_status(browser, static_server):
    world = CardWorld([CARD_LOCKED])
    page, frame, _errors = _open_card_chat(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show locked")
        assert card.get_attribute("data-locked") == "1"
        assert "cuttle-action-form--locked" in (card.get_attribute("class") or "")
        assert card.locator('[data-action-form-option="a"]').is_disabled()
        status = card.locator(".cuttle-action-form-status")
        assert status.count() == 1
        assert status.get_attribute("hidden") is None
    finally:
        page.close()


def test_watch_card_multi_bar(browser, static_server):
    world = CardWorld([CARD_WATCH])
    page, frame, _errors = _open_card_chat(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show watch")
        items = card.locator(".progress-bar-item")
        assert items.count() == 2
        assert card.locator(".progress-bar-item--primary").count() == 1
        assert card.locator(".progress-bar-item--worker").count() == 1
        assert "Half" in card.locator(".cuttle-action-form-progress-label").inner_text()
    finally:
        page.close()


def test_frame_grid_live_watch_mobile_and_terminal(browser, static_server):
    grid = {'total': 240, 'inventory': 'verified', 'workers': ['tower', 'laptop'],
            'cells': [{'frame': n, 'state': 'completed' if n < 100 else 'rendering' if n < 120 else 'pending',
                       'worker': 'tower' if n < 60 else 'laptop' if n < 120 else '',
                       'gap_fill': n in (98, 99)} for n in range(1, 241)]}
    status = {'state': 'running', 'percent': 41, 'label': 'Trailer — 99/240 frames', 'grid': grid,
              'bars': [{'id': 'overall', 'label': 'Overall', 'percent': 41, 'kind': 'primary'},
                       {'id': 'tower', 'label': 'Tower · RTX 3080', 'percent': 60, 'kind': 'worker'},
                       {'id': 'laptop', 'label': 'Laptop · RTX 4070', 'percent': 40, 'kind': 'worker'}]}
    spec = {'mode': 'choice', 'title': 'Render trailer', 'watch': {
        'id': 'frames', 'url': '/output/frames-status.json', 'interval_ms': 1500, 'snapshot': status},
        'options': [{'id': 'park', 'label': "I'll reply", 'action': '__watch_park__'}]}
    world = CardWorld(['<cuttle_action_form>' + json.dumps(spec) + '</cuttle_action_form>'])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    page.route(static_server + '/output/frames-status.json*', lambda r: r.fulfill(json=status))
    page.route(static_server + '/api/action-form/run', lambda r: r.fulfill(json={
        'success': True, 'toast': 'Watching render', 'lock': 'form', 'selected': ['park'],
        'session_id': '42', 'actions': ['__watch_park__']}))
    try:
        card = _send_and_wait_card(frame, 'render frames')
        assert card.locator('.watch-cell').count() == 240
        assert card.locator('.watch-cell.is-marked').count() == 2
        assert 'gap-fill' in card.locator('.watch-cell').nth(97).get_attribute('data-tooltip')
        from pathlib import Path
        previews = Path(__file__).resolve().parents[3] / 'temp' / 'frame-grid'
        previews.mkdir(parents=True, exist_ok=True)
        card.screenshot(path=str(previews / 'desktop.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        assert card.locator('.watch-grid').evaluate('(el) => el.scrollWidth <= el.clientWidth')
        card.screenshot(path=str(previews / 'mobile.png'))
        card.locator('[data-action-form-option="park"]').click()
        status['state'] = 'done'
        status['percent'] = 100
        status['label'] = 'Trailer done — 240/240 frames'
        for cell in grid['cells']:
            cell['state'] = 'completed'
        from playwright.sync_api import expect
        expect(card.locator('.watch-cell.is-completed')).to_have_count(240, timeout=15000)
        assert not errors
    finally:
        page.close()


def test_markdown_image_and_routing_chip_survive_history_reload(browser, static_server):
    badge = {'agent': 'codex', 'model': 'test-model', 'kind': 'fallback',
             'reason': 'Previous agent unavailable; task continued.',
             'initial_agent': 'cursor', 'initial_model': 'auto', 'effort': 'high'}
    text = 'Done.\n\n![Frame grid preview](/output/shared/frame-grid.png "240 frames")'

    class RoutedWorld(CardWorld):
        def handle(self, route):
            if route.request.method == 'POST' and urlparse(route.request.url).path == '/api/chat':
                self._append('user', 'render frames')
                self._append('assistant', text)
                self.history[-1]['metadata'] = {'routing_badge': badge}
                route.fulfill(status=200, content_type='text/event-stream', body=_sse(
                    {'type': 'session', 'session_id': 42},
                    {'type': 'response', 'success': True, 'response': text, 'session_id': 42,
                     'routing_badge': badge}, {'type': 'done'}))
                return
            super().handle(route)

    world = RoutedWorld([])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    # Small valid image: no external requests or live media/API dependency.
    import base64
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=')
    page.route(static_server + '/output/shared/frame-grid.png', lambda r: r.fulfill(content_type='image/png', body=png))
    try:
        frame.locator('#chatInput').fill('show routed result')
        frame.locator('#sendButton').click()
        from playwright.sync_api import expect
        chip = frame.locator('.routing-selection-chip').last
        expect(chip).to_contain_text('Fallback')
        assert 'Codex' not in chip.inner_text()
        agent_chip = frame.locator('.message.assistant .slash-command-chip--palette-codex').last
        expect(agent_chip).to_contain_text('Codex')
        expect(agent_chip).to_contain_text('test-model')
        expect(agent_chip).to_contain_text('high')
        assert chip.locator('b').count() == 0
        thumb = frame.locator('.cuttle-media-thumb').last
        expect(thumb).to_be_visible()
        expect(thumb.locator('img')).to_have_attribute('src', '/output/shared/frame-grid.png')
        expect(thumb.locator('img')).to_have_js_property('naturalWidth', 1)
        assert '!Frame grid preview' not in frame.locator('.message.assistant').last.inner_text()
        from pathlib import Path
        previews = Path(__file__).resolve().parents[3] / 'temp' / 'frame-grid'
        previews.mkdir(parents=True, exist_ok=True)
        frame.locator('.message.assistant .message-header-row').last.screenshot(path=str(previews / 'routing-chip.png'))
        page.reload(wait_until='domcontentloaded')
        expect(frame.locator('.routing-selection-chip')).to_have_count(1)
        agent_chip = frame.locator('.message.assistant .slash-command-chip--palette-codex').last
        expect(agent_chip).to_contain_text('test-model')
        expect(agent_chip).to_contain_text('high')
        expect(frame.locator('.cuttle-media-thumb')).to_have_count(1)
        page.set_viewport_size({'width': 390, 'height': 844})
        chip = frame.locator('.routing-selection-chip')
        assert chip.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        assert not errors
    finally:
        page.close()


def test_segmented_status_chip_truncation_tooltips_and_error(browser, static_server):
    long_error = 'Request failed: ' + 'The selected agent could not complete this request. ' * 8
    world = CardWorld([long_error])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator('#chatInput').fill('show failed turn')
        frame.locator('#sendButton').click()
        from playwright.sync_api import expect
        chip = frame.locator('.cuttle-status-chip--error').last
        expect(chip).to_be_visible()
        expect(chip.locator('.slash-chip-seg')).to_have_count(3)
        expect(chip.locator('.status-chip-name')).to_have_text('Error')
        message = chip.locator('.status-chip-message')
        assert message.evaluate('(el) => el.scrollWidth > el.clientWidth')
        assert long_error.strip() in message.get_attribute('data-tooltip')
        tooltip = frame.locator('#cuttle-shared-tooltip')
        # The reply may still be auto-scrolling when it first renders; a
        # single hover can land where the chip was. Re-hover until shown.
        for _attempt in range(5):
            page.mouse.move(0, 0)
            message.hover()
            try:
                expect(tooltip).to_contain_text(long_error.strip(), timeout=1500)
                break
            except AssertionError:
                continue
        expect(tooltip).to_contain_text(long_error.strip())
        page.set_viewport_size({'width': 390, 'height': 844})
        assert chip.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
        expect(chip.locator('.status-chip-icon')).to_be_visible()
        expect(chip.locator('.status-chip-name')).to_be_visible()
        page.reload(wait_until='domcontentloaded')
        expect(frame.locator('.cuttle-status-chip--error')).to_have_count(1)
        assert not errors
        # Preview all supported types with clean sample messages using production renderer/CSS.
        page.set_viewport_size({'width': 900, 'height': 700})
        frame.locator('#chatMessages').evaluate('''el => {
            const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
            const examples = [
                ['router', 'Small code change'],
                ['reroute', 'Switched to a stronger model'],
                ['fallback', 'Previous agent unavailable; task continued'],
                ['warning', 'Default agent · Routing service unavailable'],
                ['error', 'Agent turn failed · See the reply for details']
            ];
            el.innerHTML = '<div id="status-preview" style="display:flex;flex-direction:column;gap:12px;padding:20px;width:440px;max-width:100%;box-sizing:border-box">'
                + examples.map(([kind,text]) => '<div>' + window.CuttleChatMessages.renderStatusChipHtml(kind,text,text,esc) + '</div>').join('') + '</div>';
        }''')
        from pathlib import Path
        previews = Path(__file__).resolve().parents[3] / 'temp' / 'frame-grid'
        previews.mkdir(parents=True, exist_ok=True)
        frame.locator('#status-preview').screenshot(path=str(previews / 'status-chips.png'))
    finally:
        page.close()


def test_progress_grid_is_job_agnostic(browser, static_server):
    names = ['auth', 'billing', 'search', 'sync', 'export', 'upload']
    cells = [{'key': f'test_{n}_{i}', 'group': f'shard-{i % 3 + 1}',
              'state': 'failed' if i == 7 else 'running' if i in (20, 21) else 'completed' if i < 20 else 'pending',
              'marked': i == 5, **({'note': 'AssertionError: 402 != 200'} if i == 7 else {})}
             for i, n in enumerate(names * 8)]
    grid = {'unit': 'test', 'title': 'Test suite', 'marked_label': 'retried', 'total': len(cells),
            'groups': ['shard-1', 'shard-2', 'shard-3'], 'cells': cells}
    spec = {'mode': 'choice', 'title': 'Run tests', 'watch': {
        'id': 'tests', 'url': '/output/tests-status.json', 'interval_ms': 1500, 'snapshot': {
            'state': 'running', 'percent': 42, 'label': '20/48 tests', 'grid': grid}},
        'options': [{'id': 'park', 'label': "I'll reply", 'action': '__watch_park__'}]}
    world = CardWorld(['<cuttle_action_form>' + json.dumps(spec) + '</cuttle_action_form>'])
    page, frame, errors = _open_card_chat(browser, static_server, world)
    page.route(static_server + '/output/tests-status.json*', lambda r: r.fulfill(json=spec['watch']['snapshot']))
    try:
        card = _send_and_wait_card(frame, 'run tests')
        assert card.locator('.watch-cell').count() == 48
        tip = card.locator('.watch-cell').nth(7).get_attribute('data-tooltip')
        assert tip == 'Test test_billing_7 · failed · shard-2 · AssertionError: 402 != 200'
        text = card.inner_text().lower()
        assert 'test suite · 48 tests' in text and 'retried' in text
        assert 'frame' not in text and 'gap-fill' not in text and 'missing' not in text
        from pathlib import Path
        previews = Path(__file__).resolve().parents[3] / 'temp' / 'frame-grid'
        previews.mkdir(parents=True, exist_ok=True)
        card.screenshot(path=str(previews / 'tests-grid.png'))
        assert not errors
    finally:
        page.close()
