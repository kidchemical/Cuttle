"""Phase 5 P5-D: shared application coordinator acceptance.

The SAME normalized agent turn submitted via real HTTP sync, real HTTP
SSE, and a direct non-HTTP coordinator call must produce the equivalent
execution request and application lifecycle (persist/progress/result/
delivery/post-turn), modulo declared transport framing. Written BEFORE
`api.chat_coordinator` exists (collection-error pre-failure).
"""

from __future__ import annotations

import pytest


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


USER = {"id": 7, "username": "owner", "email": "owner@example.com"}


class FakeDB:
    def __init__(self):
        self.rows = []

    def verify_auth_session(self, token):
        return dict(USER) if token == "tok-seam" else None

    def add_message(self, chat_session_id, role, content, metadata=None):
        self.rows.append({
            "session": chat_session_id, "role": role,
            "content": content, "metadata": metadata,
        })
        return len(self.rows)

    def get_chat_session(self, *a, **k):
        return {"id": 777}

    def set_session_project(self, *a, **k):
        return True

    def update_session_activity(self, *a, **k):
        return None


@pytest.fixture
def http_env(monkeypatch):
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
    calls = {}

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        calls["run"] = (agent_id, prompt, chat_session_id)
        return {"success": True, "response": "coordinated!", "type": "cursor"}

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    return wca, db, calls


def _client(wca):
    client = wca.app.test_client()
    client.set_cookie("session_token", "tok-seam")
    return client


def _direct_io(db, calls, saver_text="coordinated!"):
    """Non-HTTP surface implementation over the same fakes + owned pieces."""
    from api import chat_delivery, chat_turn_persist as persist

    def persist_user():
        persist.persist_user_turn(
            db, "acc-direct",
            message_text="/cursor do the thing",
            history_text="/cursor do the thing",
            base_meta={}, identity={"agent": "cursor"},
            badge_fn=lambda m, s, identity=None: (
                {"slash_command": {"chips": [{"label": "Cursor"}]}}
                if (identity or {}).get("agent") == "cursor" else None
            ),
            project_merge_fn=lambda meta, req, sid, cr=None: dict(meta or {}),
            request_data={},
        )

    def make_saver():
        return persist.make_assistant_saver(
            chat_session_id="acc-direct",
            db=db, request_data={},
            resolve_project=lambda data: "",
            assistant_meta_fn=lambda res: {"usage": {"t": 1}},
            project_merge_fn=lambda meta, req, sid, cr=None: dict(meta or {}),
            schedule_autoname=lambda *a, **k: None,
            project_root="/repo",
        )

    def run_harness(agent_id, prompt, *, status_queue=None):
        calls["run"] = (agent_id, prompt, "acc-direct")
        return {"success": True, "response": saver_text, "type": "cursor"}

    return dict(
        persist_user=persist_user, make_saver=make_saver, run_harness=run_harness,
    )


# --------------------------------------------------------------------------
# select_agent_turn: one decision tree
# --------------------------------------------------------------------------


def _selectors(family=()):
    return dict(
        is_router_family=lambda m: m in family,
        match_harness=lambda m, pp=None: {
            "/cursor do it": ("cursor", "do it"),
            "/cursor": ("cursor", ""),
        }.get(m),
        cloud_blocked=lambda m, mode: (
            "blocked" if (mode == "local" and m.startswith("/cursor")) else None
        ),
    )


def test_select_router_family_wins():
    from api import chat_coordinator as coord

    sel = coord.select_agent_turn(
        "/route /cursor do it", inference_mode="auto", **_selectors(family=("/route /cursor do it",))
    )
    assert sel.kind == "router_family"


def test_select_harness_mode_and_empty_arms():
    from api import chat_coordinator as coord

    sel = coord.select_agent_turn("/cursor do it", inference_mode="auto", **_selectors())
    assert (sel.kind, sel.agent_id, sel.prompt) == ("harness", "cursor", "do it")
    sel = coord.select_agent_turn("/cursor do it", inference_mode="local", **_selectors())
    assert (sel.kind, sel.block_message) == ("mode_blocked", "blocked")
    sel = coord.select_agent_turn("/cursor", inference_mode="auto", **_selectors())
    assert (sel.kind, sel.agent_id) == ("harness_empty_prompt", "cursor")


def test_select_plain_and_pipeline_arms():
    from api import chat_coordinator as coord

    assert coord.select_agent_turn("hello?", inference_mode="auto", **_selectors()).kind == "plain_router"
    assert coord.select_agent_turn("", inference_mode="auto", **_selectors()).kind == "pipeline"


