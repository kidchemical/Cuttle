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

    root = Path(__file__).resolve().parents[3]
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


def test_owner_pipeline_lane_keeps_failure_text_but_skips_supersede():
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
    # B1 kept-rule: useful failure text reaches the saver (which applies
    # the exact skip policy); the wire body is unchanged either way.
    assert status == 200 and len(saved) == 1
    assert body == {
        "success": False, "response": "bad", "session_id": "seam-own4", "type": "x",
    }
    saved.clear()

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


def test_sync_lane_suppresses_after_run_when_superseded():
    """Superseded claimed sync: no after_run (save/notify), new busy held."""
    from api import chat_delivery
    from api import chat_turn_workflow as wf

    sid = "b1-sup-sync-effect"
    calls = []

    def run_then_supersede():
        chat_delivery.end(sid)
        assert chat_delivery.try_begin(sid) is True  # newer turn takes slot
        return {"success": True, "response": "stale"}

    body, status = wf.run_agent_sync_turn(
        sid,
        delivery=chat_delivery,
        persist_user=lambda: None,
        run=run_then_supersede,
        after_run=lambda _b: calls.append("after"),
    )
    assert status == 200 and body["response"] == "stale"
    assert calls == []
    assert chat_delivery.try_begin(sid) is False  # newer busy held
    chat_delivery.end(sid)


def test_sync_lane_suppresses_after_run_when_cancelled():
    """Cancelled claimed sync: no after_run (save/notify)."""
    from api import chat_delivery
    from api import chat_turn_workflow as wf

    sid = "b1-cancel-sync-effect"
    calls = []

    def run_then_cancel():
        chat_delivery.cancel_current_turn(sid)
        return {"success": True, "response": "too late"}

    body, status = wf.run_agent_sync_turn(
        sid,
        delivery=chat_delivery,
        persist_user=lambda: None,
        run=run_then_cancel,
        after_run=lambda _b: calls.append("after"),
    )
    assert status == 200 and body["response"] == "too late"
    assert calls == []
    chat_delivery.end(sid)


def test_launch_gate_lane_ignores_normal_message():
    """Non-gate messages return None so the route continues dispatch."""
    from api.chat_turn_workflow import run_launch_gate_lane

    assert run_launch_gate_lane(
        "hello",
        session_id=1,
        auth_user=dict(USER),
        get_auth_db=lambda: None,
        button_click_history=lambda m: (m, None),
    ) is None


def test_launch_gate_lane_project_action_persists(monkeypatch):
    """A project-action button is handled + persisted by the owner."""
    from api.chat_turn_workflow import run_launch_gate_lane
    import api.project_actions as project_actions

    monkeypatch.setattr(
        project_actions,
        "handle_project_action_button",
        lambda msg, session_id=None: {
            "success": True, "response": "done", "type": "project_action",
        },
    )
    db = FakeDB()
    body = run_launch_gate_lane(
        "[button:project-action-confirm] abc123",
        session_id=777,
        auth_user=dict(USER),
        get_auth_db=lambda: db,
        button_click_history=lambda m: (m, None),
    )
    assert body is not None
    assert body["response"] == "done"
    assert body["session_id"] == 777
    assert [r["role"] for r in db.rows] == ["user", "assistant"]
    assert db.rows[1]["content"] == "done"


def test_launch_gate_lane_silent_form_locks_without_history(monkeypatch):
    """Silent owned forms lock in history; no user/assistant rows are written."""
    from api.chat_turn_workflow import run_launch_gate_lane
    import api.action_forms as action_forms

    marked = {}
    monkeypatch.setattr(
        action_forms,
        "handle_action_form_chat_message",
        lambda msg, session_id=None: {
            "success": True, "response": "locked", "type": "action_form",
            "silent": True, "lock": "form", "form_id": "f1",
        },
    )
    monkeypatch.setattr(
        action_forms,
        "mark_action_form_consumed_in_history",
        lambda **kw: marked.update(kw),
    )
    db = FakeDB()
    body = run_launch_gate_lane(
        "[action-form:f1]",
        session_id=777,
        auth_user=dict(USER),
        get_auth_db=lambda: db,
        button_click_history=lambda m: (m, None),
    )
    assert body is not None and body["session_id"] == 777
    assert db.rows == []
    assert marked.get("form_id") == "f1"
    assert marked.get("session_id") == "db_session_777"


