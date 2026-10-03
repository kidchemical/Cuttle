"""SSE reader ownership + producer protocol characterization (plan D1).

Drives the real production chat page with a controlled in-page fetch
transport: POST /api/chat with ``stream: true`` receives a REAL
``ReadableStream`` whose chunks the test enqueues with precise timing,
while every other request falls through to the real fetch plumbing and
the isolated fake API. The production ``reader.read()`` / race /
parse path in ``fetchChatPayload`` is genuine — only the bytes are
scripted.

The transport models abort explicitly (the request AbortSignal errors
the stream; ``reader.cancel()`` is observed), and the fake
history/pending-recovery endpoints stay EMPTY (no parked rows), so any
painted reply must have arrived through the stream under test —
recovery can never conceal which stream events the page received.

Desired single-response contract: one retained native read spans the
8 s timeout ticks, so a response pushed after a tick satisfies the same
pending read and paints exactly once. Proven in the qa-host iframe
(10 s hold) and on the top-level page (20 s hold). Background parked
polling and success acknowledgment are normal traffic; the no-rescue
proof is empty history/parked fixtures with the response text delivered
only on the stream — zero request traffic is not claimed.
"""

from __future__ import annotations

import json
import time

from .test_shared_diff_modal import (  # noqa: F401
    IsolatedAPI,
    apply_request_guard,
    browser,
    static_server,
)

_STREAM_JS = """\
(() => {
  const origFetch = window.fetch.bind(window);
  const enc = new TextEncoder();
  const ctl = (window.__streamCtl = {
    streams: 0, pushes: [], aborted: 0, cancelled: 0,
  });
  // Observational read instrumentation (no algorithm copy): count native
  // reader.read() calls and their settlements, but only on test streams.
  // Every other stream on the page uses the stock reader untouched.
  const readStats = (window.__readStats = {
    calls: 0, resolutions: 0, rejections: 0,
  });
  const watchedStreams = new WeakSet();
  const origGetReader = ReadableStream.prototype.getReader;
  ReadableStream.prototype.getReader = function () {
    const args = Array.prototype.slice.call(arguments);
    const reader = origGetReader.apply(this, args);
    if (!watchedStreams.has(this)) return reader;
    const origRead = reader.read.bind(reader);
    reader.read = function () {
      readStats.calls += 1;
      return origRead().then(
        (result) => { readStats.resolutions += 1; return result; },
        (err) => { readStats.rejections += 1; throw err; }
      );
    };
    return reader;
  };
  let current = null;
  ctl.lastBody = null;
  function take() {
    if (!current || current.settled) {
      throw new Error('no open test stream');
    }
    return current;
  }
  window.__streamPushText = (text) => {
    const rec = take();
    const bytes = enc.encode(String(text));
    rec.controller.enqueue(bytes);
    ctl.pushes.push({ n: bytes.length, at: Date.now() });
    return bytes.length;
  };
  window.__streamPushBytes = (arr) => {
    const rec = take();
    const bytes = new Uint8Array(arr);
    rec.controller.enqueue(bytes);
    ctl.pushes.push({ n: bytes.length, at: Date.now() });
    return bytes.length;
  };
  window.__streamClose = () => {
    const rec = take();
    rec.settled = true;
    try { rec.controller.close(); } catch (_) {}
  };
  window.__streamError = () => {
    const rec = take();
    rec.settled = true;
    try {
      rec.controller.error(new Error('boom-disconnect'));
    } catch (_) {}
  };
  window.__streamState = () => ({
    streams: ctl.streams,
    pushes: ctl.pushes.length,
    aborted: ctl.aborted,
    cancelled: ctl.cancelled,
    lastBody: ctl.lastBody,
    readCalls: window.__readStats.calls,
    readResolutions: window.__readStats.resolutions,
    readRejections: window.__readStats.rejections,
  });
  window.__readStatsReset = () => {
    window.__readStats.calls = 0;
    window.__readStats.resolutions = 0;
    window.__readStats.rejections = 0;
  };
  window.fetch = function (input, init) {
    let url = "";
    try {
      url = String((input && input.url) || input || "");
    } catch (_) { /* fall through ungated */ }
    let isChatPost = false;
    try {
      isChatPost = String((init && init.method) || 'GET').toUpperCase() === 'POST'
        && new URL(url, window.location.href).pathname === '/api/chat';
    } catch (_) { isChatPost = false; }
    let wantStream = false;
    if (isChatPost) {
      try {
        wantStream = !!JSON.parse(String((init && init.body) || '{}')).stream;
      } catch (_) { wantStream = false; }
    }
    if (!wantStream) return origFetch(input, init);
    try { ctl.lastBody = String((init && init.body) || ''); }
    catch (_) { ctl.lastBody = ''; }
    const signal = init && init.signal;
    let controller = null;
    const rec = { settled: false };
    const rs = new ReadableStream({
      start(c) { controller = c; },
      cancel() { rec.settled = true; ctl.cancelled += 1; },
    });
    watchedStreams.add(rs);
    rec.controller = {
      enqueue: (v) => controller.enqueue(v),
      close: () => controller.close(),
      error: (e) => controller.error(e),
    };
    current = rec;
    ctl.streams += 1;
    if (signal) {
      const onAbort = () => {
        ctl.aborted += 1;
        rec.settled = true;
        try {
          controller.error(
            new DOMException('stream aborted', 'AbortError'));
        } catch (_) {}
      };
      if (signal.aborted) onAbort();
      else signal.addEventListener('abort', onAbort, { once: true });
    }
    return Promise.resolve(new Response(rs, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }));
  };
})();
"""


