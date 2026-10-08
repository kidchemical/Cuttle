"""Real-Chromium fleet cards on a saved parent reply (history reload path)."""

from __future__ import annotations

from pathlib import Path

from .test_chat_action_card_render import CardWorld, _open_card_chat
from .test_shared_diff_modal import browser, static_server  # noqa: F401


def _card(n, outcome, summary, **kw):
    return {"fleet": True, "id": f"c{n}", "session_id": 900 + n, "handle": f"CH-000{900 + n}",
            "label": kw.pop("label", f"Agent {n}"), "agent": kw.pop("agent", "codex"),
            "model": kw.pop("model", "gpt-5.6"), "effort": kw.pop("effort", "low"),
            "outcome": outcome, "summary": summary, "detail": summary,
            "generating": outcome in ("running", "queued"),
            "started_at": "2026-10-04 10:00:00", "finished_at": "" if outcome == "running" else "2026-10-04 10:01:05",
            **kw}


def test_fleet_cards_render_outcomes_from_history(browser, static_server):
    world = CardWorld([])
    world._append("assistant", "Three reviewers finished; one host died.")
    world.history[-1]["metadata"] = {"subagents": [
        _card(1, "done", "Approved: the migration is idempotent and tests pass.", label="Reviewer", avatar="⚖️"),
        _card(2, "failed", "Codex exited 1: model quota exceeded for this account.", agent="cursor", model="grok-4.6", effort=""),
        _card(3, "lost", "Sub-agent turn ended without a reply: the process running it exited.", label="Scout " + "very " * 12 + "long"),
        _card(4, "cancelled", "Cancelled before replying"),
        _card(5, "running", "Working…", label="<img src=x onerror=alert(1)>"),
    ]}
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        fleet = frame.locator(".subagent-fleet").last
        fleet.wait_for(state="visible")
        cards = fleet.locator(".subagent-fleet-card")
        assert cards.count() == 5
        for state in ("done", "failed", "lost", "cancelled", "running"):
            assert fleet.locator(f".subagent-fleet-card.is-{state}").count() == 1
        assert cards.nth(0).get_attribute("data-chat-handle") == "CH-000901"
        assert "1m 05s" in cards.nth(0).inner_text()
        assert fleet.locator("img[src=x]").count() == 0
        previews = Path(__file__).resolve().parents[3] / "temp" / "fleet-cards"
        previews.mkdir(parents=True, exist_ok=True)
        fleet.screenshot(path=str(previews / "desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert fleet.evaluate("(el) => el.scrollWidth <= el.clientWidth + 1")
        fleet.screenshot(path=str(previews / "mobile.png"))
        assert not errors
    finally:
        page.close()


def test_live_status_refreshes_existing_card_without_duplicate_bubble(browser, static_server):
    world = CardWorld([])
    world._append('assistant', 'Review in progress.')
    world.history[-1]['metadata'] = {'subagents': [
        _card(1, 'running', 'Reading files', live_status_at='2026-10-05 03:00:00')]}
    fleet_row = world.history[-1]
    for i in range(6):
        world._append('assistant', f'Later reply {i}.\n\n' + 'More transcript text.\n\n' * 8)
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        page.set_viewport_size({'width': 1024, 'height': 768})
        card = frame.locator('.subagent-fleet-card').last
        card.get_by_text('Reading files', exact=True).wait_for()
        card.focus()
        card.evaluate('''el => {
            window.originalFleetCard = el;
            window.fleetFocusCalls = 0;
            const focus = HTMLElement.prototype.focus;
            HTMLElement.prototype.focus = function (...args) {
                if (this.matches('.subagent-fleet-card')) window.fleetFocusCalls++;
                return focus.apply(this, args);
            };
        }''')
        scroll_top = frame.locator('#chatMessages').evaluate('''el => {
            el.scrollTop = el.scrollHeight - el.clientHeight - 200;
            return el.scrollTop;
        }''')
        assert scroll_top > 500
        assert 'Updated:' in card.get_attribute('data-tooltip')
        fleet_row['metadata'] = {'subagents': [
            _card(1, 'running', 'Running tests <script>alert(1)</script>',
                  live_status_at='2026-10-05 03:00:01')]}
        frame.locator('html').evaluate("() => window.dispatchEvent(new Event('focus'))")
        card.get_by_text('Running tests <script>alert(1)</script>', exact=True).wait_for(timeout=20000)
        assert card.evaluate('(el) => document.activeElement === el')
        assert card.evaluate('(el) => el === window.originalFleetCard')
        assert frame.locator('html').evaluate('() => window.fleetFocusCalls') == 0
        assert abs(frame.locator('#chatMessages').evaluate('(el) => el.scrollTop') - scroll_top) <= 1
        assert frame.locator('.subagent-fleet-card').count() == 1
        assert frame.locator('.subagent-fleet-card script').count() == 0
        fleet_row['metadata'] = {'subagents': [_card(1, 'done', 'Tests passed')]}
        frame.locator('html').evaluate("() => window.dispatchEvent(new Event('focus'))")
        frame.locator('.subagent-fleet-card.is-done').get_by_text('Tests passed', exact=True).wait_for(timeout=20000)
        assert frame.locator('.subagent-fleet-card').count() == 1
        assert 'Updated:' not in card.get_attribute('data-tooltip')
        assert card.evaluate('(el) => el === window.originalFleetCard')
        assert frame.locator('html').evaluate('() => window.fleetFocusCalls') == 0
        assert abs(frame.locator('#chatMessages').evaluate('(el) => el.scrollTop') - scroll_top) <= 1
        assert not errors
    finally:
        page.close()


def test_unchanged_fleet_poll_keeps_cards_after_tooltip_enhancement(browser, static_server):
    world = CardWorld([])
    world._append('assistant', 'Finished review.')
    world.history[-1]['metadata'] = {'subagents': [_card(1, 'done', 'Tests passed')]}
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        page.set_viewport_size({'width': 1024, 'height': 768})
        card = frame.locator('.subagent-fleet-card')
        card.wait_for()
        card.evaluate('''el => {
            window.originalFleetCard = el;
            // ui_boot promotes native titles to custom tooltips on interaction.
            const icon = el.querySelector('.subagent-fleet-outcome');
            icon.setAttribute('data-tooltip', 'Done');
            icon.removeAttribute('title');
            window.messagesFetched = 0;
            const fetch = window.fetch;
            window.fetch = function (url, ...args) {
                return fetch.call(this, url, ...args).then(response => {
                    if (String(url).includes('/messages')) window.messagesFetched++;
                    return response;
                });
            };
        }''')
        frame.locator('html').evaluate("() => window.dispatchEvent(new Event('focus'))")
        frame.locator('html').evaluate('''() => new Promise(resolve => {
            const timer = setInterval(() => {
                if (window.messagesFetched) { clearInterval(timer); resolve(); }
            }, 20);
        })''')
        assert card.evaluate('(el) => el === window.originalFleetCard')
        assert not errors
    finally:
        page.close()
