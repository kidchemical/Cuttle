"""P5-E pre-move oracles: exact HTTP SSE behavior of the stream lanes.

Recorded against the route-driven pump BEFORE the coordinator stream
entry exists. After the rewiring these same tests must pass unchanged
(except the split-pinning spy test, which is inverted into the shared-entry
assertion) — any event/row/effect difference is a regression.

Isolation: fake executors (no CLIs, no prompts); real Flask test client
with REAL tmp-DB auth (the route's own ``get_auth_db`` binding is
patched — module-attr patches alone never reach it, and the lanes
tolerate the resulting anonymous user, which would make persist pins
vacuous). Tests mint numeric sessions and assert user/assistant rows
land in the temporary DB. Concurrency-free: busy is held/released
deterministically via ``chat_delivery``.
"""

from __future__ import annotations

import json

import pytest

from api import web_chat_api as wca
from api import chat_delivery


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "p5e_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    # web_chat_api binds get_auth_db at import (from-import); without
    # this the route verifies against the real DB, the cookie user is
    # unknown, and authed lanes 401. Same seam as test_chat_attachments.
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    return client, db, owner


@pytest.fixture
def fake_harness(monkeypatch):
    calls = []

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        calls.append({
            "agent_id": agent_id,
            "prompt": prompt,
            "session": chat_session_id,
            "kwargs": kwargs,
        })
        return {
            "success": True,
            "response": f"oracle-reply:{agent_id}:{prompt}",
            "type": "fake",
            "agent_id": agent_id,
        }

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    return calls


# ---------------------------------------------------------------------------
# Fail-closed execution guard (auto-applied to every test in this file).
# Any path that reaches a real harness CLI spawn or router provider call
# without an injected fake raises HERE — before subprocess/network — with
# a counter proving the attempt was blocked, not silently skipped. This
# is the backstop behind the per-test fakes (spend flags alone cannot
# stop a local spawn: a starred-slash default once drove a real Cursor
# CLI attempt that died on sandbox EROFS with no spend — disclosed, and
# now impossible to repeat silently).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_execution(monkeypatch):
    from api.agent_harness import kernel as _kernel
    from api.agent_router import dispatch as _dispatch
    from api.agent_router import integration as _integration

    calls = {"kernel": 0, "dispatch": 0}

    def _blocked_kernel(*args, **kwargs):
        calls["kernel"] += 1
        raise AssertionError(
            "fail-closed test guard: real harness CLI execution attempted "
            f"({args[0] if args else '?'}); inject a fake executor instead"
        )

    def _blocked_dispatch(*args, **kwargs):
        calls["dispatch"] += 1
        raise AssertionError(
            "fail-closed test guard: real router execution attempted; "
            "inject a fake instead"
        )

    monkeypatch.setattr(_kernel, "run_agent_web_command", _blocked_kernel)
    monkeypatch.setattr(_dispatch, "execute_decision", _blocked_dispatch)
    monkeypatch.setattr(_integration, "execute_decision", _blocked_dispatch)
    monkeypatch.setattr(
        _integration, "execute_explicit_target", _blocked_dispatch
    )
    return calls


def test_execution_guard_blocks_real_runners(_no_real_execution):
    """The guard — not spend flags — stops unmocked execution locally."""
    from api.agent_harness import kernel as _kernel
    from api.agent_router import dispatch as _dispatch
    from api.agent_router import integration as _integration

    for fn, args in (
        (_kernel.run_agent_web_command, ("cursor", "hi", "guard-sid")),
        (_dispatch.execute_decision, (object(),)),
        (_integration.execute_decision, (object(),)),
        (_integration.execute_explicit_target, (object(),)),
    ):
        with pytest.raises(AssertionError, match="fail-closed test guard"):
            fn(*args)
    assert _no_real_execution == {"kernel": 1, "dispatch": 3}


def _event_types(text):
    out = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            out.append(json.loads(line[len("data: "):]).get("type"))
        except ValueError:
            out.append("<bad-json>")
    return out


def _responses(text):
    out = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        body = json.loads(line[len("data: "):])
        if body.get("type") == "response":
            out.append(body)
    return out


def test_oracle_harness_stream_event_shape(authed_client, fake_harness):
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/cursor hello oracle", "session_id": sid},
    )
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    kinds = _event_types(text)
    assert kinds[0] == "session"
    assert kinds[-1] == "done"
    assert "response" in kinds
    responses = _responses(text)
    assert responses[-1]["response"] == "oracle-reply:cursor:hello oracle"
    assert responses[-1]["success"] is True
    assert fake_harness and fake_harness[0]["agent_id"] == "cursor"
    rows = db.get_messages(sid)
    assert [(m["role"], m["content"]) for m in rows] == [
        ("user", "/cursor hello oracle"),
        ("assistant", "oracle-reply:cursor:hello oracle"),
    ]
    assert chat_delivery.try_begin(sid) is True  # released
    chat_delivery.end(sid)


