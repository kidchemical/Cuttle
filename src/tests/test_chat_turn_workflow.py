"""Phase 5 P5-B: core turn-workflow ownership pins.

Drive the REAL `/api/chat` sync workflow and the REAL
`_generate_chat_stream` completion path with a fake DB, fake runners,
and the real delivery/busy/cancel services. Written BEFORE the
extraction; post-move the same effects must come from the coordinator
owner (`api.chat_turn_workflow`).
"""

from __future__ import annotations

import pytest


USER = {"id": 7, "username": "owner", "email": "owner@example.com"}


class FakeDB:
    def __init__(self):
        self.rows = []
        self._next_id = 0

    def verify_auth_session(self, token):
        return dict(USER) if token == "tok-seam" else None

    def add_message(self, chat_session_id, role, content, metadata=None):
        self._next_id += 1
        self.rows.append({
            "id": self._next_id,
            "session": chat_session_id,
            "role": role,
            "content": content,
            "metadata": metadata,
        })
        return self._next_id

    def get_chat_session(self, *a, **k):
        return {"id": 777}

    def set_session_project(self, *a, **k):
        return True

    def update_session_activity(self, *a, **k):
        return None


@pytest.fixture
def workflow_env(monkeypatch, tmp_path):
    from api import web_chat_api as wca

    db = FakeDB()
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    monkeypatch.setattr(
        wca, "_resolve_auth_chat_session", lambda sid: (dict(USER), 777, False)
    )
    monkeypatch.setattr(wca, "_stamp_auth_session_project", lambda *a, **k: None)
    monkeypatch.setattr(
        "api.starred_slash.apply_default_sticky_prefix", lambda m, *a, **k: m
    )
    monkeypatch.setattr(
        "api.chat_titler.schedule_session_autoname", lambda *a, **k: None
    )
    return wca, db


def _post(wca, message, sid="seam-w1", runner=None, monkeypatch=None, **kw):
    if runner is not None:
        monkeypatch.setattr(wca, "_run_pinned_harness_turn", runner)
    client = wca.app.test_client()
    client.set_cookie("session_token", "tok-seam")
    body = {"message": message, "session_id": sid, "stream": False}
    body.update(kw)
    return client.post("/api/chat", json=body)


def test_sync_harness_turn_persists_user_then_assistant(workflow_env, monkeypatch):
    wca, db = workflow_env

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        assert (agent_id, prompt) == ("cursor", "do the thing")
        return {
            "success": True,
            "response": "did it",
            "type": "cursor",
            "cursor_run": {
                "requested_model": "auto",
                "reported_model": "Sonnet",
                "request_id": "abc123",
                "cwd": "/tmp",
            },
        }

    res = _post(wca, "/cursor do the thing", runner=fake_run, monkeypatch=monkeypatch)
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["response"] == "did it"
    assert body["session_id"] == 777
    roles = [(r["session"], r["role"]) for r in db.rows]
    assert roles == [(777, "user"), (777, "assistant")]
    assert db.rows[0]["content"] == "/cursor do the thing"
    assert db.rows[1]["content"] == "did it"
    chips = db.rows[1]["metadata"]["slash_command"]["chips"]
    assert chips[0]["category"] == "cursor"
    assert chips[0]["label"].startswith("Cursor - ")

    from api import chat_delivery

    assert chat_delivery.is_busy(777) is False


def test_sync_executor_exception_persists_user_only(workflow_env, monkeypatch):
    wca, db = workflow_env

    def boom(*a, **k):
        raise RuntimeError("runner down")

    res = _post(wca, "/cursor do it", runner=boom, monkeypatch=monkeypatch)
    # The harness sync lane does not contain executor errors: the turn is
    # released by finally, the user row stays, no assistant row is written,
    # and the error propagates to the route's 500 handler.
    assert res.status_code == 500
    assert [r["role"] for r in db.rows] == ["user"]

    from api import chat_delivery

    assert chat_delivery.is_busy(777) is False


def test_sync_busy_second_send_gets_409(workflow_env, monkeypatch):
    from api import chat_delivery

    wca, db = workflow_env
    assert chat_delivery.try_begin(777) is True
    try:
        res = _post(
            wca, "/cursor hi", runner=lambda *a, **k: {"success": True}, monkeypatch=monkeypatch
        )
        assert res.status_code == 409
        assert res.get_json()["error"] == "busy"
        assert db.rows == []
    finally:
        chat_delivery.end(777)


