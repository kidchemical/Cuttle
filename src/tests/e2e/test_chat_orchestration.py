"""Orchestrating header chip + ☰ dialog (chat_orchestration.js), against fake routes."""

from __future__ import annotations

import os
import re
import json
from urllib.parse import urlparse

from .test_chat_action_card_render import CardWorld, _open_card_chat
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class OrchestrationWorld(CardWorld):
    def __init__(self, bound=False):
        super().__init__(["ok"])
        self.binding = {"success": True, "session": "CH-000042", "bound": False}
        if bound:
            self.bind("fast", "ship the slice")
        self.calls = []

    def bind(self, pid, goal):
        self.binding = {"success": True, "session": "CH-000042", "bound": True, "playbook_id": pid,
                        "playbook_name": {"fast": "Fast", "default": "Default"}[pid], "goal": goal,
                        "playbook_missing": False, "bound_at": 1, "agents": [
                            {"handle": "CH-000050", "name": "Scout", "avatar": "🔭", "agent": "codex",
                             "model": "terra", "effort": "low", "status": "running"},
                            {"handle": "CH-000051", "name": "Builder", "avatar": "", "agent": "cursor",
                             "status": "done"},
                            {"handle": "CH-000052", "name": "Judge", "avatar": "⚖️", "agent": "claude",
                             "status": "failed"}]}

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path
        reply = lambda body, status=200: route.fulfill(  # noqa: E731
            status=status, content_type="application/json", body=json.dumps(body))
        if path == "/api/experimental/flags":
            return reply({"success": True, "flags": [{"id": "orchestrator", "enabled": True}]})
        if path == "/api/orchestrator/playbooks":
            return reply({"success": True, "default_id": "default", "playbooks": [
                {"id": "default", "name": "Default"}, {"id": "fast", "name": "Fast"}]})
        if path == "/api/orchestrator/bindings/42":
            body = json.loads(req.post_data or "{}") if req.method == "PUT" else None
            self.calls.append((req.method, body))
            if req.method == "PUT":
                self.bind(body.get("playbook_id", self.binding["playbook_id"]), body.get("goal", self.binding["goal"]))
                return reply({**self.binding, "changed": sorted(body)})
            if req.method == "DELETE":
                self.binding = {"success": True, "session": "CH-000042", "bound": False}
                return reply({"success": True, "unbound": True})
            return reply(self.binding)
        if req.method == "POST" and path == "/api/chat":
            message = json.loads(req.post_data or "{}").get("message", "")
            self.calls.append(("chat", message))
            if message.startswith("/orchestrate @"):
                pid, goal = message[len("/orchestrate @"):].split(" ", 1)
                self.bind(pid, goal)  # the server-side host step
        return super().handle(route)


def test_menu_dialog_starts_orchestration_with_a_typed_turn(browser, static_server):
    from playwright.sync_api import expect
    world = OrchestrationWorld(bound=False)
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        slot = frame.locator("#chatOrchestrationSlot")
        page.wait_for_timeout(400)
        expect(slot).to_be_hidden()
        frame.locator("#chatChromeMenuBtn").click()
        frame.locator("[data-orch-start]").click()
        form = frame.locator(".chat-supervision-dialog")
        expect(form).to_be_visible()
        # Same shell as the supervision dialog: auth-modal, close button, scrolling body + footer,
        # searchable playbook dropdown.
        assert "auth-modal" in form.get_attribute("class")
        expect(form.locator(".chat-supervision-dialog-header .auth-modal-close")).to_be_visible()
        expect(form.locator(".chat-supervision-dialog-body")).to_be_visible()
        expect(form.locator(".chat-supervision-dialog-footer [type=submit]")).to_be_visible()
        expect(form.get_by_role("button", name="Playbook", exact=True)).to_be_visible()
        expect(form.locator("[name=playbookId]")).to_have_value("default")
        form.locator("[type=submit]").click()
        expect(form.locator(".chat-supervision-errors")).to_contain_text("Give the orchestrator a goal.")
        form.get_by_role("button", name="Playbook", exact=True).click()
        frame.get_by_role("option", name="Fast", exact=True).click()
        form.locator("[name=goal]").fill("ship the slice")
        form.locator("[type=submit]").click()
        expect(form).to_have_count(0)
        expect(slot.locator("[data-orch-open]")).to_contain_text("Orchestrating · Fast")
        assert ("chat", "/orchestrate @fast ship the slice") in world.calls
        assert not errors
    finally:
        page.close()


def test_dialog_closes_on_escape_and_returns_focus(browser, static_server):
    from playwright.sync_api import expect
    world = OrchestrationWorld(bound=True)
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        chip = frame.locator("#chatOrchestrationSlot [data-orch-open]")
        chip.click()
        frame.locator("[data-orch-act=edit]").click()
        form = frame.locator(".chat-supervision-dialog")
        expect(form).to_be_visible()
        page.keyboard.press("Escape")
        expect(form).to_have_count(0)
        assert not [c for c in world.calls if c[0] == "PUT"]  # cancelling changes nothing
        assert not errors
    finally:
        page.close()


def test_chip_popover_edits_and_stops_without_an_agent_turn(browser, static_server):
    from playwright.sync_api import expect
    world = OrchestrationWorld(bound=True)
    page, frame, errors = _open_card_chat(browser, static_server, world)
    page.on("dialog", lambda d: d.accept())
    try:
        chip = frame.locator("#chatOrchestrationSlot [data-orch-open]")
        expect(chip).to_contain_text("Orchestrating · Fast")
        chip.click()
        pop = frame.locator(".chat-supervision-popover")
        expect(pop).to_contain_text("ship the slice")
        # Agents as chips (running first, each opens its chat); the brief tip sits behind (i).
        agents = pop.locator(".chat-orch-agent")
        expect(agents).to_have_count(3)
        expect(agents.first).to_have_class(re.compile(r"\bis-running\b"))
        assert (agents.first.get_attribute("title") or agents.first.get_attribute("data-tooltip")) == "CH-000050 — codex · terra · low — Running"
        expect(pop.locator(".chat-supervision-section").nth(1)).to_have_text("Agents · 3, 1 running")
        expect(pop.locator(".cuttle-info[data-tooltip*='next turn']")).to_have_count(1)
        expect(pop).not_to_contain_text("a turn already running keeps the old brief")
        page.screenshot(path=os.environ.get("ORCH_POPOVER_SHOT", "/tmp/orch_popover.png"))
        pop.locator("[data-orch-act=edit]").click()
        form = frame.locator(".chat-supervision-dialog")
        expect(form.locator("[name=playbookId]")).to_have_value("fast")
        expect(form.locator("[name=goal]")).to_have_value("ship the slice")
        form.get_by_role("button", name="Playbook", exact=True).click()
        frame.get_by_role("option", name="Default (default)", exact=True).click()
        form.locator("[type=submit]").click()
        expect(form).to_have_count(0)
        expect(chip).to_contain_text("Orchestrating · Default")
        assert ("PUT", {"playbook_id": "default"}) in world.calls  # unchanged goal not sent
        assert not [c for c in world.calls if c[0] == "chat"]  # a switch is never an agent turn
        chip.click()
        frame.locator(".chat-supervision-popover [data-orch-act=stop]").click()
        expect(frame.locator("#chatOrchestrationSlot")).to_be_hidden()
        assert ("DELETE", None) in world.calls
        assert not errors
    finally:
        page.close()
