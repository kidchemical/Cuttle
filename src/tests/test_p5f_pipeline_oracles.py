"""P5-F pre-move oracles: the leftover pipeline (fallback) path.

The pipeline is not a graph engine anymore (``/pipelines`` answers
``pipelines_removed``; graphs retired with Self_Improvement). What remains:

- sync route pipeline lane (claimed ``run_pipeline_sync_turn``) whose
  executor is ``process_message_with_bot``;
- ``process_message_with_bot`` itself (compat entry: local-mode prompts,
  ``/api/sessions/send``): unclaimed ``submit_agent_turn`` for
  harness/plain-router arms, naked no-LLM fallback otherwise;
- stream route pipeline lane (``_generate_chat_stream`` lifecycle).

Recorded BEFORE the fallback ownership move. HTTP shape/row/effect
oracles must pass unchanged after it; the ``test_submit_*`` direct tests
encode the NEW contract (owned fallback instead of ``None``) and FAIL
pre-change. Fakes only: fake plain-router, tmp-DB auth, ``p5f-`` ids.

``[ERR-20261001-001]`` is fixed in this isolated branch (pending
integration, not released): the pipeline stream lane now uses the
shared saver, so ``[CANCELLED]``/system rows are skipped in every
lane. The oracle below pins the unified contract.
"""

from __future__ import annotations

import json

import pytest

from api import web_chat_api as wca
from api import chat_delivery


@pytest.fixture
def authed_db(monkeypatch, tmp_path):
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "p5f_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    # web_chat_api binds get_auth_db at import (from-import); the module-attr
    # patches above never reach it. Slash lanes tolerate the resulting
    # anonymous user, but the pipeline lane requires real auth — patch the
    # route's own binding too (same seam as test_chat_attachments).
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    return client, db, owner


@pytest.fixture
def router_abstain(monkeypatch):
    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration, "maybe_route_plain_message", lambda *a, **k: None
    )


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


def _announced_sid(text):
    """Streams announce the resolved numeric session id first."""
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[len("data: "):])
        if payload.get("type") == "session" and payload.get("session_id"):
            return int(payload["session_id"])
    raise AssertionError("no session event")


def test_oracle_pipeline_sync_fallback_shape(authed_db, router_abstain):
    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "plain oracle fallback", "sticky_agent": "none", "stream": False,
              "session_id": sid},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["type"] == "no_pipeline"
    assert "No graph is running" in body["response"]
    assert body["session_id"] == sid
    rows = db.get_messages(sid)
    roles = [m["role"] for m in rows]
    assert roles == ["user", "assistant"]  # user persisted, fallback saved
    assert rows[0]["content"] == "plain oracle fallback"
    assert "No graph is running" in rows[1]["content"]
    assert chat_delivery.try_begin(body["session_id"]) is True  # released
    chat_delivery.end(body["session_id"])