def test_oracle_harness_stream_status_events(authed_client, monkeypatch):
    seen = {}

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        queue = kwargs.get("status_queue")
        seen["queue"] = queue is not None
        if queue is not None:
            queue.put(("status", "oracle-working"))
        return {"success": True, "response": "status-ok", "type": "fake"}

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/cursor status check", "session_id": sid},
    )
    assert res.status_code == 200
    # Drain FIRST: post() returns after headers while the pump worker still
    # runs; asserting worker effects before draining is a race. The done
    # event is enqueued after finalize, so a full drain joins all effects.
    text = res.get_data(as_text=True)
    assert seen["queue"] is True  # executor received a live status queue
    bodies = [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
    statuses = [b for b in bodies if b.get("type") == "status"]
    assert "oracle-working" in [s.get("message") for s in statuses]
    rows = db.get_messages(sid)
    assert [(m["role"], m["content"]) for m in rows] == [
        ("user", "/cursor status check"),
        ("assistant", "status-ok"),
    ]
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


def test_oracle_router_family_stream_shape(authed_client, monkeypatch):
    calls = []

    def fake_router(message_content, session_id=None, project_path=None,
                    status_queue=None):
        calls.append((message_content, session_id))
        return {"success": True, "response": "oracle-router-reply", "type": "r"}

    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration, "handle_router_family_command", fake_router
    )
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/retry hello", "session_id": sid},
    )
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    kinds = _event_types(text)
    assert kinds[0] == "session"
    assert kinds[-1] == "done"
    assert _responses(text)[-1]["response"] == "oracle-router-reply"
    assert calls and calls[0][1] == sid
    rows = db.get_messages(sid)
    assert [(m["role"], m["content"]) for m in rows] == [
        ("user", "/retry hello"),
        ("assistant", "oracle-router-reply"),
    ]
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


def test_harness_stream_done_clears_live_status(authed_client, fake_harness):
    """Done must clear the head's "Connecting..." row.

    A leftover active row makes live-status pollers (app shell hub, status
    poll, message sync) repaint a "Connecting..." bubble after the reply.
    """
    from api import chat_live_status as live

    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/cursor /usage", "session_id": sid},
    )
    assert res.status_code == 200
    assert _event_types(res.get_data(as_text=True))[-1] == "done"
    assert live.get_live_status(sid).get("active") is False
    assert str(sid) not in live.active_live_session_ids()
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


def test_harness_stream_publishes_progress_to_live_status(
    authed_client, monkeypatch
):
    """Progress reaches live-status mid-turn so pollers never read "Connecting..."."""
    from api import chat_live_status as live

    seen = {}

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        kwargs["status_queue"].put(("status", "oracle-progress"))
        import time
        deadline = time.time() + 5
        while time.time() < deadline:
            seen["status"] = live.get_live_status(chat_session_id).get("status")
            if seen["status"] == "oracle-progress":
                break
            time.sleep(0.02)
        return {"success": True, "response": "progress-ok", "type": "fake"}

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/codex progress", "session_id": sid},
    )
    assert res.status_code == 200
    res.get_data(as_text=True)
    assert seen["status"] == "oracle-progress"
    assert live.get_live_status(sid).get("active") is False
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


def test_router_family_stream_done_clears_live_status(authed_client, monkeypatch):
    from api import chat_live_status as live
    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration,
        "handle_router_family_command",
        lambda *a, **k: {"success": True, "response": "router-ok", "type": "r"},
    )
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/retry hello", "session_id": sid},
    )
    assert res.status_code == 200
    assert _event_types(res.get_data(as_text=True))[-1] == "done"
    assert live.get_live_status(sid).get("active") is False
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


def test_oracle_stream_busy_shape(authed_client, fake_harness):
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    assert chat_delivery.try_begin(sid) is True
    try:
        res = client.post(
            "/api/chat",
            json={"message": "/cursor while busy", "session_id": sid},
        )
        assert res.status_code == 200
        kinds = _event_types(res.get_data(as_text=True))
        assert kinds == ["session", "status", "busy", "done"]
        assert not fake_harness  # executor never ran
        assert db.get_messages(sid) == []  # busy claims nothing
    finally:
        chat_delivery.end(sid)
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


def test_oracle_executor_exception_shape(authed_client, monkeypatch):
    def boom(agent_id, prompt, chat_session_id, **kwargs):
        raise RuntimeError("oracle-boom")

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", boom)
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/cursor blow up", "session_id": sid},
    )
    assert res.status_code == 200
    responses = _responses(res.get_data(as_text=True))
    assert responses
    assert responses[-1]["success"] is False
    assert "oracle-boom" in responses[-1]["response"]
    rows = db.get_messages(sid)
    assert [m["role"] for m in rows] == ["user", "assistant"]
    assert "oracle-boom" in rows[1]["content"]  # error reply still saved
    assert chat_delivery.try_begin(sid) is True  # released after error
    chat_delivery.end(sid)


