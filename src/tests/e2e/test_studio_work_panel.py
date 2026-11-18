"""Studio work panel, tray and header pill (L4) in Chromium, on a fixture turn.

The fixture is one assistant bubble with a linked Tasks list (one done, one
in progress with an owner, one open) and three fleet cards, mounted by the real
owners (chat_subagent_launchers.js, tasks_gizmo.js, chat_subagent_peek.js,
chat_orchestration.js). Only the network is mocked. Screenshots land in
`temp/studio_shots/l4/` for the side-by-side review.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.e2e.test_shared_diff_modal import browser, static_server  # noqa: F401

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect

REPO = Path(__file__).resolve().parents[3]
SHOTS = REPO / "temp" / "studio_shots" / "l4"
MOCK_SCENES = Path("/mnt/galaxy/Dev/Cuttle Promo/v4/project/film/scenes")

SCRIPTS = [
    "/js/shared/chat_display.js",
    "/js/shared/agent_identity.js",
    "/js/gizmos/tasks_gizmo.js",
    "/js/chat/chat_subagent_fleet.js",
    "/js/chat/chat_work_panel.js",
    "/js/chat/chat_subagent_launchers.js",
    "/js/chat/chat_subagent_peek.js",
    "/js/chat/chat_orchestration_model.js",
    "/js/chat/chat_orchestration.js",
]

FIXTURE = """<!DOCTYPE html><html><head>
<meta charset="utf-8">
<link rel="stylesheet" href="/css/ui_boot.css">
<script src="/js/shared/ui_boot.js"></script>
<link rel="stylesheet" href="/css/chat_page.css">
<link rel="stylesheet" href="/css/chat_supervision.css">
<link rel="stylesheet" href="/css/studio/studio_tokens.css">
<link rel="stylesheet" href="/css/studio/identity.css">
<link rel="stylesheet" href="/css/studio/work_panel.css">
<link rel="stylesheet" href="/css/studio/tray.css">
<style>
/* Stand-ins for the real Studio page: its surface, and a light reply card (the
   Studio bubble skin is not in this lane). The reply text sits above the panel. */
body{margin:0;min-height:100vh;background:var(--studio-surface-gradient);font-family:var(--font-sans)}
html body #typing-indicator .message-content-wrapper{background:var(--studio-bubble-bot-bg);color:var(--studio-bubble-bot-ink);border-radius:var(--studio-bubble-radius);box-shadow:var(--studio-shadow-card);padding:22px 24px;box-sizing:border-box}
html body #typing-indicator .message-content{font-size:17px;line-height:1.5;margin-bottom:16px;background:transparent;color:inherit;box-shadow:none;border:0;padding:0}
</style>
</head><body>
<main class="chat-main">
<div id="chatWidgetsStrip" style="padding:12px 24px" hidden></div>
<span class="chat-supervision-slot" id="chatOrchestrationSlot" hidden></span>
<div id="chatMessages" style="width:100%;max-width:820px;padding:24px;box-sizing:border-box">
  <div id="typing-indicator" class="message assistant" data-turn-id="turn40">
    <div class="message-content-wrapper"><div class="message-content">Plan's set. Three workers on it; a supervisor checks in every 15 minutes.</div></div>
  </div>
