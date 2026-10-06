"""Shadow Stop -> resend -> reload over REAL child Flask (S2 journeys:
ordinary baseline plus stop-resend on desktop and phone viewports).

No canned API responses and no `page.route` API mocks: the single route
is an exact-origin guard — same-origin falls through, anything else
(including explicit `ws:`/`wss:` denial) aborts; non-network schemes fall
through untouched. The page, HTTP server, SQLite history, SSE pump,
cancel route, and busy tokens are production. The ONLY fake is the
S1-armed executor (scenarios queued BEFORE each request with unique
IDs/hold/status; released by scenario ID through the nonce control
channel). Page dependencies need no faked API: the only external URLs in
`chat_page.html` are Google Fonts plus five pinned optional-CDN assets
(highlight.js / vega, see EXPECTED_ABORTED_CDN_URLS), all of which the
guard aborts and records — nothing is fetched.

Contract: uncooperative first fake keeps holding past the real cancel;
releasing it while the newer turn holds the lock must persist no stale
row and keep the newer lock (409); releasing the second paints exactly
once; reload shows the exact persisted rows.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pytest

# S2 shadow journeys are verified on Linux only (loopback child +
# headless Chromium); pure B1 tests stay platform-neutral.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="S2 shadow journeys verified on Linux only")

from api import dev_instance  # required S2 dependency: fail if missing

from .test_shared_diff_modal import browser  # noqa: F401  (existing fixture)

CANDIDATE = Path(__file__).resolve().parents[3]

FIRST = "first turn shadow hold"
SECOND = "second turn shadow resend"
SECOND_REPLY = "shadow second reply once"

_counter = itertools.count()


_B1_PROOF_FILES = ("src/api/chat_turn_workflow.py",
                   "src/api/web_chat_api.py")


@pytest.fixture(scope="module")
def shadow():
    seed = dev_instance.prepare_snapshot(CANDIDATE)
    assert seed.get("app_dir") and seed.get("codehash"), seed.keys()
    child, manifest = dev_instance.launch(
        seed, scenario="blocked", port=0)
    try:
        for key in ("origin", "nonce", "codehash", "port"):
            assert manifest.get(key), manifest.keys()
        assert manifest["codehash"] == seed["codehash"], (
            "child must run the snapshotted bytes")
        assert int(manifest["port"]) != 8080, "never the live port"
        seed_anchors = seed.get("anchor_hashes") or {}
        anchors = manifest.get("anchors") or {}
        assert len(seed_anchors) == 4, seed_anchors.keys()
        for rel, proof in anchors.items():
            assert proof.get("sha") == seed_anchors[rel], (
                f"child did not load candidate bytes: {rel}")
        print("S2 browser shadow proof: "
              f"codehash={seed['codehash']} "
              + " ".join(
                  f"{rel.rsplit('/', 1)[-1]}={seed_anchors[rel][:16]}"
                  for rel in _B1_PROOF_FILES)
              + f" files={seed.get('files')}"
              f" origin={manifest.get('origin')}"
              f" pid={manifest.get('pid')}"
              f" port={manifest.get('port')}"
              f" handshake={manifest.get('handshake')}", flush=True)
        yield child, manifest
    finally:
        try:
            end = _state(manifest["origin"], manifest["nonce"])
            started = sum(end.get("started", {}).values())
            completed = sum(end.get("completed", {}).values())
            counts = end.get("blocked_counts", {})
            blocked = end["blocked_attempts"]
            print(f"S2 browser shadow totals: started={started} "
                  f"completed={completed} blocked={len(blocked)} "
                  f"blocked_counts={counts} "
                  f"blocked_attempts={blocked}", flush=True)
        except Exception as exc:
            print(f"S2 browser shadow totals unavailable: {exc!r}",
                  flush=True)
        child.stop()
        dev_instance.discard_snapshot(seed)


def _opener(jar=None):
    import http.cookiejar

    # NOTE: an empty CookieJar is falsy — `or` would silently swap in a
    # replacement jar and strand the caller's cookies. Explicit None check.
    if jar is None:
        jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(jar))


def _register_cookie(origin):
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    opener = _opener(jar)

    def post(path, payload):
        req = urllib.request.Request(
            origin + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with opener.open(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())

    tag = next(_counter)
    status, _ = post("/api/auth/register", {
        "username": f"s2browser{tag}", "password": "shadow-pass-123"})
    assert status == 200
    status, body = post("/api/auth/sessions",
                        {"session_name": f"s2 browser {tag}"})
    assert status == 200 and body.get("session_id"), body
    token = next(
        (c.value for c in jar if c.name == "session_token"), None)
    assert token, "register must set session_token cookie"
    return token


def _new_chat(origin, token, tag):
    req = urllib.request.Request(
        origin + "/api/auth/sessions",
        data=json.dumps({"session_name": f"s2 page {tag}"}).encode(),
        headers={"Content-Type": "application/json",
                 "Cookie": f"session_token={token}"},
        method="POST")
    with _opener().open(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    assert body.get("success") is True and body.get("session_id"), body
    return body["session_id"]


def _api(origin, token, method, path, payload=None):
    import urllib.error

    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Cookie": f"session_token={token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(origin + path, data=data, headers=headers,
                                 method=method)
    try:
        with _opener().open(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _control(origin, nonce, payload, method="POST"):
    data = json.dumps(payload).encode() if method == "POST" else None
    headers = {"X-Shadow-Nonce": nonce}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(origin + "/__shadow/control", data=data,
                                 headers=headers, method=method)
    with _opener().open(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _open_guarded_page(browser, origin, token, viewport=None):
    """Fresh context, exact-origin guard, no API mocks.

    Same-origin requests fall through untouched, but the guard RECORDS
    the real serialized /api/chat POST bodies so tests prove which
    session key the page actually sent (bare N vs db_session_N)."""
    allowed = origin.rstrip("/")
    blocked = []
    chat_posts = []
    context = browser.new_context(viewport=viewport)
    host = urlparse(origin).hostname or "127.0.0.1"
    context.add_cookies([{"name": "session_token", "value": token,
                          "domain": host, "path": "/"}])

    def guard(route):
        url = route.request.url
        scheme = urlparse(url).scheme
        if scheme in ("ws", "wss"):
            blocked.append(url)  # explicit WebSocket deny, observed
            route.abort()
            return
        if scheme in ("http", "https"):
            if url == allowed or url.startswith(allowed + "/"):
                if (route.request.method == "POST"
                        and urlparse(url).path == "/api/chat"):
                    try:
                        chat_posts.append(json.loads(
                            route.request.post_data or "null"))
                    except ValueError:
                        pass
                route.fallback()
                return
            blocked.append(url)
            route.abort()
            return
        route.fallback()  # data:/blob:/about: never reach the network

    context.route("**/*", guard)
    if hasattr(context, "route_web_socket"):
        def deny_ws(ws):
            blocked.append(ws.url)
            ws.close()

        context.route_web_socket("**/*", deny_ws)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    return context, page, errors, blocked, chat_posts


def _pump_until(page, predicate, timeout_ms, label):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting: {label}")
        page.wait_for_timeout(200)


def _queue(origin, nonce, outcome, response, **kw):
    uid = f"s2w-{next(_counter)}"
    entry = {"id": uid, "outcome": outcome, "response": response,
             "hold": False, "status": []}
    entry.update(kw)
    body = _control(origin, nonce, {"queue": [entry], "release": []})
    assert body.get("success") is True, body
    return uid


def _release(origin, nonce, *uids):
    body = _control(origin, nonce, {"queue": [], "release": list(uids)})
    assert body.get("success") is True, body


def _state(origin, nonce):
    return _control(origin, nonce, {}, method="GET")


def _history_rows(origin, token, sid):
    status, body = _api(
        origin, token, "GET", f"/api/auth/sessions/{sid}/messages")
    assert status == 200 and body.get("success") is True, body
    return [(m["role"], m["content"]) for m in body["messages"]]


def _history_full(origin, token, sid):
    """Full message dicts (roles + metadata/kind for notice asserts)."""
    status, body = _api(
        origin, token, "GET", f"/api/auth/sessions/{sid}/messages")
    assert status == 200 and body.get("success") is True, body
    return body["messages"]


# Closed set of EXPECTED child route-denies: pure owned GETs the real
# page shell probes on load. The S1 profile keeps them denied-but-counted
# (vendor/probe read routes stay real 403s); settings/projects/widgets,
# static img/sounds, and PATCH sessions/<id> are allowlisted separately.
# Anything outside this set — any method/path, any file/socket/subprocess/
# executor deny — fails. Never ignore-all-403s, never fake API.
EXPECTED_ROUTE_DENIES = frozenset({
    ("GET", "/api/agents"),
    ("GET", "/api/cursor-agent/models"),
    ("GET", "/api/muse/models"),
    ("GET", "/api/doctor"),
    ("GET", "/api/terminal/status"),
    ("GET", "/api/agent-context"),
    # Project command manifest discovery; stays blocked, B1 needs no
    # file/CLI inspection behind it.
    ("GET", "/api/project-commands"),
    # Real git status spawn; stays blocked for B1.
    ("GET", "/api/git/pending-changes"),
})

# Journey routes that must NEVER appear blocked (history/SSE/paint/cancel,
# auth, control plane).
_PROTECTED_ROUTE_FRAGMENTS = ("/api/chat", "/api/auth/", "/__shadow/")


def _assert_child_guard_profile(state, label):
    """Closed-set child guard check for real-page journeys."""
    assert "blocked_counts" in state, (
        f"{label}: S1 full guard state required (blocked_counts), got "
        f"keys {sorted(state.keys())}")
    counts = state["blocked_counts"]
    for kind in ("file", "socket", "subprocess", "executor"):
        assert counts.get(kind, 0) == 0, (
            f"{label}: unexpected {kind} denies: {counts}")
    # Sole full-log key of the public contract (S1 restores it full).
    attempts = state["blocked_attempts"]
    unexpected = []
    intentional = 0
    for entry in attempts:
        if entry.startswith("[shadow-guard] blocked route: "):
            rest = entry.split("blocked route: ", 1)[1]
            method, _, path = rest.partition(" ")
            if (method, path) in EXPECTED_ROUTE_DENIES:
                intentional += 1
                continue
        unexpected.append(entry)
    print(f"{label}: intentional probe 403s={intentional} "
          f"unexpected={len(unexpected)}", flush=True)
    assert unexpected == [], (
        f"{label}: unexpected blocked attempts: {unexpected}")
    for entry in attempts:
        for fragment in _PROTECTED_ROUTE_FRAGMENTS:
            assert fragment not in entry, (
                f"{label}: journey route blocked: {entry}")


# Pinned optional-CDN assets (existing src/web/js/shared/optional_cdn.js):
# the page needs none of them, so they STAY network-blocked — but these
# five exact URLs are the expected aborts. Anything else stays a failure.
# No route allow, no download, no vendoring, no other CDN/host accepted.
EXPECTED_ABORTED_CDN_URLS = frozenset({
    "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1"
    "/build/styles/github-dark.min.css",
    "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1"
    "/build/highlight.min.js",
    "https://cdn.jsdelivr.net/npm/vega@5.30.0",
    "https://cdn.jsdelivr.net/npm/vega-lite@5.21.0",
    "https://cdn.jsdelivr.net/npm/vega-embed@6.26.0",
})


def _assert_blocked_clean(blocked):
    for url in blocked:
        host = urlparse(url).hostname or ""
        assert ":8080" not in url and ":8000" not in url \
            and ":8888" not in url, f"live/dev port touched: {url}"
        assert "/api/" not in urlparse(url).path, (
            f"API call left the origin: {url}")
        if url in EXPECTED_ABORTED_CDN_URLS:
            continue
        assert host in ("fonts.googleapis.com", "fonts.gstatic.com"), (
            f"unexpected blocked URL (asset gap must be explicit): {url}")


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 800},
                                      {"width": 390, "height": 844}])
def test_shadow_stop_resend_no_stale(shadow, browser, viewport):
    _child, manifest = shadow
    origin, nonce = manifest["origin"], manifest["nonce"]
    token = _register_cookie(origin)
    jtag = next(_counter)
    session_name = f"s2 page journey-{jtag}"
    sid = _new_chat(origin, token, f"journey-{jtag}")
    context, page, errors, blocked, chat_posts = _open_guarded_page(
        browser, origin, token, viewport)
    first_uid = None
    second_uid = None
    # Response statuses only (no headers/bodies/cookies): readiness proof.
    resp_statuses = []
    page.on("response", lambda r: resp_statuses.append(
        (urlparse(r.url).path, r.status)))
    try:
        page.goto(f"{origin}/chat_page.html?chat={sid}",
                  wait_until="domcontentloaded")
        # Fresh chat has no .message rows: wait for an enabled composer
        # instead of a first message, then prove the page adopted THIS
        # session (URL query + empty history for our sid).
        page.wait_for_function(
            "() => { const el = document.getElementById('chatInput');"
            " return !!(el && !el.disabled); }",
            timeout=15000)
        assert f"chat={sid}" in page.url, page.url
        # Readiness gate (same as baseline): the input enables before
        # async session load/draft restore completes, which wipes a
        # filled prompt so the click sends nothing (started=0). Wait
        # for the page's own authenticated history GET plus the exact
        # server session title visibly adopted.
        history_path = f"/api/auth/sessions/{sid}/messages"
        _pump_until(
            page,
            lambda: any(p == history_path and s == 200
                        for p, s in resp_statuses),
            20000, "authenticated history GET")
        page.get_by_text(session_name).wait_for(
            state="visible", timeout=20000)
        assert page.locator("#chatMessages .message").count() == 0
        assert _history_rows(origin, token, sid) == []

        first_uid = _queue(
            origin, nonce, "success", "first late reply",
            hold=True, status=["Working…", "tool 1: grep"])
        page.locator("#chatInput").fill(f"/cursor {FIRST}")
        page.locator("#sendButton").click()
        _pump_until(
            page,
            lambda: _state(origin, nonce)["started"].get(first_uid, 0)
            == 1, 20000, "first fake entry")
        assert first_uid in _state(origin, nonce).get("statuses", {}), (
            "status bytes must flow through the real pump before release")

        def _first_adopted():
            try:
                return _history_rows(origin, token, sid) == [
                    ("user", f"/cursor {FIRST}")]
            except Exception:
                return False

        _pump_until(page, _first_adopted, 20000,
                    "page posted into our session")
        page.locator("#stopButton").wait_for(state="visible")
        page.locator("#stopButton").click()
        # Stop retains local activity, while only the system notice is durable.
        page.get_by_text("⏹ Stopped generating.").wait_for(state="visible")
        assert page.locator("#stopButton").is_visible() is False
        stopped = page.locator('#chatMessages [data-stopped="true"]')
        assert stopped.count() == 1
        assert stopped.locator('.slash-command-chip--header').count() > 0
        assert stopped.locator('.slash-command-chip--header:not(.slash-command-chip--error)').count() == 0
        assert stopped.get_attribute('data-message-index') is None

        # Wait for the exact production system row before sending again.
        def _stop_persisted():
            try:
                return _history_rows(origin, token, sid) == [
                    ("user", f"/cursor {FIRST}"),
                    ("system", "⏹ Stopped generating."),
                ]
            except Exception:
                return False

        _pump_until(page, _stop_persisted, 20000, "stop system row stored")
        stop_rows = [m for m in _history_full(origin, token, sid)
                     if m["role"] == "system"]
        assert len(stop_rows) == 1, stop_rows
        assert stop_rows[0]["content"] == "⏹ Stopped generating."
        assert (stop_rows[0].get("kind") == "generation-stop"
                or (stop_rows[0].get("metadata") or {}).get("kind")
                == "generation-stop"), stop_rows

        second_uid = _queue(
            origin, nonce, "success", SECOND_REPLY,
            hold=True, status=["Working…"])
        # Plain SECOND: the existing sticky Cursor chip already prefixes
        # "/cursor" (filling it again double-prefixes, as forensics showed).
        page.locator("#chatInput").fill(SECOND)
        page.locator("#sendButton").click()
        _pump_until(
            page,
            lambda: _state(origin, nonce)["started"].get(second_uid, 0)
            == 1, 20000, "second fake entry")
        sent_second = chat_posts[-1] if chat_posts else {}
        assert isinstance(sent_second, dict) and \
            sent_second.get("message") == f"/cursor {SECOND}", (
            f"resend must serialize single-prefixed: {sent_second!r}")
        page.locator("#stopButton").wait_for(state="visible")

        # Release the uncooperative first fake while the newer turn holds
        # the lock. NOTE: `completed` counts the fake's return only, NOT
        # server finalization — the real barriers here are the 409 below
        # (newer lock survived) and eventual history consistency.
        _release(origin, nonce, first_uid)
        _pump_until(
            page,
            lambda: _state(origin, nonce)["completed"].get(first_uid, 0)
            == 1, 20000, "first fake return")
        # The busy probe must use the SAME serialized session key the
        # page really sent (recorded by the guard, not assumed): only
        # then does the 409 prove the newer page turn kept its lock.
        # History below keeps the real numeric DB sid.
        page_sids = [body.get("session_id") for body in chat_posts
                     if isinstance(body, dict)]
        assert page_sids, "page must POST /api/chat through the guard"
        probe_sid = page_sids[-1]
        assert str(probe_sid).replace("db_session_", "") == str(sid), (
            f"page posted into another session: {page_sids!r} vs {sid}")
        status, _ = _api(origin, token, "POST", "/api/chat",
                         {"message": "/cursor probe busy",
                          "session_id": probe_sid, "stream": False})
        assert status == 409, "newer turn must keep the busy lock"

        def _clean_history():
            try:
                return _history_rows(origin, token, sid) == [
                    ("user", f"/cursor {FIRST}"),
                    ("system", "⏹ Stopped generating."),
                    ("user", f"/cursor {SECOND}"),
                ]
            except Exception:
                return False

        _pump_until(page, _clean_history, 20000,
                    "eventual history consistency")
        page.locator("#stopButton").wait_for(state="visible")

        _release(origin, nonce, second_uid)
        page.get_by_text(SECOND_REPLY).wait_for(state="visible")
        assert page.locator("#stopButton").is_visible() is False
        assert page.get_by_text(SECOND_REPLY).count() == 1
        assert page.get_by_text("first late reply").count() == 0

        page.reload(wait_until="domcontentloaded")
        _pump_until(
            page,
            lambda: page.get_by_text(SECOND_REPLY).count() == 1
            and page.locator(
                '.message.system[data-kind="generation-stop"]').count() == 1
            and page.locator("#chatMessages .message.user").count() == 2,
            20000, "reload rows")
        assert _history_rows(origin, token, sid) == [
            ("user", f"/cursor {FIRST}"),
            ("system", "⏹ Stopped generating."),
            ("user", f"/cursor {SECOND}"),
            ("assistant", SECOND_REPLY),
        ]
        assert page.get_by_text("first late reply").count() == 0
        assert page.locator(
            '.message.system[data-kind="generation-stop"]').count() == 1
        # CHILD guard state at journey end, after paint + history reads —
        # not only the outside URL guard below.
        state = _state(origin, nonce)
        assert state["started"].get(first_uid, 0) == 1, state
        assert state["started"].get(second_uid, 0) == 1, state
        assert state["completed"].get(first_uid, 0) == 1, state
        assert state["completed"].get(second_uid, 0) == 1, state
        _assert_child_guard_profile(state, "stop-resend journey")
        _assert_blocked_clean(blocked)
        assert errors == [], errors
    except BaseException:
        # Failure dump BEFORE releasing/closing: real server state,
        # never faked; no cookies/auth headers dumped.
        try:
            fail_state = _state(origin, nonce)
        except Exception as exc:
            fail_state = f"<state unreadable: {exc}>"
        try:
            fail_history = _history_rows(origin, token, sid)
        except Exception as exc:
            fail_history = f"<history unreadable: {exc}>"
        print("JOURNEY DIAGNOSTIC "
              f"sid={sid} chat_posts={chat_posts} "
              f"child_state={fail_state} history={fail_history} "
              f"errors={errors} blocked={blocked} "
              f"dom={_dom_snapshot(page)}", flush=True)
        raise
    finally:
        # Release held fakes BEFORE closing the page: a held turn must
        # never strand the run behind a hung diagnostic read.
        for _uid in (first_uid, second_uid):
            if not _uid:
                continue
            try:
                _release(origin, nonce, _uid)
            except Exception:
                pass
        context.close()


def _dom_snapshot(page):
    """Defensive DOM read for failure diagnostics (never raises)."""
    out = {}
    for key, js in (
            ("input_disabled",
             "() => { const el = document.getElementById('chatInput');"
             " return el ? !!el.disabled : 'missing'; }"),
            ("input_value",
             "() => { const el = document.getElementById('chatInput');"
             " return el ? String(el.value || '').slice(0, 120) : 'missing'; }"),
            ("send_visible",
             "() => { const el = document.getElementById('sendButton');"
             " return el ? !!(el.offsetParent !== null) : 'missing'; }"),
            ("stop_visible",
             "() => { const el = document.getElementById('stopButton');"
             " return el ? !!(el.offsetParent !== null) : 'missing'; }"),
            ("message_count",
             "() => document.querySelectorAll('#chatMessages .message').length"),
            ("last_message",
             "() => { const els = document.querySelectorAll("
             "'#chatMessages .message');"
             " return els.length ? els[els.length - 1].innerText.slice(0, 200)"
             " : ''; }"),
            ("toast_count",
             "() => document.querySelectorAll('.toast,.notice,.toast-message'"
             ").length"),
            ("toast_text",
             "() => Array.from(document.querySelectorAll("
             "'.toast,.notice,.toast-message')).map((el) => el.innerText)"
             ".join(' | ').slice(0, 300)"),
            ("body_error_hint",
             "() => (document.body ? document.body.innerText : '')"
             ".slice(0, 300)"),
    ):
        try:
            out[key] = page.evaluate(js)
        except Exception:
            out[key] = "n/a"
    return out


def test_shadow_ordinary_reply_paints(shadow, browser):
    """Baseline: an unheld scripted turn paints through the real pump."""
    _child, manifest = shadow
    origin, nonce = manifest["origin"], manifest["nonce"]
    token = _register_cookie(origin)
    ctag = next(_counter)
    session_name = f"s2 page baseline-{ctag}"
    sid = _new_chat(origin, token, f"baseline-{ctag}")
    uid = _queue(origin, nonce, "success", "ordinary shadow reply")
    context, page, errors, blocked, chat_posts = _open_guarded_page(
        browser, origin, token)
    # Response statuses only (no headers/bodies/cookies here): the
    # /api/chat body itself is read once, after paint, over real HTTP.
    resp_statuses = []
    page.on("response", lambda r: resp_statuses.append(
        (urlparse(r.url).path, r.status)))
    chat_status = None
    chat_body_snippet = None
    try:
        page.goto(f"{origin}/chat_page.html?chat={sid}",
                  wait_until="domcontentloaded")
        page.wait_for_function(
            "() => { const el = document.getElementById('chatInput');"
            " return !!(el && !el.disabled); }",
            timeout=15000)
        assert f"chat={sid}" in page.url, page.url
        # Readiness gate (no sleeps): the input enables before async
        # session load/draft restore completes, which can clobber the
        # filled prompt. Wait for the page's own authenticated history
        # GET plus the exact server session title visibly adopted.
        history_path = f"/api/auth/sessions/{sid}/messages"
        _pump_until(
            page,
            lambda: any(p == history_path and s == 200
                        for p, s in resp_statuses),
            20000, "authenticated history GET")
        page.get_by_text(session_name).wait_for(
            state="visible", timeout=20000)
        assert page.locator("#chatMessages .message").count() == 0
        assert _history_rows(origin, token, sid) == []
        with page.expect_response(
                lambda r: urlparse(r.url).path == "/api/chat"
                and r.request.method == "POST",
                timeout=30000) as resp_info:
            page.locator("#chatInput").fill("/cursor ordinary turn")
            page.locator("#sendButton").click()
        resp = resp_info.value
        chat_status = resp.status
        sent = chat_posts[-1] if chat_posts else {}
        assert isinstance(sent, dict) and \
            str(sent.get("session_id")) == str(sid), (
            f"posted body session must be this chat: {sent!r} vs {sid}")
        assert sent.get("message") == "/cursor ordinary turn", sent
        page.get_by_text("ordinary shadow reply").wait_for(
            state="visible", timeout=30000)
        try:
            chat_body_snippet = (resp.text() or "")[:500]
        except Exception as exc:
            chat_body_snippet = f"<body unreadable: {exc}>"
        print(f"baseline chat POST: status={chat_status} "
              f"body={chat_body_snippet} "
              f"posts={chat_posts}", flush=True)
        assert _history_rows(origin, token, sid) == [
            ("user", "/cursor ordinary turn"),
            ("assistant", "ordinary shadow reply"),
        ]
        # Child guard state at journey end, after paint + history read.
        state = _state(origin, nonce)
        assert state["started"].get(uid, 0) == 1, state
        _assert_child_guard_profile(state, "ordinary baseline")
        _assert_blocked_clean(blocked)
        assert errors == [], errors
    except BaseException:
        # Failure diagnostics BEFORE context.close(): real server state,
        # never faked; no cookies/auth headers dumped.
        try:
            fail_state = _state(origin, nonce)
        except Exception as exc:
            fail_state = f"<state unreadable: {exc}>"
        try:
            fail_history = _history_rows(origin, token, sid)
        except Exception as exc:
            fail_history = f"<history unreadable: {exc}>"
        print("BASELINE DIAGNOSTIC "
              f"sid={sid} chat_posts={chat_posts} "
              f"chat_status={chat_status} chat_body={chat_body_snippet} "
              f"responses={resp_statuses[-15:]} dom={_dom_snapshot(page)} "
              f"child_state={fail_state} history={fail_history} "
              f"errors={errors} blocked={blocked}", flush=True)
        raise
    finally:
        context.close()
