"""Router classification: kind of work + scope, fast path, quota cooldowns."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from api.agent_router import quota
from api.agent_router.classify import classify_turn
from api.agent_router.config import load_router_config, reset_router_config, update_router_config
from api.agent_router.dispatch import execute_decision
from api.agent_router.types import ExecutionTarget, RouterMode, RoutingDecision, TargetSource


@pytest.fixture
def router_settings(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    monkeypatch.setattr(
        "api.cursor_agent_commands.list_cursor_agent_models",
        lambda: [{"id": "auto", "label": "Auto"}, {"id": "grok-4.6", "label": "Grok 4.6"}],
    )
    reset_router_config()
    return sm


@pytest.mark.parametrize(
    "prompt,task,scope",
    [
        ("this is a test. hello", "basic_ask", "low"),
        ("what's the capital of france?", "basic_ask", "low"),
        # CH-000989: a question about slowness is not "difficulty=high".
        ("Why did this take 13s to route? It just hung on routing for 13s", "debugging", "medium"),
        ("Write the release notes for tonight's trailer drop.", "writing", None),
        ("summarize yesterday's commits for standup", "writing", None),
        ("push it", "ops", "low"),
        ("restart flask", "ops", "low"),
        ("how does the agent router pick fallbacks?", "explain", "low"),
        ("can you look at CH-000989", "explain", None),
        ("add a button to the settings page that clears the cache", "coding", "medium"),
        ("rename foo to bar in utils.py", "coding", "low"),
        ("fix the bug where the chat bubble shows routing forever", "debugging", None),
        ("compare opencode vs claude code for refactors", "research", None),
        ("re-imagine the router classification in general, across everything", "architecture", "high"),
    ],
)
def test_classifier_lanes(prompt, task, scope):
    c = classify_turn(prompt)
    assert c.task_type == task, c.to_dict()
    if scope:
        assert c.difficulty == scope, c.to_dict()


def test_ambiguous_followup_defers_to_brain():
    c = classify_turn("make it pop more")
    assert c.confidence < 0.75


def test_fast_path_skips_brain_and_explains_choice(router_settings, monkeypatch):
    update_router_config(mode=RouterMode.API.value)
    from api.agent_router import engine

    def no_brain(cfg):
        raise AssertionError("the routing brain must not run on an obvious turn")

    monkeypatch.setattr(engine, "_provider_for", no_brain)
    ctx = engine.build_context("Write the release notes for tonight's trailer drop.", session_id=1)
    decision, meta = engine.decide_with_outcome(ctx)
    assert meta["fast_path"] is True
    assert meta["use_case"] == "writing"
    assert decision.task_type == "writing"
    assert decision.target.agent == "claude"
    assert decision.reason.startswith("writing")
    assert "→ Writing & docs" in decision.reason


def test_brain_error_still_uses_classifier_lane(router_settings, monkeypatch):
    update_router_config(mode=RouterMode.API.value)
    from api.agent_router import classify, engine
    from api.agent_router.providers.base import ProviderError

    class Boom:
        name = "openai_api"

        def decide(self, context, config):
            raise ProviderError("down", retryable=True)

    monkeypatch.setattr(classify, "classifier_settings", lambda: {"fast_path": False, "fast_path_confidence": 1.0})
    monkeypatch.setattr(engine, "_provider_for", lambda cfg: Boom())
    ctx = engine.build_context("summarize yesterday's commits", session_id=1)
    decision, meta = engine.decide_with_outcome(ctx)
    assert meta["used_fallback"] is True
    assert decision.task_type == "writing"
    assert meta["use_case"] == "writing"


def test_local_and_agent_modes_have_providers(router_settings):
    from api.agent_router import engine

    cfg = load_router_config()
    cfg.provider.mode = RouterMode.LOCAL.value
    assert engine._provider_for(cfg) is not None
    cfg.provider.mode = RouterMode.AGENT.value
    assert engine._provider_for(cfg) is not None


_OUT_OF_USAGE = (
    "[FAIL] **Cursor Agent** (`agent`):\n```\nActionRequiredError: Increase limits for faster "
    "responses You're out of usage. Switch to Auto, or ask your admin to increase your limit.\n```"
)


def test_quota_cooldown_reroutes_next_turn(router_settings, monkeypatch):
    """After Cursor premium runs dry, the next deep turn starts on the next harness."""
    update_router_config(mode=RouterMode.API.value)
    from api.agent_router import engine

    quota.mark_exhausted(ExecutionTarget("cursor", "grok-4.6"), _OUT_OF_USAGE)
    ctx = engine.build_context(
        "re-imagine the router classification in general, across everything", session_id=1
    )
    decision, meta = engine.decide_with_outcome(ctx)
    assert decision.target.agent == "codex"
    assert any(n.startswith("cursor/grok-4.6") for n in meta["availability_skipped"])
    assert "skipped cursor/grok-4.6 (out of usage)" in decision.reason
    # Auto on the same account is still fine.
    assert not quota.is_exhausted(ExecutionTarget("cursor", "auto"))


def test_quota_reset_time_is_parsed():
    from datetime import datetime

    now = datetime(2026, 10, 5, 0, 0).timestamp()
    quota.mark_exhausted(
        ExecutionTarget("codex", "gpt-6.1-sol"),
        "You've hit your usage limit. try again at Oct 5th, 2026 12:55 AM.",
        now=now,
    )
    assert quota.is_exhausted(ExecutionTarget("codex", "gpt-6.1-sol"), now=now + 60)
    assert not quota.is_exhausted(ExecutionTarget("codex", "gpt-6.1-sol"), now=now + 56 * 60)


def test_dispatch_records_cooldown_and_success_clears_it(router_settings):
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="cool1",
        task_type="coding",
        difficulty="high",
        target=ExecutionTarget("cursor", "grok-4.6"),
        confidence=0.7,
        reason="t",
        escalation_target=ExecutionTarget("codex", ""),
        source=TargetSource.ROUTER.value,
        fallbacks=[],
    )
    calls: List[str] = []

    def cursor_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        calls.append(f"cursor:{model}")
        if model == "auto":
            return {"success": True, "response": "auto ok", "type": "cursor_command"}
        return {"success": True, "response": _OUT_OF_USAGE, "type": "cursor_error"}

    def codex_runner(*a, **k):
        calls.append("codex")
        return {"success": True, "response": "codex ok", "type": "codex_command"}

    execute_decision(
        decision, "x", chat_session_id=5, config=cfg,
        runners={"cursor": cursor_runner, "codex": codex_runner},
    )
    assert calls == ["cursor:grok-4.6", "cursor:auto"]
    assert quota.is_exhausted(ExecutionTarget("cursor", "grok-4.6"))
    quota.note_success(ExecutionTarget("cursor", "grok-4.6"))
    assert not quota.is_exhausted(ExecutionTarget("cursor", "grok-4.6"))


def test_table_chain_second_entry_runs_when_primary_cannot(router_settings):
    """A use-case chain [grok, codex, claude] must try codex when grok never ran."""
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="chain1",
        task_type="architecture",
        difficulty="high",
        target=ExecutionTarget("cursor", "grok-4.6"),
        confidence=0.7,
        reason="t",
        escalation_target=ExecutionTarget("codex", ""),
        source=TargetSource.ROUTER.value,
        fallbacks=[ExecutionTarget("claude", "")],
    )
    calls: List[str] = []

    def cursor_runner(*a, model=None, **k):
        calls.append(f"cursor:{model}")
        return {"success": True, "response": "[FAIL] connection refused", "type": "cursor_error"}

    def codex_runner(*a, **k):
        calls.append("codex")
        return {"success": True, "response": "codex ok", "type": "codex_command"}

    out = execute_decision(
        decision, "x", chat_session_id=6, config=cfg,
        runners={"cursor": cursor_runner, "codex": codex_runner, "claude": codex_runner},
    )
    assert calls == ["cursor:grok-4.6", "codex"]
    assert "codex ok" in out["response"]