class StreamWorld:
    """Minimal fake server: seed history, benign non-stream chat."""

    def __init__(self):
        now = time.time()
        self.history = [
            {"id": 1, "role": "user", "content": "seed",
             "timestamp": (now - 30) * 1000},
        ]
        self.next_id = 2
        self.chat_posts = []
        self.cancel_posts = []
        # Every parked-recovery poll served and the shape returned: the
        # fixture only ever serves the empty shape, so a painted reply
        # provably arrived through the stream under test.
        self.pending_served = []
        self._iso = IsolatedAPI()
        self.iso_requests = self._iso.requests

    def handle(self, route):
        from urllib.parse import urlparse

        req = route.request
        path = urlparse(req.url).path
        if "pending-result" in path:
            self.pending_served.append({"path": path, "empty": True})
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True, "data": []}))
            return
        if req.method == "POST" and path == "/api/chat-cancel":
            try:
                cancel_body = json.loads(req.post_data or "{}")
            except ValueError:
                cancel_body = {}
            self.cancel_posts.append(cancel_body)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True,
                                           "cancelled": True}))
            return
        if path.endswith("/messages"):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True,
                                           "session_name": "Fx",
                                           "messages": list(self.history)}))
            return
        if req.method == "POST" and path == "/api/chat":
            try:
                body = json.loads(req.post_data or "{}")
            except ValueError:
                body = {}
            self.chat_posts.append(body)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True,
                                           "response": "NONSTREAM-FALLBACK",
                                           "session_id": 42}))
            return
        self._iso.handle(route)


def _open_stream(browser, static_server, world):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    apply_request_guard(page.context, static_server)
    page.add_init_script(_STREAM_JS)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", world.handle)
    page.route(f"{static_server}/qa-host.html", lambda route: route.fulfill(
        content_type="text/html",
        body="<iframe style=\"border:0;width:100%;height:96vh\""
             " src=\"/chat_page.html?chat=42\"></iframe>"))
    page.goto(static_server + "/qa-host.html", wait_until="domcontentloaded")
    frame = page.frame_locator("iframe")
    frame.locator("#chatMessages .message.user").first.wait_for(
        state="visible", timeout=30000)
    return page, frame, errors


