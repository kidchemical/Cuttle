"""Shadow widget-order journey over REAL child Flask: single assistant message
carrying a base Tasks widget then a same-id patch (add + set_done).

No canned API responses and no `page.route` API mocks: the single route is
the exact-origin guard reused from test_shadow_chat_stop_resend (same-origin
falls through, anything else aborts). The page, HTTP server, SQLite history,
SSE pump, and widget store/rewrite are production. The ONLY fake is the
S1-armed executor (one deterministic successful response queued BEFORE the
request). The journey proves the D3 server fix end to end: the save-path
rewrite settles base-then-patch, the assistant row stores chip text (no raw
tags), the store payload is ordered, and no client-fallback PUT/PATCH fires.

S2 shadow journey, verified on Linux only (loopback child + headless
Chromium); pure unit tests stay platform-neutral.
"""

from __future__ import annotations

import json
import sys
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="S2 shadow journeys verified on Linux only")

from .test_shared_diff_modal import (  # noqa: E402  (existing fixture)
    browser,  # noqa: F401
)
from .test_shadow_chat_stop_resend import (  # noqa: E402  (reuse, no new harness)
    _api,
    _assert_blocked_clean,
    _assert_child_guard_profile,
    _counter,
    _history_rows,
    _new_chat,
    _open_guarded_page,
    _pump_until,
    _queue,
    _register_cookie,
    _state,
    shadow,  # noqa: F401  (module fixture, registered by import)
)

BASE_ITEMS = [{"id": "1", "text": "a", "done": False}]
WIDGET_BASE = (
    '<cuttle_widget id="w-shadow-1" type="tasks" title="Ship" scope="session">'
    + json.dumps({"items": BASE_ITEMS}) + "</cuttle_widget>"
)
WIDGET_PATCH = (
    '<cuttle_widget id="w-shadow-1" type="tasks" title="Ship" scope="session"'
    ' op="patch">'
    + json.dumps({"add": [{"id": "2", "text": "b"}],
                  "set_done": ["1"]}) + "</cuttle_widget>"
)
EXECUTOR_RESPONSE = (
    f"Widget order check {WIDGET_BASE} then {WIDGET_PATCH} done.")


def _widget_rows(origin, token, sid):
    status, body = _api(
        origin, token, "GET", f"/api/gizmos/tasks?session_id={sid}")
    assert status == 200 and body.get("success") is True, body
    return body["gizmos"]


