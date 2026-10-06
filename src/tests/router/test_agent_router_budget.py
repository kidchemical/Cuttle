"""Budget-aware routing + per-target effort."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from api.agent_router import budget
from api.agent_router.config import load_router_config, reset_router_config, update_router_config
from api.agent_router.dispatch import execute_decision
from api.agent_router.types import ExecutionTarget, RouterMode, RoutingDecision, TargetSource

NOW = 1791185000.0

# Shapes recorded from the live /usage fetchers (2026-10-05).
CODEX_AT_LIMIT = {
    "success": True,
    "rate_limit": {
        "primary_window": {"used_percent": 100, "reset_at": 1791186931},
        "secondary_window": {"used_percent": 63, "reset_at": 1791683169},
        "limit_reached": True,
        "allowed": False,
    },
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
}
CLAUDE_HALF = {
    "success": True,
    "windows": [
        {"label": "5-hour", "used_percent": 53.0, "reset_at": 1791201600},
        {"label": "Weekly", "used_percent": 27.0, "reset_at": 1791194400},
    ],
    "extra_usage": {"is_enabled": False},
}
CURSOR_INCLUDED_SPENT = {
    "success": True,
    "period": {"billingCycleEnd": "1792606290000"},
    "plan_usage": {"apiPercentUsed": 100, "autoPercentUsed": 100, "remainingBonus": False},
}


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


def _enable_budget(sm, **extra):
    raw = sm.get_setting("agent_router") or {}
    raw["budget"] = {"enabled": True, **extra}
    sm.set_setting("agent_router", raw)


def _live_snapshot():
    budget.set_accounts([
        budget.parse_codex(CODEX_AT_LIMIT, NOW),
        budget.parse_claude(CLAUDE_HALF, NOW),
        budget.parse_cursor(CURSOR_INCLUDED_SPENT, NOW),
    ])


def test_parsers_read_live_shapes():
    codex = budget.parse_codex(CODEX_AT_LIMIT, NOW)
    assert codex.blocked and codex.headroom == 0.0 and codex.reset_at == 1791186931
    assert "5-hour 100%" in codex.label
    claude = budget.parse_claude(CLAUDE_HALF, NOW)
    assert not claude.blocked and abs(claude.headroom - 0.47) < 1e-6
    cursor = budget.parse_cursor(CURSOR_INCLUDED_SPENT, NOW)
    assert cursor.premium_blocked and not cursor.blocked


def test_target_state_cursor_auto_unmetered():
    _live_snapshot()
    accs = budget.snapshot()
    assert budget.target_state(ExecutionTarget("cursor", "auto"), accs, low_headroom=0.15)[0] == "ok"
    assert budget.target_state(ExecutionTarget("cursor", "grok-4.6"), accs, low_headroom=0.15)[0] == "blocked"
    assert budget.target_state(ExecutionTarget("codex", "gpt-6.1-sol"), accs, low_headroom=0.15)[0] == "blocked"
    assert budget.target_state(ExecutionTarget("muse", ""), accs, low_headroom=0.15)[0] == "unknown"


def test_budget_off_keeps_declared_order(router_settings):
    """Without the tickbox, live limits are not consulted (only seen failures)."""
    update_router_config(mode=RouterMode.API.value)
    from api.agent_router import engine

    _live_snapshot()
    ctx = engine.build_context("re-imagine the router classification in general, across everything")
    decision, meta = engine.decide_with_outcome(ctx)
    assert decision.target.agent == "cursor"  # seed architecture chain starts on Grok
    assert "availability_skipped" not in meta


def test_budget_aware_routes_to_what_will_run(router_settings):
    update_router_config(mode=RouterMode.API.value)
    _enable_budget(router_settings)
    from api.agent_router import engine

    _live_snapshot()
    ctx = engine.build_context("re-imagine the router classification in general, across everything")
    decision, meta = engine.decide_with_outcome(ctx)
    # Seed "Architecture & design" chain is [grok, codex, claude]: Grok (Cursor
    # premium) and Codex (5-hour at 100%) are at their limit → Claude runs first.
    assert decision.target.agent == "claude"
    assert [n.split(" ")[0] for n in meta["availability_skipped"]] == ["cursor/grok-4.6", "codex/default"]
    assert "5-hour 100%" in decision.reason


def test_budget_low_headroom_goes_behind_healthy(router_settings):
    _enable_budget(router_settings, low_headroom=0.6)
    from api.agent_router.engine import _order_by_availability

    _live_snapshot()
    d = RoutingDecision(
        decision_id="lh", task_type="writing", difficulty="medium",
        target=ExecutionTarget("claude", ""), confidence=0.9, reason="r",
        escalation_target=ExecutionTarget("muse", ""), source=TargetSource.ROUTER.value,
    )
    d, notes = _order_by_availability(d)
    assert d.target.agent == "muse"  # unknown beats Claude at 47% < 60% headroom
    assert d.escalation_target.agent == "claude"


def test_dispatch_skips_budget_blocked_fallbacks(router_settings):
    _enable_budget(router_settings)
    _live_snapshot()
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="bd", task_type="coding", difficulty="medium",
        target=ExecutionTarget("cursor", "auto"), confidence=0.8, reason="r",
        escalation_target=ExecutionTarget("codex", "gpt-6.1-sol"), source=TargetSource.ROUTER.value,
        fallbacks=[ExecutionTarget("claude", "sonnet")],
    )
    calls: List[str] = []

    def runner(name):
        def _r(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
            calls.append(name)
            if name == "cursor":
                return {"success": True, "response": "[FAIL] connection refused", "type": "cursor_error"}
            return {"success": True, "response": f"{name} ok", "type": f"{name}_command"}
        return _r

    out = execute_decision(
        decision, "x", chat_session_id=9, config=cfg,
        runners={"cursor": runner("cursor"), "codex": runner("codex"), "claude": runner("claude")},
    )
    assert calls == ["cursor", "claude"]
    assert "claude ok" in out["response"]


def test_budget_settings_partial_save_keeps_enabled(router_settings):
    budget.save_budget_settings({"enabled": True})
    saved, err = budget.save_budget_settings({"low_headroom": 0.3})
    assert err is None and saved["enabled"] is True and saved["low_headroom"] == 0.3


# ── effort ──────────────────────────────────────────────────────────────────

def test_effort_validated_per_agent():
    from api.agent_router.registry import validate_execution_target

    t, err = validate_execution_target("codex", "gpt-6.1-sol", effort="high")
    assert err is None and t.effort == "high"
    t, err = validate_execution_target("claude", "opus", effort="banana")
    assert t is None and "not valid" in err
    t, err = validate_execution_target("cursor", "grok-4.6", effort="high")
    assert t is None and "no separate effort" in err
    t, err = validate_execution_target("codex", "", effort="default")
    assert err is None and t.effort == ""


def test_use_case_target_keeps_effort(router_settings):
    from api.agent_router.use_cases import load_use_cases, save_use_cases

    saved, err = save_use_cases([{
        "name": "Deep", "criteria": {"task_types": ["architecture"]},
        "routing": {"targets": [{"agent": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"}]},
    }])
    assert err is None
    assert load_use_cases(seed=False)[0]["routing"]["targets"][0] == {
        "agent": "codex", "model": "gpt-6.1-sol", "effort": "xhigh",
    }


def test_config_targets_keep_effort(router_settings):
    cfg, err = update_router_config(
        escalation_target={"agent": "codex", "model": "gpt-6.1-sol", "effort": "high"},
        fallbacks=[{"agent": "claude", "model": "sonnet", "effort": "low"}],
    )
    assert err is None
    cfg = load_router_config()
    assert cfg.escalation_target.effort == "high"
    assert cfg.fallbacks.ordered[0].effort == "low"


def test_dispatch_passes_effort_to_runner(router_settings):
    cfg = load_router_config()
    seen: Dict[str, Any] = {}

    def codex_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, effort=None, **kw):
        seen.update(model=model, effort=effort)
        return {"success": True, "response": "ok", "type": "codex_command"}

    decision = RoutingDecision(
        decision_id="ef", task_type="architecture", difficulty="high",
        target=ExecutionTarget("codex", "gpt-6.1-sol", "xhigh"), confidence=0.9, reason="r",
        escalation_target=ExecutionTarget("codex", ""), source=TargetSource.ROUTER.value,
    )
    execute_decision(decision, "x", chat_session_id=10, config=cfg, runners={"codex": codex_runner})
    assert seen == {"model": "gpt-6.1-sol", "effort": "xhigh"}


def test_default_runner_maps_effort_to_execute_kwargs(monkeypatch):
    from api.agent_router import dispatch

    captured: Dict[str, Any] = {}

    def fake_run(agent_id, prompt, sid, **kw):
        captured.update(agent=agent_id, **kw)
        return {"success": True, "response": "ok"}

    monkeypatch.setattr("api.agent_harness.runners.run_harness_web_command", fake_run)
    runners = dispatch._default_runners()
    runners["claude"]("hi", 1, model="opus", effort="high")
    assert captured["execute_kwargs"] == {"reasoning_effort": "high"}
    assert captured["model_override"] == "opus"
