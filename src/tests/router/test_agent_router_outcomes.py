"""Durable outcome records and the clean-session CuttleRouter smoke contract."""

from __future__ import annotations

from pathlib import Path

from api.agent_router.dispatch import execute_decision
from api.agent_router.outcomes import list_outcomes, metrics_summary, set_feedback
from api.agent_router.types import (
    ExecutionTarget,
    RouterConfig,
    RouterProviderConfig,
    RoutingDecision,
    TargetSource,
)


def _decision() -> RoutingDecision:
    return RoutingDecision(
        decision_id=RoutingDecision.new_id(),
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.8,
        reason="smoke route",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        source=TargetSource.ROUTER.value,
    )


def test_dispatch_persists_every_attempt(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "router.db"
    monkeypatch.setenv("CUTTLE_ROUTER_DB", str(db_path))
    decision = _decision()
    calls = []

    def cursor_runner(prompt, session_id, model=None, **_kwargs):
        calls.append(model)
        if model == "auto":
            return {"success": True, "response": "[FAIL] connection refused", "type": "cursor_error"}
        return {
            "success": True,
            "response": "fixed",
            "type": "cursor_command",
            "query_id": "query-2",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    cfg = RouterConfig(
        provider=RouterProviderConfig(mode="api"),
        default_target=decision.target,
        escalation_target=decision.escalation_target,
    )
    result = execute_decision(
        decision,
        "fix it",
        chat_session_id="smoke-session",
        project_path="C:/Projects/Cuttle",
        config=cfg,
        runners={"cursor": cursor_runner},
    )

    rows = sorted(
        list_outcomes(decision_id=decision.decision_id, db_path=db_path),
        key=lambda row: row["attempt_index"],
    )
    assert calls == ["auto", "grok-4.6"]
    assert len(rows) == 2
    assert rows[0]["failure_kind"] == "transport"
    assert rows[0]["success"] == 0
    assert rows[1]["failure_kind"] == "none"
    assert rows[1]["success"] == 1
    assert rows[1]["total_tokens"] == 15
    assert rows[1]["query_id"] == "query-2"
    assert rows[1]["latency_ms"] >= 0
    assert result["router"]["decision_id"] == decision.decision_id

    assert set_feedback(
        "bad",
        session_id="smoke-session",
        db_path=db_path,
    ) == decision.decision_id
    summary = metrics_summary(days=7, db_path=db_path)
    grok = next(row for row in summary if row["target_model"] == "grok-4.6")
    assert grok["bad_feedback"] == 1

    from api.agent_router.commands import handle_router_command

    reply = handle_router_command("metrics summary 7")
    assert reply["type"] == "router_metrics"
    assert reply["metrics"]
    assert "tok/turn" in reply["response"]
    assert "15 total" in reply["response"]
    feedback = handle_router_command(
        f"feedback good {decision.decision_id}",
        session_id="smoke-session",
    )
    assert feedback["type"] == "router_feedback"


def test_clean_unpinned_message_hits_cuttle_router(tmp_path: Path, monkeypatch):
    """No star/pin routes; a starred command stays an explicit bypass."""
    from api import starred_slash
    from api.agent_router import integration
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    monkeypatch.setattr(starred_slash, "infer_session_sticky_prefix", lambda _sid: None)

    decision = _decision()
    routed = []
    monkeypatch.setattr(integration, "decide", lambda _ctx, _cfg: decision)
    monkeypatch.setattr(
        integration,
        "execute_decision",
        lambda selected, prompt, **_kwargs: routed.append((selected, prompt))
        or {"success": True, "response": "router smoke", "type": "router_execution"},
    )

    starred_slash.set_starred_prefixes([])
    clean = starred_slash.apply_default_sticky_prefix("smoke request", no_agent=False)
    result = integration.maybe_route_plain_message(clean, session_id=None)
    assert result and result["response"] == "router smoke"
    assert routed and routed[0][1] == "smoke request"

    starred_slash.set_starred_prefixes(["/cursor"])
    pinned = starred_slash.apply_default_sticky_prefix("smoke request", no_agent=False)
    assert pinned.startswith("/cursor ")
    assert integration.maybe_route_plain_message(pinned, session_id=None) is None

    unpinned = starred_slash.apply_default_sticky_prefix("smoke request", no_agent=True)
    assert unpinned == "smoke request"
    assert integration.maybe_route_plain_message(unpinned, session_id=None) is not None


def test_web_chat_plain_message_returns_router_result_before_pipeline(monkeypatch):
    """Regression smoke: clean Auto-mode chat must not fall into the legacy local pipeline."""
    from api import web_chat_api as web
    from api.agent_router import integration

    class Session:
        @staticmethod
        def get_recent_messages(_limit):
            return []

    monkeypatch.setattr(web, "PIPELINE_AVAILABLE", True)
    monkeypatch.setattr(web, "get_or_create_session", lambda _sid: Session())
    monkeypatch.setattr(web, "_resolve_request_project_path", lambda _data: "C:/Projects/Cuttle")
    monkeypatch.setattr(web, "_emit_chat_complete_mobile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        integration,
        "maybe_route_plain_message",
        lambda *_args, **_kwargs: {
            "success": True,
            "response": "CuttleRouter handled it",
            "type": "router_execution",
        },
    )

    result = web.process_message_with_bot(
        "plain smoke request",
        "router-smoke",
        inference_mode="auto",
    )
    assert result["type"] == "router_execution"
    assert result["response"] == "CuttleRouter handled it"
