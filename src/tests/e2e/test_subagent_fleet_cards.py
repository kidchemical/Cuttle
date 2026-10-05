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
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        card = frame.locator('.subagent-fleet-card').last
        card.get_by_text('Reading files', exact=True).wait_for()
        card.focus()
        assert 'Updated:' in card.get_attribute('data-tooltip')
        world.history[-1]['metadata'] = {'subagents': [
            _card(1, 'running', 'Running tests <script>alert(1)</script>',
                  live_status_at='2026-10-05 03:00:01')]}
        frame.locator('html').evaluate("() => window.dispatchEvent(new Event('focus'))")
        card.get_by_text('Running tests <script>alert(1)</script>', exact=True).wait_for(timeout=20000)
        assert card.evaluate('(el) => document.activeElement === el')
        assert frame.locator('.subagent-fleet-card').count() == 1
        assert frame.locator('.subagent-fleet-card script').count() == 0
        world.history[-1]['metadata'] = {'subagents': [_card(1, 'done', 'Tests passed')]}
        frame.locator('html').evaluate("() => window.dispatchEvent(new Event('focus'))")
        frame.locator('.subagent-fleet-card.is-done').get_by_text('Tests passed', exact=True).wait_for(timeout=20000)
        assert frame.locator('.subagent-fleet-card').count() == 1
        assert 'Updated:' not in card.get_attribute('data-tooltip')
        assert not errors
    finally:
        page.close()
