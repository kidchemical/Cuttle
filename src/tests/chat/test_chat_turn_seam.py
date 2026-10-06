"""Phase 5 P5-A: transport-neutral turn envelope + selection seam.

Pins the exact pre-extraction behavior of the `/api/chat` request head
and the `process_message_with_bot` selection head before they delegate
to `api.chat_turn`. Pure decisions/transforms only: no Flask, no DB, no
runners. Injected matchers keep the seam transport-neutral.
"""

from __future__ import annotations

import pytest

from api import chat_turn as ct


def _harness_factory(mapping):
    def match(message):
        return mapping.get(message)

    return match


def _restart_factory(markers=("/restart",)):
    def is_restart(message):
        text = (message or "").strip()
        return any(text == m or text.startswith(m + " ") for m in markers)

    return is_restart


def _cloud_blocked(message, inference_mode):
    if inference_mode == "local" and (message or "").startswith("/cursor"):
        return "blocked-in-local"
    return None


# --------------------------------------------------------------------------
# normalize_chat_post
# --------------------------------------------------------------------------


def test_normalize_strips_invisible_leading_and_whitespace():
    req = ct.normalize_chat_post({"message": "﻿\u200b  /cursor hi  "})
    assert req.error is None
    assert req.message == "/cursor hi"


def test_normalize_defaults():
    req = ct.normalize_chat_post({"message": "hello"})
    assert req.error is None
    assert req.attachments == []
    assert req.session_id is None
    assert req.inference_mode == "auto"
    assert req.wants_stream is True


def test_normalize_stream_flag_forms():
    assert ct.normalize_chat_post({"message": "hi", "stream": False}).wants_stream is False
    for raw in ("false", "0", "no", "off", " False "):
        req = ct.normalize_chat_post({"message": "hi", "stream": raw})
        assert req.wants_stream is False, raw
    for raw in (True, "true", "yes", "1", ""):
        req = ct.normalize_chat_post({"message": "hi", "stream": raw})
        assert req.wants_stream is True, raw


def test_normalize_inference_modes():
    assert ct.normalize_chat_post({"message": "hi"}).inference_mode == "auto"
    req = ct.normalize_chat_post({"message": "hi", "inference_mode": "Local"})
    assert req.inference_mode == "local"
    req = ct.normalize_chat_post({"message": "hi", "inference_mode": "bogus"})
    assert req.inference_mode == "auto"


def test_normalize_missing_message_is_an_error():
    assert ct.normalize_chat_post(None).error == "No message provided"
    assert ct.normalize_chat_post({}).error == "No message provided"
    assert ct.normalize_chat_post({"session_id": 1}).error == "No message provided"


def test_normalize_empty_message_without_attachments_is_an_error():
    req = ct.normalize_chat_post({"message": "   "})
    assert req.error == "Empty message"


def test_normalize_attachment_only_send_has_no_error():
    atts = [{"filename": "shot.png"}]
    req = ct.normalize_chat_post({"message": "", "attachments": atts})
    assert req.error is None
    assert req.attachments == atts


def test_normalize_non_string_message_coerced():
    req = ct.normalize_chat_post({"message": None, "attachments": [{"f": 1}]})
    assert req.error is None
    assert req.message == ""


# --------------------------------------------------------------------------
# classify_selection
# --------------------------------------------------------------------------


def test_classify_restart_wins_over_harness_match():
    sel = ct.classify_selection(
        "/restart status",
        inference_mode="auto",
        match_harness=_harness_factory({"/restart status": ("restart", "x")}),
        is_restart=_restart_factory(),
        cloud_blocked=_cloud_blocked,
    )
    assert sel.kind == "restart"


def test_classify_harness_turn():
    sel = ct.classify_selection(
        "/cursor do it",
        inference_mode="auto",
        match_harness=_harness_factory({"/cursor do it": ("cursor", "do it")}),
        is_restart=_restart_factory(),
        cloud_blocked=_cloud_blocked,
    )
    assert sel.kind == "harness"
    assert sel.agent_id == "cursor"
    assert sel.prompt == "do it"


def test_classify_harness_empty_prompt():
    sel = ct.classify_selection(
        "/cursor",
        inference_mode="auto",
        match_harness=_harness_factory({"/cursor": ("cursor", "")}),
        is_restart=_restart_factory(),
        cloud_blocked=_cloud_blocked,
    )
    assert sel.kind == "harness_empty_prompt"
    assert "prompt after /cursor" in sel.block_message


def test_classify_local_mode_blocks_cloud_cli():
    sel = ct.classify_selection(
        "/cursor do it",
        inference_mode="local",
        match_harness=_harness_factory({"/cursor do it": ("cursor", "do it")}),
        is_restart=_restart_factory(),
        cloud_blocked=_cloud_blocked,
    )
    assert sel.kind == "mode_blocked"
    assert sel.block_message == "blocked-in-local"


def test_classify_plain_message_goes_to_router():
    sel = ct.classify_selection(
        "just a question",
        inference_mode="auto",
        match_harness=_harness_factory({}),
        is_restart=_restart_factory(),
        cloud_blocked=_cloud_blocked,
    )
    assert sel.kind == "router"