</div>
</main>
<div id="hdr" style="padding:24px"></div>
</body></html>"""

ORCH_BINDING = {"bound": True, "playbook_name": "Release crew", "playbook_id": "release-crew",
                "goal": "Ship v0.8", "hold": "", "agents": [], "bound_at": 1}

CATALOG = {"success": True, "agents": [
    {"id": "codex", "label": "Codex", "color": "#10a37f"},
    {"id": "claude", "label": "Claude Code", "color": "#d97757"},
    {"id": "cursor", "label": "Cursor", "color": "#4b5563"},
]}


def _tasks_payload(items):
    return {"success": True, "revision": 1, "gizmos": [{
        "id": "g-release", "type": "tasks", "status": "active", "title": "Release v0.8",
        "origin_turn_id": "turn40", "scope": "session", "session_id": 40, "edit_mode": "agent",
        "description": "", "linked_sessions": [], "payload": {"items": items}, "config": {"items": items},
    }]}


BASE_ITEMS = [
    {"id": "t1", "text": "Plan the release", "done": True},
    {"id": "t2", "text": "Write the changelog", "done": False, "in_progress": True,
     "owner": {"kind": "chat", "id": "CH-000041", "label": "Scout", "agent": "codex"}},
    {"id": "t3", "text": "Run the test suite", "done": False},
]

CARDS_JSON = json.dumps([
    {"handle": "CH-000041", "label": "Changelog", "display_name": "Changelog entries", "agent": "codex", "fleet": True,
     "outcome": "running", "summary": "Parsing the changelog entries", "generating": True, "turn_id": "turn40"},
    {"handle": "CH-000042", "label": "Fin", "display_name": "Fin", "agent": "claude", "fleet": True,
     "outcome": "done", "summary": "Shipped the draft notes", "turn_id": "turn40",
     "started_at": "2026-11-18 10:00:00", "finished_at": "2026-11-18 10:02:05"},
    {"handle": "CH-000043", "label": "Pine", "display_name": "Pine", "agent": "cursor", "fleet": True,
     "outcome": "queued", "summary": "Waiting to start", "turn_id": "turn40"},
])


def _open(browser, base, *, studio=True, width=1280, state=None, orch_binding=None, theme="light"):
    """A context with the mocked network, the fixture page, and the owners loaded."""
    context = browser.new_context(viewport={"width": width, "height": 860})
    store = {"tasks": state if state is not None else _tasks_payload(BASE_ITEMS), "hold_calls": [],
             "binding": orch_binding if orch_binding is not None else ORCH_BINDING}

    def route_api(route):
        url = route.request.url
        path = url.split("?", 1)[0]
        if path.endswith("/api/agents"):
            body = CATALOG
        elif path.endswith("/api/gizmos/tasks"):
            body = store["tasks"]
        elif path.endswith("/api/experimental/flags"):
            body = {"success": True, "kill_switch": False, "flags": []}
        elif "/api/orchestrator/bindings/40/hold" in path or "/api/orchestrator/bindings/40/resume" in path:
            store["hold_calls"].append((path.rsplit("/", 1)[-1], route.request.post_data or ""))
            body = {"success": True, "hold": "paused"}
        elif path.endswith("/api/orchestrator/bindings/40"):
            body = {"success": True, **store["binding"]}
        else:
            body = {"success": True}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    context.route("**/api/**", route_api)
    context.route("**/chat_page.html*", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<html><body>child chat</body></html>"))
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(base + "/settings_page.html")
    page.evaluate(
        """([studio, theme]) => {
            localStorage.setItem('cuttleChatDisplay', studio ? 'studio' : 'classic');
            localStorage.setItem('cuttleChatStudioFlag', '1');
            // ui_boot.js reads this before first paint: `dark` puts dark-mode on <html>.
            localStorage.setItem('theme', theme);
        }""",
        [studio, theme],
    )
    page.set_content(FIXTURE)
    for src in SCRIPTS:
        page.add_script_tag(url=base + src)
    page.evaluate(
        """(cards) => {
            const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
            const parseChatHandleToken = (t) => {
                const m = String(t).match(/^CH-0*(\\d+)$/i);
                return m ? { display: 'CH-' + String(m[1]).padStart(6, '0'), sessionId: m[1] } : null;
            };
            window.L = CuttleChatSubagentLaunchers.create({
                win: window, escapeHtmlInline: esc, parseChatHandleToken,
                avatarHtml: () => '', chipCategoryClass: () => '' });
            window.TG = CuttleTaskGizmos.create({ getSessionId: () => 40, getProjectPath: () => '' });
            window.PEEK = CuttleChatSubagentPeek.create({
                win: window, doc: document, parseChatHandleToken, navigate: () => {} });
            window.CARDS = cards;
            // The page's click → open call site (chat_page.js bindChatHandleLinkClicks).
            document.addEventListener('click', (e) => {
                const card = e.target.closest && e.target.closest('.subagent-fleet-card');
                if (!card) return;
                e.preventDefault();
                window.PEEK.open(card.getAttribute('data-chat-handle'), {
                    title: null, status: (() => { const st = card.querySelector('.subagent-fleet-outcome');
                        return st && (st.getAttribute('title') || st.getAttribute('data-tooltip')); })() });
            });
        }""",
        json.loads(CARDS_JSON),
    )
    return context, page, errors, store


def _mount_turn(page):
    page.evaluate("async () => { await TG.refresh(); L.syncLive(CARDS); }")
    expect(page.locator("#typing-indicator .work-panel")).to_have_count(1)


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(450)  # let the panel's reveal and the thread shift finish
    page.screenshot(path=str(SHOTS / name))


def test_studio_panel_mounts_in_the_turn_bubble(browser, static_server):  # noqa: F811
    context, page, errors, _store = _open(browser, static_server)
    try:
        _mount_turn(page)
        panel = page.locator("#typing-indicator .work-panel")
        expect(panel.locator(".work-panel-cols.is-two")).to_have_count(1)
        # Tasks left: one list, three rows, one done tick, one glowing unticked box.
        expect(panel.locator(".work-list")).to_have_count(1)
        expect(panel.locator(".chat-widget-task")).to_have_count(3)
        expect(panel.locator(".chat-widget-task.is-done")).to_have_count(1)
        row = panel.locator(".chat-widget-task.is-in-progress")
        expect(row).to_have_count(1)
        assert row.locator("input[type=checkbox]").is_checked() is False
        assert row.locator(".agent-identity-name").inner_text() == "Codex"  # agent name; the child title is the tooltip
        expect(row.locator(".work-owner")).to_have_attribute("data-tooltip", "Scout")
        # Owner dot in the harness colour from the catalog (#10a37f).
        dot_bg = row.locator(".agent-dot").evaluate("el => getComputedStyle(el).backgroundColor")
        assert dot_bg == "rgb(16, 163, 127)", dot_bg  # #10a37f
        # Glow: the in-progress box animates.
        assert row.locator("input[type=checkbox]").evaluate(
            "el => getComputedStyle(el).animationName") == "studio-work-glow"
        # Cards right: three, identity + name + one line, no progress bar.
        cards = panel.locator(".subagent-fleet-card")
        expect(cards).to_have_count(3)
        assert panel.locator("progress, [role=progressbar], .progress").count() == 0
        assert "%" not in panel.inner_text()
        outcome = cards.nth(1).locator(".subagent-fleet-outcome")
        assert (outcome.get_attribute("title") or outcome.get_attribute("data-tooltip")) == "Done"
        # The composer dock does not repeat a list the panel shows.
        assert page.is_hidden("#chatWidgetsStrip")
        _shot(page, "work-panel-studio.png")
        assert errors == []
    finally:
        context.close()


def _mean_channel(rgb: str) -> float:
    nums = [float(x) for x in re.findall(r"[\d.]+", rgb)[:3]]
    return sum(nums) / len(nums)


def test_dark_mode_panel_uses_the_dark_tokens(browser, static_server):  # noqa: F811
    """The page's own dark mode: ui_boot.js puts dark-mode on <html> from the theme pref."""
    context, page, errors, _store = _open(browser, static_server, theme="dark")
    try:
        assert page.evaluate("document.documentElement.classList.contains('dark-mode')")
        assert page.evaluate("document.documentElement.classList.contains('cuttle-studio')")
        _mount_turn(page)
        tasks_bg = page.locator(".work-panel-tasks").evaluate("el => getComputedStyle(el).backgroundColor")
        assert _mean_channel(tasks_bg) < 80, tasks_bg
        title_ink = page.locator(".work-list-title").evaluate("el => getComputedStyle(el).color")
        assert _mean_channel(title_ink) > 180, title_ink
        card_bg = page.locator("#typing-indicator .studio-fleet-card").first.evaluate(
            "el => getComputedStyle(el).backgroundColor")
        assert _mean_channel(card_bg) < 80, card_bg
        _shot(page, "work-panel-studio-dark.png")
        assert errors == []
    finally:
        context.close()


