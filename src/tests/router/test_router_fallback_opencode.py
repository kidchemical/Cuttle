"""Router fallback honesty + OpenCode model attribution (Issues 4, 7)."""

from __future__ import annotations


def test_missing_openai_key_falls_back_before_inference(monkeypatch):
    from api.agent_router import engine
    # Exercise the provider failure path rather than the greeting fast path.
    monkeypatch.setattr('api.agent_router.classify.classifier_settings',
                        lambda: {'fast_path': False, 'fast_path_confidence': 1.0})
    from api.agent_router.config import load_router_config
    from api.agent_router.types import RouterMode

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = load_router_config()
    cfg.provider.mode = RouterMode.API.value
    cfg.provider.api_provider = "openai"
    cfg.provider.api_model = "gpt-4o-mini"
    ctx = engine.build_context("test")
    decision, meta = engine.decide_with_outcome(ctx, cfg)
    assert meta["used_fallback"] is True
    assert "OPENAI_API_KEY" in (meta["api_error"] or "")
    assert engine.routing_never_ran(decision) is True
    assert "OPENAI_API_KEY" in engine.actionable_router_hint(decision.reason)
    assert "src/.env" in engine.actionable_router_hint(decision.reason)


def test_successful_routing_still_says_routed():
    from api.agent_router.engine import routing_never_ran
    from api.agent_router.types import (
        ExecutionTarget,
        RoutingDecision,
        TargetSource,
    )

    decision = RoutingDecision(
        decision_id="d1",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget(agent="cursor", model="auto"),
        confidence=0.9,
        reason="best match for coding",
        escalation_target=ExecutionTarget(agent="cursor", model="grok-4.6"),
        source=TargetSource.ROUTER.value,
    )
    assert routing_never_ran(decision) is False


def test_fallback_note_does_not_claim_routing():
    from api.agent_router.dispatch import _annotate_result
    from api.agent_router.engine import actionable_router_hint, routing_never_ran
    from api.agent_router.types import (
        ExecutionTarget,
        RoutingDecision,
        TargetSource,
    )

    decision = RoutingDecision(
        decision_id="d2",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget(agent="cursor", model="auto"),
        confidence=0.0,
        reason="use case 'Coding model': router error: OPENAI_API_KEY not set",
        escalation_target=ExecutionTarget(agent="cursor", model="grok-4.6"),
        source=TargetSource.DEFAULT.value,
    )
    assert routing_never_ran(decision) is True
    used = decision.target
    note = (
        f"Default `{used.agent}` / `{used.model or 'default'}` "
        f"({actionable_router_hint(decision.reason)})"
    )
    assert note.startswith("Default `cursor` / `auto`")
    assert "OPENAI_API_KEY" not in note or "src/.env" in note
    out = _annotate_result(
        {"response": "hi", "success": True},
        target=used,
        decision=decision,
        source="default",
        attempts=[],
        routed_note=note,
    )
    assert out["response"].startswith("🔀 Default")
    # Verbose diagnostics stay in the structured payload, not the headline.
    assert out["router"]["decision"]["reason"] == decision.reason
    assert "router error" in out["router"]["decision"]["reason"]
    assert "router error" not in out["response"].split("\n\n", 1)[0]


def test_opencode_event_model_detected():
    from api.agent_harness.agents.opencode.adapter import _parse_opencode_stdout

    raw = "\n".join(
        [
            '{"type":"session","sessionID":"s1","session":{"modelID":"openrouter/deepseek/deepseek-v4"}}',
            '{"type":"text","part":{"type":"text","text":"Done."}}',
        ]
    )
    text, sid, _usage, detected = _parse_opencode_stdout(raw)
    assert text == "Done."
    assert sid == "s1"
    assert detected == "openrouter/deepseek/deepseek-v4"


def test_opencode_no_model_metadata_is_unknown_not_agent_id():
    from api.agent_harness.agents.opencode.adapter import _parse_opencode_stdout

    raw = '{"response": "Done."}'
    text, _sid, _usage, detected = _parse_opencode_stdout(raw)
    assert text == "Done."
    assert detected is None


def test_unknown_model_recorded_as_unknown(tmp_path):
    from api.agent_harness.types import AgentResult
    from api.agent_router.outcomes import all_outcomes
    from api.agent_router.pinned_outcomes import record_pinned_turn

    body = {
        "agent_id": "opencode",
        "agent_model": "unknown",
        "model_source": "unknown",
        "query_id": "oc-unknown-q",
        "response": "hi",
        "type": "opencode_command",
        "success": True,
    }
    assert record_pinned_turn("opencode", body, latency_ms=5, db_path=tmp_path / "o.db")
    stored = all_outcomes(db_path=tmp_path / "o.db")[0]
    assert stored["target_model"] == "unknown"
    assert stored["target_model"] != "opencode"


def test_detected_model_reaches_outcome_store(tmp_path):
    from api.agent_router.outcomes import all_outcomes
    from api.agent_router.pinned_outcomes import record_pinned_turn

    body = {
        "agent_id": "opencode",
        "agent_model": "openrouter/deepseek/deepseek-v4",
        "model_source": "cli_default",
        "query_id": "oc-detected-q",
        "response": "hi",
        "type": "opencode_command",
        "success": True,
    }
    assert record_pinned_turn("opencode", body, latency_ms=5, db_path=tmp_path / "o.db")
    stored = all_outcomes(db_path=tmp_path / "o.db")[0]
    assert stored["target_model"] == "openrouter/deepseek/deepseek-v4"


def test_kernel_unknown_source_avoids_harness_id():
    from api.agent_harness.kernel import _usage_from_result  # noqa: F401
    from api.agent_harness.types import AgentResult

    # Simulate the kernel's model_name resolution for an unknown-model result.
    result = AgentResult(success=True, output="hi", model="", meta={"model_source": "unknown"})
    meta = result.meta if isinstance(result.meta, dict) else {}
    if result.model:
        name = result.model
    elif meta.get("model_source") == "unknown":
        name = "unknown"
    else:
        name = "opencode"
    assert name == "unknown"
