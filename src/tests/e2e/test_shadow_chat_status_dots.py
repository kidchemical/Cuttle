"""Shadow journey: history status dots over a REAL child Flask.

A turn finishes in a chat the user switched away from. Its history row must
drop the running spinner and show the green unread dot, without a refresh.
Only the executor is fake (S1 scenario queue); page, SSE, history and
live-status are production.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="S2 shadow journeys verified on Linux only")

from .test_shadow_chat_stop_resend import (  # noqa: F401  (fixtures)
    _counter,
    _new_chat,
    _open_guarded_page,
    _pump_until,
    _queue,
    _register_cookie,
    _release,
    _state,
    browser,
    shadow,
)

_ROW_JS = """(sid) => [...document.querySelectorAll('.chat-history-item')]
    .find((el) => String(el.dataset.sessionId).replace('db_session_', '') === String(sid))"""


def _row_state(page, sid):
    return page.evaluate(
        "(sid) => { const row = (" + _ROW_JS + ")(sid);"
        " if (!row) return null;"
        " return { running: row.classList.contains('is-running')"
        "            || !!row.querySelector('.history-running-icon'),"
        "          unread: row.classList.contains('has-unread')"
        "            && !!row.querySelector('.history-unread-icon') }; }",
        str(sid),
    )


def _wait_composer(page):
    page.wait_for_function(
        "() => { const el = document.getElementById('chatInput');"
        " return !!(el && !el.disabled); }", timeout=15000)


def _wait_open(page, sid):
    page.wait_for_function(
        "(sid) => new URLSearchParams(location.search).get('chat') === String(sid)",
        arg=str(sid), timeout=15000)
    _wait_composer(page)


def _send(page, text):
    page.locator("#chatInput").fill(text)
    page.locator("#sendButton").click()


def test_background_turn_finish_shows_unread_not_spinner(shadow, browser):
    _child, manifest = shadow
    origin, nonce = manifest["origin"], manifest["nonce"]
    token = _register_cookie(origin)
    tag = next(_counter)
    sid_a = _new_chat(origin, token, f"dots-a-{tag}")
    sid_b = _new_chat(origin, token, f"dots-b-{tag}")
    context, page, errors, _blocked, _posts = _open_guarded_page(
        browser, origin, token, {"width": 1280, "height": 800})
    held = None
    try:
        # B gets one finished turn so it is listed (empty chats are hidden).
        page.goto(f"{origin}/chat_page.html?chat={sid_b}",
                  wait_until="domcontentloaded")
        _wait_open(page, sid_b)
        page.get_by_text(f"s2 page dots-b-{tag}").first.wait_for(
            state="visible", timeout=20000)
        quick = _queue(origin, nonce, "success", "b seeded reply")
        _send(page, "/cursor seed b")
        page.get_by_text("b seeded reply").first.wait_for(
            state="visible", timeout=20000)
        _pump_until(page, lambda: _row_state(page, sid_b) == {
            "running": False, "unread": False}, 20000,
            f"B listed idle (last={_row_state(page, sid_b)})")
        assert _state(origin, nonce)["completed"].get(quick) == 1

        page.evaluate("(sid) => window.chatPageLoadSession(String(sid))", str(sid_a))
        _wait_open(page, sid_a)
        page.get_by_text(f"s2 page dots-a-{tag}").first.wait_for(
            state="visible", timeout=20000)
        pending_polls = []
        page.on("request", lambda r: pending_polls.append(r.url)
                if "/api/chat-pending-result" in r.url else None)
        held = _queue(origin, nonce, "success", "background reply done",
                      hold=True, status=["Working…"])
        _send(page, "/cursor work in the background")
        _pump_until(page, lambda: _state(origin, nonce)["started"].get(held, 0) == 1,
                    20000, "held turn started")
        _pump_until(page, lambda: (_row_state(page, sid_a) or {}).get("running"),
                    20000, f"A spins while running (last={_row_state(page, sid_a)})")

        page.evaluate("(sid) => (" + _ROW_JS + ")(sid).click()", str(sid_b))
        _wait_open(page, sid_b)
        page.wait_for_timeout(1500)
        assert (_row_state(page, sid_a) or {}).get("running"), (
            f"A must keep spinning after switching away: {_row_state(page, sid_a)}")
        # Navigation detaches before currentSessionId moves to B; the watch
        # must survive that and poll A (it used to exit before its first poll).
        assert any(f"session_id={sid_a}" in u for u in pending_polls), (
            "leaving a running chat must start its detached completion watch")

        _release(origin, nonce, held)
        held = None
        _pump_until(page, lambda: _row_state(page, sid_a) == {
            "running": False, "unread": True}, 30000,
            f"A shows unread, not spinner (last={_row_state(page, sid_a)})")
        assert _row_state(page, sid_b) == {"running": False, "unread": False}
        assert errors == [], errors
    finally:
        if held:
            try:
                _release(origin, nonce, held)
            except Exception:
                pass
        context.close()


def _offline_turn(origin, nonce, token, sid, response):
    from .test_shadow_chat_stop_resend import _api
    scenario = _queue(origin, nonce, 'success', response)
    status, body = _api(origin, token, 'POST', '/api/chat', {
        'session_id': sid, 'message': '/cursor offline observer test', 'stream': False,
    })
    assert status == 200 and body.get('success'), body
    assert _state(origin, nonce)['completed'].get(scenario) == 1


def test_unobserved_reply_recovers_after_reload_and_read_on_another_device(shadow, browser):
    from .test_shadow_chat_stop_resend import _api
    _child, manifest = shadow
    origin, nonce = manifest['origin'], manifest['nonce']
    token = _register_cookie(origin)
    tag = next(_counter)
    sid_a = _new_chat(origin, token, f'recovery-a-{tag}')
    sid_b = _new_chat(origin, token, f'recovery-b-{tag}')
    # No browser, watcher, or running-edge poll exists for either completion.
    _offline_turn(origin, nonce, token, sid_a, 'unobserved reply')
    _offline_turn(origin, nonce, token, sid_b, 'viewing another chat')
    context, page, errors, _blocked, _posts = _open_guarded_page(browser, origin, token)
    other = None
    try:
        page.goto(f'{origin}/chat_page.html?chat={sid_b}', wait_until='domcontentloaded')
        _wait_open(page, sid_b)
        _pump_until(page, lambda: _row_state(page, sid_a) == {'running': False, 'unread': True},
                    20000, 'fresh device recovers unobserved reply')
        page.reload(wait_until='domcontentloaded')
        _wait_open(page, sid_b)
        _pump_until(page, lambda: _row_state(page, sid_a) == {'running': False, 'unread': True},
                    20000, 'reload preserves unread')
        other, reader, reader_errors, _, _ = _open_guarded_page(browser, origin, token)
        reader.goto(f'{origin}/chat_page.html?chat={sid_a}', wait_until='domcontentloaded')
        _wait_open(reader, sid_a)
        reader.get_by_text('unobserved reply', exact=True).wait_for(timeout=20000)
        _pump_until(reader, lambda: not _api(origin, token, 'GET',
                    f'/api/auth/sessions/{sid_a}/messages')[1]['attention']['hasUnread'],
                    20000, 'actual displayed transcript is acknowledged')
        # The first device never mounted A; it learns about the read from server snapshots.
        page.reload(wait_until='domcontentloaded')
        _wait_open(page, sid_b)
        _pump_until(page, lambda: _row_state(page, sid_a) == {'running': False, 'unread': False},
                    20000, 'cross-device read clears old history dot')
        assert not errors and not reader_errors, (errors, reader_errors)
    finally:
        if other:
            other.close()
        context.close()


def test_question_attention_recovers_without_mount_and_clears_after_remote_answer(shadow, browser):
    import json
    _child, manifest = shadow
    origin, nonce = manifest['origin'], manifest['nonce']
    token = _register_cookie(origin)
    tag = next(_counter)
    sid_a = _new_chat(origin, token, f'question-a-{tag}')
    sid_b = _new_chat(origin, token, f'question-b-{tag}')
    card = '<cuttle_action_form>' + json.dumps({'id': 'offline-question', 'mode': 'choice',
        'resume': True, 'options': [{'id': 'yes', 'label': 'Yes'}]}) + '</cuttle_action_form>'
    _offline_turn(origin, nonce, token, sid_a, card)
    _offline_turn(origin, nonce, token, sid_b, 'another chat while a question waits')
    context, page, errors, _, _ = _open_guarded_page(browser, origin, token)
    def has_input():
        return page.evaluate('(sid) => {const row = (' + _ROW_JS + ')(sid);'
            ' return !!(row && row.classList.contains("has-input") && row.querySelector(".history-input-icon"));}', str(sid_a))
    try:
        page.goto(f'{origin}/chat_page.html?chat={sid_b}', wait_until='domcontentloaded')
        _wait_open(page, sid_b)
        _pump_until(page, has_input, 20000, 'unmounted question has a blue history dot')
        _offline_turn(origin, nonce, token, sid_a, 'question answered on another device')
        # Reload the viewing device, without navigating to/mounting A's card.
        page.reload(wait_until='domcontentloaded')
        _wait_open(page, sid_b)
        _pump_until(page, lambda: not has_input(), 20000, 'remote answer clears blue dot')
        _pump_until(page, lambda: _row_state(page, sid_a) == {'running': False, 'unread': True},
                    20000, 'the remote answer leaves an ordinary unread reply')
        assert not errors, errors
    finally:
        context.close()
