"""/orchestrate chip + Orchestrator App — real-Chromium behavior.

Picking `/orchestrate` shows a segmented "Orchestrate · <playbook>" chip; its
click opens the shared chip modal to choose a playbook (the chip's prefix
carries `@id`) or make it the default. The Orchestrator App duplicates
playbooks, switches agents off, and adds temporary agents.
Uses the shared isolated API from `test_shared_diff_modal`.
"""

from __future__ import annotations

import json

from playwright.sync_api import expect

from .test_shared_diff_modal import (  # noqa: F401
    IsolatedAPI,
    apply_request_guard,
    browser,
    static_server,
    surface,
)

PLAYBOOKS = [
    {"schema": 2, "id": "default", "name": "Default org chart", "seed": True, "missing_profiles": [],
     "tiers": [{"id": "worker", "label": "Grunt", "role": "worker", "use_for": "",
                "members": [{"profile": "grunt", "on": True}]}],
     "limits": {"solo_attempts": 2, "solo_minutes": 40, "child_retries": 1, "setup_retries": 2},
     "supervisor": {"every_sec": 900, "agent": "", "instructions": "", "require_branch": "",
                    "require_path": "", "forbid_branches": []}, "notes": ""},
]
PLAYBOOKS.append({**PLAYBOOKS[0], "id": "fast", "name": "Fast", "seed": False})


def _route_orchestrator(page, puts):
    state = {"default": "default"}

    def handler(route):
        req = route.request
        path = req.url.split("/api/", 1)[1].split("?", 1)[0]
        if path == "orchestrator/default" and req.method == "PUT":
            state["default"] = json.loads(req.post_data or "{}")["id"]
            puts.append(("default", state["default"]))
            body = {"success": True, "default_id": state["default"]}
        elif path.startswith("orchestrator/playbooks/") and req.method == "PUT":
            doc = json.loads(req.post_data or "{}")
            puts.append(("save", doc))
            body = {"success": True, "playbook": doc}
        elif path == "orchestrator/playbooks":
            body = {"success": True, "playbooks": PLAYBOOKS, "default_id": state["default"]}
        elif path == "orchestrator/bindings":
            body = {"success": True, "bindings": []}
        elif path == "agent-profiles":
            body = {"success": True, "profiles": [
                {"id": "grunt", "name": "Grunt", "avatar": "", "agent": "cursor", "model": "auto", "effort": ""}]}
        elif path == "agents":
            body = {"success": True, "agents": [
                {"id": "cursor", "label": "Cursor", "models": ["auto", "grok-4.6"], "efforts": [],
                 "supports_model_pin": True, "supports_effort": False}]}
        elif path == "project-commands":
            body = {"success": True, "commands": [
                {"name": "orchestrate", "title": "orchestrate", "description": "Run as an orchestrator"}]}
        else:
            return route.fallback()
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/orchestrator/**", handler)
    page.route("**/api/agent-profiles*", handler)
    page.route("**/api/agents*", handler)
    page.route("**/api/project-commands*", handler)


def test_orchestrate_chip_picks_playbook_and_default(surface):
    page, _api, errors = surface("chat_page.html")
    puts = []
    _route_orchestrator(page, puts)
    page.reload(wait_until="domcontentloaded")  # project commands load at boot
    page.wait_for_function("typeof window.loadChatSession === 'function'")
    page.evaluate("() => window.loadChatSession(42)")
    box = page.locator("#chatInput")
    box.wait_for(state="visible")
    box.click()
    box.type("/orchestrate")
    item = page.locator("#chatSlashCommandMenu .slash-command-item--project-cmd").first
    item.wait_for(state="visible", timeout=5000)
    item.click()
    chip = page.locator("#chatSlashChipsRow .slash-command-chip[data-chip-index]").first
    expect(chip.locator(".slash-chip-seg--model")).to_have_text("Default org chart")
    chip.click()
    modal = page.locator("#chipConfigModal")
    expect(modal.locator("#chipConfigTitle")).to_have_text("Orchestrate")
    expect(modal.locator("#orchChipDefault")).to_be_checked()
    page.evaluate("""() => { const s = document.querySelector('#orchChipPlaybook');
        s.value = 'fast'; s.dispatchEvent(new Event('change', { bubbles: true })); }""")
    expect(chip.locator(".slash-chip-seg--model")).to_have_text("Fast")
    expect(modal.locator("#orchChipDefault")).not_to_be_checked()
    composed = page.evaluate("""() => { const t = document.querySelector('#chatInput'); t.value = 'ship it';
        return window.composeMessageWithSlashChip ? window.composeMessageWithSlashChip(t) : null; }""")
    if composed is not None:
        assert composed.endswith("/orchestrate @fast ship it")
    modal.locator("#orchChipDefault").check()
    expect(modal.locator("#orchChipDefault")).to_be_disabled()
    assert puts == [("default", "fast")]
    page.screenshot(path="temp/orchestrate-chip-modal.png")
    page.locator("#chipConfigDone").click()
    expect(chip.locator(".slash-chip-seg--model")).to_have_text("Fast")
    assert not errors, errors


def test_orchestrator_app_duplicates_toggles_and_adds_temporary_agent(surface):
    page, _api, errors = surface("orchestrator_page.html")
    puts = []
    _route_orchestrator(page, puts)
    page.reload(wait_until="domcontentloaded")
    page.locator("#orchContent").wait_for(state="visible")
    expect(page.locator("#orchMakeDefault")).to_have_text("★ Default")
    page.locator("#orchDuplicate").click()
    expect(page.locator("#orchId")).to_have_value("default-copy")
    expect(page.locator("#orchName")).to_have_value("Default org chart (copy)")
    page.locator("#orchTiers [data-add-temp]").first.click()
    page.locator("#chipConfigModal").wait_for(state="visible")
    page.evaluate("""() => { const s = document.querySelector('#chipConfigAgent');
        s.value = Array.from(s.options).find(o => /cursor/i.test(o.value)).value;
        s.dispatchEvent(new Event('change', { bubbles: true })); }""")
    page.locator("#chipConfigDone").click()
    temp = page.locator("#orchTiers .orch-member.is-temp")
    expect(temp).to_have_count(1)
    first = page.locator("#orchTiers .orch-member").first
    first.locator("[data-toggle-member]").uncheck(force=True)
    expect(first).to_have_class(__import__("re").compile(r"\bis-off\b"))
    page.locator("#orchSave").click()
    page.wait_for_function("() => document.querySelector('#orchSaveState').textContent.length > 0")
    saved = [doc for kind, doc in puts if kind == "save"]
    assert saved, puts
    members = saved[-1]["tiers"][0]["members"]
    assert members[0] == {"profile": "grunt", "on": False}
    assert members[1]["agent"] == "cursor" and members[1]["on"] is True and members[1]["name"]
    assert saved[-1]["id"] == "default-copy"
    page.screenshot(path="temp/orchestrator-temp-agent.png", full_page=True)
    assert not errors, errors
