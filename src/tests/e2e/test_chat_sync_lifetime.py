"""Sync-lifetime staleness characterization for the production chat page.

Uses actual production chat assets (chat_page.html in the qa-host iframe)
with the existing static/browser/exact-origin guard fixtures, fresh
storage per test, and explicitly controlled fake histories. No live app,
database, provider, or daemon. Transport only is intercepted, faithfully:
the page-side fetch wrapper returns a REAL native Response immediately
(headers + status, so production fetchWithTimeout clears its header
timer), while the BODY is a held native ReadableStream whose
enqueue/close the test controls. The fake API's bytes still flow in the
background and are released verbatim unless a test stages an explicit
stale/malformed override. Request aborts error the held body with an
AbortError and reject late release. The hold decision is captured
synchronously at request start via count-based arms, so later requests
can never be accidentally held by a changed flag. Per-request timing
(tFetchReturned/tBodyReadStarted/tBodySettled/aborted) proves fetch
returned while JSON decoding was still pending. Syncs are triggered
through the page's own public focus/visibility handler; a Playwright
clock advances the 20s in-flight policy without touching production
constants.

Hazards (executive preflight, reproduced on the faithful body
fixture, now inverted to regression expectations by the D2a ownership
fix — request identity + captured _loadSessionSeq + session match before
any body-derived effect, token-scoped claim cleanup):
  H1 stale malformed body must never paint waiting after A->B.
  H2 A->B->A stale body must not revert the newer A title.
  H3 stale completion must not permit a third GET while the newer body
     is pending; a fresh sync runs after current completion.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlparse, parse_qs

import pytest

from .test_shared_diff_modal import (  # noqa: F401
    IsolatedAPI,
    apply_request_guard,
    browser,
    static_server,
)

SID_A = 1001
SID_B = 1002

FETCH_HOOK = """\
window.__d2 = { seq: 0, arms: [], log: [], held: [] };
window.__d2enc = new TextEncoder();
window.__d2arm = function (substr, n) {
  window.__d2.arms.push({ substr: String(substr), n: (n || 1) });
};
(function () {
  const raw = window.fetch.bind(window);
  window.fetch = function (url, opts) {
    const u = String((url && url.url) || url);
    const m = u.match(/\\/api\\/auth\\/sessions\\/(\\d+)\\/messages/);
    if (!m) return raw(url, opts);
    const id = ++window.__d2.seq;
    const entry = { id: id, url: u, tReq: Date.now(), tFetchReturned: 0,
      tBodyReadStarted: 0, tBodySettled: 0, aborted: false,
      held: false, released: false, _ctrl: null, _rawText: null };
    window.__d2.log.push(entry);
    // Hold decision is captured HERE at request start, never later.
    let hold = false;
    for (const arm of window.__d2.arms) {
      if (arm.n > 0 && u.indexOf(arm.substr) !== -1) {
        arm.n--; hold = true; break;
      }
    }
    const signal = opts && opts.signal;
    let streamCtrl = null;
    const onAbort = function () {
      entry.aborted = true;
      if (streamCtrl) {
        try {
          streamCtrl.error(new DOMException(
            'The operation was aborted.', 'AbortError'));
        } catch (_) {}
      }
    };
    if (signal) {
      if (signal.aborted) onAbort();
      else signal.addEventListener('abort', onAbort, { once: true });
    }
    // The fake API's bytes flow in the background; held bodies release
    // them verbatim (or an explicit test override) through the stream.
    const bg = raw(url, opts).then((r) => r.text()).then((t) => {
      entry._rawText = t;
      return t;
    }, () => {});
    // Observation only: record when the page starts/finishes decoding
    // every messages body, held or passing through.
    const instrument = function (resp) {
      const nativeJson = resp.json.bind(resp);
      resp.json = function () {
        if (!entry.tBodyReadStarted) entry.tBodyReadStarted = Date.now();
        return nativeJson().then((v) => {
          entry.tBodySettled = Date.now(); return v;
        }, (e) => {
          entry.tBodySettled = Date.now(); throw e;
        });
      };
      return resp;
    };
    if (!hold) {
      return bg.then((t) => {
        entry.tFetchReturned = Date.now();
        return instrument(new Response(t, { status: 200,
          headers: { 'content-type': 'application/json' } }));
      });
    }
    const stream = new ReadableStream({
      start: function (c) { streamCtrl = c; entry._ctrl = c; },
    });
    const resp = instrument(new Response(stream, { status: 200,
      headers: { 'content-type': 'application/json' } }));
    entry.tFetchReturned = Date.now();
    entry.held = true;
    window.__d2.held.push(entry);
    return Promise.resolve(resp);
  };
})();
window.__d2heldIds = () => window.__d2.held
  .filter((e) => !e.released && !e.aborted).map((e) => e.id);
