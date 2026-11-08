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
const paused = Object.assign({}, sched, {state: 'paused', paused_at: now - 3 * 86400});
const v = M.view({supervising: [sched], supervised_by: [paused],
  queued: [{id: 'q1', from: 'CH-000001', text: 'next\n  step'}]}, now, {locale: 'en-US'});
const timed = M.view({supervising: [Object.assign({}, paused, {paused_until: now + 3600})]},
  now, {locale: 'en-US'});
const bad = M.supervisePayload({target: 'CH-000002', agent: '', every: 7, instructions: ' ',
  requiredBranch: 'main', forbidden: 'main, master'});
const good = M.supervisePayload(Object.assign(M.dialogDefaults({handle: 'CH-000002', branch: 'dev/x'}),
  {agent: 'codex', worker: 'cursor auto, codex terra', forbidden: ' main ,, master '}));
console.log(JSON.stringify({
  intervals: [300, 3600, 5400, 45].map(M.fmtInterval),
  chip: v.chips[0], badge: v.badges[0], queued: v.queued[0], warn: v.warn, empty: v.empty,
  emptyView: M.view({}, now), due: M.nextLabel(Object.assign({}, sched, {next_check: now - 1}), now),
  pausedChip: timed.chips[0], pauseChoices: M.PAUSE_CHOICES.map((c) => c.id),
  pauseBodies: ['1h', '4h', 'indefinite', 'nonsense'].map((id) => M.pauseBody(id, now * 1000)),
  tomorrow: M.pauseBody('tomorrow', now * 1000),
  bad, good,
  pins: M.pinChoices({models: ['a', {id: 'b', label: 'B'}], efforts: ['low'], model_efforts: {b: ['x', 'y']}}, 'b'),
  noPins: M.pinChoices({supports_model_pin: false, supports_effort: false, models: ['a'], efforts: ['low']}, ''),
  picked: M.supervisePayload(Object.assign(M.dialogDefaults({handle: 'CH-000002',
    tasks: [{id: 'own', title: 'Own', selected: true}, {id: 'proj', scope: 'project'}]}),
    {agent: 'codex', model: 'b', effort: 'x', taskIds: ['proj', 'ghost']})).body,
  edit: M.dialogDefaults({handle: 'CH-000002', tasks: [{id: 'own', selected: true}, {id: 'proj'}]},
    {id: 's9', agent: 'codex', model: 'b', effort: 'x', every: 600, instructions: 'mine',
     forbidden_branches: ['main', 'prod'], model_tiers: {worker: ['cursor auto']}, task_ids: ['proj']}),
  pbDefaults: M.dialogDefaults({handle: 'CH-000002', playbook: {id: 'pb', name: 'Strict', every: 600, agent: 'codex',
    instructions: 'Be strict', forbidden_branches: ['main'], model_tiers: {worker: ['cursor auto']},
    checkpoints: {tests: {when: 'fail', do: ['supervisor:rev']}}}}),
  found: M.scheduleById({supervising: [{id: 'a'}], supervised_by: [{id: 'b', target: 'CH-000002'}]}, 'b'),
  opBadges: [
    {origin: 'chat_ops', chat_op_kind: 'checkin', parent_label: 'Supervision check-in'},
    {origin: 'chat_ops', parent_label: 'Supervision check-in'},
    {origin: 'chat_ops', chat_op_kind: 'send', parent_label: 'Sent by CH-000007'},
    {origin: 'chat_ops', parent_label: 'Background work finished'},
    {origin: 'subagent', parent_label: 'Parent chat'}, null,
  ].map((o) => M.chatOpBadge(o)),
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
    # Rows Cuttle sent (not the user) name their kind; older rows read it from the label.
    assert [b and (b["kind"], b["label"]) for b in r["opBadges"]] == [
        ("checkin", "Supervision check-in"), ("checkin", "Supervision check-in"),
        ("send", "Sent by CH-000007"), ("notice", "Background work finished"), None, None]
    chip = r["chip"]
    assert chip["text"].startswith("Supervising CH-000002 · every 15m · next ")
    assert chip["rules"] == "stay on dev/x · never main" and chip["tasks"] == "2/5 tasks done"
    assert [h["text"] for h in chip["history"]] == [
        "skipped, busy", "skipped, no change",
        "woke supervisor (idle_with_work), previous check-in still pending",
        "rule broken, woke supervisor (busy)"]
    assert [h["tone"] for h in chip["history"]] == ["muted", "muted", "active", "warn"]
    assert r["badge"]["paused"] is True
    # A paused schedule says since when, right on the badge — never a dimmed chip alone.
    assert r["badge"]["text"].startswith("Supervised by CH-000001 · Paused since ")
    assert r["badge"]["text"].endswith(" · no auto-resume")
    assert r["badge"]["pausedText"] == r["badge"]["text"].split(" · ", 1)[1]
    assert r["badge"]["next"] == "paused"
    assert r["pausedChip"]["text"].startswith("Supervising CH-000002 · Paused since ")
    assert " · resumes " in r["pausedChip"]["text"]
    # Pausing offers timed options first and an indefinite one last.
    assert r["pauseChoices"] == ["1h", "4h", "tomorrow", "indefinite"]
    assert r["pins"] == {"models": [{"id": "a", "label": "a"}, {"id": "b", "label": "B"}], "efforts": ["x", "y"]}
    assert r["noPins"] == {"models": [], "efforts": []}
    assert r["picked"]["model"] == "b" and r["picked"]["effort"] == "x"
    assert r["picked"]["task_ids"] == ["proj"]  # unknown ids dropped
    assert "task_ids" not in r["good"]["body"]  # no lists offered: follow whatever appears
    e = r["edit"]
    assert e["editId"] == "s9" and e["agent"] == "codex" and e["model"] == "b" and e["every"] == 600
    assert e["forbidden"] == "main, prod" and e["worker"] == "cursor auto" and e["instructions"] == "mine"
    assert [(t["id"], t["selected"]) for t in e["tasks"]] == [("own", False), ("proj", True)]
    assert r["found"]["target"] == "CH-000002"
    pb = r["pbDefaults"]
    assert pb["playbookName"] == "Strict" and pb["every"] == 600 and pb["agent"] == "codex"
    assert pb["forbidden"] == "main" and pb["worker"] == "cursor auto" and pb["instructions"] == "Be strict"
    assert {k: v["on"] for k, v in pb["checkpoints"].items()} == {
        "milestone": False, "tests": True, "repeat_failures": False, "watched_paths": False}
    assert pb["checkpoints"]["tests"]["do"] == "supervisor:rev" and pb["checkpoints"]["tests"]["when"] == "fail"
    assert r["pauseBodies"] == [{"hours": 1}, {"hours": 4}, {"indefinite": True}, {"hours": 1}]
    assert r["tomorrow"]["until"] > 1_800_000_000
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
                    self.overview["supervising"][0]["paused_at"] = time.time() - 120
                    body = json.loads(req.post_data or "{}")
                    if body.get("hours"):
                        self.overview["supervising"][0]["paused_until"] = (
                            time.time() + float(body["hours"]) * 3600)
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
        pop.locator("[data-sup-act=pause-menu]").click()
        expect(pop.locator(".chat-supervision-pause-menu")).to_contain_text("Pause for how long?")
        pop.locator("[data-sup-pause='1h']").click()
        frame.locator("[data-sup-open=chip].is-paused").wait_for()
        assert world.posts[-1] == ("/api/chat-ops/operations/sup1/pause", {"hours": 1})
        expect(frame.locator("[data-sup-open=chip]")).to_contain_text("Paused since")

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
        form.locator('.chat-supervision-advanced summary').click()
        expect(form.locator("[name=requiredBranch]")).to_have_value("dev/x")
        form.locator("[name=requiredBranch]").fill("main")
        form.locator("[type=submit]").click()
        expect(form.locator(".chat-supervision-errors")).to_contain_text("also in the forbidden list")
        form.locator("[name=requiredBranch]").fill("dev/x")
        form.get_by_role('button', name='Supervisor agent', exact=True).click()
        frame.get_by_role('option', name='Codex', exact=True).click()
        form.get_by_role('button', name='Check every', exact=True).click()
        frame.get_by_role('option', name='10m', exact=True).click()
        form.locator("[type=submit]").click()
        expect(form).to_have_count(0)
        path, body = world.posts[-1]
        assert path == "/api/chat-ops/supervise"
        assert body["target"] == "CH-000042" and body["agent"] == "codex" and body["every"] == 600
        assert body["forbidden_branches"] == ["main", "master"]
        # New schedules wake on milestones and repeated failures by default.
        assert body["checkpoints"] == {"milestone": {"do": ["wake"]}, "repeat_failures": {"do": ["wake"], "n": 3}}
        assert not errors
    finally:
        page.close()


def test_dialog_checkpoint_rows_post_their_settings(browser, static_server):
    from playwright.sync_api import expect
    world = SupervisionWorld(flag=True)
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator("#chatChromeMenuBtn").click()
        frame.locator("[data-sup-start]").click()
        form = frame.locator(".chat-supervision-dialog")
        expect(form).to_be_visible()
        watched = form.locator("[name=cp_on_watched_paths]")
        watched.scroll_into_view_if_needed()
        expect(form.locator("[name=cp_regex_watched_paths]")).to_be_hidden()  # options show once switched on
        watched.click()
        form.locator("[name=cp_regex_watched_paths]").fill("^api/")
        form.locator("[name=cp_on_milestone]").click()  # off
        page.screenshot(path=os.environ.get("SUPERVISION_DIALOG_SHOT", "/tmp/supervision_dialog.png"))
        form.locator("[type=submit]").click()
        expect(form).to_have_count(0)
        body = world.posts[-1][1]
        assert body["checkpoints"] == {"repeat_failures": {"do": ["wake"], "n": 3},
                                       "watched_paths": {"do": ["stop", "wake"], "regex": "^api/"}}
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
        # Distinct from a typed message: kind attribute, header badge, no accent fill.
        expect(bubble).to_have_attribute("data-chat-op", "checkin")
        expect(bubble.locator(".message-header-row")).to_contain_text("Supervision check-in")
        fill = bubble.locator(".message-content").evaluate("e => getComputedStyle(e).borderTopStyle")
        assert fill == "dashed"
        image = bubble.locator(".message-content").evaluate("e => getComputedStyle(e).backgroundImage")
        assert image == "none"  # not the user's accent gradient
        # Its parent is this same chat, so no launcher pill repeats the label.
        expect(bubble.locator(".subagent-launchers, .subagent-launcher")).to_have_count(0)
        page.wait_for_timeout(800)
        expect(frame.locator("#typing-indicator-remote")).to_be_visible(timeout=3000)
        page.screenshot(path=os.environ.get("SUPERVISION_CHECKIN_SHOT", "/tmp/supervision_checkin.png"))
        assert not errors
    finally:
        page.close()


def test_dialog_closes_on_escape_right_after_it_opens(browser, static_server):
    from playwright.sync_api import expect
    world = SupervisionWorld(flag=True)
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        frame.locator("#chatChromeMenuBtn").click()
        frame.locator("[data-sup-start]").click()
        form = frame.locator(".chat-supervision-dialog")
        expect(form).to_be_visible()
        page.keyboard.press("Escape")  # focus starts on a dropdown trigger, which must not swallow it
        expect(form).to_have_count(0)
        assert world.posts == []
        assert not errors
    finally:
        page.close()
