"""Frustration ("rage") detection: explicit complaints flag prior attempts.

"still not fixed" / "you said you fixed it" — even fully rephrased — is
explicit negative feedback. The router escalates the chain and rates the
session's recent routed attempts bad. No LLM, no tokens.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from api.agent_router import frustration
from api.agent_router.outcomes import list_outcomes, record_attempt
from api.agent_router.repeats import (
    build_repeat_handoff,
    evaluate_repeat,
    reset_session,
)
from api.agent_router.types import (
    ExecutionTarget,
    RouterConfig,
    RoutingDecision,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    from api.agent_router.repeats import reset_session

    reset_session("f1")
    yield
    reset_session("f1")


@pytest.fixture
def db(tmp_path: Path, monkeypatch):
    p = tmp_path / "outcomes.db"
    import api.agent_router.rage_investigator as ri
    import api.agent_router.repeats as repeats

    real_list = repeats.list_outcomes
    real_fb = repeats.set_feedback

    def _patch(mod):
        monkeypatch.setattr(
            mod, "list_outcomes",
            lambda *, decision_id=None, limit=100, **_: real_list(
                decision_id=decision_id, limit=limit, db_path=p
            ),
        )
        monkeypatch.setattr(
            mod, "set_feedback",
            lambda feedback, *, session_id=None, decision_id=None, **_: real_fb(
                feedback, decision_id=decision_id, db_path=p
            ),
        )

    _patch(repeats)
    _patch(ri)
    return p


def _session_decisions(db_path, session_id, limit):
    from api.agent_router.outcomes import recent_session_decision_ids

    return recent_session_decision_ids(session_id, limit=limit, db_path=db_path)


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


def _record_ok(db, dec, session="f1"):
    record_attempt(
        decision=dec, attempt_index=0, target=dec.target, source="router",
        failure_kind="none", reason="", latency_ms=10.0, session_id=session, db_path=db,
    )


# ── detection ────────────────────────────────────────────────────────────────

def test_detects_frustration_phrases():
    assert frustration.detect_frustration("still not fixed") == "still not fixed"
    assert frustration.detect_frustration("You said you fixed this!") == "you said you fixed"
    assert frustration.detect_frustration("STILL BROKEN, ugh") == "still broken"


def test_nth_time_phrase_matches():
    assert frustration.detect_frustration("for the third time, fix it") == "for the third time"


def test_fresh_bug_report_is_not_frustration():
    assert frustration.detect_frustration("the login is not working") is None
    assert frustration.detect_frustration("fix the spawn bug") is None
    assert frustration.detect_frustration("can you try again please") is None


def test_rage_config_disable_and_custom_phrases(tmp_path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    raw = sm.get_setting("agent_router") or {}
    raw["rage"] = {"enabled": False}
    sm.set_setting("agent_router", raw)
    assert frustration.detect_frustration("still broken") is None

    raw["rage"] = {"enabled": True, "phrases": ["custom rage phrase"]}
    sm.set_setting("agent_router", raw)
    assert frustration.detect_frustration("custom rage phrase!!") == "custom rage phrase"
    assert frustration.detect_frustration("still broken") is None  # replaced, not extended


# ── escalation + flagging ────────────────────────────────────────────────────

def test_frustration_flags_prior_attempts_and_escalates(db):
    cfg = RouterConfig()
    dec1, _ = evaluate_repeat(_decision(decision_id="first"), "fix the spawn bug", "f1", cfg=cfg)
    _record_ok(db, dec1)

    dec2, meta = evaluate_repeat(
        _decision(decision_id="second"), "still not fixed", "f1", cfg=cfg
    )
    fr = meta["frustration"]
    assert fr["phrase"] == "still not fixed"
    assert fr["flagged_decisions"] == ["first"]  # fallback: single most recent
    assert [a["decision_id"] for a in fr["evidence"]] == ["first", "second"]
    assert fr["evidence"][0]["norm"] == "fix the spawn bug"
    assert meta["repeat_count"] == 1
    assert dec2.target == ExecutionTarget("cursor", "grok-4.6")

    rows = [r for r in list_outcomes(db_path=db) if r["decision_id"] == "first"]
    assert rows[0]["user_feedback"] == "bad"


def test_rephrased_frustration_still_escalates(db):
    """Wording changed entirely — rage phrase carries the evidence."""
    cfg = RouterConfig()
    evaluate_repeat(_decision(decision_id="a"), "fix the spawn bug in mob_ai", "f1", cfg=cfg)
    dec, meta = evaluate_repeat(
        _decision(decision_id="b"), "you said you fixed the spawn issue but it persists", "f1", cfg=cfg
    )
    assert meta["frustration"]["phrase"] == "you said you fixed"
    assert meta["repeat_count"] == 1
    assert dec.target == ExecutionTarget("cursor", "grok-4.6")


def test_frustration_compounds_across_rephrases(db):
    cfg = RouterConfig()
    evaluate_repeat(_decision(decision_id="a"), "fix the spawn bug", "f1", cfg=cfg)
    _d2, m2 = evaluate_repeat(
        _decision(decision_id="b"), "still broken", "f1", cfg=cfg
    )
    assert m2["repeat_count"] == 1
    _d3, m3 = evaluate_repeat(
        _decision(decision_id="c"), "it still fails, same error", "f1", cfg=cfg
    )
    assert m3["repeat_count"] == 2
    assert _d3.target == ExecutionTarget("codex", "")  # hop 3


def test_correlation_flags_only_the_matching_ask(db):
    """The complaint names its issue — only the attempt for THAT issue flags."""
    cfg = RouterConfig()
    asks = [
        ("write a haiku about octopuses", "d_haiku"),
        ("fix the mob spawn bug in the cave level", "d_spawn"),
    ]
    for ask, did in asks:
        dec, _ = evaluate_repeat(_decision(decision_id=did), ask, "f1", cfg=cfg)
        _record_ok(db, dec)

    dec3, meta = evaluate_repeat(
        _decision(decision_id="d3"), "still broken — the mob spawn bug in the cave", "f1", cfg=cfg
    )
    fr = meta["frustration"]
    assert fr["correlation"] == "matched"
    assert fr["flagged_decisions"] == ["d_spawn"]  # only the cave-spawn ask, not the haiku
    feedbacks = {r["decision_id"]: r["user_feedback"] for r in list_outcomes(db_path=db)}
    assert feedbacks["d_spawn"] == "bad"
    assert feedbacks["d_haiku"] is None  # unrelated ask untouched


def test_unrelated_asks_fall_back_to_single_most_recent(db):
    """No issue description in the complaint → flag exactly one prior, not three."""
    cfg = RouterConfig()
    asks = [
        ("write a haiku about octopuses", "d1"),
        ("summarize the meeting notes", "d2"),
        ("draft a release announcement", "d3"),
    ]
    for ask, did in asks:
        dec, _ = evaluate_repeat(_decision(decision_id=did), ask, "f1", cfg=cfg)
        _record_ok(db, dec)

    evaluate_repeat(_decision(decision_id="d4"), "STILL NOT FIXED", "f1", cfg=cfg)
    feedbacks = {
        r["decision_id"]: r["user_feedback"]
        for r in list_outcomes(db_path=db)
    }
    assert feedbacks["d3"] == "bad"  # most recent prior only
    assert feedbacks["d2"] is None
    assert feedbacks["d1"] is None


def test_cancelled_attempts_never_flagged(db):
    cfg = RouterConfig()
    dec, _ = evaluate_repeat(_decision(decision_id="cx"), "fix the spawn bug", "f1", cfg=cfg)
    record_attempt(
        decision=dec, attempt_index=0, target=dec.target, source="router",
        failure_kind="cancelled", reason="user stop", latency_ms=10.0,
        session_id="f1", db_path=db,
    )
    dec2, meta = evaluate_repeat(_decision(decision_id="cy"), "still broken", "f1", cfg=cfg)
    assert meta["frustration"]["flagged_decisions"] == []
    rows = [r for r in list_outcomes(db_path=db) if r["decision_id"] == "cx"]
    assert rows[0]["user_feedback"] is None
    # escalation still happens — direct user testimony
    assert dec2.target == ExecutionTarget("cursor", "grok-4.6")


def test_frustration_disabled_means_no_escalation(db, monkeypatch):
    monkeypatch.setattr(
        frustration, "load_rage_config",
        lambda: {"enabled": False, "phrases": frustration.DEFAULT_PHRASES},
    )
    cfg = RouterConfig()
    evaluate_repeat(_decision(decision_id="a"), "fix the spawn bug", "f1", cfg=cfg)
    dec, meta = evaluate_repeat(_decision(decision_id="b"), "still broken", "f1", cfg=cfg)
    assert meta["frustration"] is None
    assert meta["repeat_count"] == 0
    assert dec.target == ExecutionTarget("cursor", "auto")


def test_frustration_handoff_note():
    p = build_repeat_handoff(
        "still broken",
        {"repeat_count": 1, "escalated_from": "cursor:auto",
         "frustration": {"phrase": "still broken", "flagged_decisions": ["d1"]}},
    )
    assert "frustrated" in p
    assert "Re-diagnose from scratch" in p


# ── background investigator ──────────────────────────────────────────────────

def _wait_active_clear(sid, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        from api.agent_router.rage_investigator import _ACTIVE, _LOCK

        with _LOCK:
            if sid not in _ACTIVE:
                return True
        time.sleep(0.05)
    return False


def fake_kernel_stub(agent_id, prompt, chat_session_id, **kw):
    import json as _json

    return {
        "success": True,
        "response": _json.dumps([
            {"decision_id": "d1", "verdict": "bad", "reason": "did not verify"},
        ]),
        "type": "chat",
    }


def test_investigator_disabled_skips(db, monkeypatch):
    from api.agent_router import rage_investigator as ri

    started = ri.maybe_start_investigation(
        session_id="f1",
        frustration_meta={"phrase": "still broken", "complaint": "x",
                          "evidence": [{"decision_id": "d1", "norm": "fix bug"}]},
        _config={"enabled": True, "phrases": list(frustration.DEFAULT_PHRASES),
                 "investigator": {"enabled": False, "agent": "cursor", "model": "auto",
                                  "timeout_s": 240}},
    )
    assert started is False


def test_investigator_runs_in_background_and_applies_verdicts(db, monkeypatch):
    from api.agent_router import rage_investigator as ri

    calls = {}

    def fake_kernel(agent_id, prompt, chat_session_id, **kw):
        calls["agent"] = agent_id
        calls["model"] = kw.get("model_override")
        calls["timeout"] = kw.get("timeout")
        calls["prompt"] = prompt
        return fake_kernel_stub(agent_id, prompt, chat_session_id, **kw)

    monkeypatch.setattr(ri, "run_agent_web_command", fake_kernel)

    # the investigator's verdict needs a real attempt row to land on
    record_attempt(
        decision=_decision(decision_id="d1"), attempt_index=0,
        target=ExecutionTarget("cursor", "auto"), source="router",
        failure_kind="none", reason="", latency_ms=10.0, session_id="f2", db_path=db,
    )

    started = ri.maybe_start_investigation(
        session_id="f2",
        frustration_meta={"phrase": "still broken", "complaint": "fix bug",
                          "evidence": [{"decision_id": "d1", "norm": "fix bug"}]},
    )
    assert started is True
    # parallel: call returned immediately — wait for the thread to finish
    assert _wait_active_clear("f2")
    assert calls.get("agent") == "cursor"
    assert "d1" in calls.get("prompt", "") and "fix bug" in calls.get("prompt", "")

    rows = [r for r in list_outcomes(db_path=db) if r["decision_id"] == "d1"]
    assert rows[0]["user_feedback"] == "bad"


def test_investigator_blocked_while_one_is_running(db, monkeypatch):
    from api.agent_router import rage_investigator as ri

    ri._ACTIVE.add("f3")
    try:
        started = ri.maybe_start_investigation(
            session_id="f3",
            frustration_meta={"phrase": "x", "complaint": "c",
                              "evidence": [{"decision_id": "d1", "norm": "a"}]},
        )
        assert started is False
    finally:
        with ri._LOCK:
            ri._ACTIVE.discard("f3")


def test_verdict_parsing_tolerates_garbage():
    from api.agent_router.rage_investigator import parse_verdicts

    assert parse_verdicts("no json here") == []
    assert parse_verdicts('[{"decision_id":"d1","verdict":"bad"}]') == [
        {"decision_id": "d1", "verdict": "bad", "reason": ""}
    ]
    assert parse_verdicts('[{"decision_id":"d1","verdict":"invented"}]') == []


def test_investigator_cancelled_attempts_skipped(db):
    from api.agent_router import rage_investigator as ri
    from api.agent_router.outcomes import record_attempt

    dec = _decision(decision_id="cx")
    record_attempt(
        decision=dec, attempt_index=0, target=dec.target, source="router",
        failure_kind="cancelled", reason="user stop", latency_ms=10.0,
        session_id="f4", db_path=db,
    )
    applied = ri.apply_verdicts([{"decision_id": "cx", "verdict": "bad", "reason": ""}])
    assert applied == 0
    rows = [r for r in list_outcomes(db_path=db) if r["decision_id"] == "cx"]
    assert rows[0]["user_feedback"] is None