window.__d2pendingIds = () => window.__d2.held.filter((e) =>
  !e.released && !e.aborted && e.tBodyReadStarted > 0 && !e.tBodySettled)
  .map((e) => e.id);
window.__d2release = (id, override) => {
  const e = window.__d2.held.find((x) => x.id === id);
  if (!e) throw new Error('d2: no held entry ' + id);
  if (e.released) throw new Error('d2: double release ' + id);
  if (e.aborted) throw new Error('d2: late release after abort ' + id);
  const body = (override === undefined) ? e._rawText : override;
  if (body == null) throw new Error('d2: raw bytes not arrived for ' + id);
  e.released = true;
  e._ctrl.enqueue(window.__d2enc.encode(String(body)));
  e._ctrl.close();
};
window.__d2log = () => window.__d2.log.map((e) => (
  { id: e.id, url: e.url, tReq: e.tReq,
    tFetchReturned: e.tFetchReturned, tBodyReadStarted: e.tBodyReadStarted,
    tBodySettled: e.tBodySettled, aborted: e.aborted,
    held: e.held, released: e.released }));
"""


def _msg(mid, role, content):
    return {"id": mid, "role": role, "content": content,
            "timestamp": time.time() * 1000}


class SyncWorld:
    """Per-session fake chat transport with request identity logging."""

    def __init__(self):
        self.scenario = {
            str(SID_A): {
                "session_name": "Alpha-NEW",
                "followups": [],
                "messages": [_msg(1, "user", "alpha question"),
                             _msg(2, "assistant", "alpha answer")],
                "live": {"active": False, "generating": False,
                         "status": None},
            },
            str(SID_B): {
                "session_name": "Beta",
                "followups": [],
                "messages": [_msg(11, "user", "beta question")],
                "live": {"active": False, "generating": False,
                         "status": None},
            },
        }
        self.chat_posts = []
        self.live_seen = []

    def handle(self, route):
        req = route.request
        parsed = urlparse(req.url)
        path = parsed.path
        if req.method == "POST" and path == "/api/chat":
            self.chat_posts.append(req.post_data)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": False}))
            return
        if path == "/api/auth/sessions":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True, "sessions": [
                              {"id": SID_A, "session_name": "Alpha-NEW"},
                              {"id": SID_B, "session_name": "Beta"}]}))
            return
        if "/messages" in path:
            sid = path.rstrip("/").split("/")[-2]
            sc = self.scenario.get(sid, self.scenario[str(SID_A)])
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True,
                                           "session_name": sc["session_name"],
                                           "followups": sc["followups"],
                                           "messages": sc["messages"]}))
            return
        if path == "/api/chat-live-status":
            sid = parse_qs(parsed.query).get("session_id", [""])[0]
            self.live_seen.append(sid)
            live = self.scenario.get(sid, {}).get("live", {"active": False})
            # Production fetchChatLiveStatus keeps the payload ONLY when
            # data.success is truthy; without it the snapshot is null.
            payload = {"success": True}
            payload.update(live)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(payload))
            return
        IsolatedAPI().handle(route)


def _open(browser, static_server, world, sid):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    apply_request_guard(page.context, static_server)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.add_init_script(FETCH_HOOK)
    page.route(f"{static_server}/api/**", world.handle)
    page.route(f"{static_server}/qa-host.html", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             f" src=\"/chat_page.html?chat={sid}\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator("#chatMessages .message.user").first.wait_for(
        state="visible")
    return page, frame, errors


def _pump_until(page, predicate, timeout_ms, label):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting: {label}")
        page.wait_for_timeout(200)


def _ef(page):
    """Real Frame object for evaluate (FrameLocator only does locators)."""
    for f in page.frames:
        if "chat_page" in (f.url or ""):
            return f
    raise AssertionError("chat iframe not found")


def _log(page, frame):
    return _ef(page).evaluate("window.__d2log()")


def _held(page, frame):
    return _ef(page).evaluate("window.__d2heldIds()")


def _pending(page, frame):
    """Held bodies the page has started decoding but not finished."""
    return _ef(page).evaluate("window.__d2pendingIds()")


def _entry(page, frame, hid):
    for e in _log(page, frame):
        if e["id"] == hid:
            return e
    raise AssertionError(f"no log entry {hid}")


def _sync(page, frame):
    """Trigger the page's own visible-focus sync handler (public event)."""
    _ef(page).evaluate("window.dispatchEvent(new FocusEvent('focus'))")


