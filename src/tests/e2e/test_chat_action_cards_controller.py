"""Public-controller lifecycle tests for CuttleChatActionCards (plan C2).

Drives the production page with the same isolated API world as
``test_chat_action_card_effects`` (no Flask, provider, subprocess, model,
or login), then constructs a SECOND controller through the public
``CuttleChatActionCards.mountCards(root, host)`` factory over an isolated
in-page container holding a clone of a real painted card. The test host
records every request with its AbortSignal and settlement, delegating to
real ``fetch``, so the tests exercise real DOM wiring and real API
plumbing through the public surface — no runtime test hooks, no
source-only assertions.

Abort proofs reuse the effects suite's browser response gate
(``_GATE_JS_TMPL``): a genuinely pending network response is held before
dispose/destroy, and the tests assert the signal aborted. The gate holds
the response after it arrives, so the abort lands post-resolve: on
release the held fetch fulfills into a dead loop, which must ignore it —
no late paint/lock/persist/resume may follow. A disposed connected card
cannot POST by click.
"""

from __future__ import annotations

import json

from .test_shared_diff_modal import (  # noqa: F401
    browser,
    static_server,
)
from .test_chat_action_card_effects import (
    CARD_CHOICE,
    CARD_WATCH,
    EffectsWorld,
    _gate_pending,
    _gate_release,
    _open,
    _send_and_wait_card,
)

_SETUP_JS = """\
() => {
  const src = document.querySelector('#chatMessages .cuttle-action-form');
  if (!src) return 0;
  let holder = document.getElementById('cards-test-root');
  if (!holder) {
    holder = document.createElement('div');
    holder.id = 'cards-test-root';
    holder.setAttribute('style', 'position:fixed;top:0;left:0;'
      + 'z-index:999999;background:#fff;max-height:60vh;overflow:auto;');
    document.body.appendChild(holder);
  }
  holder.innerHTML = src.outerHTML;
  const spy = { requests: [], sends: [], resumes: [], controls: [] };
  window.__cardsSpy = spy;
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const ctl = window.CuttleChatActionCards.mountCards(holder, {
    request: (url, o) => {
      const entry = { url: String(url), signal: (o && o.signal) || null,
                      settled: 'pending' };
      spy.requests.push(entry);
      const init = Object.assign({}, (o && o.options) || {});
      if (entry.signal) init.signal = entry.signal;
      return fetch(url, init).then(
        (resp) => { entry.settled = 'fulfilled'; return resp; },
        (err) => { entry.settled = 'rejected'; throw err; });
    },
    sendAnswerText: (m) => { spy.sends.push(m && m.text); },
    resumeWithStatus: (t) => { spy.resumes.push(String(t)); return Promise.resolve(); },
    sendControlCommand: (t) => { spy.controls.push(String(t)); return Promise.resolve(); },
    context: () => ({ sessionId: '42', projectPath: '',
                      authSessionId: '42', isAuthMode: true }),
    storage: {
      getItem: (k) => window.sessionStorage.getItem(k),
      setItem: (k, v) => window.sessionStorage.setItem(k, v),
      removeItem: (k) => window.sessionStorage.removeItem(k),
    },
    notify: () => {},
    syncMessages: () => Promise.resolve(),
    publishRestartEvent: () => {},
    setHistoryAwaiting: () => {},
    syncHistoryAwaiting: () => {},
    syncComposerStop: () => {},
    paintAssistantMessage: () => {},
    escapeHtml: esc,
  });
  window.__cardsCtl = ctl;
  return holder.querySelectorAll('.cuttle-action-form').length;
}\
"""


def _clone_into_test_root(frame):
    n = frame.locator("html").evaluate(_SETUP_JS)
    assert n == 1, "test root must hold exactly one cloned card"


def _spy_entries(frame):
    return frame.locator("html").evaluate(
        "() => (window.__cardsSpy.requests || []).map((e) => "
        "({url: e.url, settled: e.settled, "
        "aborted: !!(e.signal && e.signal.aborted)}))")


def _spy_matching(frame, sub):
    return [e for e in _spy_entries(frame) if sub in e["url"]]


def _wait_for(frame, page, expr, timeout_ms=15000):
    waited = 0
    while waited < timeout_ms:
        if frame.locator("html").evaluate(expr):
            return True
        page.wait_for_timeout(250)
        waited += 250
    return False


def _wait_spy_count(frame, page, sub, minimum, timeout_ms=15000):
    assert _wait_for(
        frame, page,
        "() => (window.__cardsSpy.requests || []).filter((e) => e.url.indexOf(%s) >= 0).length >= %d"
        % (json.dumps(sub), minimum), timeout_ms), \
        f"spy {sub} never reached {minimum}"


