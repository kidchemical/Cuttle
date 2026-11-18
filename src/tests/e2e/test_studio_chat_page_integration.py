"""Studio display on the REAL chat page (integration 1), in Chromium.

Loads ``src/web/chat_page.html`` itself. Only the network is mocked: the
experimental flags, the Tasks list and two fleet children of one history turn,
the supervision overview, the agent catalog and the project commands. Nothing
Studio-specific is loaded by the test; every surface must be mounted by the
page's own wiring (chat_page.html tags and chat_page.js call sites).

Studio (flag on, pref studio): the bubble header shows the agent's dot and
name, the turn's work panel sits inside that bubble, the supervision chip
opens the Studio panel, a fleet card opens the push tray, `/orc` bolds its
typed prefix, and a sent `/orchestrate @id` bubble shows the orchestrate chip.
Classic (flag off): none of that markup exists and the Classic chip, popover and
plain prefix are unchanged.

Screenshots for the side-by-side go to ``temp/studio_shots/integrate/``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.e2e.test_chat_supervision import SupervisionWorld
from tests.e2e.test_shared_diff_modal import PROJECT, apply_request_guard, browser, static_server  # noqa: F401

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect

REPO = Path(__file__).resolve().parents[3]
SHOTS = REPO / "temp" / "studio_shots" / "integrate"
SESSION = 40
TURN = "turn40"

CATALOG = [
    {"id": "codex", "label": "Codex", "color": "#10a37f", "models": [], "efforts": [],
     "supports_model_pin": False, "supports_effort": False},
    {"id": "claude", "label": "Claude Code", "color": "#d97757", "models": [], "efforts": [],
     "supports_model_pin": False, "supports_effort": False},
    {"id": "cursor", "label": "Cursor", "color": "#4b5563", "models": ["auto"], "efforts": [],
     "supports_model_pin": True, "supports_effort": False},
]

TASKS = {"success": True, "revision": 1, "gizmos": [{
    "id": "g-release", "type": "tasks", "status": "active", "title": "Release v0.8",
    "origin_turn_id": TURN, "scope": "session", "session_id": SESSION, "edit_mode": "agent",
    "description": "", "linked_sessions": [],
    "payload": {"items": [
        {"id": "t1", "text": "Plan the release", "done": True},
        {"id": "t2", "text": "Write the changelog", "done": False, "in_progress": True,
         "owner": {"kind": "chat", "id": "CH-000041", "label": "Scout", "agent": "codex"}},
        {"id": "t3", "text": "Run the test suite", "done": False},
    ]},
    "config": {"items": []},
}]}

CHILDREN = [
    {"handle": "CH-000041", "label": "Changelog entries", "agent": "codex", "fleet": True,
     "outcome": "running", "summary": "Parsing the changelog entries", "generating": True, "turn_id": TURN},
    {"handle": "CH-000042", "label": "Fin", "agent": "claude", "fleet": True,
     "outcome": "done", "summary": "Shipped the draft notes", "turn_id": TURN,
     "started_at": "2026-11-18 10:00:00", "finished_at": "2026-11-18 10:02:05"},
]

PROJECT_COMMANDS = {"success": True, "commands": [
    {"name": "orchestrate", "title": "orchestrate", "description": "Run as an orchestrator"},
]}


def _flag(flag_id, enabled):
    return {"id": flag_id, "label": flag_id, "description": "", "default": False, "category": "chat",
            "risk": "low", "enabled": enabled, "configured_enabled": enabled, "needs_restart": False,
            "blocked_by": []}


def _history():
    return [
        {"id": 1, "role": "user", "content": "Ship the release.", "timestamp": "2026-11-18 09:00:00",
         "metadata": {}},
        {"id": 2, "role": "assistant",
         "content": "Plan's set. Three workers on it; a supervisor checks in every 15 minutes.",
         "timestamp": "2026-11-18 09:01:00",
         "metadata": {"turn_id": TURN, "query_id": TURN, "completed_at": int(time.time()),
                      "slash_command": {"chips": [{"label": "Codex", "meta": "/codex", "category": "codex"}]},
                      "subagents": CHILDREN}},
    ]


class ChatWorld:
    """Answers the chat page's API calls; the supervision routes reuse SupervisionWorld."""

    def __init__(self, *, flag, history=True):
        self.flag = flag
        self.history = history
        self.chat_ops = SupervisionWorld(flag=True)
        # The chip only needs the "supervising" side. A "supervised by" entry would lock the composer.
        self.chat_ops.overview["supervised_by"] = []
        self.chat_posts = []

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path

        def reply(body):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        if path == "/api/experimental/flags":
            return reply({"success": True, "kill_switch": False, "flags": [
                _flag("chat_studio_display", self.flag), _flag("chat_supervision", True)]})
        if path.startswith("/api/chat-ops/"):
            return self.chat_ops.handle(route)
        if path == "/api/auth/me":
            return reply({"authenticated": True, "user": {"id": 1, "display_name": "Owner", "role": "owner"}})
        if path == "/api/auth/sessions":
            return reply({"success": True, "sessions": []})
        if path == f"/api/auth/sessions/{SESSION}/messages":
            return reply({"success": True, "messages": _history() if self.history else []})
        if path == "/api/agents":
            return reply({"success": True, "agents": CATALOG})
        if path == "/api/gizmos/tasks":
            return reply(TASKS)
        if path == "/api/projects":
            return reply({"success": True, "data": [PROJECT]})
        if path == "/api/project-commands":
            return reply(PROJECT_COMMANDS)
        if path == "/api/chat" and req.method == "POST":
            self.chat_posts.append(req.post_data or "")
            return reply({"success": True, "reply": "ok"})
        return reply({"success": True, "data": []})