def _arm(page, frame, substr, n=1):
    _ef(page).evaluate(f"window.__d2arm({json.dumps(substr)}, {n})")


def _release(page, frame, hid, override=None):
    arg = "undefined" if override is None else json.dumps(override)
    _ef(page).evaluate(f"window.__d2release({hid}, {arg})")


def _title(page, frame):
    return _ef(page).evaluate(
        "String(document.getElementById('chatSessionTitleText')"
        ".textContent || '')")


def _goto_session(page, frame, sid, painted_text):
    """Real navigation UI: history panel button, then the session item."""
    panel_btn = frame.locator("#chatHistoryPanelButton")
    if panel_btn.count() and panel_btn.first.is_visible():
        panel_btn.first.click()
        page.wait_for_timeout(800)
    item = frame.locator(
        f".chat-history-item[data-session-id='{sid}']").first
    item.wait_for(state="visible", timeout=10000)
    item.click()
    frame.get_by_text(painted_text).first.wait_for(
        state="visible", timeout=15000)


def _assert_body_pending(page, frame, hid, label):
    """Prove fetch returned while JSON decoding is still pending."""
    e = _entry(page, frame, hid)
    assert e["tFetchReturned"] > 0, f"{label}: fetch never returned"
    assert e["tBodyReadStarted"] > 0, f"{label}: decode never started"
    assert e["tBodySettled"] == 0, f"{label}: body already settled"
    assert e["aborted"] is False, f"{label}: body aborted"


def test_sync_paints_seeded_rows(browser, static_server):
    """Positive control: unheld sync paints fake rows; fixture can drive."""
    world = SyncWorld()
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        frame.get_by_text("alpha answer").wait_for(state="visible")
        before = len(_log(page, frame))
        _sync(page, frame)
        _pump_until(page, lambda: len(_log(page, frame)) > before, 15000,
                    "focus sync GET")
        _pump_until(page, lambda: any(
            e["tBodySettled"] > 0 for e in _log(page, frame)[before:]),
                    15000, "focus sync completion")
        assert frame.get_by_text("alpha answer").count() >= 1
        assert errors == []
    finally:
        page.close()


def test_fresh_overlap_coalesces(browser, static_server):
    """Positive control: second sync while fresh does NOT start a new GET."""
    world = SyncWorld()
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        _arm(page, frame, f"sessions/{SID_A}/messages", 1)
        _sync(page, frame)
        _pump_until(page, lambda: len(_pending(page, frame)) == 1, 15000,
                    "first sync body pending")
        hid = _pending(page, frame)[0]
        _assert_body_pending(page, frame, hid, "first sync")
        n_gets = len(_log(page, frame))
        _sync(page, frame)
        page.wait_for_timeout(1500)
        assert len(_log(page, frame)) == n_gets
        _release(page, frame, hid)
        _pump_until(page, lambda: all(
            e["tBodySettled"] > 0 for e in _log(page, frame)), 15000,
                    "held sync completion")
        assert errors == []
    finally:
        page.close()