def test_launch_gate_lane_wired_into_chat_route(workflow_env, monkeypatch):
    """POST /api/chat routes a project-action button through the owner lane."""
    wca, db = workflow_env
    import api.project_actions as project_actions

    monkeypatch.setattr(
        project_actions,
        "handle_project_action_button",
        lambda msg, session_id=None: {
            "success": True, "response": "gate-ok", "type": "project_action",
        },
    )
    res = _post(wca, "[button:project-action-confirm] abc123")
    assert res.status_code == 200
    body = res.get_json()
    assert body["response"] == "gate-ok"
    assert body["session_id"] == 777


def _shell_lane(*args, **kwargs):
    from api.chat_turn_workflow import run_shell_action_lane
    return run_shell_action_lane(*args, **kwargs)


def test_shell_action_lane_prompt_action_replaces_message(monkeypatch):
    """A prompt project command expands into the message; never terminal."""
    import api.project_commands as pc

    monkeypatch.setattr(
        pc,
        "try_expand_message_project_command",
        lambda msg, path: {
            "action": "prompt", "message": "EXPANDED", "command": {"name": "build"},
        },
    )
    body, mc, busy = _shell_lane(
        "/build",
        session_id=777,
        auth_user=dict(USER),
        data={},
        resolve_project_path=lambda d: "/tmp/proj",
        persist_user_turn=lambda sid: None,
        make_saver=lambda sid: None,
        rewrite_actions=lambda *a, **k: None,
    )
    assert body is None and mc == "EXPANDED" and busy is False


def test_shell_action_lane_shell_action_persists(monkeypatch):
    """A shell project command runs as no-LLM, persists, and returns a body."""
    import api.project_commands as pc

    monkeypatch.setattr(
        pc,
        "try_expand_message_project_command",
        lambda msg, path: {
            "action": "shell", "command": {"name": "deploy"}, "args": "prod",
        },
    )
    monkeypatch.setattr(
        pc, "run_project_command_shell",
        lambda cmd, args, session_id=None: {"success": True, "output": "ok"},
    )
    monkeypatch.setattr(
        pc, "format_project_command_shell_reply",
        lambda cmd, result: "output-body",
    )
    persisted, saved = [], []
    body, mc, busy = _shell_lane(
        "/deploy prod",
        session_id=777,
        auth_user=dict(USER),
        data={},
        resolve_project_path=lambda d: "/tmp/proj",
        persist_user_turn=lambda sid: persisted.append(sid),
        make_saver=lambda sid: (lambda b: saved.append(b)),
        rewrite_actions=lambda *a, **k: None,
    )
    assert body["response"] == "output-body" and body["session_id"] == 777
    assert persisted == [777]
    assert saved and saved[0]["response"] == "output-body"
    assert busy is False


def test_shell_action_lane_shell_busy_signals_route(monkeypatch):
    """A busy chat returns the busy signal instead of running the shell."""
    from api import chat_delivery
    import api.project_commands as pc

    sid = "b1-shell-busy"
    monkeypatch.setattr(
        pc,
        "try_expand_message_project_command",
        lambda msg, path: {
            "action": "shell", "command": {"name": "deploy"}, "args": "",
        },
    )
    assert chat_delivery.try_begin(sid) is True
    try:
        body, _mc, busy = _shell_lane(
            "/deploy",
            session_id=sid,
            auth_user=dict(USER),
            data={},
            resolve_project_path=lambda d: "/tmp/proj",
            persist_user_turn=lambda s: None,
            make_saver=lambda s: None,
            rewrite_actions=lambda *a, **k: None,
        )
        assert body is None and busy is True
    finally:
        chat_delivery.end(sid)


