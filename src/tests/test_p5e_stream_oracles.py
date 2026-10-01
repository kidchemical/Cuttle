"""P5-E pre-move oracles: exact HTTP SSE behavior of the stream lanes.

Recorded against the route-driven pump BEFORE the coordinator stream
entry exists. After the rewiring these same tests must pass unchanged
(except the split-pinning spy test, which is inverted into the shared-entry
assertion) — any event/row/effect difference is a regression.

Isolation: unique ``p5e-`` session ids; fake executors (no CLIs, no
prompts); real Flask test client with tmp-DB auth. Concurrency-free:
busy is held/released deterministically via ``chat_delivery``.
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
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    return client


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
    res = authed_client.post(
        "/api/chat",
        json={"message": "/cursor hello oracle", "session_id": "p5e-h1"},
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
    assert chat_delivery.try_begin("p5e-h1") is True  # released
    chat_delivery.end("p5e-h1")


def test_oracle_harness_stream_status_events(authed_client, monkeypatch):
    seen = {}

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        queue = kwargs.get("status_queue")
        seen["queue"] = queue is not None
        if queue is not None:
            queue.put(("status", "oracle-working"))
        return {"success": True, "response": "status-ok", "type": "fake"}

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    res = authed_client.post(
        "/api/chat",
        json={"message": "/cursor status check", "session_id": "p5e-h2"},
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
    assert chat_delivery.try_begin("p5e-h2") is True
    chat_delivery.end("p5e-h2")


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
    res = authed_client.post(
        "/api/chat",
        json={"message": "/retry hello", "session_id": "p5e-r1"},
    )
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    kinds = _event_types(text)
    assert kinds[0] == "session"
    assert kinds[-1] == "done"
    assert _responses(text)[-1]["response"] == "oracle-router-reply"
    assert calls and calls[0][1] == "p5e-r1"
    assert chat_delivery.try_begin("p5e-r1") is True
    chat_delivery.end("p5e-r1")


def test_oracle_stream_busy_shape(authed_client, fake_harness):
    assert chat_delivery.try_begin("p5e-busy") is True
    try:
        res = authed_client.post(
            "/api/chat",
            json={"message": "/cursor while busy", "session_id": "p5e-busy"},
        )
        assert res.status_code == 200
        kinds = _event_types(res.get_data(as_text=True))
        assert kinds == ["session", "status", "busy", "done"]
        assert not fake_harness  # executor never ran
    finally:
        chat_delivery.end("p5e-busy")
    assert chat_delivery.try_begin("p5e-busy") is True
    chat_delivery.end("p5e-busy")


def test_oracle_executor_exception_shape(authed_client, monkeypatch):
    def boom(agent_id, prompt, chat_session_id, **kwargs):
        raise RuntimeError("oracle-boom")

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", boom)
    res = authed_client.post(
        "/api/chat",
        json={"message": "/cursor blow up", "session_id": "p5e-err"},
    )
    assert res.status_code == 200
    responses = _responses(res.get_data(as_text=True))
    assert responses
    assert responses[-1]["success"] is False
    assert "oracle-boom" in responses[-1]["response"]
    assert chat_delivery.try_begin("p5e-err") is True  # released after error
    chat_delivery.end("p5e-err")


def test_oracle_empty_prompt_shape(authed_client, fake_harness):
    res = authed_client.post(
        "/api/chat", json={"message": "/cursor", "session_id": "p5e-empty"}
    )
    assert res.status_code == 200
    body = res.get_json()
    assert "prompt after /cursor" in body["response"]
    assert not fake_harness


def test_oracle_rewrite_passthrough_shape(authed_client, monkeypatch):
    """Marker-bearing replies pass through the rewrite path identically."""
    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        return {
            "success": True,
            "response": "plain text, no markers here",
            "type": "fake",
        }

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    res = authed_client.post(
        "/api/chat",
        json={"message": "/cursor rewrite me", "session_id": "p5e-rw"},
    )
    assert res.status_code == 200
    assert _responses(res.get_data(as_text=True))[-1]["response"] == (
        "plain text, no markers here"
    )
    assert chat_delivery.try_begin("p5e-rw") is True
    chat_delivery.end("p5e-rw")


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
    assert [kind for kind, _ in events] == ["status", "done"]
    assert events[0][1] == "direct-working"
    assert events[1][1]["response"] == "direct-reply"
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
    assert [kind for kind, _ in events] == ["done"]
    assert log == [
        "begin", "persist_user", "executor", "release", "release",
    ]
    assert chat_delivery.try_begin("p5e-dcancel") is True  # free + sticky cleared
    chat_delivery.end("p5e-dcancel")


def test_http_cursor_cli_alias_streams_through_shared_entry(
    authed_client, fake_harness
):
    """Legacy /cursor-cli streams as /cursor through the same shared entry."""
    res = authed_client.post(
        "/api/chat",
        json={"message": "/cursor-cli hello alias",
              "session_id": "p5e-alias"},
    )
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    kinds = _event_types(text)
    assert kinds[0] == "session"
    assert kinds[-1] == "done"
    assert _responses(text)[-1]["response"] == "oracle-reply:cursor:hello alias"
    assert fake_harness and fake_harness[0]["agent_id"] == "cursor"
    assert fake_harness[0]["prompt"] == "hello alias"
    assert chat_delivery.try_begin("p5e-alias") is True
    chat_delivery.end("p5e-alias")


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
