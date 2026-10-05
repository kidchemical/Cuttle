"""Tests for the Cuttle agent router layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from api.agent_router.config import (
    load_router_config,
    reset_router_config,
    save_router_config,
    update_router_config,
)
from api.agent_router.dispatch import execute_decision
from api.agent_router.engine import (
    _BrainGuard,
    decide,
    routing_brain_active,
    should_invoke_router,
)
from api.agent_router.providers.api_openai import parse_and_validate_decision
from api.agent_router.providers.base import ProviderError
from api.agent_router.types import (
    ExecutionTarget,
    FailureKind,
    RouterConfig,
    RouterMode,
    RouterProviderConfig,
    RoutingContext,
    RoutingDecision,
    TargetSource,
)


@pytest.fixture
def router_settings(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    # Never call the real Cursor Agent CLI during tests.
    monkeypatch.setattr(
        "api.cursor_agent_commands.list_cursor_agent_models",
        lambda: [
            {"id": "auto", "label": "Auto"},
            {"id": "grok-4.6", "label": "Grok 4.6"},
        ],
    )
    reset_router_config()
    return sm


def _cfg(**kwargs) -> RouterConfig:
    cfg = load_router_config()
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def test_router_disabled_preserves_bypass(router_settings):
    update_router_config(mode="off")
    should, reason = should_invoke_router("fix the pig spawn")
    assert should is False
    assert "off" in reason


def test_explicit_session_selection_bypasses(router_settings):
    update_router_config(mode="api")
    should, reason = should_invoke_router("/cursor fix the scarecrow")
    assert should is False
    assert "cursor" in reason.lower() or "explicit" in reason.lower()


def test_starred_selection_equivalent_to_manual(router_settings, monkeypatch):
    """Starred sticky prefix on the message bypasses exactly like a manual /cursor."""
    update_router_config(mode="api")
    # Message already has sticky prefix (what starring produces on send).
    should_starred, r1 = should_invoke_router("/cursor implement fog")
    should_manual, r2 = should_invoke_router("/cursor implement fog")
    assert should_starred is False and should_manual is False
    assert r1 == r2


def test_valid_api_decision_selects_registered_target(router_settings):
    cfg = load_router_config()
    payload = {
        "task_type": "coding",
        "difficulty": "low",
        "target_agent": "cursor",
        "target_model": "auto",
        "confidence": 0.9,
        "reason": "routine fix",
        "escalation_target": {"agent": "cursor", "model": "grok-4.6"},
    }
    d = parse_and_validate_decision(payload, cfg)
    assert d.target.agent == "cursor"
    assert d.target.model == "auto"


def test_invented_agent_rejected(router_settings):
    cfg = load_router_config()
    with pytest.raises(ProviderError):
        parse_and_validate_decision(
            {
                "task_type": "coding",
                "difficulty": "low",
                "target_agent": "made-up-agent",
                "target_model": "auto",
                "confidence": 0.5,
                "reason": "nope",
                "escalation_target": {"agent": "cursor", "model": "auto"},
            },
            cfg,
        )


def test_router_api_failure_falls_back_to_cursor_auto(router_settings, monkeypatch):
    update_router_config(mode="api")

    class Boom:
        name = "openai_api"

        def decide(self, context, config):
            raise ProviderError("quota exceeded", retryable=False)

    monkeypatch.setattr(
        "api.agent_router.engine._provider_for",
        lambda cfg: Boom(),
    )
    ctx = RoutingContext(user_request="add a button")
    d = decide(ctx)
    assert d.source == TargetSource.DEFAULT.value
    assert d.target.agent == "cursor"
    assert d.target.model == "auto"


def test_auto_task_failure_escalates_to_grok(router_settings):
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="testdec1",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.8,
        reason="test",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        source=TargetSource.ROUTER.value,
    )
    calls: List[str] = []

    def cursor_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        calls.append(model or "auto")
        if (model or "auto") == "auto":
            return {
                "success": True,
                "response": "[FAIL] Cursor Agent exited with code 1\nbuild failed",
                "type": "cursor_error",
            }
        return {"success": True, "response": "fixed it", "type": "cursor_command"}

    result = execute_decision(
        decision,
        "fix the build",
        chat_session_id=1,
        runners={"cursor": cursor_runner, "codex": lambda *a, **k: {}},
        config=cfg,
    )
    assert calls == ["auto", "grok-4.6"]
    assert "fixed it" in result["response"]
    assert result["router"]["source"] == TargetSource.ESCALATION.value


def test_grok_budget_falls_back_to_codex(router_settings):
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="testdec2",
        task_type="architecture",
        difficulty="high",
        target=ExecutionTarget("cursor", "grok-4.6"),
        confidence=0.7,
        reason="hard",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        source=TargetSource.ROUTER.value,
    )

    def cursor_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        return {
            "success": True,
            "response": "❌ You've hit your usage limit / budget exhausted",
            "type": "cursor_error",
        }

    def codex_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        return {"success": True, "response": "codex ok", "type": "codex_command"}

    result = execute_decision(
        decision,
        "redesign networking",
        chat_session_id=2,
        runners={"cursor": cursor_runner, "codex": codex_runner},
        config=cfg,
    )
    assert "codex ok" in result["response"]
    assert result["router"]["target"]["agent"] == "codex"


def test_fallback_exhaustion_clear_error(router_settings):
    cfg = load_router_config()
    # Empty fallbacks after primary fails
    cfg.fallbacks.ordered = []
    decision = RoutingDecision(
        decision_id="testdec3",
        task_type="coding",
        difficulty="low",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.5,
        reason="x",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        source=TargetSource.ROUTER.value,
    )

    def fail(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        return {
            "success": True,
            "response": "[FAIL] exited with code 1",
            "type": "cursor_error",
        }

    result = execute_decision(
        decision,
        "do thing",
        chat_session_id=3,
        runners={"cursor": fail},
        config=cfg,
        allow_escalation=True,
    )
    # Escalation also fails → exhaustion
    assert "All execution targets failed" in result["response"]
    assert result["type"] == "router_error"
    assert len(result["router"]["attempts"]) >= 2


def test_user_cancellation_is_terminal(router_settings):
    """Stop must not buy an escalation or spawn a fallback agent."""
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="testcancel",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.8,
        reason="test",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        source=TargetSource.ROUTER.value,
    )
    calls: List[str] = []

    def cursor_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        calls.append(f"cursor:{model or 'auto'}")
        return {
            "success": True,
            "response": "[CANCELLED] Cursor Agent run was cancelled (chat deleted or stopped).",
            "type": "cursor_error",
        }

    def codex_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        calls.append("codex")
        return {"success": True, "response": "codex ok", "type": "codex_command"}

    result = execute_decision(
        decision,
        "inspect the implementation",
        chat_session_id=11,
        runners={"cursor": cursor_runner, "codex": codex_runner},
        config=cfg,
    )
    assert calls == ["cursor:auto"]
    assert "All execution targets failed" not in result["response"]
    assert result["router"]["attempts"][0]["failure_kind"] == FailureKind.CANCELLED.value


def test_looks_like_code_change_skips_smoke_prompts():
    from api.agent_router.policy import looks_like_code_change_request

    assert looks_like_code_change_request("test") is False
    assert looks_like_code_change_request("hi") is False
    assert looks_like_code_change_request("contest results") is False
    assert looks_like_code_change_request("fix the bug") is True
    assert looks_like_code_change_request("write a test for login") is True


def test_cancellation_classified_before_transport():
    from api.agent_router.policy import classify_failure

    kind, _reason = classify_failure(
        {
            "success": True,
            "type": "cursor_error",
            "response": "[CANCELLED] Cursor Agent run was cancelled (chat deleted or stopped).",
        }
    )
    assert kind == FailureKind.CANCELLED.value

    kind, _reason = classify_failure(
        {"success": True, "type": "codex_error", "response": "Codex CLI timed out after 1800s"}
    )
    assert kind == FailureKind.TRANSPORT.value


def test_superseded_turn_stops_the_chain(router_settings, monkeypatch):
    """A newer user turn ends the old turn's escalation/fallback spending."""
    from api import chat_delivery

    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="teststale",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.8,
        reason="test",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        source=TargetSource.ROUTER.value,
    )
    calls: List[str] = []

    assert chat_delivery.try_begin(4242) is True
    try:
        def cursor_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
            calls.append(f"cursor:{model or 'auto'}")
            # While this run was working the user stopped it and sent again.
            chat_delivery.end(4242)
            chat_delivery.try_begin(4242)
            return {
                "success": True,
                "response": "[FAIL] Cursor Agent exited with code 1",
                "type": "cursor_error",
            }

        def codex_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
            calls.append("codex")
            return {"success": True, "response": "codex ok", "type": "codex_command"}

        result = execute_decision(
            decision,
            "fix the build",
            chat_session_id=4242,
            runners={"cursor": cursor_runner, "codex": codex_runner},
            config=cfg,
        )
    finally:
        chat_delivery.end(4242)

    assert calls == ["cursor:auto"]
    assert "All execution targets failed" not in result["response"]