def test_shell_action_lane_wired_into_chat_route(workflow_env, monkeypatch):
    """POST /api/chat routes a shell project command through the owner lane."""
    wca, db = workflow_env
    import api.project_commands as pc

    monkeypatch.setattr(
        pc,
        "try_expand_message_project_command",
        lambda msg, path: {
            "action": "shell", "command": {"name": "deploy"}, "args": "",
        },
    )
    monkeypatch.setattr(
        pc, "run_project_command_shell",
        lambda cmd, args, session_id=None: {"success": True, "output": "ok"},
    )
    monkeypatch.setattr(
        pc, "format_project_command_shell_reply",
        lambda cmd, result: "cmd-output",
    )
    res = _post(wca, "/deploy")
    assert res.status_code == 200
    body = res.get_json()
    assert body["response"] == "cmd-output"
    assert body["type"] == "project_command"


# --------------------------------------------------------------------------
# /help text lane (Phase 6B slice 4)
# --------------------------------------------------------------------------


def test_help_response_body_is_verbatim():
    from api.chat_turn_workflow import help_response

    body = help_response(42)
    assert body["success"] is True
    assert body["type"] == "help"
    assert body["session_id"] == 42
    assert body["response"].startswith("**🦑 Cuttle Web Chat Commands**")
    assert "/cursor" in body["response"]
    assert "/retry frontier" in body["response"]


def test_help_lane_wired_into_chat_route(workflow_env):
    wca, _db = workflow_env
    res = _post(wca, "/help")
    assert res.status_code == 200
    body = res.get_json()
    assert body["type"] == "help"
    assert body["success"] is True


# --------------------------------------------------------------------------
# Router-config + route/retry lanes (Phase 6B slices 5-6)
# --------------------------------------------------------------------------


def _classify(message, *, data=None, session_id=777, project="/tmp/proj"):
    from api.chat_turn_workflow import classify_router_family

    return classify_router_family(
        message,
        data=data or {},
        session_id=session_id,
        resolve_project_path=lambda _d: project,
    )


def test_classify_router_family_flags():
    assert _classify("/router status")["router_cfg"] is True
    assert _classify("/route cursor do it")["route_or_retry"] is True
    assert _classify("/retry frontier")["route_or_retry"] is True
    plain = _classify("/cursor hi")
    assert plain["router_cfg"] is False and plain["route_or_retry"] is False
    assert plain["project_path"] == "/tmp/proj"
    assert plain["fallthrough_body"] is None


def test_classify_router_family_parse_failure_falls_back(monkeypatch):
    def boom(_m):
        raise RuntimeError("parse down")

    monkeypatch.setattr(
        "api.agent_router.commands.parse_router_command", boom
    )
    out = _classify("/router status")
    assert out["router_cfg"] is False and out["route_or_retry"] is False
    assert out["project_path"] is None
    assert out["fallthrough_body"] is None


def test_router_config_lane_handles_and_persists(monkeypatch):
    from api.chat_turn_workflow import run_router_config_lane
    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration,
        "handle_router_family_command",
        lambda msg, session_id=None, project_path=None: {
            "success": True, "response": "router-cfg", "type": "router_status",
        },
    )
    db = FakeDB()
    body = run_router_config_lane(
        "/router status",
        session_id=777,
        project_path="/tmp/proj",
        auth_user=dict(USER),
        persist_user_turn=lambda sid: db.add_message(sid, "user", "/router status"),
        get_auth_db=lambda: db,
    )
    assert body["response"] == "router-cfg"
    assert body["session_id"] == 777
    assert [r["role"] for r in db.rows] == ["user", "assistant"]
    assert db.rows[1]["metadata"] == {"origin": "agent_router"}


def test_router_config_lane_error_body(monkeypatch):
    from api.chat_turn_workflow import run_router_config_lane
    import api.agent_router.integration as integration

    def boom(*_a, **_k):
        raise RuntimeError("router down")

    monkeypatch.setattr(integration, "handle_router_family_command", boom)
    body = run_router_config_lane(
        "/router status",
        session_id=777,
        project_path=None,
        auth_user=None,
        persist_user_turn=lambda sid: None,
        get_auth_db=lambda: None,
    )
    assert body["type"] == "router_error"
    assert "router down" in body["response"]
    assert body["session_id"] == 777