def test_sync_cancelled_turn_returns_reply_without_persisting_assistant(
    workflow_env, monkeypatch
):
    from api import chat_delivery

    wca, db = workflow_env

    def run_then_cancel(*a, **k):
        chat_delivery.cancel_current_turn(777)
        return {"success": True, "response": "late reply", "type": "cursor"}

    res = _post(
        wca, "/cursor slow one", sid="seam-w4", runner=run_then_cancel,
        monkeypatch=monkeypatch,
    )
    assert res.status_code == 200
    assert res.get_json()["response"] == "late reply"
    # User row is written before the run; the saver skips the assistant row
    # once the turn is cancelled; the turn is still released.
    assert [r["role"] for r in db.rows] == ["user"]
    assert chat_delivery.is_busy(777) is False


def test_stream_finalize_orders_claim_run_save_release_and_parks_result(
    workflow_env,
):
    wca, _db = workflow_env
    from api import chat_delivery

    order = []

    def fake_process(status_queue=None):
        order.append("run")
        return {"success": True, "response": "streamed!", "type": "cursor"}

    def on_save(res):
        order.append("save")
        assert res["response"] == "streamed!"

    def on_claimed():
        order.append("claimed")

    chunks = list(wca._generate_chat_stream(fake_process, "seam-w5", on_result=on_save, on_claimed=on_claimed))
    assert any('"done"' in c or "'done'" in c or "done" in c for c in chunks)
    assert order == ["claimed", "run", "save"]
    assert chat_delivery.is_busy("seam-w5") is False
    parked = chat_delivery.take_result("seam-w5")
    assert parked and parked["response"] == "streamed!"


# --------------------------------------------------------------------------
# Architectural pin: owners never import the entry module back
# --------------------------------------------------------------------------


def test_workflow_and_runner_owners_have_no_entry_module_import():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    owned = [
        "src/api/chat_turn.py",
        "src/api/chat_turn_workflow.py",
        "src/api/chat_live_status.py",
        "src/api/chat_status.py",
        "src/api/chat_metadata.py",
        "src/api/agent_harness/runners.py",
        "src/api/agent_harness/kernel.py",
        "src/api/subagents/turns.py",
        "src/api/subagents/identity.py",
        "src/api/internal_http.py",
        "src/api/agent_router/dispatch.py",
        "src/api/agent_router/supervised/adapters.py",
    ]
    for rel in owned:
        text = (root / rel).read_text(encoding="utf-8")
        assert "web_chat_api" not in text, rel


# --------------------------------------------------------------------------
# Owner unit tests: api.chat_turn_workflow order/policy (mutation-sensitive)
# --------------------------------------------------------------------------


def test_owner_agent_lane_orders_persist_run_after_release():
    from api import chat_delivery, chat_turn_workflow as wf

    calls = []
    body, status = wf.run_agent_sync_turn(
        "seam-own1",
        delivery=chat_delivery,
        persist_user=lambda: calls.append("persist"),
        run=lambda: calls.append("run") or {"success": True, "response": "ok"},
        after_run=lambda _b: calls.append("after"),
    )
    assert (body["response"], status) == ("ok", 200)
    assert calls == ["persist", "run", "after"]
    assert chat_delivery.is_busy("seam-own1") is False


def test_owner_agent_lane_busy_returns_409_shape():
    from api import chat_delivery, chat_turn_workflow as wf

    assert chat_delivery.try_begin("seam-own2") is True
    try:
        body, status = wf.run_agent_sync_turn(
            "seam-own2",
            delivery=chat_delivery,
            persist_user=lambda: None,
            run=lambda: {"success": True},
            after_run=lambda _b: None,
        )
        assert status == 409
        assert body["error"] == "busy" and body["busy"] is True
        assert body["session_id"] == "seam-own2"
    finally:
        chat_delivery.end("seam-own2")


def test_owner_agent_lane_exception_still_releases():
    from api import chat_delivery, chat_turn_workflow as wf

    def boom():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        wf.run_agent_sync_turn(
            "seam-own3",
            delivery=chat_delivery,
            persist_user=lambda: None,
            run=boom,
            after_run=lambda _b: None,
        )
    assert chat_delivery.is_busy("seam-own3") is False