def _send(frame, text):
    frame.locator("#chatInput").fill(text)
    frame.locator("#sendButton").click()


def _js(frame, expr):
    return frame.locator("html").evaluate(expr)


def _push_text(frame, text):
    return _js(frame, "() => window.__streamPushText(%s)" % json.dumps(text))


def _push_bytes(frame, values):
    return _js(frame, "() => window.__streamPushBytes(%s)" % json.dumps(values))


def _frame(obj):
    return "data: " + json.dumps(obj) + "\n\n"


def _session_frame(sid=42):
    return _frame({"type": "session", "session_id": sid})


def _response_frame(text, sid=42):
    return _frame({"type": "response", "success": True,
                   "response": text, "session_id": sid})


def _wait_text(frame, page, text, timeout_ms=15000):
    waited = 0
    while waited < timeout_ms:
        if frame.get_by_text(text).count() >= 1:
            return True
        page.wait_for_timeout(250)
        waited += 250
    return False


def _wait_typing(frame, page, text, timeout_ms=10000):
    waited = 0
    while waited < timeout_ms:
        try:
            got = frame.locator(
                "#typing-indicator .typing-status, #typing-status"
            ).first.text_content() or ""
        except Exception:
            got = ""
        if text in got:
            return True
        page.wait_for_timeout(250)
        waited += 250
    return False


def _rescue_marks(world):
    """Parked-recovery polls served during the turn and what they got."""
    return list(world.pending_served)


def _assistant_count(frame):
    # The live typing indicator carries .message.assistant: exclude it so
    # only painted reply bubbles count.
    return frame.locator(
        "#chatMessages .message.assistant:not(#typing-indicator)").count()


def _assistant_texts(frame):
    return frame.locator("#chatMessages .message.assistant").evaluate_all(
        "(els) => els.map((el) => (el.innerText || '').slice(0, 200))")


def test_short_delay_streams_live_positive_control(browser, static_server):
    # Positive control: session + response inside the read window paint
    # promptly through the stream. Every later timing claim is measured
    # against this path working.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "hello")
        page.wait_for_timeout(500)
        _push_text(frame, _session_frame())
        _push_text(frame, _response_frame("HELLO-POSITIVE")
                   + _frame({"type": "done"}))
        assert _wait_text(frame, page, "HELLO-POSITIVE", 10000), \
            "stream reply never painted"
        state = _js(frame, "() => window.__streamState()")
        assert state["streams"] == 1, \
            "exactly one stream transport must serve the turn"
        assert json.loads(state["lastBody"] or "{}").get("stream") is True
        assert errors == []
    finally:
        page.close()


def test_response_after_read_timeout_paints_once(browser, static_server):
    # Desired single-final-response regression (qa-host iframe, so the
    # hold here is 10 s): the status barrier proves read#1 was consumed
    # and read#2 is pending before the 8 s interval starts. One full
    # timeout tick passes with no new native read; the response pushed
    # after it satisfies that same pending read and paints exactly once
    # — parked polls serve empty (background polling is normal) and
    # there is no second POST.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "slow reply")
        page.wait_for_timeout(400)
        _push_text(frame, _session_frame()
                   + _frame({"type": "status", "message": "WORKING-BARRIER"}))
        assert _wait_typing(frame, page, "WORKING-BARRIER", 10000), \
            "status barrier never painted"
        page.wait_for_timeout(8500)  # one full 8 s tick, still held
        assert _js(frame, "() => window.__streamState().readCalls") == 2, \
            "timeout tick must not create another native read"
        _push_text(frame, _response_frame("LATE-BUT-KEPT"))
        assert _wait_text(frame, page, "LATE-BUT-KEPT", 10000), \
            "response after the read timeout never painted"
        state = _js(frame, "() => window.__streamState()")
        assert state["readCalls"] == 2, \
            "late chunk must satisfy the retained read, not a new one"
        assert frame.get_by_text("LATE-BUT-KEPT").count() == 1
        assert _assistant_count(frame) == 1
        assert len(world.chat_posts) == 0, \
            "painted reply must come from the stream, not a retry POST"
        assert all(r["empty"] for r in _rescue_marks(world)), \
            "a parked row supplied the reply instead of the stream"
        assert errors == []
    finally:
        page.close()