def test_router_provider_cannot_recursively_route(router_settings, monkeypatch):
    update_router_config(mode="api")
    # Dummy credential only: the engine credential gate runs before the
    # mocked provider, and the fake below raises before any network.
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-test-key")
    seen = {"nested_should": None, "called": False}

    class Nested:
        name = "openai_api"

        def decide(self, context, config):
            seen["called"] = True
            assert routing_brain_active() is True
            # Nested should_invoke must refuse
            should, reason = should_invoke_router("nested task", config=config)
            seen["nested_should"] = (should, reason)
            raise ProviderError("stop", retryable=False)

    monkeypatch.setattr("api.agent_router.engine._provider_for", lambda cfg: Nested())
    decide(RoutingContext(user_request="outer"))
    assert seen["called"] is True
    assert seen["nested_should"][0] is False
    assert "recursion" in seen["nested_should"][1].lower() or "brain" in seen["nested_should"][1].lower()


def test_router_status_shows_config(router_settings):
    from api.agent_router.commands import format_status, handle_router_command

    update_router_config(mode="api", api_model="gpt-4o-mini")
    body = handle_router_command("status")
    text = body["response"]
    assert "mode `api`" in text
    assert "gpt-4o-mini" in text
    assert "cursor" in text
    assert "Fallback" in text or "fallback" in text.lower()
    assert format_status()