def test_live_tasks_change_repaints_in_place(browser, static_server):  # noqa: F811
    state = _tasks_payload(BASE_ITEMS)
    context, page, errors, store = _open(browser, static_server, state=state)
    try:
        _mount_turn(page)
        page.evaluate("() => { window.__panel = document.querySelector('#typing-indicator .work-panel'); }")
        newer = _tasks_payload([
            {"id": "t1", "text": "Plan the release", "done": True},
            {"id": "t2", "text": "Write the changelog", "done": True},
            {"id": "t3", "text": "Run the test suite", "done": False,
             "owner": {"kind": "chat", "id": "CH-000042", "label": "Fin", "agent": "claude"},
             "in_progress": True},
            {"id": "t4", "text": "Tag and publish", "done": False},
        ])
        store["tasks"] = newer
        page.evaluate("async () => { await TG.refresh(); }")
        panel = page.locator("#typing-indicator .work-panel")
        expect(panel.locator(".chat-widget-task")).to_have_count(4)
        expect(panel.locator(".chat-widget-task.is-done")).to_have_count(2)
        expect(panel.locator(".chat-widget-task.is-in-progress .agent-identity-name")).to_have_text("Claude Code")
        # New row eases in; the panel root is the same element (no remount).
        assert panel.locator(".chat-widget-task.is-entering").count() >= 1
        assert page.evaluate("() => document.querySelector('#typing-indicator .work-panel') === window.__panel")
        assert page.is_hidden("#chatWidgetsStrip")
        assert errors == []
    finally:
        context.close()