def test_split_utf8_across_chunks(browser, static_server):
    # A multi-byte code point split across two reads still decodes: the
    # page decodes with { stream: true }.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "split rune")
        page.wait_for_timeout(500)
        _push_text(frame, _session_frame())
        raw = ("data: " + json.dumps({"type": "response", "success": True,
                                      "response": "A✓B",
                                      "session_id": 42},
                                     ensure_ascii=False)
               + "\n\n").encode("utf-8")
        cut = raw.index("✓".encode("utf-8")) + 1
        _push_bytes(frame, list(raw[:cut]))
        page.wait_for_timeout(500)
        _push_bytes(frame, list(raw[cut:]))
        assert _wait_text(frame, page, "A✓B", 15000), \
            "split code point never reassembled"
        assert errors == []
    finally:
        page.close()


def test_split_delimiter_and_json_across_chunks(browser, static_server):
    # A frame split mid-JSON and a delimiter split across reads both
    # reassemble through the buffer: no frame fires until \n\n completes.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "split frame")
        page.wait_for_timeout(500)
        _push_text(frame, _session_frame())
        _push_text(frame, 'data: {"type": "response", "success": true,')
        page.wait_for_timeout(500)
        _push_text(frame, '"response": "SPLIT-OK", "session_id": 42}\n')
        page.wait_for_timeout(500)
        assert frame.get_by_text("SPLIT-OK").count() == 0, \
            "partial frame painted before its delimiter"
        _push_text(frame, '\n')
        assert _wait_text(frame, page, "SPLIT-OK", 15000), \
            "split frame never reassembled"
        assert errors == []
    finally:
        page.close()


def test_multiple_frames_per_chunk(browser, static_server):
    # Session + status + response in a single read are all accepted
    # without error and the response still ends the turn. Intra-chunk
    # apply order is code-read (one synchronous pass), not
    # timing-proven; cross-read status-then-response order is proven by
    # the barrier tests above.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "one chunk")
        page.wait_for_timeout(500)
        _push_text(frame, _session_frame()
                   + _frame({"type": "status", "message": "WORKING-ALL"})
                   + _response_frame("MULTI-OK"))
        assert _wait_text(frame, page, "MULTI-OK", 15000), \
            "response in a multi-frame chunk never painted"
        assert errors == []
    finally:
        page.close()


def test_malformed_and_unknown_frames_ignored(browser, static_server):
    # Unparseable JSON, unknown event types, and data: without the
    # trailing space never reach classification; the valid frame after
    # them still paints.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "bad frames")
        page.wait_for_timeout(500)
        _push_text(frame, _session_frame())
        _push_text(frame, 'data: {not json}\n\n'
                   + 'data: {"type": "mystery", "x": 1}\n\n'
                   + 'data:{"type": "response", "success": true,'
                   + ' "response": "NOSPACE", "session_id": 42}\n\n')
        page.wait_for_timeout(2000)
        assert frame.get_by_text("NOSPACE").count() == 0, \
            "data: without trailing space was classified"
        _push_text(frame, _response_frame("AFTER-BAD-OK"))
        assert _wait_text(frame, page, "AFTER-BAD-OK", 15000), \
            "valid frame after malformed frames never painted"
        assert errors == []
    finally:
        page.close()