def test_route_retry_sync_turn_runs_router_and_persists(monkeypatch):
    from api.chat_turn_workflow import run_route_retry_sync_turn
    from api import chat_delivery
    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration,
        "handle_router_family_command",
        lambda msg, session_id=None, project_path=None, status_queue=None: {
            "success": True, "response": "routed", "type": "router_execution",
        },
    )
    sid = "6b-retry-sync"
    persisted = []
    body, status = run_route_retry_sync_turn(
        "/retry frontier",
        session_id=sid,
        data={},
        project_path="/tmp/proj",
        wants_stream=False,
        auth_user=dict(USER),
        persist_user_turn=lambda s: persisted.append(s),
        make_saver=lambda _sid: None,
        delivery=chat_delivery,
    )
    assert status == 200 and body["response"] == "routed"
    assert body["session_id"] == sid
    assert persisted == [sid]
    assert chat_delivery.try_begin(sid) is True  # released
    chat_delivery.end(sid)


def test_route_retry_stream_turn_builds_prepared_and_io():
    from api.chat_turn_workflow import build_route_retry_stream_turn

    prepared, io = build_route_retry_stream_turn(
        "/route cursor do it",
        session_id=777,
        data={},
        project_path="/tmp/proj",
        wants_stream=True,
        auth_user=dict(USER),
        persist_user_turn=lambda sid: None,
        make_saver=lambda sid, project_path=None: None,
        notify_mobile=lambda sid, body: None,
    )
    assert prepared.message == "/route cursor do it"
    assert prepared.session_id == 777
    assert prepared.project_path == "/tmp/proj"
    assert callable(io.run_router)
    assert io.run_harness is io.format_shortcut


def test_route_retry_lane_wired_into_chat_route_sync(workflow_env, monkeypatch):
    wca, db = workflow_env
    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration,
        "handle_router_family_command",
        lambda msg, session_id=None, project_path=None, status_queue=None: {
            "success": True, "response": "wired-routed", "type": "router_execution",
        },
    )
    res = _post(wca, "/retry frontier")
    assert res.status_code == 200
    body = res.get_json()
    assert body["response"] == "wired-routed"
    assert body["session_id"] == 777


# --------------------------------------------------------------------------
# Harness lane (Phase 6B slice 7)
# --------------------------------------------------------------------------


def test_harness_empty_prompt_body_is_verbatim():
    from api.chat_turn_workflow import harness_empty_prompt_body

    body = harness_empty_prompt_body("cursor", 42)
    assert body["success"] is True
    assert body["type"] == "cursor_error"
    assert body["session_id"] == 42
    assert 'prompt after /cursor' in body["response"]


def test_build_harness_stream_turn_builds_prepared_and_io():
    from api.chat_turn_workflow import build_harness_stream_turn

    ran = []

    def run_harness(agent_id, prompt, status_queue=None):
        ran.append((agent_id, prompt))
        return {"success": True, "response": "ok", "type": "cursor"}

    prepared, io = build_harness_stream_turn(
        "/cursor do it",
        session_id=777,
        data={},
        project_path="/tmp/proj",
        wants_stream=True,
        agent_id="cursor",
        prompt="do it",
        auth_user=dict(USER),
        persist_user_turn=lambda sid: None,
        make_saver=lambda sid, project_path=None: None,
        notify_mobile=lambda sid, body: None,
        run_harness=run_harness,
    )
    assert prepared.message == "/cursor do it"
    assert prepared.session_id == 777
    assert prepared.project_path == "/tmp/proj"
    assert io.run_harness is run_harness
    shortcut = io.format_shortcut(
        "harness_empty_prompt", type("S", (), {"agent_id": "cursor"})()
    )
    assert shortcut["type"] == "cursor_error"


def test_run_harness_sync_turn_runs_persists_and_releases():
    from api import chat_delivery
    from api.chat_turn_workflow import run_harness_sync_turn

    sid = "6b-harness-sync"
    persisted = []
    saved = []
    body, status = run_harness_sync_turn(
        "/cursor do it",
        session_id=sid,
        data={},
        project_path="/tmp/proj",
        wants_stream=False,
        agent_id="cursor",
        prompt="do it",
        auth_user=dict(USER),
        persist_user_turn=lambda s: persisted.append(s),
        make_saver=lambda s, project_path=None: (lambda b: saved.append(b)),
        notify_mobile=lambda s, b: None,
        delivery=chat_delivery,
        run_harness=lambda aid, prompt, status_queue=None: {
            "success": True, "response": "ran", "type": "cursor",
        },
    )
    assert status == 200 and body["response"] == "ran"
    assert body["session_id"] == sid
    assert persisted == [sid]
    assert saved and saved[0]["response"] == "ran"
    assert chat_delivery.try_begin(sid) is True  # released
    chat_delivery.end(sid)