# --------------------------------------------------------------------------
# split_db_session_id / build_turn_context
# --------------------------------------------------------------------------


def test_split_db_session_id():
    assert ct.split_db_session_id("db_session_12") == 12
    assert ct.split_db_session_id(7) == 7
    assert ct.split_db_session_id("plain-id") is None
    assert ct.split_db_session_id("db_session_nope") is None
    assert ct.split_db_session_id(None) is None


def test_build_turn_context_shape():
    user_ctx, session_data = ct.build_turn_context(
        session_id="abc",
        session_kind=None,
        routing_key=None,
        is_owner=True,
        inference_mode="auto",
        recent_messages=[{"role": "user"}],
    )
    assert user_ctx["display_name"] == "Web User"
    assert user_ctx["id"] == "abc"
    assert user_ctx["is_owner"] is True
    assert user_ctx["web_ui"] is True
    assert user_ctx["recent_messages"] == [{"role": "user"}]
    assert session_data["session_kind"] == "web_anon"
    assert session_data["routing_key"] == "web_anon_abc"
    assert session_data["platform"] == "webchat"
    assert session_data["user_id"] == "web_abc"


# --------------------------------------------------------------------------
# Coordinator integration: real process_message_with_bot, fake executors
# --------------------------------------------------------------------------


@pytest.fixture
def coordinator(monkeypatch):
    from api import web_chat_api as wca

    calls = {}

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        calls["run"] = (agent_id, prompt, chat_session_id, kwargs)
        return {"success": True, "response": "fake-run", "type": "fake"}

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)

    import api.agent_router.integration as integration

    def fake_route(message_content, session_id=None, project_path=None, status_queue=None):
        calls["route"] = (message_content, session_id)
        return calls.get("route_result")

    monkeypatch.setattr(integration, "maybe_route_plain_message", fake_route)
    return wca, calls


def test_coordinator_harness_arm_uses_real_matcher(coordinator):
    wca, calls = coordinator
    out = wca.process_message_with_bot("/cursor do the thing", "seam-h1")
    assert out["type"] == "fake"
    agent_id, prompt, sid, _kw = calls["run"]
    assert (agent_id, prompt, sid) == ("cursor", "do the thing", "seam-h1")


def test_coordinator_local_mode_blocks_cloud_cli(coordinator, monkeypatch):
    wca, calls = coordinator
    # Upstream llama.cpp launch gate is not under test; let the turn through.
    monkeypatch.setattr(
        wca, "_handle_local_llm_launch_gate", lambda m, s, mode: (None, m)
    )
    out = wca.process_message_with_bot(
        "/cursor do the thing", "seam-h2", inference_mode="local"
    )
    assert out["type"] == "mode_blocked"
    assert "run" not in calls


def test_coordinator_harness_empty_prompt(coordinator):
    wca, calls = coordinator
    out = wca.process_message_with_bot("/cursor", "seam-h3")
    assert out["type"] == "cursor_error"
    assert "prompt after /cursor" in out["response"]
    assert "run" not in calls


def test_coordinator_router_arm_parses_db_session(coordinator):
    wca, calls = coordinator
    calls["route_result"] = {"success": True, "response": "routed", "type": "r"}
    out = wca.process_message_with_bot("plain question", "db_session_41")
    assert out["response"] == "routed"
    assert calls["route"][1] == 41


def test_coordinator_fallback_when_router_abstains(coordinator):
    wca, calls = coordinator
    calls["route_result"] = None
    out = wca.process_message_with_bot("plain question", "seam-h5")
    assert out["response"]  # _no_pipeline_chat_result fallback shape
    assert calls["route"][0] == "plain question"


def test_coordinator_restart_handler_failure_falls_through_to_router(
    coordinator, monkeypatch
):
    wca, calls = coordinator
    calls["route_result"] = {"success": True, "response": "routed", "type": "r"}
    import api.flask_restart as restart_mod

    def boom(*_a, **_kw):
        raise RuntimeError("handler down")

    monkeypatch.setattr(restart_mod, "handle_restart_slash", boom)
    out = wca.process_message_with_bot("/restart status", "seam-h6")
    assert out["response"] == "routed"
    assert calls["route"][0] == "/restart status"


# --------------------------------------------------------------------------
# Route delegation: real /api/chat 400 contracts through the seam
# --------------------------------------------------------------------------


def test_route_rejects_missing_message_with_400():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    res = client.post("/api/chat", json={"session_id": "seam-r1"})
    assert res.status_code == 400
    assert res.get_json() == {"success": False, "error": "No message provided"}


def test_route_rejects_empty_message_with_400():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    res = client.post("/api/chat", json={"message": "   ", "session_id": "seam-r2"})
    assert res.status_code == 400
    assert res.get_json() == {"success": False, "error": "Empty message"}


def test_build_turn_context_explicit_kind_and_key():
    _, session_data = ct.build_turn_context(
        session_id=9,
        session_kind="guild",
        routing_key="rk",
        is_owner=False,
        inference_mode="cloud",
        recent_messages=[],
    )
    assert session_data["session_kind"] == "guild"
    assert session_data["routing_key"] == "rk"
    assert session_data["inference_mode"] == "cloud"