def test_busy_event_enqueues_without_reply(browser, static_server):
    # A busy event ends the stream turn with no assistant bubble: the
    # optimistic user bubble is withdrawn and the turn is queued as a
    # follow-up instead.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "busy turn")
        page.wait_for_timeout(500)
        _push_text(frame, _session_frame()
                   + _frame({"type": "busy", "session_id": 42}))
        waited = 0
        while frame.locator("#chatMessages .message.user").count() > 1 \
                and waited < 15000:
            page.wait_for_timeout(250)
            waited += 250
        assert frame.locator("#chatMessages .message.user").count() == 1, \
            "busy turn left its optimistic user bubble"
        page.wait_for_timeout(2000)
        assert _assistant_count(frame) == 0, \
            "busy turn painted an assistant bubble"
        assert errors == []
    finally:
        page.close()


def test_eof_without_final_event_paints_nothing(browser, static_server):
    # Clean EOF before any response frame ends the stream without a
    # result; with empty recovery endpoints nothing paints.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "empty stream")
        page.wait_for_timeout(500)
        _push_text(frame, _session_frame())
        page.wait_for_timeout(500)
        _js(frame, "() => window.__streamClose()")
        page.wait_for_timeout(12000)
        assert _assistant_count(frame) == 0, \
            "EOF without a response frame painted a reply"
        assert errors == []
    finally:
        page.close()


def test_disconnect_error_retries_nonstream(browser, static_server):
    # A mid-read transport error is not a read timeout: it propagates out
    # of the read loop, and the page retries exactly once with
    # stream=false — that fallback reply is what paints.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "broken stream")
        page.wait_for_timeout(500)
        _push_text(frame, _session_frame())
        page.wait_for_timeout(500)
        _js(frame, "() => window.__streamError()")
        assert _wait_text(frame, page, "NONSTREAM-FALLBACK", 15000), \
            "non-stream retry after disconnect never painted"
        assert len(world.chat_posts) == 1, \
            "disconnect must retry exactly once without streaming"
        assert world.chat_posts[0].get("stream") is not True
        assert errors == []
    finally:
        page.close()


def test_stop_aborts_stream_read(browser, static_server):
    # Stop aborts the request signal mid-read: the transport errors the
    # stream as AbortError and the turn settles as cancelled.
    world = StreamWorld()
    page, frame, errors = _open_stream(browser, static_server, world)
    try:
        _send(frame, "stoppable")
        frame.locator("#stopButton").wait_for(state="visible", timeout=15000)
        _push_text(frame, _session_frame())
        frame.locator("#stopButton").click()
        frame.get_by_text("⏹ Generation cancelled.").wait_for(
            state="visible", timeout=15000)
        assert _js(frame, "() => window.__streamState().aborted") >= 1, \
            "stop never reached the stream transport"
        assert len(world.cancel_posts) == 1
        assert frame.locator("#stopButton").is_visible() is False
        assert _assistant_count(frame) == 0
        assert errors == []
    finally:
        page.close()


def _open_standalone(browser, static_server, world):
    # Top-level page (no qa-host iframe): inAppShell is false, so the
    # 20 s standalone hold policy applies. The page object doubles as
    # the frame for the shared helpers.
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    apply_request_guard(page.context, static_server)
    page.add_init_script(_STREAM_JS)
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route(f"{static_server}/api/**", world.handle)
    page.goto(static_server + "/chat_page.html?chat=42",
              wait_until="domcontentloaded")
    page.locator("#chatMessages .message.user").first.wait_for(
        state="visible", timeout=30000)
    return page, page, errors