def test_stale_malformed_sync_does_not_paint_after_switch(
        browser, static_server):
    """H1: stale malformed A body resolves in B with old live-status active.

    The held A body (not delayed headers) resolves after the switch; the
    old active snapshot must not reach remote-wait paint in the new chat.
    """
    world = SyncWorld()
    world.scenario[str(SID_A)]["live"] = {
        "active": True, "generating": True, "status": "A still running"}
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        _arm(page, frame, f"sessions/{SID_A}/messages", 1)
        _sync(page, frame)
        _pump_until(page, lambda: len(_pending(page, frame)) == 1, 15000,
                    "A sync body pending")
        held_id = _pending(page, frame)[0]
        _assert_body_pending(page, frame, held_id, "A sync")
        _goto_session(page, frame, SID_B, "beta question")
        # Barrier: still exactly one held body, still decoding-pending.
        assert _pending(page, frame) == [held_id]
        _release(page, frame, held_id, '{"success":false}')
        # Poll transiently: a later B sync may heal a stale paint, so the
        # settled state alone cannot distinguish "never painted" from
        # "painted then healed". Record the maximum observed ghost count.
        seen = 0
        for _ in range(20):
            seen = max(seen, frame.locator(
                "#typing-indicator-remote").count())
            page.wait_for_timeout(100)
        ghost = frame.locator("#typing-indicator-remote").count()
        current = _title(page, frame)
        # Regression: a stale body must NEVER paint waiting in the session
        # it no longer belongs to. (Old source paints ever=1 here.)
        assert seen == 0, (
            f"H1 regressed: ever={seen} settled={ghost} "
            f"title={current!r} errors={errors}")
        assert "Beta" in current
        assert errors == []
    finally:
        page.close()


def test_prior_visit_sync_cannot_revert_current_title(browser, static_server):
    """H2: A->B->A; old held A body resolves after newer A data painted."""
    world = SyncWorld()
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        _arm(page, frame, f"sessions/{SID_A}/messages", 1)
        _sync(page, frame)
        _pump_until(page, lambda: len(_pending(page, frame)) == 1, 15000,
                    "old A sync body pending")
        held_id = _pending(page, frame)[0]
        _assert_body_pending(page, frame, held_id, "old A sync")
        _goto_session(page, frame, SID_B, "beta question")
        _goto_session(page, frame, SID_A, "alpha answer")
        title_new = _title(page, frame)
        assert "Alpha-NEW" in title_new
        rows_before = frame.locator("#chatMessages .message").count()
        # Barrier: newer A traffic passed through; the old body is pending.
        assert _pending(page, frame) == [held_id]
        stale = json.dumps({"success": True, "session_name": "Alpha-STALE",
                            "followups": [], "messages": world.scenario[
                                str(SID_A)]["messages"]})
        _release(page, frame, held_id, stale)
        page.wait_for_timeout(1500)
        title_after = _title(page, frame)
        rows_after = frame.locator("#chatMessages .message").count()
        # Regression: the newer A paint wins; the stale body is dropped.
        # (Old source reverts to 'Alpha-STALE' here.)
        assert "Alpha-STALE" not in title_after, (
            f"H2 regressed: title={title_after!r} "
            f"rows {rows_before}->{rows_after} errors={errors}")
        assert "Alpha-NEW" in title_after
        assert rows_after == rows_before
        assert errors == []
    finally:
        page.close()