def _wait_clone_pending(frame, page, sub="/output/job-1.json",
                        timeout_ms=20000):
    # The clone's own request went out AND the gate holds a response, so
    # the read is genuinely pending (not merely unsent).
    assert _wait_for(
        frame, page,
        "() => (window.__cardsSpy.requests || []).some((e) => e.url.indexOf(%s) >= 0)"
        " && (typeof window.__fetchGatePending === 'function' && window.__fetchGatePending() >= 1)"
        % json.dumps(sub), timeout_ms), \
        "clone read never genuinely pending"


def _wait_settled(frame, page, sub, index, state, timeout_ms=15000):
    assert _wait_for(
        frame, page,
        "() => ((window.__cardsSpy.requests || []).filter((e) => e.url.indexOf(%s) >= 0)[%d] || {}).settled === %s"
        % (json.dumps(sub), index, json.dumps(state)), timeout_ms), \
        f"spy {sub}[{index}] never settled {state}"


def _clone_fingerprint(frame):
    return frame.locator("html").evaluate(
        "() => { const c = document.querySelector("
        "'#cards-test-root .cuttle-action-form');"
        " if (!c) return null;"
        " const st = c.querySelector('.cuttle-action-form-status');"
        " const lab = c.querySelector('.cuttle-action-form-progress-label');"
        " return { locked: c.getAttribute('data-locked'),"
        " status: st ? st.textContent : null,"
        " label: lab ? lab.textContent : null }; }")


def _mount_test_root(frame):
    frame.locator("html").evaluate(
        "() => window.__cardsCtl.mount("
        "document.getElementById('cards-test-root'))")


def _dispose_clone(frame):
    frame.locator("html").evaluate(
        "() => window.__cardsCtl.disposeCard(document.querySelector("
        "'#cards-test-root .cuttle-action-form'))")


def _cleanup(frame, page):
    frame.locator("html").evaluate(
        "() => { try { window.__cardsCtl.destroy(); } catch (_) {} "
        "try { document.getElementById('cards-test-root').remove(); }"
        " catch (_) {} }")
    page.close()


def test_repeated_mount_wires_one_handler_and_posts_once(
        browser, static_server):
    world = EffectsWorld([CARD_CHOICE, "noted"])
    page, frame, errors = _open(browser, static_server, world)
    try:
        _send_and_wait_card(frame, "show choice")
        _clone_into_test_root(frame)
        frame.locator("html").evaluate(
            "() => { window.__cardsCtl.mount("
            "document.getElementById('cards-test-root')); "
            "window.__cardsCtl.mount("
            "document.getElementById('cards-test-root')); }")
        frame.locator(
            "#cards-test-root [data-action-form-option=\"a\"]").click()
        frame.locator("#cards-test-root .cuttle-action-form--locked").wait_for(
            state="attached", timeout=15000)
        assert len(world.run_posts) == 1  # one handler, one request
        assert len(_spy_matching(frame, "/api/action-form/run")) == 1
        assert errors == []
    finally:
        _cleanup(frame, page)


def test_dispose_aborts_pending_read_and_blocks_clicks(
        browser, static_server):
    world = EffectsWorld([CARD_WATCH, "noted"])
    page, frame, errors = _open(browser, static_server, world,
                                fetch_gate_url="/output/job-1.json")
    try:
        _send_and_wait_card(frame, "show watch")
        _clone_into_test_root(frame)
        _mount_test_root(frame)
        _wait_clone_pending(frame, page)
        before = _clone_fingerprint(frame)
        assert before is not None
        _dispose_clone(frame)
        # The in-flight read's signal aborted synchronously on dispose.
        pending = _spy_matching(frame, "/output/job-1.json")
        assert len(pending) >= 1
        assert pending[0]["aborted"] is True
        # Baseline after the test's own seed send: no resume may add one.
        posts_at_dispose = world.chat_posts
        assert posts_at_dispose >= 1
        # Terminal on release: a live loop would lock and persist; a dead
        # one must show no late paint, lock, persist, or resume.
        world.job_finish = True
        _gate_release(frame)
        _wait_settled(frame, page, "/output/job-1.json", 0, "fulfilled")
        page.wait_for_timeout(3000)
        assert len(_spy_matching(frame, "/output/job-1.json")) == len(pending)
        assert _clone_fingerprint(frame) == before
        assert _clone_fingerprint(frame)["locked"] != "1"
        assert world.chat_posts == posts_at_dispose  # no resume send
        # Disposed and still connected, yet its clicks start nothing: the
        # handlers detached with the lifetime.
        frame.locator(
            "#cards-test-root [data-action-form-option=\"a\"]").click()
        page.wait_for_timeout(1500)
        assert len(world.run_posts) == 0
        assert len(_spy_matching(frame, "/api/action-form/run")) == 0
        assert errors == []
    finally:
        _cleanup(frame, page)