def test_oracle_pipeline_sync_router_accepts(authed_db, monkeypatch):
    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration,
        "maybe_route_plain_message",
        lambda *a, **k: {"success": True, "response": "routed!",
                         "type": "plain"},
    )
    res = client.post(
        "/api/chat",
        json={"message": "plain oracle routed", "sticky_agent": "none", "stream": False,
              "session_id": sid},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["response"] == "routed!"
    assert body["session_id"] == sid
    rows = db.get_messages(sid)
    assert [m["role"] for m in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "routed!"
    assert chat_delivery.try_begin(body["session_id"]) is True
    chat_delivery.end(body["session_id"])


def test_oracle_pipeline_stream_fallback_shape(authed_db, router_abstain):
    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    res = client.post(
        "/api/chat",
        json={"message": "plain oracle stream", "sticky_agent": "none", "session_id": sid},
    )
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    kinds = _event_types(text)
    assert kinds[0] == "session"
    assert kinds[-1] == "done"
    responses = _responses(text)
    assert responses[-1]["success"] is True
    assert "No graph is running" in responses[-1]["response"]
    assert _announced_sid(text) == sid
    rows = db.get_messages(sid)
    assert [m["role"] for m in rows] == ["user", "assistant"]
    assert "No graph is running" in rows[1]["content"]
    assert chat_delivery.try_begin(sid) is True  # released
    chat_delivery.end(sid)


def test_oracle_sessions_send_stays_unclaimed(authed_db, router_abstain):
    """Cross-session send: owned fallback, no rows, never claimed."""
    client, db, uid = authed_db
    target = db.create_chat_session(uid)
    res = client.post(
        "/api/sessions/send",
        json={"target_session": target, "message": "plain send note"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert "No graph is running" in body["response"]
    assert db.get_messages(target) == []  # unclaimed: nothing persisted
    assert chat_delivery.try_begin(target) is True  # never claimed
    chat_delivery.end(target)


def test_oracle_pipeline_stream_busy_shape(authed_db, router_abstain):
    client, _db, _uid = authed_db
    assert chat_delivery.try_begin(77) is True
    try:
        res = client.post(
            "/api/chat",
            json={"message": "plain while busy", "sticky_agent": "none", "session_id": 77},
        )
        assert res.status_code == 409  # shared auth-section pre-check: JSON
        body = res.get_json()
        assert body["busy"] is True
    finally:
        chat_delivery.end(77)
    assert chat_delivery.try_begin(77) is True
    chat_delivery.end(77)


def test_oracle_pipeline_stream_cancelled_row_divergence(
    authed_db, monkeypatch
):
    """[ERR-20261001-001] unified (B1): pipeline stream skips [CANCELLED].

    No turn was cancelled here — the executor itself reports a cancelled
    line with success set and the turn is kept, but the shared saver now
    owns pipeline persistence, so the status line never becomes history.
    Transport still terminates normally for the consumer.
    """
    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration,
        "maybe_route_plain_message",
        lambda *a, **k: {"success": True,
                         "response": "[CANCELLED] stopped by user",
                         "type": "plain"},
    )
    res = client.post(
        "/api/chat",
        json={"message": "plain cancelled line", "sticky_agent": "none", "session_id": sid},
    )
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert _event_types(text)[-1] == "done"
    assert _announced_sid(text) == sid
    rows = db.get_messages(sid)
    assert [m["role"] for m in rows] == ["user"]


def test_oracle_pipeline_stream_cancel_discards(authed_db, monkeypatch):
    """Stop mid-run: user row stays, late result saves/parks nothing."""
    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    import api.agent_router.integration as integration

    def cancelling_route(message_content, session_id=None, **kwargs):
        chat_delivery.cancel_current_turn(session_id)
        return {"success": True, "response": "late-reply", "type": "plain"}

    monkeypatch.setattr(
        integration, "maybe_route_plain_message", cancelling_route
    )
    res = client.post(
        "/api/chat",
        json={"message": "plain stop me", "sticky_agent": "none", "session_id": sid},
    )
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert _event_types(text)[-1] == "done"  # consumer still sees termination
    assert _announced_sid(text) == sid
    rows = db.get_messages(sid)
    assert [m["role"] for m in rows] == ["user"]  # late reply discarded
    assert chat_delivery.try_begin(sid) is True  # free + sticky cleared
    chat_delivery.end(sid)


# ---------------------------------------------------------------------------
# New-contract direct tests. These FAIL pre-change (submit returns None /
# StreamTurnIO has no pipeline arm) and pass once the fallback is owned.
# ---------------------------------------------------------------------------

class _EntryDelivery:
    """Effect recorder over the REAL busy/token/cancel guards.

    Claim, token, staleness, and cancel semantics are chat_delivery's own;
    only the parked-result store is recorded instead of executed.
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


def _compat_io(log, run_router):
    from api.chat_coordinator import AgentTurnIO

    return AgentTurnIO(
        run_harness=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("harness must not run in fallback test")),
        run_router=run_router,
        persist_user=lambda: log.append("persist_user"),
        make_saver=lambda: None,
        should_save=lambda _b: False,
        notify_mobile=None,
        format_shortcut=lambda kind, sel: {"shortcut": kind},
    )


def test_submit_sync_pipeline_returns_owned_fallback_not_none():
    """Empty message (pipeline kind): owned no-LLM outcome, never None."""
    from api.chat_coordinator import (
        PreparedAgentTurn,
        submit_agent_turn,
    )
    from api.chat_turn_workflow import pipeline_fallback_result

    log = []
    out = submit_agent_turn(
        PreparedAgentTurn(message="", session_id="p5f-d1"),
        io=_compat_io(log, lambda **k: None),
        delivery=chat_delivery,
        claim=False,
    )
    assert out.body is not None  # pre-change: None (caller reimplemented)
    assert out.body == pipeline_fallback_result()
    assert out.body["type"] == "no_pipeline"
    assert log == ["persist_user"]


def test_submit_sync_abstain_returns_owned_fallback():
    """Plain-router abstain: owned no-LLM outcome, never None."""
    from api.chat_coordinator import (
        PreparedAgentTurn,
        submit_agent_turn,
    )
    from api.chat_turn_workflow import pipeline_fallback_result

    log = []
    out = submit_agent_turn(
        PreparedAgentTurn(message="plain abstain", session_id="p5f-d2"),
        io=_compat_io(log, lambda **k: None),
        delivery=chat_delivery,
        claim=False,
    )
    assert out.body is not None  # pre-change: None (naked tail in route)
    assert out.body == pipeline_fallback_result()


def test_submit_stream_pipeline_arm_uses_owned_skeleton():
    """Stream pipeline arm: same lifecycle order as the harness twin."""
    from api.chat_coordinator import (
        AgentSelection,
        PreparedAgentTurn,
        StreamTurnIO,
        submit_agent_stream_turn,
    )

    log = []

    def run_pipeline(status_queue=None):
        log.append("executor")
        status_queue.put(("status", "pipeline-working"))
        return {"success": True, "response": "pipeline-reply", "type": "plain"}

    def _saver(body):
        log.append("saver")

    io = StreamTurnIO(
        run_harness=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("harness must not run")),
        run_router=lambda **k: (_ for _ in ()).throw(
            AssertionError("router must not run")),
        persist_user=lambda: log.append("persist_user"),
        make_saver=lambda: _saver,
        notify_mobile=lambda body: log.append("notify"),
        format_shortcut=lambda kind, sel: {"shortcut": kind},
        run_pipeline=run_pipeline,  # pre-change: TypeError (no such member)
    )
    events = list(
        submit_agent_stream_turn(
            PreparedAgentTurn(
                message="plain direct stream", session_id="p5f-d3"),
            io=io,
            delivery=_EntryDelivery(log),
            is_router_family=lambda m: False,
            selection=AgentSelection(kind="pipeline"),
        )
    )
    assert [kind for kind, _ in events] == [
        "connecting", "status", "done"
    ]
    assert events[1][1] == "pipeline-working"
    assert events[2][1]["response"] == "pipeline-reply"
    assert log == [
        "begin", "persist_user", "executor", "saver", "notify", "park",
        "release", "release",
    ]
    assert chat_delivery.try_begin("p5f-d3") is True
    chat_delivery.end("p5f-d3")


def test_stale_finalize_never_releases_newer_turn():
    """Late completion: discarded AND the live turn keeps its slot."""
    from api.chat_turn_workflow import finalize_stream_result

    saved = []
    assert chat_delivery.try_begin("p5f-dstale") is True
    token_old = chat_delivery.current_turn("p5f-dstale")
    chat_delivery.end("p5f-dstale")
    assert chat_delivery.try_begin("p5f-dstale") is True  # newer turn
    kept = finalize_stream_result(
        chat_delivery,
        "p5f-dstale",
        token_old,
        {"success": True, "response": "late"},
        on_result=saved.append,
    )
    assert kept is False
    assert saved == []
    assert chat_delivery.try_begin("p5f-dstale") is False  # newer turn held
    chat_delivery.end("p5f-dstale")
    assert chat_delivery.try_begin("p5f-dstale") is True
    chat_delivery.end("p5f-dstale")


def _live_status():
    from api import chat_live_status as live

    return live


def test_stale_completion_keeps_newer_lingering_status():
    """Stale old worker never clears a newer turn's live-status entry.

    Newer turn began AND finished (slot free) but its inactive entry
    lingers. Pre-fix the adapter inferred freshness from the free slot
    and cleared it; the owned entry must carry the real token staleness.
    """
    from api import web_chat_api as wca

    live = _live_status()
    sid = "p5f-stale-keep"
    try:
        chat_delivery.end(sid)
    except Exception:
        pass

    def fake_run(status_queue=None):
        # Newer turn runs to completion while the old worker is in flight.
        chat_delivery.end(sid)
        assert chat_delivery.try_begin(sid) is True
        live.set_live_status(sid, "newer done", active=False)
        chat_delivery.end(sid)
        return {"success": True, "response": "old late reply", "type": "plain"}

    saved = []
    chunks = list(
        wca._generate_chat_stream(fake_run, sid, on_result=saved.append)
    )
    assert _event_types("\n".join(chunks))[-1] == "done"
    assert saved == []  # stale result discarded
    assert chat_delivery.try_begin(sid) is True  # slot free
    chat_delivery.end(sid)
    # The lingering newer entry must survive the stale completion.
    assert live.get_live_status(sid).get("status") == "newer done"
    live.clear_live_status(sid)


def test_current_completion_clears_own_status():
    """Unchanged: a current turn's done still clears its own status."""
    from api import web_chat_api as wca

    live = _live_status()
    sid = "p5f-current-clear"
    try:
        chat_delivery.end(sid)
    except Exception:
        pass
    live.set_live_status(sid, "working", active=True)

    saved = []
    chunks = list(
        wca._generate_chat_stream(
            lambda status_queue=None: {
                "success": True, "response": "fresh", "type": "plain",
            },
            sid,
            on_result=saved.append,
        )
    )
    assert _event_types("\n".join(chunks))[-1] == "done"
    assert saved and saved[0]["response"] == "fresh"
    assert live.get_live_status(sid).get("active") is False
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)
    live.clear_live_status(sid)


def test_cancelled_completion_clears_status():
    """Unchanged: Stop then done clears (stale but cancelled)."""
    from api import web_chat_api as wca

    live = _live_status()
    sid = "p5f-cancel-clear"
    try:
        chat_delivery.end(sid)
    except Exception:
        pass
    live.set_live_status(sid, "working", active=True)

    def fake_run(status_queue=None):
        chat_delivery.cancel_current_turn(sid)  # user hits Stop
        return {"success": True, "response": "too late", "type": "plain"}

    saved = []
    chunks = list(
        wca._generate_chat_stream(fake_run, sid, on_result=saved.append)
    )
    assert _event_types("\n".join(chunks))[-1] == "done"
    assert saved == []
    assert live.get_live_status(sid).get("active") is False
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)
    live.clear_live_status(sid)


def test_entry_reports_completion_staleness():
    """The owned entry reports token staleness for the done-clear."""
    from api.chat_coordinator import (
        AgentSelection,
        PreparedAgentTurn,
        submit_agent_stream_turn,
    )

    log = []

    def run_pipeline(status_queue=None):
        log.append("executor")
        chat_delivery.end("p5f-dstale-flag")
        assert chat_delivery.try_begin("p5f-dstale-flag") is True
        # Published after the rebegin: the drain must filter it as stale.
        status_queue.put(("status", "stale-progress"))
        return {"success": True, "response": "late", "type": "plain"}

    def _saver(body):
        log.append("saver")

    from api.chat_coordinator import StreamTurnIO

    io = StreamTurnIO(
        run_harness=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("harness must not run")),
        run_router=lambda **k: (_ for _ in ()).throw(
            AssertionError("router must not run")),
        persist_user=lambda: log.append("persist_user"),
        make_saver=lambda: _saver,
        notify_mobile=lambda body: log.append("notify"),
        format_shortcut=lambda kind, sel: {"shortcut": kind},
        run_pipeline=run_pipeline,
    )
    completion = {}
    events = list(
        submit_agent_stream_turn(
            PreparedAgentTurn(
                message="plain stale flag", session_id="p5f-dstale-flag"),
            io=io,
            delivery=_EntryDelivery(log),
            is_router_family=lambda m: False,
            selection=AgentSelection(kind="pipeline"),
            completion=completion,  # pre-fix: TypeError (no such param)
        )
    )
    # Stale progress filtered, done still terminates the consumer.
    assert [kind for kind, _ in events] == ["connecting", "done"]
    assert completion == {"stale": True, "cancelled": False}
    assert "saver" not in log and "park" not in log
    # The newer turn legitimately holds the slot: a stale worker must
    # never release it. Releasing here is test cleanup, not the entry.
    assert chat_delivery.try_begin("p5f-dstale-flag") is False
    chat_delivery.end("p5f-dstale-flag")
    assert chat_delivery.try_begin("p5f-dstale-flag") is True
    chat_delivery.end("p5f-dstale-flag")


def test_stream_persist_failure_releases_and_reports():
    """Claim acquired but user persist throws: released, executor silent."""
    from api.chat_coordinator import (
        AgentSelection,
        PreparedAgentTurn,
        submit_agent_stream_turn,
    )
    from api.chat_turn_workflow import run_agent_stream_turn

    def boom():
        raise RuntimeError("p5f-persist-boom")

    events = list(
        run_agent_stream_turn(
            "p5f-dpersist",
            delivery=chat_delivery,
            persist_user=boom,
            run=lambda events: (_ for _ in ()).throw(
                AssertionError("executor must not run")),
            make_saver=lambda: None,
            notify_mobile=None,
        )
    )
    assert len(events) == 1
    assert events[0][0] == "done"
    assert events[0][1]["success"] is False
    assert "p5f-persist-boom" in events[0][1]["response"]
    assert chat_delivery.try_begin("p5f-dpersist") is True
    chat_delivery.end("p5f-dpersist")