def test_replaced_sync_finisher_cannot_release_newer_claim(browser, static_server):
    """H3 regression: stale finally cannot clear the newer pending claim.

    The old fetch already returned (header timer cleared long before the
    20s policy timeout); only its body stayed pending. After the clock
    passes inflightMax, the second sync resets and proceeds with its own
    pending body; releasing the first must not permit a third request
    while the second body is still outstanding.
    """
    world = SyncWorld()
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        page.clock.install()
        _arm(page, frame, f"sessions/{SID_A}/messages", 2)
        _sync(page, frame)
        _pump_until(page, lambda: len(_pending(page, frame)) == 1, 15000,
                    "first sync body pending")
        first_id = _pending(page, frame)[0]
        _assert_body_pending(page, frame, first_id, "first sync")
        page.clock.fast_forward(21000)
        # Proof the 8s header timer was already cleared by header
        # resolution: the held body was never aborted and is still
        # decoding-pending after the clock passed both timers.
        _assert_body_pending(page, frame, first_id,
                             "first sync after 21s")
        _sync(page, frame)
        _pump_until(page, lambda: len(_pending(page, frame)) == 2, 15000,
                    "second sync started after timeout reset")
        baseline = {e["id"] for e in _log(page, frame)}
        first_id, second_id = _pending(page, frame)
        _assert_body_pending(page, frame, second_id, "second sync")
        _release(page, frame, first_id)
        _pump_until(page, lambda: any(
            e["id"] == first_id and e["tBodySettled"] > 0
            for e in _log(page, frame)), 15000, "first body finalized")
        _sync(page, frame)
        page.wait_for_timeout(1500)
        # Regression: the stale completion must NOT permit a third request
        # while the second body is still pending — the newer claim survives.
        # (Old source starts a third GET here.)
        log = _log(page, frame)
        extra = [e for e in log if e["id"] not in baseline
                 and e["id"] not in (first_id, second_id)]
        second = [e for e in log if e["id"] == second_id][0]
        assert extra == [], f"H3 regressed: overlap {extra} errors={errors}"
        assert second["tBodySettled"] == 0
        assert second["aborted"] is False
        _release(page, frame, second_id)
        _pump_until(page, lambda: any(
            e["id"] == second_id and e["tBodySettled"] > 0
            for e in _log(page, frame)), 15000, "second body finalized")
        for hid in _held(page, frame):
            _release(page, frame, hid)
        # Eventual sync: once the current body completes, a new sync runs.
        settled = len(_log(page, frame))
        _sync(page, frame)
        _pump_until(page, lambda: len(_log(page, frame)) == settled + 1,
                    15000, "eventual sync after completion")
        _pump_until(page, lambda: all(
            e["tBodySettled"] > 0 for e in _log(page, frame)),
                    15000, "all syncs finalized")
        assert errors == []
    finally:
        page.close()


def _set_parent_hidden(page, hidden):
    # Environment staging (outer qa-host page only): spoof the
    # document.hidden getter production's pageIsBackgrounded reads.
    # No production JS state is touched. This Chromium build has no
    # CDP Emulation.setVisibilityState, so the getter is the only
    # faithful background control available here.
    page.evaluate(
        "Object.defineProperty(document, 'hidden',"
        " {configurable: true, get: () => %s})"
        % ("true" if hidden else "false"))


def test_hidden_tick_skips_then_foreground_recovers(browser, static_server):
    """Eligibility first: a hidden tick dispatches nothing; foreground
    recovery replaces the stale claim and completes. Claim happens only
    when a sync will actually dispatch, so skipped ticks advance no
    request token."""
    world = SyncWorld()
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        page.clock.install()
        _arm(page, frame, f"sessions/{SID_A}/messages", 6)
        _sync(page, frame)
        _pump_until(page, lambda: len(_pending(page, frame)) == 1, 15000,
                    "first sync body pending")
        page.clock.fast_forward(21000)
        # The scheduled tick may itself replace during the jump; only
        # pre-hide requests may ever be pending afterward.
        page.wait_for_timeout(1000)
        _set_parent_hidden(page, True)
        # Prove the page really is backgrounded (predicate prod uses).
        _pump_until(page, lambda: _ef(page).evaluate(
            "!!(window.parent && window.parent !== window"
            " && window.parent.document.hidden)"), 10000,
            "page backgrounded")
        n = len(_log(page, frame))
        pre = set(_held(page, frame))
        assert len(pre) >= 1
        _sync(page, frame)
        page.wait_for_timeout(1500)
        # Hidden tick dispatched nothing; only pre-hide bodies pending.
        assert len(_log(page, frame)) == n
        assert set(_held(page, frame)) == pre
        assert set(_pending(page, frame)) <= pre
        _set_parent_hidden(page, False)
        # Foreground recovery: the stale claim is replaced exactly once,
        # whether the scheduled tick or the focus handler wins the race
        # (both are real page syncs through the same claim gate).
        n_ff = len(_log(page, frame))
        page.clock.fast_forward(21000)
        _sync(page, frame)
        _pump_until(page, lambda: len(_log(page, frame)) == n_ff + 1,
                    15000, "recovery sync started")
        for hid in _held(page, frame):
            _release(page, frame, hid)
        _pump_until(page, lambda: all(
            e["tBodySettled"] > 0 for e in _log(page, frame)),
                    15000, "all syncs finalized")
        assert frame.get_by_text("alpha answer").count() >= 1
        assert errors == []
    finally:
        page.close()