def test_owner_pipeline_lane_skips_save_on_failure_or_supersede():
    from api import chat_delivery, chat_turn_workflow as wf

    saved = []
    body, status = wf.run_pipeline_sync_turn(
        "seam-own4",
        delivery=chat_delivery,
        persist_user=lambda: None,
        run=lambda: {"success": False, "response": "bad", "type": "x"},
        save_assistant=lambda r: saved.append(r) or r,
        build_body=lambda r: wf.build_pipeline_body(r, "seam-own4", usage_meta_fn=lambda _r: None),
    )
    assert status == 200 and saved == []
    assert body == {
        "success": False, "response": "bad", "session_id": "seam-own4", "type": "x",
    }

    def run_then_cancel():
        chat_delivery.cancel_current_turn("seam-own5")
        return {"success": True, "response": "late"}

    body, _ = wf.run_pipeline_sync_turn(
        "seam-own5",
        delivery=chat_delivery,
        persist_user=lambda: None,
        run=run_then_cancel,
        save_assistant=lambda r: saved.append(r) or r,
        build_body=lambda r: wf.build_pipeline_body(r, "seam-own5", usage_meta_fn=lambda _r: None),
    )
    assert body["response"] == "late" and saved == []
    assert chat_delivery.is_busy("seam-own5") is False


def test_owner_pipeline_body_carries_query_report_run_usage():
    from api import chat_turn_workflow as wf

    body = wf.build_pipeline_body(
        {"success": True, "response": "r", "query_id": "q", "report_url": "u",
         "cursor_run": {"a": 1}},
        "seam-own6",
        usage_meta_fn=lambda _r: {"tokens": 5},
    )
    assert body == {
        "success": True, "response": "r", "session_id": "seam-own6",
        "type": "pipeline_execution", "query_id": "q", "report_url": "u",
        "cursor_run": {"a": 1}, "usage": {"tokens": 5},
    }


def test_owner_finalize_orders_save_notify_park_release():
    from api import chat_delivery, chat_turn_workflow as wf

    calls = []
    assert chat_delivery.try_begin("seam-own7") is True
    token = chat_delivery.current_turn("seam-own7")
    kept = wf.finalize_stream_result(
        chat_delivery, "seam-own7", token, {"success": True, "response": "hi"},
        on_result=lambda _r: calls.append("save"),
        notify_mobile=lambda _r: calls.append("notify"),
    )
    assert kept is True
    parked = chat_delivery.take_result("seam-own7")
    assert parked["response"] == "hi"
    assert calls == ["save", "notify"]
    assert chat_delivery.is_busy("seam-own7") is False


def test_owner_finalize_superseded_saves_nothing_but_releases():
    from api import chat_delivery, chat_turn_workflow as wf

    calls = []
    assert chat_delivery.try_begin("seam-own8") is True
    token = chat_delivery.current_turn("seam-own8")
    chat_delivery.cancel_current_turn("seam-own8")
    kept = wf.finalize_stream_result(
        chat_delivery, "seam-own8", token, {"success": True, "response": "late"},
        on_result=lambda _r: calls.append("save"),
        notify_mobile=lambda _r: calls.append("notify"),
    )
    assert kept is False and calls == []
    assert chat_delivery.take_result("seam-own8") is None
    assert chat_delivery.is_busy("seam-own8") is False


def test_owner_finalize_contains_save_errors_and_stale_token_release():
    from api import chat_delivery, chat_turn_workflow as wf

    assert chat_delivery.try_begin("seam-own9") is True
    token = chat_delivery.current_turn("seam-own9")

    def bad_save(_r):
        raise RuntimeError("db down")

    kept = wf.finalize_stream_result(
        chat_delivery, "seam-own9", token, {"success": True, "response": "hi"},
        on_result=bad_save, notify_mobile=None,
    )
    assert kept is True  # save failed but park/release still happened
    assert chat_delivery.take_result("seam-own9")["response"] == "hi"
    assert chat_delivery.is_busy("seam-own9") is False


def test_stream_cancelled_turn_saves_and_parks_nothing(workflow_env):
    wca, _db = workflow_env
    from api import chat_delivery

    saved = []

    def fake_process(status_queue=None):
        chat_delivery.cancel_current_turn("seam-w6")
        return {"success": True, "response": "too late", "type": "cursor"}

    chunks = list(
        wca._generate_chat_stream(
            fake_process, "seam-w6", on_result=lambda r: saved.append(r), on_claimed=lambda: None
        )
    )
    assert saved == []
    assert chat_delivery.take_result("seam-w6") is None
    assert chat_delivery.is_busy("seam-w6") is False
    # The caller still sees the reply over SSE; it is just never persisted.
    assert any('"response"' in c and "too late" in c for c in chunks)
