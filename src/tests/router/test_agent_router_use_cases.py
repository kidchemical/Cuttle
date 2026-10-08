"""Phase 0 use-case routing table: schema, matching, authority override."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.agent_router.config import load_router_config, reset_router_config, update_router_config
from api.agent_router.engine import decide_with_outcome
from api.agent_router.types import ExecutionTarget, RoutingContext, RoutingDecision
from api.agent_router.use_cases import (
    apply_table,
    default_use_cases,
    load_use_cases,
    match_use_cases,
    normalize_use_case,
    save_use_cases,
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


def test_fresh_install_seeds_default_table(router_settings):
    seeded = load_use_cases()
    ids = [uc["id"] for uc in seeded]
    # one block per kind of work, simple → frontier, then a catch-all
    assert ids == [
        "general-chat", "ops", "writing", "explain", "research",
        "coding-model", "architecture", "frontier-coding", "anything-else",
    ]
    prio = [uc["priority"] for uc in seeded]
    assert prio == sorted(prio)
    assert {uc["id"]: uc["priority"] for uc in seeded}["coding-model"] == 20
    # Second load must be stable (no duplicate seeding)
    assert [uc["id"] for uc in load_use_cases()] == ids


def test_stored_priorities_load_unchanged(router_settings):
    """Stored blocks load as-is: no seed migration rewrites priorities on load."""
    sm = router_settings
    raw = sm.get_setting("agent_router") or {}
    raw["use_cases"] = [
        {"id": "general-chat", "name": "General chat / simple requests", "priority": 10,
         "criteria": {"task_types": ["basic_ask"], "difficulties": ["low"], "code_changes": False},
         "routing": {"targets": [{"agent": "cursor", "model": "auto"}]}},
        {"id": "frontier-coding", "name": "Frontier coding model", "priority": 20,
         "criteria": {"task_types": ["coding"], "difficulties": ["high"]},
         "routing": {"targets": [{"agent": "cursor", "model": "grok-4.6"}]}},
        {"id": "coding-model", "name": "Coding model", "priority": 30,
         "criteria": {"task_types": ["coding"], "difficulties": ["low", "medium"]},
         "routing": {"targets": [{"agent": "cursor", "model": "auto"}]}},
        # user-customized block must not be touched
        {"id": "research", "name": "Research", "priority": 20,
         "criteria": {"task_types": ["research"]},
         "routing": {"targets": [{"agent": "antigravity", "model": "gemini-3.7-flash-high"}]}},
    ]
    sm.set_setting("agent_router", raw)

    loaded = load_use_cases()
    prio = {uc["id"]: uc["priority"] for uc in loaded}
    assert prio["frontier-coding"] == 20
    assert prio["coding-model"] == 30
    assert prio["general-chat"] == 10
    assert prio["research"] == 20  # not a seed block — untouched
    # load is read-only: stored priorities were not rewritten, second load stable
    stored = {uc["id"]: uc["priority"] for uc in sm.get_setting("agent_router")["use_cases"]}
    assert stored == {"general-chat": 10, "frontier-coding": 20, "coding-model": 30, "research": 20}
    assert load_use_cases() == loaded


def test_match_prefers_frontier_for_hard_coding(router_settings):
    table = default_use_cases()
    m = match_use_cases(
        table, task_type="coding", difficulty="high", code_changes=None, prompt="refactor module x"
    )
    assert m and m[0]["id"] == "frontier-coding"


def test_match_general_chat_requires_no_code_changes(router_settings):
    table = default_use_cases()
    m = match_use_cases(
        table, task_type="basic_ask", difficulty="low", code_changes=False, prompt="what is 2+2"
    )
    assert m and m[0]["id"] == "general-chat"

    # Same shape but a code request no longer matches general chat
    m2 = match_use_cases(
        table, task_type="basic_ask", difficulty="low", code_changes=True, prompt="fix the bug"
    )
    assert not any(uc["id"] == "general-chat" for uc in m2)


def test_keywords_any_match(router_settings):
    uc, err = normalize_use_case(
        {
            "name": "Cuttle questions",
            "criteria": {"keywords": ["cuttle", "squid"]},
            "routing": {"targets": [{"agent": "cursor", "model": "auto"}]},
        }
    )
    assert err is None
    assert match_use_cases(
        [uc], task_type="other", difficulty="low", code_changes=None, prompt="how does CUTTLE route?"
    )
    assert not match_use_cases(
        [uc], task_type="other", difficulty="low", code_changes=None, prompt="unrelated"
    )


def test_save_rejects_invalid_target_and_keeps_disk_truth(router_settings):
    existing = load_use_cases()  # seeds the default table
    saved, err = save_use_cases(
        [{"name": "Bad", "routing": {"targets": [{"agent": "not-an-agent", "model": "x"}]}}]
    )
    assert err and "Unknown agent" in err
    # previous table untouched on disk
    assert [uc["id"] for uc in load_use_cases(seed=False)] == [
        uc["id"] for uc in existing
    ]


def test_targets_chain_maps_onto_decision(router_settings):
    """Ordered chain: 1st runs, 2nd is the escalation hop, rest are fallback layers."""
    save_use_cases(
        [
            {
                "name": "Deep work",
                "criteria": {"task_types": ["architecture"]},
                "routing": {
                    "targets": [
                        {"agent": "cursor", "model": "grok-4.6"},
                        {"agent": "claude", "model": "sonnet"},
                        {"agent": "codex", "model": ""},
                        {"agent": "antigravity", "model": "gemini-3.7-flash-high"},
                    ],
                },
            }
        ]
    )
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="t3",
        task_type="architecture",
        difficulty="high",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.6,
        reason="brain",
        escalation_target=cfg.escalation_target,
    )
    decision, meta = apply_table(
        decision, RoutingContext(user_request="design a system", code_changes_requested=True), cfg
    )
    assert meta["table_applied"] is True
    assert decision.target == ExecutionTarget("cursor", "grok-4.6")
    assert decision.escalation_target == ExecutionTarget("claude", "sonnet")
    assert decision.fallbacks == [
        ExecutionTarget("codex", ""),
        ExecutionTarget("antigravity", "gemini-3.7-flash-high"),
    ]


def test_obsolete_routing_shape_is_rejected_without_changing_saved_routes(router_settings):
    existing = load_use_cases()
    saved, err = save_use_cases([{
        "name": "Old shape",
        "routing": {"preferred": {"agent": "cursor", "model": "auto"}},
    }])
    assert err == "Use routing.targets for the ordered target chain."
    assert saved == existing
    assert load_use_cases(seed=False) == existing


def test_apply_table_overrides_brain_choice(router_settings):
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="t1",
        task_type="architecture",
        difficulty="high",
        target=ExecutionTarget("cursor", "auto"),  # brain choice
        confidence=0.7,
        reason="brain says auto",
        escalation_target=cfg.escalation_target,
    )
    ctx = RoutingContext(user_request="design a large refactor", code_changes_requested=True)
    decision, meta = apply_table(decision, ctx, cfg)
    assert meta["table_applied"] is True
    assert meta["use_case"] == "architecture"
    assert decision.target == ExecutionTarget("cursor", "grok-4.6")
    assert decision.raw.get("use_case_id") == "architecture"


def test_apply_table_never_use_filters_chain(router_settings):
    save_use_cases(
        [
            {
                "name": "NoHermes",
                "criteria": {"task_types": ["other"]},
                "routing": {
                    "preferred": {"agent": "cursor", "model": "auto"},
                    "fallbacks": [{"agent": "hermes", "model": ""}],
                    "never_use": [{"agent": "hermes"}],
                },
            }
        ]
    )
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id="t2",
        task_type="other",
        difficulty="low",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.5,
        reason="brain",
        escalation_target=cfg.escalation_target,
    )
    decision, meta = apply_table(
        decision, RoutingContext(user_request="misc", code_changes_requested=False), cfg
    )
    assert meta["table_applied"] is True
    targets = [decision.target, decision.escalation_target, *(decision.fallbacks or [])]
    assert all(t.agent != "hermes" for t in targets)


def test_engine_table_wins_over_brain(router_settings, monkeypatch):
    """End-to-end decide_with_outcome: brain proposes Auto, table applies frontier."""
    update_router_config(mode="api")

    class FakeProvider:
        name = "fake"

        def decide(self, context, cfg):
            return RoutingDecision(
                decision_id="f1",
                task_type="coding",
                difficulty="high",
                target=ExecutionTarget("cursor", "auto"),
                confidence=0.9,
                reason="brain auto",
                escalation_target=cfg.escalation_target,
            )

    from api.agent_router import classify, engine

    # This test is about the brain + table; keep the classifier fast path out.
    monkeypatch.setattr(classify, "classifier_settings", lambda: {"fast_path": False, "fast_path_confidence": 1.0})
    monkeypatch.setattr(engine, "_provider_for", lambda cfg: FakeProvider())
    ctx = engine.build_context("hard refactor of the scheduler", session_id=1)
    decision, meta = decide_with_outcome(ctx)
    assert decision.target == ExecutionTarget("cursor", "grok-4.6")
    assert meta["use_case"] == "frontier-coding"