def test_non_json_body_cleans_up_claim(browser, static_server):
    """A decode-throwing body must clear its own claim; sync recovers."""
    world = SyncWorld()
    page, frame, errors = _open(browser, static_server, world, SID_A)
    try:
        _arm(page, frame, f"sessions/{SID_A}/messages", 1)
        _sync(page, frame)
        _pump_until(page, lambda: len(_pending(page, frame)) == 1, 15000,
                    "bad sync body pending")
        hid = _pending(page, frame)[0]
        rows_before = frame.locator("#chatMessages .message").count()
        _release(page, frame, hid, "not-json{{{")
        _pump_until(page, lambda: any(
            e["id"] == hid and e["tBodySettled"] > 0
            for e in _log(page, frame)), 15000, "bad body finalized")
        # console.warn is not a pageerror; no rows change; claim cleared
        # so the next trigger starts a fresh sync that settles.
        assert errors == []
        assert frame.locator("#chatMessages .message").count() \
            == rows_before
        settled = len(_log(page, frame))
        _sync(page, frame)
        _pump_until(page, lambda: len(_log(page, frame)) == settled + 1,
                    15000, "recovery sync starts")
        _pump_until(page, lambda: all(
            e["tBodySettled"] > 0 for e in _log(page, frame)),
                    15000, "recovery sync settles")
        assert errors == []
    finally:
        page.close()


def test_remote_active_waiting_paints_in_current_session(
        browser, static_server):
    """Positive control: live-active + user-last transcript shows waiting.

    Guards the H1 fix against over-suppression: the gated branches must
    still paint remote waiting for the CURRENT session.
    """
    world = SyncWorld()
    world.scenario[str(SID_B)]["live"] = {
        "active": True, "generating": True, "status": "B still running"}
    page, frame, errors = _open(browser, static_server, world, SID_B)
    try:
        frame.get_by_text("beta question").wait_for(state="visible")
        _sync(page, frame)
        frame.locator("#typing-indicator-remote").first.wait_for(
            state="attached", timeout=15000)
        assert errors == []
    finally:
        page.close()


def test_malformed_body_paints_waiting_in_current_session(
        browser, static_server):
    """Positive control: malformed body with active snapshot paints only
    when the session is still current — the exact branch H1 gates."""
    world = SyncWorld()
    world.scenario[str(SID_B)]["live"] = {
        "active": True, "generating": True, "status": "B still running"}
    page, frame, errors = _open(browser, static_server, world, SID_B)
    try:
        _arm(page, frame, f"sessions/{SID_B}/messages", 1)
        _sync(page, frame)
        _pump_until(page, lambda: len(_pending(page, frame)) == 1, 15000,
                    "B sync body pending")
        hid = _pending(page, frame)[0]
        _release(page, frame, hid, '{"success":false}')
        frame.locator("#typing-indicator-remote").first.wait_for(
            state="attached", timeout=15000)
        assert errors == []
    finally:
        page.close()