def test_destroy_aborts_pending_reads_and_freezes_cards(
        browser, static_server):
    world = EffectsWorld([CARD_WATCH, "noted"])
    page, frame, errors = _open(browser, static_server, world,
                                fetch_gate_url="/output/job-1.json")
    try:
        _send_and_wait_card(frame, "show watch")
        _clone_into_test_root(frame)
        _mount_test_root(frame)
        _wait_clone_pending(frame, page)
        before = _clone_fingerprint(frame)
        frozen = len(_spy_matching(frame, "/output/job-1.json"))
        frame.locator("html").evaluate(
            "() => window.__cardsCtl.destroy()")
        pending = _spy_matching(frame, "/output/job-1.json")
        assert len(pending) >= 1
        assert pending[0]["aborted"] is True
        world.job_finish = True
        _gate_release(frame)
        _wait_settled(frame, page, "/output/job-1.json", 0, "fulfilled")
        page.wait_for_timeout(5000)  # past one full poll interval
        assert len(_spy_matching(frame, "/output/job-1.json")) == frozen
        assert _clone_fingerprint(frame) == before
        # Post-destroy mounts are no-ops: wiring a fresh clone and
        # clicking its option must not reach the API.
        runs_before = len(world.run_posts)
        frame.locator("html").evaluate(
            "() => { const holder = document.getElementById("
            "'cards-test-root'); const card = holder.querySelector("
            "'.cuttle-action-form'); holder.appendChild(card.cloneNode(true));"
            " window.__cardsCtl.mount(holder); }")
        frame.locator(
            "#cards-test-root .cuttle-action-form:nth-of-type(2)"
            " [data-action-form-option=\"a\"]").click()
        page.wait_for_timeout(1500)
        assert len(world.run_posts) == runs_before
        assert len(_spy_matching(frame, "/api/action-form/run")) == 0
        assert errors == []
    finally:
        frame.locator("html").evaluate(
            "() => { try { document.getElementById("
            "'cards-test-root').remove(); } catch (_) {} }")
        page.close()


def test_dispose_during_poll_wait_stops_loop(browser, static_server):
    world = EffectsWorld([CARD_WATCH, "noted"])
    page, frame, errors = _open(browser, static_server, world,
                                fetch_gate_url="/output/job-1.json")
    try:
        _send_and_wait_card(frame, "show watch")
        _clone_into_test_root(frame)
        _mount_test_root(frame)
        _wait_clone_pending(frame, page)
        # Let exactly one poll complete; the loop then sleeps 4000 ms, so
        # disposing now lands inside the wait, not inside a read.
        _gate_release(frame)
        _wait_settled(frame, page, "/output/job-1.json", 0, "fulfilled")
        _dispose_clone(frame)
        page.wait_for_timeout(5000)
        assert len(_spy_matching(frame, "/output/job-1.json")) == 1
        assert errors == []
    finally:
        _cleanup(frame, page)


def test_remount_after_dispose_resumes_cleanly(browser, static_server):
    world = EffectsWorld([CARD_WATCH, "noted"])
    page, frame, errors = _open(browser, static_server, world,
                                fetch_gate_url="/output/job-1.json")
    try:
        _send_and_wait_card(frame, "show watch")
        _clone_into_test_root(frame)
        _mount_test_root(frame)
        _wait_clone_pending(frame, page)
        _dispose_clone(frame)
        assert _spy_matching(frame, "/output/job-1.json")[0]["aborted"] \
            is True
        # Re-mount installs one fresh lifetime while the gate still holds:
        # exactly one new pending read proves fresh wiring with no
        # duplicate handlers; the old continuation stays settled.
        _mount_test_root(frame)
        _wait_spy_count(frame, page, "/output/job-1.json", 2)
        page.wait_for_timeout(5000)  # past one full poll interval
        polls = _spy_matching(frame, "/output/job-1.json")
        assert len(polls) == 2  # old dead, new waiting — never two streams
        assert polls[0]["aborted"] is True
        assert polls[1]["settled"] == "pending"
        assert _clone_fingerprint(frame)["locked"] != "1"
        # Release: the new lifetime completes and keeps polling alone.
        _gate_release(frame)
        _wait_settled(frame, page, "/output/job-1.json", 1, "fulfilled")
        _wait_spy_count(frame, page, "/output/job-1.json", 3,
                        timeout_ms=12000)
        assert _clone_fingerprint(frame)["locked"] != "1"
        assert errors == []
    finally:
        _cleanup(frame, page)


