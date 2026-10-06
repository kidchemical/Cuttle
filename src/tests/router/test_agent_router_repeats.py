"""Silent-failure escalation: repeated prompts escalate the target chain.

The scenario: "fix this bug" → model completes, claims fixed → it isn't →
the user re-sends the same prompt. That re-send is the evidence; each repeat
escalates one hop and rates the prior attempt bad in the outcome store.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from api.agent_router.outcomes import list_outcomes, record_attempt
from api.agent_router.repeats import (
    build_repeat_handoff,
    evaluate_repeat,
    normalize_prompt,
    reset_session,
)
from api.agent_router.types import (
    ExecutionTarget,
    RouterConfig,
    RoutingDecision,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_session("r1")
    yield
    reset_session("r1")


@pytest.fixture
def db(tmp_path: Path, monkeypatch):
    p = tmp_path / "outcomes.db"
    import api.agent_router.repeats as repeats

    monkeypatch.setattr(repeats, "list_outcomes", _list_with_db(p))
    monkeypatch.setattr(repeats, "set_feedback", _feedback_with_db(p))
    return p


def _list_with_db(captured):
    def _list(*, decision_id=None, limit=100, **_):
        return list_outcomes(decision_id=decision_id, limit=limit, db_path=captured)

    return _list


def _feedback_with_db(captured):
    from api.agent_router.outcomes import set_feedback as real

    def _set(feedback, *, session_id=None, decision_id=None, **_):
        return real(feedback, session_id=session_id, decision_id=decision_id, db_path=captured)

    return _set


def _decision(**kw):
    base = dict(
        decision_id="d1",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.9,
        reason="brain",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        fallbacks=[ExecutionTarget("codex", "")],
    )
    base.update(kw)
    return RoutingDecision(**base)


def test_normalize_strips_slash_and_case():
    assert normalize_prompt("/Cursor  Fix   the BUG ") == "fix the bug"


def test_first_send_does_not_escalate():
    dec, meta = evaluate_repeat(_decision(), "fix the spawn bug", "r1", cfg=RouterConfig())
    assert meta["repeat_count"] == 0
    assert dec.target == ExecutionTarget("cursor", "auto")


def test_repeat_escalates_one_hop_and_marks_prior_bad(db):
    cfg = RouterConfig()
    dec1, _ = evaluate_repeat(_decision(decision_id="first"), "fix the spawn bug", "r1", cfg=cfg)
    # record the first attempt as a 'successful' turn (exit 0 — the silent failure)
    record_attempt(
        decision=dec1, attempt_index=0, target=dec1.target, source="router",
        failure_kind="none", reason="", latency_ms=10.0, session_id="r1", db_path=db,
    )

    dec2, meta = evaluate_repeat(
        _decision(decision_id="second"), "fix the spawn bug", "r1", cfg=cfg
    )
    assert meta["repeat_count"] == 1
    assert meta["escalated_from"] == "cursor:auto"
    assert dec2.target == ExecutionTarget("cursor", "grok-4.6")  # hop 2
    assert dec2.escalation_target == ExecutionTarget("codex", "")
    assert dec2.fallbacks is None

    rows = [r for r in list_outcomes(db_path=db) if r["decision_id"] == "first"]
    assert rows and rows[0]["user_feedback"] == "bad"


def test_near_identical_prompt_counts_as_repeat(db):
    cfg = RouterConfig()
    evaluate_repeat(_decision(decision_id="a"), "fix the spawn bug in mob_ai", "r1", cfg=cfg)
    dec, meta = evaluate_repeat(
        _decision(decision_id="b"), "fix the spawn bug in the mob_ai!", "r1", cfg=cfg
    )
    assert meta["repeat_count"] == 1
    assert dec.target != ExecutionTarget("cursor", "auto")


def test_different_prompt_does_not_escalate(db):
    cfg = RouterConfig()
    evaluate_repeat(_decision(decision_id="a"), "fix the spawn bug", "r1", cfg=cfg)
    dec, meta = evaluate_repeat(_decision(decision_id="b"), "write me a haiku", "r1", cfg=cfg)
    assert meta["repeat_count"] == 0
    assert dec.target == ExecutionTarget("cursor", "auto")


def test_second_repeat_takes_third_hop(db):
    cfg = RouterConfig()
    evaluate_repeat(_decision(decision_id="a"), "fix the spawn bug", "r1", cfg=cfg)
    evaluate_repeat(_decision(decision_id="b"), "fix the spawn bug", "r1", cfg=cfg)
    dec, meta = evaluate_repeat(_decision(decision_id="c"), "fix the spawn bug", "r1", cfg=cfg)
    assert meta["repeat_count"] == 2
    assert dec.target == ExecutionTarget("codex", "")  # hop 3
    assert dec.escalation_target == ExecutionTarget("cursor", "grok-4.6")  # config default
    assert dec.fallbacks is None
    assert meta["chain_exhausted"] is True


def test_repeat_after_cancel_is_not_evidence(db):
    cfg = RouterConfig()
    dec1, _ = evaluate_repeat(_decision(decision_id="c1"), "fix the spawn bug", "r1", cfg=cfg)
    record_attempt(
        decision=dec1, attempt_index=0, target=dec1.target, source="router",
        failure_kind="cancelled", reason="user stop", latency_ms=10.0,
        session_id="r1", db_path=db,
    )
    dec2, meta = evaluate_repeat(_decision(decision_id="c2"), "fix the spawn bug", "r1", cfg=cfg)
    assert meta["repeat_count"] == 0  # cancelled ≠ quality evidence
    assert dec2.target == ExecutionTarget("cursor", "auto")
    rows = [r for r in list_outcomes(db_path=db) if r["decision_id"] == "c1"]
    assert rows[0]["user_feedback"] is None  # never rated


def test_old_repeat_outside_window_is_fresh(db):
    cfg = RouterConfig()
    t0 = time.time() - 3 * 86400
    evaluate_repeat(_decision(decision_id="old"), "fix the spawn bug", "r1", cfg=cfg, now=t0)
    dec, meta = evaluate_repeat(
        _decision(decision_id="new"), "fix the spawn bug", "r1", cfg=cfg, now=t0 + 3 * 86400 + 60
    )
    assert meta["repeat_count"] == 0


def test_handoff_note_explains_escalation():
    p = build_repeat_handoff("fix the spawn bug", {"repeat_count": 2, "escalated_from": "cursor:auto"})
    assert "repeated request" in p
    assert "fix the spawn bug" in p
    # unchanged when no repeat
    assert build_repeat_handoff("fix the spawn bug", {"repeat_count": 0}) == "fix the spawn bug"