def test_shadow_widget_order_base_then_patch(shadow, browser):
    _child, manifest = shadow
    origin, nonce = manifest["origin"], manifest["nonce"]
    token = _register_cookie(origin)
    ctag = next(_counter)
    session_name = f"s2 page widget-order-{ctag}"
    sid = _new_chat(origin, token, f"widget-order-{ctag}")
    uid = _queue(origin, nonce, "success", EXECUTOR_RESPONSE)
    context, page, errors, blocked, chat_posts = _open_guarded_page(
        browser, origin, token)
    # Attempted page requests (method + path): even a denied or unfinished
    # mutation is visible here, unlike response-only recording.
    attempts = []
    page.on("request", lambda req: attempts.append(
        (req.method, urlparse(req.url).path)))
    resp_statuses = []
    page.on("response", lambda r: resp_statuses.append(
        (urlparse(r.url).path, r.status)))
    try:
        page.goto(f"{origin}/chat_page.html?chat={sid}",
                  wait_until="domcontentloaded")
        page.wait_for_function(
            "() => { const el = document.getElementById('chatInput');"
            " return !!(el && !el.disabled); }",
            timeout=15000)
        assert f"chat={sid}" in page.url, page.url
        history_path = f"/api/auth/sessions/{sid}/messages"
        _pump_until(
            page,
            lambda: any(p == history_path and s == 200
                        for p, s in resp_statuses),
            20000, "authenticated history GET")
        page.get_by_text(session_name).wait_for(
            state="visible", timeout=20000)
        assert _history_rows(origin, token, sid) == []

        page.locator("#chatInput").fill("/cursor widget order check")
        with page.expect_response(
                lambda r: urlparse(r.url).path == "/api/chat"
                and r.request.method == "POST",
                timeout=60000) as resp_info:
            page.locator("#sendButton").click()
        chat_resp = resp_info.value
        assert chat_resp.status == 200, chat_resp.status
        ctype = (chat_resp.headers or {}).get("content-type", "")
        assert "text/event-stream" in ctype, ctype
        chat_body = chat_resp.text() or ""
        # Parse production SSE frames: `data: {...}` JSON per line.
        frames = []
        for line in chat_body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    frames.append(json.loads(line[len("data:"):]))
                except ValueError:
                    pass
        kinds = [f.get("type") for f in frames
                 if isinstance(f, dict)]
        # Exactly one terminal done; one successful response carrying this
        # turn's content (error frames also say success:true, so match the
        # executor reply text, not a flag).
        assert kinds.count("done") == 1, kinds
        responses = [f for f in frames
                     if isinstance(f, dict) and f.get("type") == "response"]
        assert any("Widget order check" in str(
            f.get("response", "")) for f in responses), kinds
        print(f"widget-order chat POST: status={chat_resp.status} "
              f"stream_bytes={len(chat_body)} frames={kinds}", flush=True)

        def _done():
            try:
                rows = _history_rows(origin, token, sid)
                return (len(rows) == 2
                        and rows[0][0] == "user"
                        and rows[1][0] == "assistant")
            except Exception:
                return False

        _pump_until(page, _done, 30000, "assistant row persisted")
        rows = _history_rows(origin, token, sid)
        assert rows[0] == ("user", "/cursor widget order check")
        role, assistant = rows[1]
        assert role == "assistant"
        assert "<cuttle_widget" not in assistant.lower(), assistant
        assert "pinned above composer" in assistant, assistant
        page.get_by_text("pinned above composer").first.wait_for(
            state="visible", timeout=30000)

        # Store proof over the real widget API: base item done, added item
        # present, document order kept.
        page.locator('#chatWidgetsStrip [data-widget-id="w-shadow-1"]').wait_for(state="visible")
        widgets = _widget_rows(origin, token, sid)
        assert [w["id"] for w in widgets] == ["w-shadow-1"], widgets
        items = widgets[0]["payload"]["items"]
        assert [(i["id"], i["text"], i["done"]) for i in items] == [
            ("1", "a", True), ("2", "b", False)], items

        # Reload: rewritten chip text persists, no raw tags, same rows.
        page.reload(wait_until="domcontentloaded")
        _pump_until(
            page,
            lambda: page.get_by_text(
                "pinned above composer").count() >= 1,
            20000, "chip repainted after reload")
        assert _history_rows(origin, token, sid) == rows
        widgets = _widget_rows(origin, token, sid)
        assert [(i["id"], i["text"], i["done"])
                for i in widgets[0]["payload"]["items"]] == [
            ("1", "a", True), ("2", "b", False)]

        # Whole-journey no-fallback fence, checked AFTER reload: no
        # attempted PUT/PATCH to /api/widgets/* at any point (store-backed
        # GET list reads are expected; request events fire even for
        # denied or unfinished attempts).
        fired = [(m, p) for m, p in attempts
                 if m in ("PUT", "PATCH")
                 and p.startswith("/api/widgets/")]
        assert fired == [], fired

        state = _state(origin, nonce)
        assert state["started"].get(uid, 0) == 1, state
        _assert_child_guard_profile(state, "widget-order journey")
        _assert_blocked_clean(blocked)
        assert errors == [], errors
    except BaseException:
        try:
            fail_state = _state(origin, nonce)
        except Exception as exc:
            fail_state = f"<state unreadable: {exc}>"
        try:
            fail_history = _history_rows(origin, token, sid)
        except Exception as exc:
            fail_history = f"<history unreadable: {exc}>"
        print("WIDGET-ORDER DIAGNOSTIC "
              f"sid={sid} chat_posts={chat_posts} "
              f"attempts={attempts} "
              f"child_state={fail_state} history={fail_history} "
              f"errors={errors} blocked={blocked}", flush=True)
        raise
    finally:
        context.close()