def test_oracle_empty_prompt_shape(authed_client, fake_harness):
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat", json={"message": "/cursor", "session_id": sid}
    )
    assert res.status_code == 200
    body = res.get_json()
    assert "prompt after /cursor" in body["response"]
    assert not fake_harness
    assert db.get_messages(sid) == []  # usage hint, never a turn


def test_oracle_rewrite_passthrough_shape(authed_client, monkeypatch):
    """Marker-bearing replies pass through the rewrite path identically."""
    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        return {
            "success": True,
            "response": "plain text, no markers here",
            "type": "fake",
        }

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/cursor rewrite me", "session_id": sid},
    )
    assert res.status_code == 200
    assert _responses(res.get_data(as_text=True))[-1]["response"] == (
        "plain text, no markers here"
    )
    rows = db.get_messages(sid)
    assert [(m["role"], m["content"]) for m in rows] == [
        ("user", "/cursor rewrite me"),
        ("assistant", "plain text, no markers here"),
    ]
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


# ---------------------------------------------------------------------------
# P5-E shared-entry coverage. Same fakes as the oracles above, but these
# drive the owned coordinator entry directly (no HTTP): they fail if the
# route lanes bypass submit_agent_stream_turn / run_agent_stream_turn, and
# they pin the lifecycle order the HTTP oracles observe from outside.
# ---------------------------------------------------------------------------

class _EntryDelivery:
    """Effect recorder over the REAL busy/token/cancel guards.

    Claim, token, staleness, and cancel semantics are chat_delivery's own;
    only persistence (saver) and the parked-result store are recorded
    instead of executed, keeping these tests DB-free.
    """

    def __init__(self, log):
        self.log = log

    def try_begin(self, sid):
        self.log.append("begin")
        return chat_delivery.try_begin(sid)

    def current_turn(self, sid):
        return chat_delivery.current_turn(sid)

    def end(self, sid, turn=None):
        self.log.append("release")
        return chat_delivery.end(sid, turn=turn)

    def is_stale_turn(self, sid, token):
        return chat_delivery.is_stale_turn(sid, token)

    def is_turn_cancelled(self, sid):
        return chat_delivery.is_turn_cancelled(sid)

    def store_result(self, sid, result):
        self.log.append("park")


def _entry_io(log, run, shortcut_marker=None):
    from api.chat_coordinator import StreamTurnIO

    def _saver(body):
        log.append("saver")

    return StreamTurnIO(
        run_harness=run,
        run_router=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("router arm must not run in harness entry test")
        ),
        persist_user=lambda: log.append("persist_user"),
        make_saver=lambda: _saver,
        notify_mobile=lambda body: log.append("notify"),
        format_shortcut=lambda kind, sel: {"shortcut": kind,
                                           "marker": shortcut_marker},
    )


def test_direct_stream_entry_matches_http_lifecycle():
    """Direct entry emits the same event/effect order the HTTP oracles pin."""
    from api.chat_coordinator import (
        AgentSelection,
        PreparedAgentTurn,
        submit_agent_stream_turn,
    )

    log = []

    def run(agent_id, prompt, status_queue=None, **kwargs):
        log.append("executor")
        status_queue.put(("status", "direct-working"))
        return {"success": True, "response": "direct-reply", "type": "fake"}

    prepared = PreparedAgentTurn(
        message="/cursor hello direct", session_id="p5e-direct"
    )
    selection = AgentSelection(
        kind="harness", agent_id="cursor", prompt="hello direct"
    )
    events = list(
        submit_agent_stream_turn(
            prepared,
            io=_entry_io(log, run),
            delivery=_EntryDelivery(log),
            is_router_family=lambda m: False,
            selection=selection,
        )
    )
    assert [kind for kind, _ in events] == [
        "connecting", "status", "done"
    ]
    assert events[0] == ("connecting", None)  # liveness precedes worker start
    assert events[1][1] == "direct-working"
    assert events[2][1]["response"] == "direct-reply"
    # Contract order: persist → run → save → notify → park → release
    # (the trailing second release is the belt-and-suspenders finally:
    # token-guarded, a no-op once finalize already ended the turn).
    assert log == [
        "begin",
        "persist_user",
        "executor",
        "saver",
        "notify",
        "park",
        "release",
        "release",
    ]
    assert chat_delivery.try_begin("p5e-direct") is True  # slot free
    chat_delivery.end("p5e-direct")