def test_config_persists(router_settings):
    update_router_config(mode="off")
    assert load_router_config().provider.mode == "off"
    update_router_config(mode="api", api_model="gpt-4o-mini")
    cfg = load_router_config()
    assert cfg.provider.mode == "api"
    assert cfg.provider.api_model == "gpt-4o-mini"


def test_invalid_api_model_rejected(router_settings):
    cfg, err = update_router_config(api_model="not a model")
    assert err
    assert load_router_config().provider.api_model == "gpt-4o-mini"


def test_plain_message_invokes_when_enabled(router_settings):
    update_router_config(mode="api")
    should, _ = should_invoke_router("please refactor auth_db.py")
    assert should is True


def test_brain_guard_context():
    assert routing_brain_active() is False
    with _BrainGuard():
        assert routing_brain_active() is True
        with _BrainGuard():
            assert routing_brain_active() is True
    assert routing_brain_active() is False


def test_selection_chip_uses_recorded_target_and_preserves_legacy(monkeypatch):
    from api.agent_router.dispatch import _annotate_result
    from api.chat_metadata import routing_badge_from_router
    import api.experimental
    decision = RoutingDecision(
        decision_id='badge-test', target=ExecutionTarget('cursor', 'auto'),
        task_type='coding', difficulty='medium', confidence=.9,
        reason='Small code change', escalation_target=ExecutionTarget('cursor', 'grok-4.6'))
    monkeypatch.setattr(api.experimental, 'is_enabled', lambda _: True)
    result = _annotate_result({'success': True, 'response': 'Done', 'agent_model': 'actual-model', 'agent_effort': 'high'}, target=ExecutionTarget('codex', 'model'),
        decision=decision, source='fallback', attempts=[], routed_note='Fallback `codex` / `model` after: transport')
    assert result['response'] == 'Done'
    assert result['routing_badge']['kind'] == 'fallback'
    assert result['routing_badge']['initial_agent'] == 'cursor'
    assert result['routing_badge']['agent'] == 'codex'
    assert result['routing_badge']['model'] == 'actual-model'
    assert result['routing_badge']['effort'] == 'high'
    assert 'transport' in result['routing_badge']['reason']
    assert routing_badge_from_router({'target': {'agent': 'codex'}, 'source': 'router'}) is None
    manual = dict(result['router'], source='manual_override')
    assert routing_badge_from_router(manual) is None
    monkeypatch.setattr(api.experimental, 'is_enabled', lambda _: False)
    legacy = _annotate_result({'success': True, 'response': 'Done'}, target=decision.target,
        decision=decision, source='router', attempts=[], routed_note='Routed to `cursor`')
    assert legacy['response'].startswith('🔀 Routed to')
    assert 'routing_badge' not in legacy
