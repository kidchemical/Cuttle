"""Action-card activation and lifetime through the production page (plan C2).

Drives the real chat page with an isolated API (no Flask, provider,
subprocess, model, or login) plus fake local status endpoints. The page's
``activateEnhancements`` wiring is exercised end to end: option submit posts
exactly once to ``/api/action-form/run`` with the card's own session, cancel
locks without resuming, persisted restart ids recover through the fake
``/api/flask/restart/status``, watch URLs poll until terminal, and removing
a card mid-watch leaves no page errors.

Covers C2 characterization only: repeated activation with one request, the
originating-card session rule, cancel/locked behavior, failed-run retry,
restart recovery, watch bars, and removal during pending watch/restart
requests.

Two card contracts are covered explicitly. The NORMAL production path is
server-issued `<cuttle_action_form_pending id="...">` markup
(`api.action_forms.rewrite_action_forms` assigns the id + HMAC signature,
stamps `session_id`, and the page pending parser hands the id to
`renderActionFormCard` as formId). Raw `<cuttle_action_form>` tags are the
FALLBACK path (no form id, no fallback token; the run body carries the
inline spec instead).
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

from .test_shared_diff_modal import (  # noqa: F401
    IsolatedAPI,
    apply_request_guard,
    browser,
    static_server,
)


def _sse(*events):
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events)


def _fulfill_chat(route, sid, text):
    route.fulfill(status=200, content_type="text/event-stream",
                  body=_sse({"type": "session", "session_id": sid},
                            {"type": "response", "success": True,
                             "response": text, "session_id": sid},
                            {"type": "done"}))


def _card(spec):
    """Raw-tag fallback card: no server id, no fallback token."""
    return "Card:\n<cuttle_action_form>" + json.dumps(spec) + \
        "</cuttle_action_form>"


def _pending(form_id, spec):
    """Faithful server-issued pending markup (id + signed spec body)."""
    body = dict(spec)
    body["id"] = form_id
    body.setdefault("sig", "sig-test")
    return ("Card:\n<cuttle_action_form_pending id=\"" + form_id + "\">\n"
            + json.dumps(body) + "\n</cuttle_action_form_pending>")


CARD_QA_FORM = _card({
    "mode": "form", "title": "Note", "session_id": "42",
    "fields": [{"id": "note", "label": "Note"}],
})

CARD_CHOICE = _card({
    "mode": "choice", "title": "Pick", "session_id": "42",
    "options": [{"id": "a", "label": "A"},
                {"id": "cancel", "label": "No thanks"}],
})

CARD_FOREIGN_SESSION = _card({
    "mode": "choice", "title": "Other chat card", "session_id": "99",
    "options": [{"id": "a", "label": "A"}],
})

CARD_RESTART = _card({
    "mode": "choice", "title": "Restart Flask", "session_id": "42",
    "locked": True, "restartId": "r-7",
    "options": [{"id": "graceful", "label": "Graceful",
                 "action": "flask.restart"}],
})

CARD_WATCH = _card({
    "mode": "choice", "title": "Bake", "session_id": "42",
    "watch": {"id": "job", "url": "/output/job-1.json"},
    "options": [{"id": "a", "label": "A"}],
})

PENDING_WATCH = _pending("form-9f2", {
    "mode": "choice", "title": "Bake", "session_id": "42",
    "watch": {"id": "job", "url": "/output/job-1.json"},
    "options": [{"id": "a", "label": "A"}],
})

PENDING_RESTART = _pending("flask-restart-g3", {
    "mode": "choice", "title": "Restart Flask", "session_id": "42",
    "locked": True, "restartId": "r-9",
    "restartFormGroup": "flask-restart-g3",
    "options": [{"id": "graceful", "label": "Graceful",
                 "action": "flask.restart"}],
})


class EffectsWorld:
    """Fake server: scripted chat replies, recorded action-form traffic."""

    def __init__(self, replies):
        now = time.time()
        self.history = [
            {"id": 1, "role": "user", "content": "seed",
             "timestamp": (now - 30) * 1000},
        ]
        self.next_id = 2
        self.replies = list(replies)
        self.run_posts = []
        self.watch_state_posts = []
        self.dismiss_posts = []
        self.restart_status_hits = 0
        self.job_polls = 0
        self.messages_hits = 0
        self.run_reply = {"success": True, "toast": "Done.",
                          "lock": "form", "selected": ["a"],
                          "session_id": "42"}
        self.run_replies = []  # optional per-click script, drained first
        self.chat_posts = 0  # POST /api/chat count (detects resume sends)
        self.restart_status = {"status": {"restart_id": "r-7",
                                          "state": "healthy"},
                               "live_generation": 3,
                               "restart_form_id": ""}
        self.job_finish = False  # when set, polls answer done

    def _append(self, role, content):
        row = {"id": self.next_id, "role": role, "content": content,
               "timestamp": time.time() * 1000}
        self.next_id += 1
        self.history.append(row)

    def _json(self, route, payload):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload))

    def handle(self, route):
        req = route.request
        parsed = urlparse(req.url)
        path = parsed.path
        if req.method == "POST" and path == "/api/chat":
            self.chat_posts += 1
            try:
                body = json.loads(req.post_data or "{}")
            except ValueError:
                body = {}
            self._append("user", body.get("message", ""))
            text = self.replies.pop(0) if self.replies else "noted"
            self._append("assistant", text)
            _fulfill_chat(route, 42, text)
            return
        if path == "/api/action-form/run":
            try:
                body = json.loads(req.post_data or "{}")
            except ValueError:
                body = {}
            self.run_posts.append(body)
            reply = (self.run_replies.pop(0) if self.run_replies
                     else self.run_reply)
            self._json(route, reply)
            return
        if path == "/api/action-form/watch-state":
            try:
                self.watch_state_posts.append(
                    json.loads(req.post_data or "{}"))
            except ValueError:
                pass
            self._json(route, {"success": True})
            return
        if path == "/api/action-form/dismiss":
            self.dismiss_posts.append(req.post_data)
            self._json(route, {"success": True})
            return
        if path == "/api/flask/restart/status":
            self.restart_status_hits += 1
            self._json(route, self.restart_status)
            return
        if path == "/output/job-1.json":
            self.job_polls += 1
            # Always fulfill immediately: holding lives in the test-side
            # browser fetch gate (see _GATE_JS), never in this handler.
            # Blocking a synchronous Playwright route callback stalls the
            # driver, so nothing here may wait on the test.
            if self.job_finish:
                self._json(route, {"state": "done", "percent": 100,
                                   "label": "Done", "run_id": "run-1",
                                   "bars": [{"id": "all", "percent": 100}]})
            else:
                self._json(route, {"state": "running", "percent": 10,
                                   "label": "Working", "run_id": "run-1",
                                   "bars": [{"id": "all", "percent": 10}]})
            return
        if path.endswith("/messages"):
            self.messages_hits += 1
            self._json(route, {"success": True, "session_name": "Fx",
                               "messages": list(self.history)})
            return
        if path == "/api/chat-live-status":
            self._json(route, {"active": False, "generating": False,
                               "status": None})
            return
        if path == "/api/auth/sessions":
            self._json(route, {"success": True,
                               "sessions": [{"id": 42,
                                             "session_name": "Fx"}]})
            return
        IsolatedAPI().handle(route)


# Test-side browser fetch gate: installed via add_init_script BEFORE page
# load, so every frame (including the chat iframe) wraps window.fetch
# before production code runs. Actual network is always delegated to the
# original fetch; only responses whose URL contains the exact gated path
# are withheld from production code until the test releases them. No
# production hooks, no Python blocking in route handlers, no runtime change.
_GATE_JS_TMPL = """\
(() => {
  const sub = %s;
  const origFetch = window.fetch.bind(window);
  const waiters = [];
  let released = false;
  window.__fetchGatePending = () => waiters.length;
  window.__fetchGateRelease = () => {
    released = true;
    const pending = waiters.splice(0);
    pending.forEach((resolve) => { try { resolve(); } catch (_) {} });
  };
  window.fetch = function (input, init) {
    let promise;
    try {
      promise = origFetch(input, init);
    } catch (err) {
      return Promise.reject(err);
    }
    let url = "";
    try {
      url = String((input && input.url) || input || "");
    } catch (_) { /* fall through ungated */ }
    if (new URL(url, window.location.href).pathname !== sub) return promise;
    return promise.then((resp) => {
      if (released) return resp;
      return new Promise((resolve) => { waiters.push(() => resolve(resp)); });
    });
  };
})();
"""


def _open(browser, static_server, world, fetch_gate_url=None):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    apply_request_guard(page.context, static_server)
    if fetch_gate_url is not None:
        page.add_init_script(_GATE_JS_TMPL % json.dumps(fetch_gate_url))
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", world.handle)
    page.route(f"{static_server}/output/**", world.handle)
    page.route(f"{static_server}/qa-host.html", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             " src=\"/chat_page.html?chat=42\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator("#chatMessages .message.user").first.wait_for(
        state="visible")
    return page, frame, errors


def _send(frame, text):
    frame.locator("#chatInput").fill(text)
    frame.locator("#sendButton").click()


def _send_and_wait_card(frame, text):
    _send(frame, text)
    card = frame.locator(".cuttle-action-form").last
    card.wait_for(state="visible")
    return card


def _gate_pending(frame):
    """Held (network-done, production-unseen) gated responses, or -1."""
    return int(frame.locator("html").evaluate(
        "() => (typeof window.__fetchGatePending === 'function'"
        " ? window.__fetchGatePending() : -1)"))


def _gate_release(frame):
    frame.locator("html").evaluate("() => window.__fetchGateRelease()")


def _seed_card_history(world, card_text, user_text="seed card"):
    """Load the card as a persisted history row: no optimistic send, so no
    send/history claiming can replace the painted node unexpectedly."""
    now = time.time()
    world.history = [
        {"id": 1, "role": "user", "content": user_text,
         "timestamp": (now - 30) * 1000},
        {"id": 2, "role": "assistant", "content": card_text,
         "timestamp": (now - 20) * 1000},
    ]
    world.next_id = 3


def _wait_history_card(frame, timeout=30000):
    card = frame.locator(".cuttle-action-form").first
    card.wait_for(state="visible", timeout=timeout)
    return card


def _remove_card_and_history_row(frame, world, marker):
    frame.locator(".cuttle-action-form").evaluate_all(
        "(els) => els.forEach((c) => c.remove())")
    world.history = [r for r in world.history
                     if marker not in str(r.get("content", ""))]


def test_qa_submit_posts_once_and_writes_one_answer(browser, static_server):
    world = EffectsWorld([CARD_QA_FORM, "noted"])
    world.run_reply = {"success": True, "toast": "Saved.", "lock": "form",
                       "selected": [], "session_id": "42",
                       "should_resume": True,
                       "injected_user_message": "[form-answers] note=hi"}
    page, frame, errors = _open(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show form")
        card.locator('[data-field-id="note"]').fill("hi")
        card.locator("[data-action-form-submit]").click()
        # The page renders the injected Q&A answer as "Answers note=hi".
        frame.get_by_text("note=hi").first.wait_for(
            state="visible", timeout=15000)
        assert len(world.run_posts) == 1
        assert str(world.run_posts[0].get("session_id")) == "42"
        assert world.run_posts[0].get("selection", {}).get("fields", {}).get(
            "note") == "hi"
        assert card.get_attribute("data-locked") == "1"
        users = frame.locator(".message.user")
        hits = sum(1 for i in range(users.count())
                   if "note=hi" in users.nth(i).inner_text())
        assert hits == 1  # this send is the only answer-bubble writer
        assert errors == []
    finally:
        page.close()


def test_cancel_option_locks_without_resume(browser, static_server):
    world = EffectsWorld([CARD_CHOICE, "noted"])
    world.run_reply = {"success": True, "toast": "Cancelled.",
                       "lock": "form", "selected": ["cancel"],
                       "session_id": "42"}
    page, frame, errors = _open(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show choice")
        assistants_before = frame.locator(".message.assistant").count()
        card.locator('[data-action-form-option="cancel"]').click()
        card.get_by_text("Cancelled.").first.wait_for(
            state="visible", timeout=15000)
        assert len(world.run_posts) == 1
        assert world.run_posts[0].get("selection", {}).get("cancel") is True
        assert card.get_attribute("data-locked") == "1"
        page.wait_for_timeout(1500)
        assert frame.locator(".message.assistant").count() == \
            assistants_before  # no resume bubble on cancel
        # Locked card sends nothing: re-enable the control (locked classes
        # stay) and fire the real handler — the locked guard must swallow it.
        frame.locator('[data-action-form-option="a"]').evaluate(
            "(el) => { el.disabled = false; el.click(); }")
        page.wait_for_timeout(1000)
        assert len(world.run_posts) == 1  # locked card sends nothing
        assert errors == []
    finally:
        page.close()


def test_failed_run_does_not_lock_and_retries(browser, static_server):
    # Errors never lock one-shot cards, so a bad run can be fixed and
    # retried: first click shows the error unlocked, second click posts
    # again and locks on success. No resume bubble follows the failure.
    world = EffectsWorld([CARD_CHOICE, "noted"])
    world.run_replies = [
        {"success": False, "toast": "Bad project path.",
         "session_id": "42"},
        {"success": True, "toast": "Done.", "lock": "form",
         "selected": ["a"], "session_id": "42"},
    ]
    page, frame, errors = _open(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show choice")
        assistants_before = frame.locator(".message.assistant").count()
        card.locator('[data-action-form-option="a"]').click()
        card.get_by_text("Bad project path.").first.wait_for(
            state="visible", timeout=15000)
        assert len(world.run_posts) == 1
        assert card.get_attribute("data-locked") == "0"
        page.wait_for_timeout(1000)
        assert frame.locator(".message.assistant").count() == \
            assistants_before
        card.locator('[data-action-form-option="a"]').click()
        card.get_by_text("Done.").first.wait_for(
            state="visible", timeout=15000)
        assert len(world.run_posts) == 2
        assert card.get_attribute("data-locked") == "1"
        assert errors == []
    finally:
        page.close()


def test_run_uses_originating_card_session(browser, static_server):
    world = EffectsWorld([CARD_FOREIGN_SESSION, "noted"])
    world.run_reply = {"success": True, "toast": "Recorded.",
                       "lock": "form", "selected": ["a"],
                       "session_id": "99", "should_resume": True,
                       "injected_user_message": "[form-answers] foreign"}
    page, frame, errors = _open(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show foreign")
        assert card.get_attribute("data-session-id") == "99"
        card.locator('[data-action-form-option="a"]').click()
        card.get_by_text("Recorded.").first.wait_for(
            state="visible", timeout=15000)
        assert len(world.run_posts) == 1
        assert str(world.run_posts[0].get("session_id")) == "99"
        page.wait_for_timeout(1500)
        assert frame.get_by_text("[form-answers] foreign").count() == 0
        assert errors == []
    finally:
        page.close()


def test_raw_persisted_restart_card_recovers(browser, static_server):
    # Fallback path (raw tag with a restartId stamp): the locked card still
    # resumes its watcher from data-restart-id. The pending-contract removal
    # case is covered separately below.
    world = EffectsWorld([CARD_RESTART])
    page, frame, errors = _open(browser, static_server, world)
    try:
        msgs_before = world.messages_hits
        card = _send_and_wait_card(frame, "show restart")
        assert card.get_attribute("data-locked") == "1"
        assert card.get_attribute("data-restart-id") == "r-7"
        card.get_by_text("Flask restarted").first.wait_for(
            state="visible", timeout=20000)
        assert world.restart_status_hits >= 1
        assert world.messages_hits > msgs_before  # recovery sync ran
        assert "is-pending" not in (card.get_attribute("class") or "")
        assert errors == []
    finally:
        page.close()


def test_raw_watch_card_without_form_id_skips_persist(browser, static_server):
    # Fallback-path characterization (raw tag, no server id): the page
    # cannot persist watch snapshots without a form id, so no watch-state
    # POST is expected here. The normal pending contract is covered below.
    world = EffectsWorld([CARD_WATCH])
    page, frame, errors = _open(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show watch")
        card.get_by_text("Working").first.wait_for(state="visible", timeout=15000)
        world.job_finish = True
        card.get_by_text("Done").first.wait_for(
            state="visible", timeout=25000)
        assert world.job_polls >= 2
        assert card.get_attribute("data-locked") == "1"
        assert card.get_attribute("data-form-id") == ""
        assert world.watch_state_posts == []
        assert errors == []
    finally:
        page.close()


def test_pending_watch_card_persists_snapshot_and_terminal(
        browser, static_server):
    # Normal production contract: server-issued pending markup carries the
    # registered form id, and watch snapshots + terminal state persist with
    # the form id and originating session. The option click proves the run
    # token preserves the same registered identity.
    world = EffectsWorld([PENDING_WATCH])
    world.run_reply = {"success": True, "toast": "Recorded.",
                       "lock": "form", "selected": ["a"],
                       "session_id": "42"}
    page, frame, errors = _open(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show pending watch")
        assert card.get_attribute("data-form-id") == "form-9f2"
        # Click while the job is still running: terminal Done is only
        # served after job_finish is set below, so no race is possible.
        card.locator('[data-action-form-option="a"]').click()
        card.get_by_text("Recorded.").first.wait_for(
            state="visible", timeout=15000)
        assert len(world.run_posts) == 1
        assert world.run_posts[0].get("token") == "form-9f2"
        assert str(world.run_posts[0].get("session_id")) == "42"
        world.job_finish = True
        # The run locked the card, so Done lives in the hidden progress
        # label: wait on the persisted terminal post, not visible text.
        deadline = time.time() + 25
        while not any(p.get("terminal")
                      for p in world.watch_state_posts) \
                and time.time() < deadline:
            page.wait_for_timeout(250)
        assert world.job_polls >= 2
        assert len(world.watch_state_posts) >= 2
        assert all(p.get("form_id") == "form-9f2"
                   for p in world.watch_state_posts)
        assert all(str(p.get("session_id")) == "42"
                   for p in world.watch_state_posts)
        terminals = [p for p in world.watch_state_posts if p.get("terminal")]
        assert len(terminals) >= 1
        assert errors == []
    finally:
        page.close()


def test_removed_card_with_held_request_settles_cleanly(
        browser, static_server):
    # Fetch-gate mechanism: the exact job-status network response completes
    # but is withheld from production code until test release — nothing
    # blocks inside a route handler. The card loads as a persisted history
    # row (no optimistic send), and removal also drops its history row, so
    # periodic sync cannot recreate it.
    world = EffectsWorld([])
    _seed_card_history(world, PENDING_WATCH)
    world.job_finish = True  # the held response is terminal done
    page, frame, errors = _open(browser, static_server, world,
                                fetch_gate_url="/output/job-1.json")
    try:
        card = _wait_history_card(frame)
        assert card.get_attribute("data-form-id") == "form-9f2"
        deadline = time.time() + 20
        while _gate_pending(frame) < 1 and time.time() < deadline:
            page.wait_for_timeout(250)
        assert _gate_pending(frame) >= 1  # held: nothing resolved yet
        assert world.job_polls >= 1  # the held request really went out
        assert world.watch_state_posts == []
        _remove_card_and_history_row(
            frame, world, "cuttle_action_form_pending")
        # Instant of removal: responses still pending, card gone.
        assert _gate_pending(frame) >= 1
        assert frame.locator(".cuttle-action-form").count() == 0
        # No resurrection across real subsequent history syncs: the row is
        # gone, so polls must not repaint the card.
        syncs_before = world.messages_hits
        deadline = time.time() + 30
        while world.messages_hits < syncs_before + 2 \
                and time.time() < deadline:
            page.wait_for_timeout(250)
        assert world.messages_hits >= syncs_before + 2
        assert frame.locator(".cuttle-action-form").count() == 0
        assert _gate_pending(frame) >= 1  # still nothing resolved
        assert world.watch_state_posts == []
        polls_at_release = world.job_polls
        _gate_release(frame)  # held responses complete terminal done
        page.wait_for_timeout(4000)  # late iteration would finish by now
        # Desired C2a lifecycle: a detached card applies nothing — no
        # progress paint, no snapshot/terminal persistence, no resume.
        # The running marker is cleared on the detachment exit, so the
        # settled loop issues no further polls either.
        assert world.watch_state_posts == []
        assert world.job_polls == polls_at_release
        assert world.chat_posts == 0  # no send here, no resume send
        assert frame.locator(".cuttle-action-form").count() == 0
        assert errors == []
    finally:
        page.close()


def test_removed_pending_restart_card_stops_polling(browser, static_server):
    # Desired C2a lifecycle: the restart watcher stops issuing requests
    # once its card is detached. The card loads as a persisted history
    # row, and removal also drops its history row, so a zero card count
    # with flat request counts proves the poller exited — not a repaint.
    world = EffectsWorld([])
    _seed_card_history(world, PENDING_RESTART)
    world.restart_status = {"status": {"restart_id": "r-9",
                                       "state": "starting_new_flask"},
                            "live_generation": 3, "restart_form_id": ""}
    page, frame, errors = _open(browser, static_server, world)
    try:
        card = _wait_history_card(frame)
        assert card.get_attribute("data-locked") == "1"
        assert card.get_attribute("data-restart-id") == "r-9"
        deadline = time.time() + 15
        while world.restart_status_hits < 1 and time.time() < deadline:
            page.wait_for_timeout(250)
        assert world.restart_status_hits >= 1
        _remove_card_and_history_row(
            frame, world, "cuttle_action_form_pending")
        assert frame.locator(".cuttle-action-form").count() == 0
        hits_at_removal = world.restart_status_hits
        page.wait_for_timeout(6000)  # ~3 polls at the 2 s cadence
        assert world.restart_status_hits == hits_at_removal
        assert frame.locator(".cuttle-action-form").count() == 0
        assert errors == []
    finally:
        page.close()


def test_held_restart_response_after_removal_no_late_effects(
        browser, static_server):
    # A restart-status response is genuinely pending (fetch gate) when the
    # card is removed; releasing it terminal-healthy afterwards must cause
    # no detached UI mutation and no recovery message sync. Anchors the
    # release right after a periodic sync so any watcher-triggered fetch
    # would land inside the 4 s window (idle cadence is 12 s).
    world = EffectsWorld([])
    _seed_card_history(world, PENDING_RESTART)
    world.restart_status = {"status": {"restart_id": "r-9",
                                       "state": "healthy"},
                            "live_generation": 3, "restart_form_id": ""}
    page, frame, errors = _open(browser, static_server, world,
                                fetch_gate_url="/api/flask/restart/status")
    try:
        card = _wait_history_card(frame)
        assert card.get_attribute("data-locked") == "1"
        assert card.get_attribute("data-restart-id") == "r-9"
        deadline = time.time() + 20
        while _gate_pending(frame) < 1 and time.time() < deadline:
            page.wait_for_timeout(250)
        assert _gate_pending(frame) >= 1  # genuinely pending at removal
        assert world.restart_status_hits >= 1
        _remove_card_and_history_row(
            frame, world, "cuttle_action_form_pending")
        assert frame.locator(".cuttle-action-form").count() == 0
        # Anchor: wait for a periodic sync, then release immediately.
        syncs_before = world.messages_hits
        deadline = time.time() + 30
        while world.messages_hits <= syncs_before \
                and time.time() < deadline:
            page.wait_for_timeout(250)
        assert world.messages_hits > syncs_before
        hits_at_release = world.restart_status_hits
        msgs_at_release = world.messages_hits
        _gate_release(frame)  # held healthy response now completes
        page.wait_for_timeout(4000)
        assert world.restart_status_hits == hits_at_release
        assert world.messages_hits == msgs_at_release
        assert frame.locator(".cuttle-action-form").count() == 0
        assert errors == []
    finally:
        page.close()


def test_resync_repaint_still_submits_once(browser, static_server):
    world = EffectsWorld([CARD_CHOICE])
    page, frame, errors = _open(browser, static_server, world)
    try:
        card = _send_and_wait_card(frame, "show choice")
        # Server-side edit of the painted row: the periodic history sync must
        # repaint this message in place (update-in-place) and re-activate it.
        # No follow-up send here: sending would dismiss-lock the open card.
        for row in world.history:
            if row["role"] == "assistant" and "cuttle_action_form" in str(
                    row["content"]):
                row["content"] = str(row["content"]) + "\n(sync edited)"
        frame.get_by_text("(sync edited)").first.wait_for(
            state="visible", timeout=30000)
        option = frame.locator(".cuttle-action-form").last.locator(
            '[data-action-form-option="a"]')
        option.wait_for(state="visible", timeout=30000)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if option.is_enabled():
                    break
            except Exception:
                pass
            page.wait_for_timeout(250)
        assert option.is_enabled()
        assert frame.locator(".cuttle-action-form").last.get_attribute(
            "data-locked") == "0"
        option.click()
        card.get_by_text("Done.").first.wait_for(
            state="visible", timeout=15000)
        page.wait_for_timeout(1000)
        assert len(world.run_posts) == 1  # re-activation did not duplicate
        assert errors == []
    finally:
        page.close()