def test_same_turn_http_sync_and_direct_agree(http_env):
    from api import chat_coordinator as coord
    from api import chat_delivery

    wca, db, calls = http_env
    res = _client(wca).post("/api/chat", json={
        "message": "/cursor do the thing", "session_id": "acc-sync", "stream": False,
    })
    assert res.status_code == 200
    assert res.get_json()["response"] == "coordinated!"
    http_run = calls["run"]
    http_rows = [(r["session"], r["role"], r["content"]) for r in db.rows]

    db2 = FakeDB()
    calls2 = {}
    parts = _direct_io(db2, calls2)
    prepared = coord.PreparedAgentTurn(
        message="/cursor do the thing", session_id="acc-direct",
    )
    io = coord.AgentTurnIO(
        run_harness=parts["run_harness"],
        run_router=lambda *, status_queue=None: None,
        persist_user=parts["persist_user"],
        make_saver=parts["make_saver"],
        should_save=lambda body: bool(body.get("success")),
        notify_mobile=lambda body: None,
        format_shortcut=lambda kind, sel: {"success": True, "response": sel.block_message},
    )
    out = coord.submit_agent_turn(
        prepared, io=io, delivery=chat_delivery, claim=True,
    )
    assert out.status == 200
    assert out.selection.kind == "harness"
    body = out.body
    assert body["response"] == "coordinated!"
    # Equivalent execution request (modulo surface session id).
    assert calls2["run"][:2] == http_run[:2] == ("cursor", "do the thing")
    direct_rows = [(r["role"], r["content"]) for r in db2.rows]
    http_shapes = [(r[1], r[2]) for r in http_rows]
    assert direct_rows == http_shapes == [("user", "/cursor do the thing"),
                                          ("assistant", "coordinated!")]
    assert chat_delivery.is_busy("acc-direct") is False


def test_same_turn_http_sse_matches_sync_lifecycle(http_env):
    from api import chat_delivery

    wca, db, calls = http_env
    res = _client(wca).post("/api/chat", json={
        "message": "/cursor do the thing", "session_id": "acc-sse", "stream": True,
    })
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert "coordinated!" in text
    assert text.rstrip().endswith('data: {"type": "done"}')
    assert calls["run"][:2] == ("cursor", "do the thing")
    roles = [(r["session"], r["role"], r["content"]) for r in db.rows]
    assert roles == [(777, "user", "/cursor do the thing"),
                     (777, "assistant", "coordinated!")]
    assert chat_delivery.is_busy(777) is False
    assert chat_delivery.take_result(777)["response"] == "coordinated!"


def test_threaded_sse_save_merges_captured_request_project(http_env):
    wca, db, calls = http_env
    res = _client(wca).post("/api/chat", json={
        "message": "/cursor stamped please", "session_id": "acc-stamp",
        "stream": True, "project_path": "/tmp/cuttle-proj",
        "project_name": "Cuttle",
    })
    assert res.status_code == 200
    assert "coordinated!" in res.get_data(as_text=True)
    asst_rows = [r for r in db.rows if r["role"] == "assistant"]
    assert len(asst_rows) == 1
    meta = asst_rows[0]["metadata"] or {}
    # The worker-thread save merged the captured body (P5-C delta), not {}.
    assert meta.get("project_path") == "/tmp/cuttle-proj"
    assert meta.get("project_name") == "Cuttle"


def test_saver_never_mutates_captured_request_data(http_env):
    import copy

    wca, db, calls = http_env
    body = {"message": "/cursor do it", "session_id": "acc-frozen",
            "stream": False, "project_path": "/tmp/p"}
    snapshot = copy.deepcopy(body)
    res = _client(wca).post("/api/chat", json=body)
    assert res.status_code == 200
    assert body == snapshot


def test_sessions_send_compat_contract_preserved(http_env, monkeypatch):
    wca, db, calls = http_env
    monkeypatch.setattr(
        wca, "_require_session_actor", lambda sid: (dict(USER), "acc-x", None)
    )
    res = _client(wca).post("/api/sessions/send", json={
        "target_session": "acc-x", "message": "/cursor cross-session",
    })
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["success"] is True
    assert payload["response"] == "coordinated!"
    assert payload["session_id"] == "acc-x"
    assert calls["run"][:2] == ("cursor", "cross-session")
