"""Phase B (basic) drift detection: spike → demotion → recovery.

Exit criteria from .cuttle/docs/agent-router-todo.md: "injecting synthetic fail spike
demotes a target in tests; recovering clears demotion."
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from api.agent_router.config import load_router_config, reset_router_config
from api.agent_router.dispatch import execute_decision
from api.agent_router.drift import (
    active_demotions,
    apply_demotion_avoidance,
    clear_demotion,
    compute_drift,
    evaluate_drift,
)
from api.agent_router.types import (
    ExecutionTarget,
    RouterConfig,
    RoutingDecision,
    RoutingContext,
)


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


@pytest.fixture
def db(tmp_path: Path):
    return tmp_path / "outcomes.db"


def _record(db, dec, idx, kind, ts, target=None, session="s1"):
    from api.agent_router.outcomes import record_attempt

    record_attempt(
        decision=dec,
        attempt_index=idx,
        target=target or ExecutionTarget("cursor", "auto"),
        source="router",
        failure_kind=kind,
        reason="" if kind == "none" else "synthetic",
        latency_ms=100.0,
        session_id=session,
        db_path=db,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE router_outcomes SET recorded_at=? WHERE id=(SELECT MAX(id) FROM router_outcomes)",
        (ts,),
    )
    conn.commit()
    conn.close()


def _seed_spike(db, now, *, n_baseline=10, n_window=6):
    dec = RoutingDecision(
        decision_id="seed",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.9,
        reason="seed",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
    )
    for i in range(n_baseline):
        _record(db, dec, i, "none" if i < n_baseline - 1 else "task", now - (i + 2) * 86400)
    for i in range(n_window):
        _record(db, dec, 100 + i, "transport", now - 60 * i)
    return dec


def test_fail_spike_flags_and_demotes(router_settings, db):
    now = time.time()
    _seed_spike(db, now)
    report = compute_drift(db_path=db, now=now + 120)
    entry = next(e for e in report if e["key"] == "cursor:auto")
    assert entry["drift"] is True
    assert entry["baseline_fail_rate"] == pytest.approx(0.1)
    assert entry["window_fail_rate"] == pytest.approx(1.0)

    result = evaluate_drift(db_path=db, now=now + 120)
    dem = result["demotions"]["cursor:auto"]
    assert "regression" in dem["reason"]
    assert dem["until"] > now
    # persisted to settings
    assert "cursor:auto" in active_demotions(now=now + 200)


def test_recovery_clears_demotion_when_no_new_failures(router_settings, db):
    now = time.time()
    _seed_spike(db, now)
    evaluate_drift(db_path=db, now=now + 120)
    assert "cursor:auto" in active_demotions(now=now + 200)

    # No new attempts after the demotion (router routed around the target) —
    # the next evaluation must recover it instead of re-flagging stale rows.
    result = evaluate_drift(db_path=db, now=now + 400)
    assert "cursor:auto" not in result["demotions"]
    actions = [c["action"] for c in result["changes"]]
    assert "recovered" in actions


def test_ttl_expiry_recovers(router_settings, db):
    now = time.time()
    _seed_spike(db, now)
    evaluate_drift(db_path=db, now=now + 120)
    # long after TTL with no fresh evidence
    result = evaluate_drift(db_path=db, now=now + 8 * 86400)
    assert "cursor:auto" not in result["demotions"]
    assert any(c["action"] == "recovered" for c in result["changes"])


def test_persistent_failures_extend_demotion(router_settings, db):
    now = time.time()
    _seed_spike(db, now)
    evaluate_drift(db_path=db, now=now + 120, demotion_ttl_minutes=30)
    dem1 = active_demotions(now=now + 200)["cursor:auto"]

    # fresh failures AFTER the flag → evidence persists → demotion extends
    dec = RoutingDecision(
        decision_id="seed2",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.9,
        reason="seed",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
    )
    for i in range(5):
        _record(db, dec, 200 + i, "task", now + 400 + i)

    result = evaluate_drift(db_path=db, now=now + 500, demotion_ttl_minutes=30)
    assert result["demotions"]["cursor:auto"]["until"] > dem1["until"]


def test_cancelled_is_never_quality_evidence(router_settings, db):
    now = time.time()
    dec = RoutingDecision(
        decision_id="seed",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("codex", ""),
        confidence=0.9,
        reason="seed",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
    )
    for i in range(6):
        _record(db, dec, i, "cancelled", now - 60 * i, target=ExecutionTarget("codex", ""))
    report = compute_drift(db_path=db, now=now + 120)
    entry = next(e for e in report if e["key"] == "codex:")
    assert entry["window_attempts"] == 0
    assert entry["drift"] is False


def test_apply_demotion_avoidance_shifts_chain(router_settings):
    cfg = RouterConfig()
    decision = RoutingDecision(
        decision_id="d1",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.9,
        reason="brain",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        fallbacks=[ExecutionTarget("codex", "")],
    )
    # cursor:grok-4.6 is demoted → chain must shift to codex
    from managers.settings_manager import get_settings_manager

    raw = get_settings_manager().get_setting("agent_router") or {}
    raw["demotions"] = {
        "cursor:grok-4.6": {
            "agent": "cursor",
            "model": "grok-4.6",
            "reason": "test",
            "flagged_at": time.time() - 60,
            "until": time.time() + 1800,
        }
    }
    get_settings_manager().set_setting("agent_router", raw)

    decision, meta = apply_demotion_avoidance(decision, cfg)
    assert decision.target == ExecutionTarget("cursor", "auto")  # healthy primary stays
    assert decision.escalation_target == ExecutionTarget("codex", "")
    assert "cursor:grok-4.6" in meta["demoted_skipped"]

    assert clear_demotion("cursor", "grok-4.6") is True
    assert "cursor:grok-4.6" not in active_demotions()


def test_dispatch_skips_demoted_fallback(router_settings, db, monkeypatch):
    """execute_decision must not route into a demoted fallback target."""
    cfg = load_router_config()
    now = time.time()

    from managers.settings_manager import get_settings_manager

    raw = get_settings_manager().get_setting("agent_router") or {}
    raw["demotions"] = {
        "codex:": {
            "agent": "codex",
            "model": "",
            "reason": "test",
            "flagged_at": now - 60,
            "until": now + 1800,
        }
    }
    get_settings_manager().set_setting("agent_router", raw)

    decision = RoutingDecision(
        decision_id="d9",
        task_type="coding",
        difficulty="medium",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.9,
        reason="brain",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
    )

    ran = []

    def fake_runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        agent = "cursor"
        ran.append(agent)
        return {"success": True, "response": "ok", "type": "chat"}

    monkeypatch.setattr(
        "api.agent_router.dispatch._default_runners",
        lambda: {"cursor": fake_runner, "codex": lambda *a, **k: ran.append("codex") or {"success": True, "response": "x", "type": "chat"}},
    )

    result = execute_decision(
        decision,
        "hello",
        chat_session_id=1,
        config=cfg,
        runners={"cursor": fake_runner, "codex": fake_runner},
    )
    assert result["success"] is True
    assert "codex" not in ran  # demoted fallback was never attempted


def test_health_api_round_trip(router_settings):
    from api import web_chat_api as w

    with w.app.test_client() as client:
        resp = client.get("/api/router/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["config"]["default_target"]["agent"] == "cursor"
        assert isinstance(data["use_cases"], list)
        assert isinstance(data["demotions"], dict)

        resp = client.post("/api/router/health/refresh")
        assert resp.status_code == 200
        h = resp.get_json()
        assert h["success"] is True
        assert isinstance(h["metrics"], list)
        assert "report" in h

        resp = client.put(
            "/api/router/config",
            json={"mode": "off", "use_cases": data["use_cases"]},
        )
        assert resp.status_code == 200
        assert resp.get_json()["config"]["provider"]["mode"] == "off"