def test_run_harness_sync_turn_busy_returns_409():
    from api import chat_delivery
    from api.chat_turn_workflow import run_harness_sync_turn

    sid = "6b-harness-busy"
    assert chat_delivery.try_begin(sid) is True
    try:
        body, status = run_harness_sync_turn(
            "/cursor do it",
            session_id=sid,
            data={},
            project_path="/tmp/proj",
            wants_stream=False,
            agent_id="cursor",
            prompt="do it",
            auth_user=dict(USER),
            persist_user_turn=lambda s: None,
            make_saver=lambda s, project_path=None: None,
            notify_mobile=lambda s, b: None,
            delivery=chat_delivery,
            run_harness=lambda *a, **k: {"success": True},
        )
        assert status == 409 and body["error"] == "busy"
    finally:
        chat_delivery.end(sid)


def test_harness_lane_wired_into_chat_route_sync(workflow_env, monkeypatch):
    wca, db = workflow_env

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        return {"success": True, "response": "harness-wired", "type": "cursor"}

    res = _post(wca, "/cursor do it", runner=fake_run, monkeypatch=monkeypatch)
    assert res.status_code == 200
    body = res.get_json()
    assert body["response"] == "harness-wired"
    assert body["session_id"] == 777
    assert [r["role"] for r in db.rows] == ["user", "assistant"]


def test_harness_lane_wired_into_chat_route_empty_prompt(workflow_env):
    wca, _db = workflow_env
    res = _post(wca, "/cursor")
    assert res.status_code == 200
    body = res.get_json()
    assert body["type"] == "cursor_error"
    assert "prompt after /cursor" in body["response"]


# --------------------------------------------------------------------------
# Pipeline lane (Phase 6B slice 8)
# --------------------------------------------------------------------------


def test_build_pipeline_stream_turn_builds_prepared_and_io():
    from api.chat_turn_workflow import build_pipeline_stream_turn

    def run_pipeline(status_queue=None):
        return {"success": True, "response": "p", "type": "pipeline_execution"}

    prepared, io = build_pipeline_stream_turn(
        "plain probe",
        session_id=777,
        data={},
        project_path="/tmp/proj",
        wants_stream=True,
        persist_user=lambda: None,
        make_saver=lambda: None,
        notify_mobile=lambda b: None,
        run_pipeline=run_pipeline,
    )
    assert prepared.message == "plain probe"
    assert prepared.session_id == 777
    assert prepared.project_path == ""
    assert io.run_pipeline is run_pipeline
    assert io.run_harness is io.format_shortcut


def test_run_pipeline_sync_lane_runs_saves_and_releases():
    from api import chat_delivery
    from api.chat_turn_workflow import run_pipeline_sync_lane

    sid = "6b-pipeline-sync"
    persisted = []
    saved = []
    body, status = run_pipeline_sync_lane(
        sid,
        delivery=chat_delivery,
        persist_user=lambda: persisted.append(sid),
        run=lambda: {"success": True, "response": "pipe", "type": "pipeline_execution"},
        make_saver=lambda: (lambda b: saved.append(b)),
        usage_meta_fn=lambda _r: None,
    )
    assert status == 200 and body["response"] == "pipe"
    assert body["type"] == "pipeline_execution"
    assert persisted == [sid]
    assert saved and saved[0]["response"] == "pipe"
    assert chat_delivery.try_begin(sid) is True  # released
    chat_delivery.end(sid)


def test_pipeline_lane_wired_into_chat_route_sync(workflow_env, monkeypatch):
    wca, db = workflow_env
    monkeypatch.setattr(
        wca,
        "process_message_with_bot",
        lambda *a, **k: {"success": True, "response": "pipe-wired", "type": "pipeline_execution"},
    )
    res = _post(wca, "plain pipeline probe")
    assert res.status_code == 200
    body = res.get_json()
    assert body["response"] == "pipe-wired"
    assert body["session_id"] == 777
