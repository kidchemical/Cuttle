"""Composer chip config modal — real-Chromium behavior.

Clicking the project chip opens a centered modal with Project + Branch
dropdowns; clicking an agent chip opens one with Agent + Model + Effort
dropdowns. Uses the shared isolated API from `test_shared_diff_modal`.
"""

from __future__ import annotations

import json

from .test_shared_diff_modal import (  # noqa: F401
    IsolatedAPI,
    apply_request_guard,
    browser,
    static_server,
    surface,
)

PROJECT_CHIP = "#chatSlashChipsRow .slash-command-chip--project"
AGENT_CHIP = "#chatSlashChipsRow .slash-command-chip[data-chip-index]"
MODAL = "#chipConfigModal"


def _route_branches(page):
    def handler(route):
        body = {
            "success": True,
            "data": [
                {"name": "main", "current": True, "remote": False},
                {"name": "dev", "current": False, "remote": False},
            ],
            "current": "main",
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    page.route("**/api/git/branches*", handler)


def _add_cursor_chip(page):
    box = page.locator("#chatInput")
    box.wait_for(state="visible")
    box.click()
    box.type("/cursor")
    page.wait_for_selector("#chatSlashCommandMenu .slash-command-item", timeout=4000)
    page.locator("#chatSlashCommandMenu .slash-command-item-main").first.click()
    page.locator(AGENT_CHIP).first.wait_for(state="visible")


def test_project_chip_opens_modal_with_project_and_branch_dropdowns(surface):
    page, _api, errors = surface("chat_page.html")
    _route_branches(page)
    chip = page.locator(PROJECT_CHIP).first
    chip.wait_for(state="visible")
    chip.click()
    modal = page.locator(MODAL)
    modal.wait_for(state="visible")
    assert modal.locator("#chipConfigTitle").inner_text() == "Working project"
    proj = modal.locator("#chipConfigProject")
    assert proj.count() == 1
    assert "Cuttle" in proj.locator("option").first.inner_text()
    # Branch dropdown populates (async) from the routed branches.
    branch = modal.locator("#chipConfigBranch")
    page.wait_for_function(
        "() => { const s = document.querySelector('#chipConfigBranch');"
        " return s && !s.disabled && s.querySelectorAll('option').length >= 2; }",
        timeout=4000,
    )
    assert branch.locator("option", has_text="dev").count() >= 1
    assert not errors, errors


def test_project_modal_switches_branch(surface):
    page, api, errors = surface("chat_page.html")
    _route_branches(page)
    chip = page.locator(PROJECT_CHIP).first
    chip.wait_for(state="visible")
    chip.click()
    page.wait_for_function(
        "() => { const s = document.querySelector('#chipConfigBranch');"
        " return s && !s.disabled; }",
        timeout=4000,
    )
    page.select_option("#chipConfigBranch", "dev")
    page.wait_for_timeout(150)
    posts = [r for r in api.requests if r[0] == "/api/git/branch"]
    assert posts, "expected a POST /api/git/branch"
    payload = json.loads(posts[-1][2])
    assert payload["action"] == "switch"
    assert payload["branch"] == "dev"
    assert not errors, errors


def test_project_modal_closes_on_done_and_escape(surface):
    page, _api, errors = surface("chat_page.html")
    _route_branches(page)
    chip = page.locator(PROJECT_CHIP).first
    chip.wait_for(state="visible")
    chip.click()
    assert page.locator(MODAL).first.is_visible()
    page.locator("#chipConfigDone").click()
    page.wait_for_timeout(30)
    assert not page.locator(MODAL).first.is_visible()
    chip.click()
    assert page.locator(MODAL).first.is_visible()
    page.keyboard.press("Escape")
    page.wait_for_timeout(30)
    assert not page.locator(MODAL).first.is_visible()
    assert not errors, errors


def test_agent_chip_opens_modal_with_agent_model_effort(surface):
    page, _api, errors = surface("chat_page.html")
    _add_cursor_chip(page)
    page.locator(AGENT_CHIP).first.click()
    modal = page.locator(MODAL)
    modal.wait_for(state="visible")
    assert modal.locator("#chipConfigTitle").inner_text() == "Agent"
    agent = modal.locator("#chipConfigAgent")
    assert agent.count() == 1
    labels = agent.locator("option").all_inner_texts()
    assert any("Cursor" in t for t in labels)
    assert modal.locator("#chipConfigModel").count() == 1
    assert not errors, errors


def test_agent_modal_switches_agent_to_muse(surface):
    page, _api, errors = surface("chat_page.html")
    _add_cursor_chip(page)
    page.locator(AGENT_CHIP).first.click()
    page.locator(MODAL).wait_for(state="visible")
    page.select_option("#chipConfigAgent", "muse")
    page.wait_for_timeout(80)
    assert page.locator(MODAL).first.is_visible()
    assert page.locator("#chipConfigTitle").inner_text() == "Agent"
    # Muse supports effort -> an Effort dropdown appears.
    effort = page.locator("#chipConfigEffort")
    assert effort.count() == 1
    opts = effort.locator("option").all_inner_texts()
    assert opts
    assert all("effort" not in t.lower() for t in opts), opts
    assert not errors, errors