def test_standalone_response_after_two_ticks_paints_once(
        browser, static_server):
    # 20 s standalone policy: the status barrier proves the second read
    # is pending, then two full 8 s ticks (at +8 s and +16 s) pass with
    # no new native read; the response at barrier + 16.5 s — still
    # inside the 20 s hold — satisfies the retained read and paints
    # exactly once.
    world = StreamWorld()
    page, frame, errors = _open_standalone(browser, static_server, world)
    try:
        _send(frame, "slow standalone")
        page.wait_for_timeout(400)
        _push_text(frame, _session_frame()
                   + _frame({"type": "status", "message": "WORKING-20S"}))
        assert _wait_typing(frame, page, "WORKING-20S", 10000), \
            "status barrier never painted"
        page.wait_for_timeout(16500)  # ticks at +8 s and +16 s, held
        assert _js(frame, "() => window.__streamState().readCalls") == 2, \
            "timeout ticks must not create another native read"
        _push_text(frame, _response_frame("LATE-20S-KEPT"))
        assert _wait_text(frame, page, "LATE-20S-KEPT", 10000), \
            "response after two ticks never painted"
        assert _js(frame, "() => window.__streamState().readCalls") == 2
        assert frame.get_by_text("LATE-20S-KEPT").count() == 1
        assert _assistant_count(frame) == 1
        assert len(world.chat_posts) == 0
        assert all(r["empty"] for r in _rescue_marks(world)), \
            "a parked row rescued the turn instead of the stream"
        assert errors == []
    finally:
        page.close()


def test_standalone_stall_detaches_and_cancels_reader(
        browser, static_server):
    # No bytes at all: ticks observe without new reads until the 20 s
    # hold decision, which cancels the retained reader. Nothing paints
    # and the turn never revives.
    world = StreamWorld()
    page, frame, errors = _open_standalone(browser, static_server, world)
    try:
        _send(frame, "stalled")
        page.wait_for_timeout(400)
        _push_text(frame, _session_frame())
        waited = 0
        while _js(frame, "() => window.__streamState().cancelled") < 1 \
                and waited < 45000:
            page.wait_for_timeout(500)
            waited += 500
        assert _js(frame, "() => window.__streamState().cancelled") >= 1, \
            "stalled hold never cancelled the retained reader"
        assert _js(frame, "() => window.__streamState().readCalls") == 2, \
            "stall ticks must not create native reads"
        page.wait_for_timeout(5000)
        assert _assistant_count(frame) == 0, \
            f"stalled turn painted: {_assistant_texts(frame)}"
        assert errors == []
    finally:
        page.close()


def test_standalone_stop_after_timeout_cannot_revive(
        browser, static_server):
    # Stop after a read timeout aborts the single retained read: the turn
    # settles cancelled, and the modeled abort scope refuses post-abort
    # bytes at the transport (an errored real stream likewise delivers
    # nothing further), so no later byte can revive it.
    world = StreamWorld()
    page, frame, errors = _open_standalone(browser, static_server, world)
    try:
        _send(frame, "stoppable late")
        page.wait_for_timeout(400)
        _push_text(frame, _session_frame()
                   + _frame({"type": "status", "message": "WORKING-STOP"}))
        assert _wait_typing(frame, page, "WORKING-STOP", 10000), \
            "status barrier never painted"
        page.wait_for_timeout(8500)  # past one tick on the retained read
        assert _js(frame, "() => window.__streamState().readCalls") == 2
        frame.locator("#stopButton").wait_for(state="visible", timeout=15000)
        frame.locator("#stopButton").click()
        frame.get_by_text("⏹ Generation cancelled.").wait_for(
            state="visible", timeout=15000)
        assert _js(frame, "() => window.__streamState().aborted") >= 1
        assert len(world.cancel_posts) == 1
        assert frame.locator("#stopButton").is_visible() is False
        pushes_before = _js(frame, "() => window.__streamState().pushes")
        refused = False
        try:
            _push_text(frame, _response_frame("TOO-LATE"))
        except Exception:
            refused = True
        assert refused, "transport must refuse post-abort pushes"
        assert _js(frame, "() => window.__streamState().pushes") \
            == pushes_before, "no late bytes accepted after abort"
        page.wait_for_timeout(5000)
        assert _assistant_count(frame) == 0
        assert _js(frame, "() => window.__streamState().readCalls") == 2, \
            "cancelled turn must not issue another native read"
        assert errors == []
    finally:
        page.close()