_THROWING_SETUP_JS = """\
() => {
  const src = document.querySelector('#chatMessages .cuttle-action-form');
  if (!src) return 0;
  let holder = document.getElementById('cards-test-root');
  if (!holder) {
    holder = document.createElement('div');
    holder.id = 'cards-test-root';
    holder.setAttribute('style', 'position:fixed;top:0;left:0;'
      + 'z-index:999999;background:#fff;max-height:60vh;overflow:auto;');
    document.body.appendChild(holder);
  }
  holder.innerHTML = src.outerHTML + src.outerHTML;
  holder.querySelectorAll('.cuttle-action-form').forEach((c) => {
    c.setAttribute('data-session-id', '43');
  });
  const spy = { requests: [], sends: [], resumes: [], controls: [],
                syncs: 0, paints: 0 };
  window.__cardsSpy = spy;
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const ctl = window.CuttleChatActionCards.mountCards(holder, {
    request: (url, o) => {
      const entry = { url: String(url), signal: (o && o.signal) || null,
                      settled: 'pending' };
      spy.requests.push(entry);
      const init = Object.assign({}, (o && o.options) || {});
      if (entry.signal) init.signal = entry.signal;
      return fetch(url, init).then(
        (resp) => { entry.settled = 'fulfilled'; return resp; },
        (err) => { entry.settled = 'rejected'; throw err; });
    },
    sendAnswerText: (m) => { spy.sends.push(m && m.text); },
    resumeWithStatus: (t) => { spy.resumes.push(String(t)); return Promise.resolve(); },
    sendControlCommand: (t) => { spy.controls.push(String(t)); return Promise.resolve(); },
    context: () => ({ sessionId: '42', projectPath: '',
                      authSessionId: '42', isAuthMode: true }),
    storage: {
      getItem: (k) => window.sessionStorage.getItem(k),
      setItem: (k, v) => { throw new Error('quota exceeded'); },
      removeItem: (k) => window.sessionStorage.removeItem(k),
    },
    notify: () => {},
    syncMessages: () => { spy.syncs += 1; return Promise.resolve(); },
    publishRestartEvent: () => {},
    setHistoryAwaiting: () => {},
    syncHistoryAwaiting: () => {},
    syncComposerStop: () => {},
    paintAssistantMessage: () => { spy.paints += 1; },
    escapeHtml: esc,
  });
  window.__cardsCtl = ctl;
  return holder.querySelectorAll('.cuttle-action-form').length;
}\
"""


def test_storage_failure_still_coalesces_one_post(browser, static_server):
    # Storage failure must not break delivery or raise: setItem throws
    # (quota / private-mode model) while getItem/removeItem keep
    # delegating, yet two concurrent offers for the same logical
    # followup coalesce into one POST through the in-memory flight.
    # The clones run as session 43 so the page card's own ack cannot
    # swallow their offers — the count stays deterministic.
    world = EffectsWorld([CARD_WATCH, "noted"])
    world.job_manual = True  # polls answer running until job_final lands
    page, frame, errors = _open(browser, static_server, world)
    try:
        _send_and_wait_card(frame, "show watch")
        n = frame.locator("html").evaluate(_THROWING_SETUP_JS)
        assert n == 2, "test root must hold exactly two cloned cards"
        frame.locator("html").evaluate(
            "() => window.__cardsCtl.mount("
            "document.getElementById('cards-test-root'))")
        # All three loops poll running; now deliver one terminal payload
        # carrying the discord form to every loop at once.
        world.job_final = {
            "state": "done", "percent": 100, "label": "Done",
            "run_id": "run-1", "bars": [{"id": "all", "percent": 100}],
            "build_id": "b9",
            "discord_form": {"mode": "choice", "title": "Post where"},
        }
        world.job_finish = True
        # One POST from the page card plus exactly one coalesced POST
        # from the two clones sharing a flight (three without it).
        waited = 0
        while len(world.followup_posts) < 2 and waited < 25000:
            page.wait_for_timeout(250)
            waited += 250
        assert len(world.followup_posts) == 2
        page.wait_for_timeout(4000)
        assert len(world.followup_posts) == 2  # no retry storm, no claim
        assert errors == []
    finally:
        _cleanup(frame, page)


def test_move_within_root_preserves_watch_loop(browser, static_server):
    world = EffectsWorld([CARD_WATCH, "noted"])
    page, frame, errors = _open(browser, static_server, world)
    try:
        _send_and_wait_card(frame, "show watch")
        _clone_into_test_root(frame)
        _mount_test_root(frame)
        _wait_spy_count(frame, page, "/output/job-1.json", 1)
        # Re-append inside the same root: remove+insert fire together, so
        # the observer must see a contained node and preserve the loop.
        frame.locator("html").evaluate(
            "() => { const holder = document.getElementById("
            "'cards-test-root'); const card = holder.querySelector("
            "'.cuttle-action-form'); holder.appendChild(card); }")
        _wait_spy_count(frame, page, "/output/job-1.json", 2,
                        timeout_ms=12000)
        assert errors == []
    finally:
        _cleanup(frame, page)