def test_card_opens_a_push_tray_and_escape_returns_focus(browser, static_server):  # noqa: F811
    context, page, errors, _store = _open(browser, static_server)
    try:
        _mount_turn(page)
        card = page.locator("#typing-indicator .subagent-fleet-card[data-chat-handle='CH-000041']")
        card.click()
        tray = page.locator(".subagent-peek")
        expect(tray).to_be_visible()
        assert tray.get_attribute("data-layout") == "push"
        assert page.evaluate("document.documentElement.classList.contains('cuttle-peek-push')")
        # The transcript column (.chat-main, as on the real page) shifts left by the tray's width; no backdrop.
        # The thread shift transitions; wait for it to settle before measuring.
        page.wait_for_function("parseFloat(getComputedStyle(document.querySelector('.chat-main')).marginRight) > 400")
        margin = page.evaluate("parseFloat(getComputedStyle(document.querySelector('.chat-main')).marginRight)")
        assert margin > 400, margin
        assert page.locator(".subagent-peek-backdrop").evaluate("el => getComputedStyle(el).display") == "none"
        thread_right = page.locator("#chatMessages").bounding_box()["x"] + page.locator("#chatMessages").bounding_box()["width"]
        assert thread_right <= 1280 - margin + 1
        # Parent header carries the identity (dot + name) of the clicked card.
        expect(tray.locator(".subagent-peek-identity .agent-identity-name")).to_have_text("Codex")
        # Header: line 1 "<agent> · <child title>", line 2 "<handle> · sub-agent of <parent Tasks list>".
        expect(tray.locator(".subagent-peek-line--1")).to_have_text(re.compile(r"Codex\s*·\s*Changelog entries"))
        expect(tray.locator(".subagent-peek-line--2")).to_have_text(
            re.compile(r"CH-000041\s*·\s*sub-agent of Release v0\.8"))
        # The open card keeps the selected state until the tray closes.
        expect(card).to_have_class(re.compile(r"\bis-selected\b"))
        assert card.get_attribute("aria-pressed") == "true"
        _shot(page, "tray-studio.png")
        page.keyboard.press("Escape")
        expect(tray).to_be_hidden()
        expect(card).not_to_have_class(re.compile(r"\bis-selected\b"))
        assert page.evaluate("document.documentElement.classList.contains('cuttle-peek-push')") is False
        focused = page.evaluate("() => document.activeElement && document.activeElement.getAttribute('data-chat-handle')")
        assert focused == "CH-000041", focused
        assert errors == []
    finally:
        context.close()


