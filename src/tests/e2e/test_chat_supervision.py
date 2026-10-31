"""Chat supervision UI: pure view-model (node) and the chat page in a browser.

* node — chip/badge/history/queued rows and the "Supervise this chat…"
  payload validation in ``src/web/js/chat/chat_supervision_model.js``
* browser — header pills stay hidden while ``chat_supervision`` is off or the
  flag lookup fails, appear when it turns on in-page, the chip popover shows
  skipped checks and pauses, a queued message cancels, and the chat menu
  opens the dialog that POSTs a supervise request
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from .test_chat_action_card_render import CardWorld, _open_card_chat
from .test_shared_diff_modal import browser, static_server  # noqa: F401

MODEL = Path(__file__).resolve().parents[2] / "web" / "js" / "chat" / "chat_supervision_model.js"

HARNESS = r"""
const M = require(process.env.MODEL);
const now = 1_800_000_000;
const sched = {id: 's1', state: 'pending', supervisor: 'CH-000001', target: 'CH-000002', every: 900,
  next_check: now + 600, agent: 'cursor', required_branch: 'dev/x', forbidden_branches: ['main'],
  violations: ['on forbidden branch main'], last_seen: {state: 'busy', tasks: [{done: 2, total: 5}]},
  history: [
    {at: now - 60, outcome: 'skipped', detail: {reason: 'busy'}},
    {at: now - 960, outcome: 'skipped', detail: {reason: 'unchanged'}},
    {at: now - 1860, outcome: 'woke', detail: {state: 'idle_with_work', already_waiting: true}},
    {at: now - 2760, outcome: 'violation', detail: {state: 'busy', violations: ['on forbidden branch main']}},
  ]};
const v = M.view({supervising: [sched], supervised_by: [Object.assign({}, sched, {state: 'paused'})],
  queued: [{id: 'q1', from: 'CH-000001', text: 'next\n  step'}]}, now, {locale: 'en-US'});
const bad = M.supervisePayload({target: 'CH-000002', agent: '', every: 7, instructions: ' ',
  requiredBranch: 'main', forbidden: 'main, master'});
const good = M.supervisePayload(Object.assign(M.dialogDefaults({handle: 'CH-000002', branch: 'dev/x'}),
  {agent: 'codex', worker: 'cursor auto, codex terra', forbidden: ' main ,, master '}));