def _open(browser, static_server, world, *, pref, cache, theme="light"):  # noqa: F811
    """Open the chat; wait for the turn bubble when the world has history, else the composer."""
    """The real chat page, with ui_boot's per-browser state set before first paint."""
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    apply_request_guard(page.context, static_server)
    page.add_init_script(
        "try {"
        f"localStorage.setItem('cuttleChatDisplay', {json.dumps(pref)});"
        f"localStorage.setItem('cuttleChatStudioFlag', {json.dumps(cache)});"
        f"localStorage.setItem('theme', {json.dumps(theme)});"
        "} catch (_) {}"
    )
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", world.handle)
    page.goto(f"{static_server}/chat_page.html", wait_until="domcontentloaded")
    page.wait_for_function("typeof window.loadChatSession === 'function'")
    page.evaluate(f"() => window.loadChatSession({SESSION})")
    if world.history:
        _assistant_bubble(page).wait_for(state="visible")
    else:
        page.locator("#chatInput").wait_for(state="visible")
    return page, errors


def _assistant_bubble(page):
    return page.locator(f'#chatMessages .message.assistant[data-turn-id="{TURN}"]').first


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / name), animations="disabled")


def _type_slash(page, text):
    """Real key presses: the slash palette filters on keystrokes, not on a programmatic fill."""
    box = page.locator("#chatInput")
    box.click()
    box.press_sequentially(text)
    page.locator("#chatSlashCommandMenu .slash-command-item").first.wait_for(state="visible", timeout=5000)


def test_studio_flag_on_mounts_header_work_panel_and_supervision(browser, static_server):  # noqa: F811
    world = ChatWorld(flag=True)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        expect(page.locator("html")).to_have_class(re.compile(r"\bcuttle-studio\b"))

        bubble = _assistant_bubble(page)
        header = bubble.locator(".studio-agent-header")
        expect(header).to_contain_text("Codex")
        assert header.locator(".agent-dot").count() == 1
        # The dot takes the agent's catalog colour, not the grey fallback.
        assert CATALOG[0]["color"] in (header.locator(".agent-dot").get_attribute("style") or "").lower()
        assert bubble.locator(".slash-command-chip").count() == 0  # Studio replaces the Classic chip

        panel = bubble.locator(".work-panel")
        expect(panel).to_be_visible()
        expect(panel).to_contain_text("Write the changelog")
        assert bubble.locator(".studio-fleet-card").count() == 2

        page.locator("#chatSupervisionSlot [data-sup-open=chip]").first.click()
        expect(page.locator(".sup-studio-panel")).to_be_visible()
        assert page.locator(".chat-supervision-popover").count() == 0

        _shot(page, "chat-studio.png")
        assert not errors, errors
    finally:
        page.context.close()