def test_narrow_pane_overlays_and_stacks(browser, static_server):  # noqa: F811
    context, page, errors, _store = _open(browser, static_server, width=700)
    try:
        page.evaluate("() => { document.getElementById('chatMessages').style.width = '560px'; }")
        _mount_turn(page)
        panel = page.locator("#typing-indicator .work-panel")
        # Stacked by the container query (the markup class is always there): cards sit under the tasks.
        tasks_box = panel.locator(".work-panel-tasks").bounding_box()
        cards_box = panel.locator(".work-panel-cards").bounding_box()
        assert cards_box["y"] >= tasks_box["y"] + tasks_box["height"] - 1, (tasks_box, cards_box)
        panel.locator(".subagent-fleet-card[data-chat-handle='CH-000041']").click()
        tray = page.locator(".subagent-peek")
        expect(tray).to_be_visible()
        assert tray.get_attribute("data-layout") == "side"
        assert page.evaluate("document.documentElement.classList.contains('cuttle-peek-push')") is False
        assert page.locator(".subagent-peek-backdrop").evaluate("el => getComputedStyle(el).display") != "none"
        _shot(page, "narrow-studio.png")
        assert errors == []
    finally:
        context.close()


def test_classic_keeps_the_fleet_block_and_the_dock(browser, static_server):  # noqa: F811
    context, page, errors, _store = _open(browser, static_server)
    try:
        page.evaluate("() => CuttleChatDisplay.setMode('classic')")
        page.evaluate("async () => { await TG.refresh(); L.syncLive(CARDS); }")
        assert page.locator("#typing-indicator .work-panel").count() == 0
        expect(page.locator("#typing-indicator .subagent-fleet")).to_have_count(1)
        assert page.evaluate("document.documentElement.classList.contains('cuttle-studio')") is False
        expect(page.locator("#chatWidgetsStrip")).to_be_visible()
        # Classic dock rows carry no Studio hooks.
        assert page.locator("#chatWidgetsStrip .is-in-progress, #chatWidgetsStrip .agent-dot").count() == 0
        _shot(page, "work-panel-classic.png")
        assert errors == []
    finally:
        context.close()


def test_classic_tray_header_stays_one_flat_row(browser, static_server):  # noqa: F811
    """Classic's header keeps its flat row: title, handle and status on one line; no Studio lines."""
    context, page, errors, _store = _open(browser, static_server)
    try:
        page.evaluate("() => CuttleChatDisplay.setMode('classic')")
        page.evaluate("async () => { await TG.refresh(); L.syncLive(CARDS); }")
        page.locator("#typing-indicator .subagent-fleet-card[data-chat-handle='CH-000041']").click()
        tray = page.locator(".subagent-peek")
        expect(tray).to_be_visible()
        assert tray.get_attribute("data-layout") == "side"
        title_y = page.locator(".subagent-peek-title").bounding_box()["y"]
        status = page.locator(".subagent-peek-status")
        expect(status).to_be_visible()  # Classic shows the outcome beside the title
        assert abs(title_y - status.bounding_box()["y"]) < 4, (title_y, status.bounding_box())
        assert page.locator(".subagent-peek-identity").is_hidden()
        assert page.locator(".subagent-peek-parent").is_hidden()
        # Classic cards carry no Studio selection.
        assert page.locator("#typing-indicator .is-selected").count() == 0
        assert errors == []
    finally:
        context.close()