def test_direct_stream_entry_busy_arm_claims_nothing():
    """A claimed session yields busy from the entry; no persist, no run."""
    from api.chat_coordinator import (
        AgentSelection,
        PreparedAgentTurn,
        submit_agent_stream_turn,
    )

    log = []
    assert chat_delivery.try_begin("p5e-dbusy") is True
    try:
        events = list(
            submit_agent_stream_turn(
                PreparedAgentTurn(
                    message="/cursor while busy", session_id="p5e-dbusy"
                ),
                io=_entry_io(log, lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("executor must not run when busy"))),
                delivery=_EntryDelivery(log),
                is_router_family=lambda m: False,
                selection=AgentSelection(
                    kind="harness", agent_id="cursor", prompt="while busy"
                ),
            )
        )
    finally:
        chat_delivery.end("p5e-dbusy")
    assert len(events) == 1
    assert events[0][0] == "busy"
    assert events[0][1]["busy"] is True
    assert log == ["begin"]  # claim attempted, persist/run never reached
    assert chat_delivery.try_begin("p5e-dbusy") is True
    chat_delivery.end("p5e-dbusy")


def test_direct_stream_entry_shortcut_arm_before_claim():
    """Local-mode cloud slash yields the shortcut arm without claiming."""
    from api.chat_coordinator import (
        PreparedAgentTurn,
        submit_agent_stream_turn,
    )

    log = []
    events = list(
        submit_agent_stream_turn(
            PreparedAgentTurn(
                message="/cursor hi", session_id="p5e-dshort",
                inference_mode="local",
            ),
            io=_entry_io(log, lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("executor must not run for shortcut"))),
            delivery=_EntryDelivery(log),
            is_router_family=lambda m: False,
        )
    )
    assert len(events) == 1
    assert events[0][0] == "shortcut"
    assert events[0][1]["shortcut"] == "mode_blocked"
    assert log == []  # not even a claim attempt
    assert chat_delivery.try_begin("p5e-dshort") is True
    chat_delivery.end("p5e-dshort")


def test_cancel_mid_worker_discards_result_but_releases():
    """Stop during the run: nothing saved/parked/notified, slot released."""
    from api.chat_coordinator import (
        AgentSelection,
        PreparedAgentTurn,
        submit_agent_stream_turn,
    )

    log = []

    def run(agent_id, prompt, status_queue=None, **kwargs):
        log.append("executor")
        chat_delivery.cancel_current_turn("p5e-dcancel")  # user hits Stop
        return {"success": True, "response": "late-reply", "type": "fake"}

    prepared = PreparedAgentTurn(
        message="/cursor hello cancel", session_id="p5e-dcancel"
    )
    selection = AgentSelection(
        kind="harness", agent_id="cursor", prompt="hello cancel"
    )
    events = list(
        submit_agent_stream_turn(
            prepared,
            io=_entry_io(log, run),
            delivery=_EntryDelivery(log),
            is_router_family=lambda m: False,
            selection=selection,
        )
    )
    # The in-flight consumer still sees stream termination (parity with the
    # pre-P5-E pump, which always forwarded done and filtered only progress),
    # but the late result is discarded: no save, no notify, no park.
    assert [kind for kind, _ in events] == ["connecting", "done"]
    assert log == [
        "begin", "persist_user", "executor", "release", "release",
    ]
    assert chat_delivery.try_begin("p5e-dcancel") is True  # free + sticky cleared
    chat_delivery.end("p5e-dcancel")


def test_http_cursor_cli_alias_streams_through_shared_entry(
    authed_client, fake_harness
):
    """Legacy /cursor-cli streams as /cursor through the same shared entry."""
    client, db, uid = authed_client
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "/cursor-cli hello alias",
              "session_id": sid},
    )
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    kinds = _event_types(text)
    assert kinds[0] == "session"
    assert kinds[-1] == "done"
    assert _responses(text)[-1]["response"] == "oracle-reply:cursor:hello alias"
    assert fake_harness and fake_harness[0]["agent_id"] == "cursor"
    assert fake_harness[0]["prompt"] == "hello alias"
    rows = db.get_messages(sid)
    assert [(m["role"], m["content"]) for m in rows] == [
        ("user", "/cursor-cli hello alias"),
        ("assistant", "oracle-reply:cursor:hello alias"),
    ]
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


def test_router_family_predicate_agrees_with_lanes():
    """Predicate True exactly for messages the lanes handle as router-family."""
    for msg in ("/retry hello", "/router hello", "/route cursor do it"):
        assert wca._is_router_family_message(msg) is True, msg
    for msg in (
        "/cursor hi",
        "/cursor-cli hi",
        "/status",
        "plain hello",
        "",
        "/muse hi",
    ):
        assert wca._is_router_family_message(msg) is False, msg