console.log(JSON.stringify({
  intervals: [300, 3600, 5400, 45].map(M.fmtInterval),
  chip: v.chips[0], badge: v.badges[0], queued: v.queued[0], warn: v.warn, empty: v.empty,
  emptyView: M.view({}, now), due: M.nextLabel(Object.assign({}, sched, {next_check: now - 1}), now),
  bad, good,
}));
"""


def _model():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "MODEL": str(MODEL)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_model_view_rows_and_payload():
    r = _model()
    assert r["intervals"] == ["5m", "1h", "90m", "45s"]
    chip = r["chip"]
    assert chip["text"].startswith("Supervising CH-000002 · every 15m · next ")
    assert chip["rules"] == "stay on dev/x · never main" and chip["tasks"] == "2/5 tasks done"
    assert [h["text"] for h in chip["history"]] == [
        "skipped, busy", "skipped, no change",
        "woke supervisor (idle_with_work), previous check-in still pending",
        "rule broken, woke supervisor (busy)"]
    assert [h["tone"] for h in chip["history"]] == ["muted", "muted", "active", "warn"]
    assert r["badge"]["text"] == "Supervised by CH-000001" and r["badge"]["paused"] is True
    assert r["badge"]["next"] == "paused"
    assert r["queued"]["preview"] == "next step"
    assert r["warn"] is True and r["empty"] is False and r["emptyView"]["empty"] is True
    assert r["due"] == "checking…"
    assert r["bad"]["ok"] is False and len(r["bad"]["errors"]) == 4
    body = r["good"]["body"]
    assert r["good"]["ok"] and body["agent"] == "codex" and body["every"] == 900
    assert body["required_branch"] == "dev/x" and body["forbidden_branches"] == ["main", "master"]
    assert body["model_tiers"] == {"worker": ["cursor auto", "codex terra"]}


def _overview(now):
    sched = {"id": "sup1", "state": "pending", "supervisor": "CH-000042", "target": "CH-001548",
             "every": 900, "next_check": now + 600, "agent": "cursor", "required_branch": "dev/x",
             "forbidden_branches": ["main"], "violations": [], "last_seen": {"state": "busy"},
             "history": [{"at": now - 60, "outcome": "skipped", "detail": {"reason": "busy"}},
                         {"at": now - 960, "outcome": "woke", "detail": {"state": "idle_with_work"}}]}
    watched = dict(sched, id="sup2", supervisor="CH-001581", target="CH-000042",
                   violations=["on forbidden branch main"])
    return {"success": True, "schema_version": 1, "ok": True, "handle": "CH-000042",
            "supervising": [sched], "supervised_by": [watched],
            "queued": [{"id": "q1", "from": "CH-001581", "text": "Run the tests next"}]}


class SupervisionWorld(CardWorld):
    def __init__(self, flag=False):
        super().__init__([])
        self.flag = flag
        self.flags_ok = True
        self.posts = []
        self.overview = _overview(time.time())

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path
        reply = lambda body, status=200: route.fulfill(  # noqa: E731
            status=status, content_type="application/json", body=json.dumps(body))
        if path == "/api/experimental/flags":
            if not self.flags_ok:
                return reply({"success": False}, 500)
            return reply({"success": True, "flags": [{"id": "chat_supervision", "enabled": self.flag}]})
        if path.startswith("/api/chat-ops/"):
            if not self.flag:
                return reply({"success": False, "disabled": True})
            if req.method == "POST":
                self.posts.append((path, json.loads(req.post_data or "{}")))
                if path.endswith("/pause"):
                    self.overview["supervising"][0]["state"] = "paused"
                if path.endswith("/q1/cancel"):
                    self.overview["queued"] = []
                if path == "/api/chat-ops/supervise":
                    return reply({"success": True, "operation": {"id": "new", "actor": 77}})
                return reply({"success": True, "operation": {"id": path.split("/")[-2]}})
            if path.endswith("/defaults"):
                return reply({"success": True, "handle": "CH-000042", "branch": "dev/x",
                              "execution_path": "/repo", "worktree": False})
            return reply(self.overview)
        if path == "/api/agents":
            return reply({"success": True, "agents": [{"id": "cursor", "label": "Cursor"},
                                                      {"id": "codex", "label": "Codex"}]})
        return super().handle(route)


def _toggle_flag(frame, world, on):
    world.flag = on
    frame.locator("html").evaluate(
        "() => window.postMessage({type: 'cuttle-experimental-flags-changed'}, location.origin)")


def test_pills_hidden_until_flag_then_popover_pause_and_queue_cancel(browser, static_server):
    from playwright.sync_api import expect
    world = SupervisionWorld(flag=False)
    page, frame, errors = _open_card_chat(browser, static_server, world)
    page.on("dialog", lambda d: d.accept())
    try:
        slot = frame.locator("#chatSupervisionSlot")
        page.wait_for_timeout(500)
        expect(slot).to_be_hidden()
        frame.locator("#chatChromeMenuBtn").click()
        expect(frame.locator("#chatChromeMenuDropdown")).to_be_visible()
        expect(frame.locator("[data-sup-start]")).to_have_count(0)
        frame.locator("#chatChromeMenuBtn").click()

        _toggle_flag(frame, world, True)
        chip = slot.locator("[data-sup-open=chip]")
        expect(chip).to_contain_text("Supervising CH-001548 · every 15m · next ")
        badge = slot.locator("[data-sup-open=badge]")
        expect(badge).to_contain_text("Supervised by CH-001581")
        assert "is-warn" in badge.get_attribute("class")
        expect(slot.locator("[data-sup-open=queued]")).to_contain_text("1 queued")

        chip.click()
        pop = frame.locator(".chat-supervision-popover")
        expect(pop.locator(".chat-supervision-history li")).to_have_count(2)
        expect(pop.locator(".chat-supervision-history li").first).to_contain_text("skipped, busy")
        page.screenshot(path=os.environ.get("SUPERVISION_SHOT", "/tmp/supervision_popover.png"))
        pop.locator("[data-sup-act=pause]").click()
        frame.locator("[data-sup-open=chip].is-paused").wait_for()
        assert world.posts[-1][0] == "/api/chat-ops/operations/sup1/pause"

        badge.click()
        expect(frame.locator(".chat-supervision-popover")).to_contain_text("on forbidden branch main")
        slot.locator("[data-sup-open=queued]").click()
        frame.locator(".chat-supervision-popover [data-sup-act=cancel]").click()
        expect(slot.locator("[data-sup-open=queued]")).to_have_count(0)
        assert world.posts[-1][0] == "/api/chat-ops/operations/q1/cancel"

        _toggle_flag(frame, world, False)
        expect(slot).to_be_hidden()
        assert not errors
    finally:
        page.close()


def test_flag_lookup_failure_keeps_ui_hidden(browser, static_server):
    from playwright.sync_api import expect
    world = SupervisionWorld(flag=True)
    world.flags_ok = False
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        page.wait_for_timeout(500)
        expect(frame.locator("#chatSupervisionSlot")).to_be_hidden()
        assert not [p for p in world.posts]
        assert not errors
    finally:
        page.close()


def test_menu_opens_dialog_and_posts_supervise(browser, static_server):
    from playwright.sync_api import expect
    world = SupervisionWorld(flag=True)
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator("#chatSupervisionSlot [data-sup-open=chip]").wait_for()
        frame.locator("#chatChromeMenuBtn").click()
        frame.locator("[data-sup-start]").click()
        form = frame.locator(".chat-supervision-dialog")
        expect(form).to_be_visible()
        expect(frame.locator("#chatChromeMenuDropdown")).to_be_hidden()
        expect(form.locator("[name=requiredBranch]")).to_have_value("dev/x")
        form.locator("[name=requiredBranch]").fill("main")
        form.locator("[type=submit]").click()
        expect(form.locator(".chat-supervision-errors")).to_contain_text("also in the forbidden list")
        form.locator("[name=requiredBranch]").fill("dev/x")
        form.locator("[name=agent]").select_option("codex")
        form.locator("[name=every]").select_option("600")
        form.locator("[type=submit]").click()
        expect(form).to_have_count(0)
        path, body = world.posts[-1]
        assert path == "/api/chat-ops/supervise"
        assert body["target"] == "CH-000042" and body["agent"] == "codex" and body["every"] == 600
        assert body["forbidden_branches"] == ["main", "master"]
        assert not errors
    finally:
        page.close()


def test_host_started_checkin_paints_promptly_as_cuttle(browser, static_server):
    """A check-in turn the browser didn't start shows its Cuttle-speaker bubble
    and the working indicator as soon as the shell reports the chat active."""
    from playwright.sync_api import expect
    world = SupervisionWorld(flag=False)
    world._append("assistant", "Earlier supervisor reply")
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        expect(frame.locator("#chatMessages .message.assistant")).to_have_count(1)
        page.wait_for_timeout(300)
        world._append("user", "/cursor Scheduled supervision check-in for CH-001548 (state: idle).")
        world.history[-1]["metadata"] = {
            "chat_op_id": "a" * 32, "speaker": "Cuttle", "speaker_kind": "parent", "avatar": "cuttle",
            "origin": "chat_ops", "parent_handle": "CH-000042", "parent_label": "Supervision check-in"}
        status = {"active": True, "generating": True, "status": "Agent is working",
                  "updated_at": time.time() + 1}
        world.live = dict(status)
        page.evaluate("s => document.querySelector('iframe').contentWindow.postMessage("
                      "{type: 'cuttle-live-status', sessionId: 42, status: s, focused: false}, '*')", status)
        bubble = frame.locator("#chatMessages .message.user.is-parent-speaker")
        expect(bubble).to_have_count(1, timeout=3000)
        expect(bubble).to_contain_text("Cuttle")
        expect(bubble).to_contain_text("Scheduled supervision check-in")
        expect(frame.locator("#typing-indicator-remote")).to_be_visible(timeout=3000)
        page.screenshot(path=os.environ.get("SUPERVISION_CHECKIN_SHOT", "/tmp/supervision_checkin.png"))
        assert not errors
    finally:
        page.close()