def test_reduced_motion_stops_the_entrance_and_the_glow(browser, static_server):  # noqa: F811
    context, page, errors, _store = _open(browser, static_server)
    try:
        page.emulate_media(reduced_motion="reduce")
        _mount_turn(page)
        row = page.locator("#typing-indicator .chat-widget-task.is-in-progress input[type=checkbox]")
        assert row.evaluate("el => getComputedStyle(el).animationName") == "none"
        assert page.locator("#typing-indicator .work-panel").evaluate(
            "el => getComputedStyle(el).transitionProperty") == "none"
        assert errors == []
    finally:
        context.close()


def test_orchestration_pill_is_live_with_inline_controls_and_a_working_state(browser, static_server):  # noqa: F811
    context, page, errors, store = _open(browser, static_server)
    try:
        page.evaluate("""async () => {
            window.ORCH = CuttleChatOrchestration.create({
                doc: document, escapeHtml: (s) => String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
                fetch: (...a) => window.fetch(...a), viewportWidth: () => innerWidth,
                navigate: () => {}, confirm: () => true, toast: () => {},
                setInterval: (...a) => window.setInterval(...a), clearInterval: (t) => window.clearInterval(t),
                enabledFlags: async () => new Set(['orchestrator']), slotId: 'chatOrchestrationSlot',
                closeMenus: () => {}, sendMessage: async () => {}, openChat: () => {},
                display: window.CuttleChatDisplay, isStudio: () => CuttleChatDisplay.isStudio() });
            ORCH.setSession(40);
            await new Promise((r) => setTimeout(r, 150));
        }""")
        pill = page.locator("#chatOrchestrationSlot .chat-orch-studio-pill")
        expect(pill).to_be_visible()
        expect(pill.locator(".chat-supervision-pill")).to_have_text(re.compile("Orchestrating · Release crew"))
        expect(pill.locator(".studio-live-dot")).to_have_count(1)
        pill.locator("[data-orch-quick=pause]").click()
        for _ in range(100):
            if store["hold_calls"]:
                break
            page.wait_for_timeout(20)
        assert store["hold_calls"] and store["hold_calls"][0][0] == "hold", store["hold_calls"]
        assert json.loads(store["hold_calls"][0][1]) == {"stop": False}
        _shot(page, "pill-studio.png")

        # Not orchestrated: a live "Working" pill while the turn runs, nothing to control.
        store["binding"] = {"bound": False}
        page.evaluate("async () => { await ORCH.refresh(); ORCH.setWorking(true); }")
        expect(page.locator("#chatOrchestrationSlot .chat-orch-working-label")).to_have_text("Working")
        page.evaluate("() => ORCH.setWorking(false)")
        expect(page.locator("#chatOrchestrationSlot")).to_be_hidden()

        # Bubble header: identity + working tag while the turn runs.
        page.evaluate("""() => { document.getElementById('hdr').innerHTML =
            CuttleWorkPanel.agentHeaderHtml('codex', { label: 'Codex - gpt-5 · high', working: true }); }""")
        expect(page.locator("#hdr .studio-working-tag")).to_have_count(1)
        expect(page.locator("#hdr .agent-identity-name")).to_have_text("Codex")
        assert errors == []
    finally:
        context.close()


def test_mock_frame_for_side_by_side(browser, static_server):  # noqa: F811
    """The promo's fleet scene (0:16–0:23), for the side-by-side review. Skipped without the film."""
    scene = MOCK_SCENES / "fleet.html"
    if not scene.exists():
        pytest.skip("promo film not present")
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        page.goto(scene.as_uri())
        # The scene's own release trigger, then the frame the review compares against.
        page.evaluate("() => { if (window.__release) window.__release(); }")
        page.wait_for_timeout(5500)
        _shot(page, "work-panel-mock.png")
    finally:
        page.close()