def test_studio_dark_screenshot(browser, static_server):  # noqa: F811
    world = ChatWorld(flag=True)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1", theme="dark")
    try:
        expect(page.locator("html")).to_have_class(re.compile(r"\bcuttle-studio\b"))
        expect(_assistant_bubble(page).locator(".work-panel")).to_be_visible()
        _shot(page, "chat-studio-dark.png")
        assert not errors, errors
    finally:
        page.context.close()


def test_studio_fleet_card_opens_push_tray(browser, static_server):  # noqa: F811
    world = ChatWorld(flag=True)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        card = _assistant_bubble(page).locator(".studio-fleet-card").first
        card.click()
        expect(page.locator('.subagent-peek[data-layout="push"]')).to_be_visible()
        _shot(page, "chat-tray-studio.png")
        assert not errors, errors
    finally:
        page.context.close()


def test_studio_slash_prefix_is_bold(browser, static_server):  # noqa: F811
    world = ChatWorld(flag=True)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        _type_slash(page, "/orc")
        prefix = page.locator("#chatSlashCommandMenu .slash-prefix-hit").first
        expect(prefix).to_be_visible()
        assert prefix.inner_text().lower().endswith("orc")
        _shot(page, "composer-slash-studio.png")
        assert not errors, errors
    finally:
        page.context.close()


def test_studio_sent_orchestrate_bubble_shows_chip(browser, static_server):  # noqa: F811
    # No history: a sticky agent from an earlier turn would prefix the send with /codex.
    world = ChatWorld(flag=True, history=False)
    page, errors = _open(browser, static_server, world, pref="studio", cache="1")
    try:
        box = page.locator("#chatInput")
        box.click()
        box.fill("/orchestrate @release-crew go")
        box.press("Escape")  # the slash palette covers the Send button while the text starts with "/"
        page.locator("#sendButton").click()
        chip = page.locator("#chatMessages .message.user .orch-bubble-chip").last
        expect(chip).to_be_visible()
        assert world.chat_posts, "the send reached the chat route"
        assert not errors, errors
    finally:
        page.context.close()


def test_flag_off_is_classic_with_no_studio_markup(browser, static_server):  # noqa: F811
    world = ChatWorld(flag=False)
    # The pref says Studio, but the server flag is off, so the effective mode is Classic.
    page, errors = _open(browser, static_server, world, pref="studio", cache="0")
    try:
        expect(page.locator("html")).not_to_have_class(re.compile(r"\bcuttle-studio\b"))
        for selector in (".studio-agent-header", ".work-panel", ".studio-fleet-card",
                         ".studio-working-tag", ".sup-studio-panel", ".orch-bubble-chip"):
            assert page.locator(selector).count() == 0, selector

        bubble = _assistant_bubble(page)
        expect(bubble.locator(".slash-command-chip").first).to_be_visible()
        assert bubble.locator(".subagent-fleet-card").count() == 2

        page.locator("#chatSupervisionSlot [data-sup-open=chip]").first.click()
        expect(page.locator(".chat-supervision-popover").first).to_be_visible()
        assert page.locator(".sup-studio-panel").count() == 0

        _type_slash(page, "/orc")
        assert page.locator("#chatSlashCommandMenu .slash-prefix-hit").count() == 0

        _shot(page, "chat-classic.png")
        assert not errors, errors
    finally:
        page.context.close()
