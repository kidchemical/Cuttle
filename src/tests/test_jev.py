"""Jev judgment layer: client, recipes, router plug, compiler rank, dashboard, regress."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.jev.client import FakeJevClient, set_client_override
from api.jev.types import parse_answer


@pytest.fixture(autouse=True)
def _no_live_jev():
    set_client_override(None)
    yield
    set_client_override(None)


def _answers(**kwargs):
    return kwargs


def test_openrouter_key_points_at_openrouter_systemone(monkeypatch):
    from api.jev.config import OPENROUTER_SYSTEMONE_BASE, resolve_jev_credentials

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("CUTTLE_JEV_BASE_URL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    key, base, src = resolve_jev_credentials()
    assert src == "openrouter"
    assert key == "sk-or-test"
    assert base == OPENROUTER_SYSTEMONE_BASE


def test_typesafe_key_wins_over_openrouter(monkeypatch):
    from api.jev.config import TYPESAFE_BASE_URL, resolve_jev_credentials

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("CUTTLE_JEV_BASE_URL", raising=False)
    key, base, src = resolve_jev_credentials()
    assert src == "typesafe"
    assert key == "ts-test"
    assert base == TYPESAFE_BASE_URL


def test_explicit_base_url_wins(monkeypatch):
    from api.jev.config import resolve_jev_credentials

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CUTTLE_JEV_BASE_URL", "https://example.test/api")
    _key, base, src = resolve_jev_credentials()
    assert src == "openrouter"
    assert base == "https://example.test/api"


def test_is_jev_model_openrouter_slug():
    from api.jev.config import is_jev_model_id

    assert is_jev_model_id("~typesafe/jev-latest")
    assert is_jev_model_id("typesafe/jev-1.13")
    assert is_jev_model_id("jev-latest")


def test_parse_choice_answer():
    a = parse_answer(
        {
            "type": "choice",
            "choice": "technical",
            "probabilities": {"billing": 0.08, "technical": 0.85, "sales": 0.07},
            "confidence": 0.82,
        }
    )
    assert a.choice == "technical"
    assert abs(a.probabilities["technical"] - 0.85) < 1e-9
    assert a.confidence == 0.82


def test_fake_client_handler():
    client = FakeJevClient(
        handler=lambda state, q: {
            "urgent": {"type": "noul", "noul": 0.91},
        }
    )
    r = client.system_one("help", {"urgent": {"type": "noul", "instructions": "urgent?"}})
    assert r.get("urgent").noul == 0.91
    assert r.model == "jev-fake"


def test_routing_recipe_picks_catalog_target():
    from api.agent_router.types import (
        ExecutionTarget,
        RouterConfig,
        RouterProviderConfig,
        RoutingContext,
    )
    from api.jev.routing import decide_routing

    cfg = RouterConfig(
        provider=RouterProviderConfig(mode="api", api_provider="jev", api_model="jev-latest"),
        default_target=ExecutionTarget("cursor", "auto"),
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
    )
    ctx = RoutingContext(
        user_request="rewrite the agent harness kernel",
        code_changes_requested=True,
        available_targets=[cfg.default_target, cfg.escalation_target],
    )
    client = FakeJevClient(
        handler=lambda state, q: {
            "task_type": {
                "type": "choice",
                "choice": "architecture",
                "confidence": 0.88,
                "probabilities": {"architecture": 0.88, "coding": 0.12},
            },
            "difficulty": {"type": "score", "score": 1.8, "confidence": 0.8},
            "target": {
                "type": "choice",
                "choice": "cursor__grok46",
                "confidence": 0.9,
                "probabilities": {"cursor__auto": 0.1, "cursor__grok46": 0.9},
            },
            "needs_execution": {"type": "noul", "noul": 0.95},
        }
    )
    d = decide_routing(ctx, cfg, client=client)
    assert d.task_type == "architecture"
    assert d.difficulty == "high"
    assert d.target.model == "grok-4.6"
    assert d.raw["provider"] == "jev"


def test_routing_low_confidence_falls_to_default():
    from api.agent_router.types import ExecutionTarget, RouterConfig, RoutingContext
    from api.jev.routing import decide_routing

    cfg = RouterConfig(
        default_target=ExecutionTarget("cursor", "auto"),
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
    )
    ctx = RoutingContext(
        user_request="hi",
        available_targets=[cfg.default_target, cfg.escalation_target],
    )
    client = FakeJevClient(
        handler=lambda state, q: {
            "task_type": {"type": "choice", "choice": "basic_ask", "confidence": 0.4, "probabilities": {}},
            "difficulty": {"type": "score", "score": 0.2},
            "target": {
                "type": "choice",
                "choice": "cursor__grok46",
                "confidence": 0.2,
                "probabilities": {"cursor__grok46": 0.4, "cursor__auto": 0.6},
            },
            "needs_execution": {"type": "noul", "noul": 0.1},
        }
    )
    d = decide_routing(ctx, cfg, client=client)
    assert d.target.model == "auto"


def test_engine_selects_jev_provider():
    from api.agent_router.engine import _provider_for
    from api.agent_router.types import RouterConfig, RouterProviderConfig

    cfg = RouterConfig(
        provider=RouterProviderConfig(mode="api", api_provider="jev", api_model="jev-latest")
    )
    assert _provider_for(cfg).name == "jev"

    cfg2 = RouterConfig(
        provider=RouterProviderConfig(mode="api", api_provider="openai", api_model="jev")
    )
    assert _provider_for(cfg2).name == "jev"


def test_api_model_jev_sets_provider(tmp_path, monkeypatch):
    from managers.settings_manager import SettingsManager
    from api.agent_router.config import reset_router_config, update_router_config

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    reset_router_config()
    cfg, err = update_router_config(api_model="jev")
    assert err is None
    assert cfg.provider.api_provider == "jev"
    assert cfg.provider.api_model == "jev-latest"


def test_rage_skips_when_not_same_issue():
    from api.jev.rage import investigate_attempts

    client = FakeJevClient(
        handler=lambda state, q: {
            "same_issue": {"type": "noul", "noul": 0.1},
            "failed_0": {"type": "noul", "noul": 0.99},
        }
    )
    out = investigate_attempts(
        complaint="what's for dinner",
        phrase="still broken",
        asks=[{"decision_id": "d1", "norm": "fix login"}],
        outcomes_by_decision={"d1": [{"target_agent": "cursor", "failure_kind": "none"}]},
        client=client,
    )
    assert out == []


def test_rage_marks_failed_attempts():
    from api.jev.rage import investigate_attempts

    client = FakeJevClient(
        handler=lambda state, q: {
            "same_issue": {"type": "noul", "noul": 0.9},
            "failed_0": {"type": "noul", "noul": 0.8},
        }
    )
    out = investigate_attempts(
        complaint="still not fixed",
        phrase="still not fixed",
        asks=[{"decision_id": "d1", "norm": "fix login"}],
        outcomes_by_decision={"d1": [{"target_agent": "cursor", "failure_kind": "none"}]},
        client=client,
    )
    assert out == [{"decision_id": "d1", "verdict": "bad", "reason": out[0]["reason"]}]
    assert "failed=0.80" in out[0]["reason"]


def test_rank_skips_when_no_need(tmp_path):
    from api.jev.rank import rank_context

    (tmp_path / ".cuttle" / "docs").mkdir(parents=True)
    (tmp_path / ".cuttle" / "docs" / "discord.md").write_text("discord.post", encoding="utf-8")
    client = FakeJevClient(
        handler=lambda state, q: {
            "needs_extra": {"type": "noul", "noul": 0.1},
            "pick": {
                "type": "choice",
                "choice": "doc:discord.md",
                "confidence": 0.9,
                "probabilities": {"doc:discord.md": 1.0},
            },
        }
    )
    ranked = rank_context(
        "hi",
        project_path=str(tmp_path),
        inventory={"docs": ["discord.md"]},
        client=client,
    )
    assert ranked["items"] == []


def test_rank_injects_winner_body(tmp_path):
    from api.jev.rank import format_ranked_block, rank_context

    (tmp_path / ".cuttle" / "docs").mkdir(parents=True)
    (tmp_path / ".cuttle" / "docs" / "local-only.md").write_text("Use discord.post only.", encoding="utf-8")
    client = FakeJevClient(
        handler=lambda state, q: {
            "needs_extra": {"type": "noul", "noul": 0.9},
            "pick": {
                "type": "choice",
                "choice": "doc:local-only.md",
                "confidence": 0.8,
                "probabilities": {"doc:local-only.md": 1.0},
            },
        }
    )
    ranked = rank_context(
        "post this in discord",
        project_path=str(tmp_path),
        inventory={"docs": ["local-only.md"]},
        client=client,
    )
    assert ranked["items"][0]["name"] == "local-only.md"
    assert "discord.post" in ranked["items"][0]["body"]
    block = format_ranked_block(ranked["items"])
    assert "Suggested runbooks" in block


def test_compiler_injects_ranked_layer(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context
    from api.jev.client import FakeJevClient, set_client_override

    (tmp_path / ".cuttle" / "rules").mkdir(parents=True)
    (tmp_path / ".cuttle" / "rules" / "01.md").write_text("Be brief.", encoding="utf-8")
    (tmp_path / ".cuttle" / "docs").mkdir(parents=True)
    (tmp_path / ".cuttle" / "docs" / "local-discord.md").write_text("discord.post", encoding="utf-8")
    set_client_override(
        FakeJevClient(
            handler=lambda state, q: {
                "needs_extra": {"type": "noul", "noul": 0.9},
                "pick": {
                    "type": "choice",
                    "choice": "doc:local-discord.md",
                    "confidence": 0.85,
                    "probabilities": {"doc:local-discord.md": 1.0},
                },
            }
        )
    )
    compiled = compile_context(
        "announce this on discord",
        project_path=str(tmp_path),
        inject_capabilities=False,
    )
    assert "ranked_context" in compiled.layers_used
    assert "discord.post" in compiled.prompt
    assert compiled.prompt.index("discord.post") < compiled.prompt.index("announce this on discord")


def test_label_respects_user_thumbs():
    from api.jev.labels import label_turn

    client = FakeJevClient(
        handler=lambda state, q: {
            "miss": {
                "type": "choice",
                "choice": "ok",
                "confidence": 0.9,
                "probabilities": {"ok": 1.0},
            },
            "user_would_accept": {"type": "noul", "noul": 0.9},
        }
    )
    lab = label_turn(
        {"success": True, "user_feedback": "bad", "failure_kind": "none"},
        client=client,
    )
    assert lab["accepted"] is False
    assert lab["miss_kind"] == "model"


def test_regress_skips_without_failures():
    from api.jev.regress import decide_regress

    d = decide_regress({"failure_count": 0, "query_failures": [], "outcome_failures": []})
    assert d["run_tests"] is False
    assert d["suite"] == "none"


def test_regress_picks_suite_and_can_skip_pytest(monkeypatch):
    from api.jev import regress as rg

    client = FakeJevClient(
        handler=lambda state, q: {
            "regressed": {"type": "noul", "noul": 0.9},
            "suite": {
                "type": "choice",
                "choice": "router",
                "confidence": 0.8,
                "probabilities": {"router": 0.8, "none": 0.2},
            },
        }
    )
    called = {}

    def fake_pytest(paths, **kw):
        called["paths"] = paths
        return {"ran": True, "returncode": 1, "stdout": "FAILED", "stderr": ""}

    monkeypatch.setattr(rg, "run_pytest", fake_pytest)
    monkeypatch.setattr(rg, "collect_evidence", lambda **kw: {
        "failure_count": 2,
        "query_failures": [{"error_message": "router boom"}],
        "outcome_failures": [],
    })
    monkeypatch.setattr(rg, "notify_broke", lambda *a, **k: called.setdefault("notified", True))
    report = rg.run_regress_once(client=client, run_tests=True, notify=True)
    assert report["decision"]["suite"] == "router"
    assert called["paths"][0].endswith("test_agent_router.py")
    assert called.get("notified") is True
    assert report["pytest"]["returncode"] == 1


def test_watch_does_not_start_under_pytest():
    from api.jev.watch import ensure_started

    assert ensure_started() is False


def test_cuttle_performance_from_outcomes(tmp_path, monkeypatch):
    from api.agent_router.outcomes import record_attempt
    from api.agent_router.types import ExecutionTarget, RoutingDecision
    from api.dashboards import service

    db = tmp_path / "o.db"
    d = RoutingDecision(
        decision_id="abc123",
        task_type="coding",
        difficulty="low",
        target=ExecutionTarget("cursor", "auto"),
        confidence=0.5,
        reason="t",
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
    )
    record_attempt(
        decision=d,
        attempt_index=0,
        target=d.target,
        source="router",
        failure_kind="none",
        reason="",
        latency_ms=1200,
        result={"cost": 0.01, "usage": {"total_tokens": 800}},
        db_path=db,
    )
    payload = service.cuttle_performance(db_path=db, label=False, days=0)
    assert payload["status"] == "live"
    assert payload["stats"]["attempts"] == 1
    assert payload["rows"][0]["harness"] == "cursor"
    assert payload["rows"][0]["model"] == "auto"


def test_cli_status(capsys):
    from api.jev.cli import main

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "available" in out
    assert "auth_source" in out
    assert "jev-latest" in out or "model" in out


def test_options_include_jev():
    """Owner-only endpoint (live `current` config); anonymous is rejected.

    Owner-200 payload shape (jev ids/models) is asserted in test_http_authz.py.
    """
    from api import web_chat_api as w

    with w.app.test_client() as client:
        res = client.get(
            "/api/agent-router/options",
            environ_base={"REMOTE_ADDR": "192.168.1.77"},
        )
        assert res.status_code == 401


def test_dashboards_catalog_performance_is_live():
    from api.dashboards import catalog

    card = catalog.get_dashboard("cuttle-performance")
    assert card["status"] == "live"


def test_flask_performance_route(tmp_path, monkeypatch):
    from flask import Flask
    from api.dashboards.routes import dashboards_bp
    from api.dashboards import service

    monkeypatch.setattr(service, "cuttle_performance", lambda **kw: {
        "success": True, "id": "cuttle-performance", "status": "live", "rows": [], "stats": {"attempts": 0}
    })
    app = Flask(__name__)
    app.register_blueprint(dashboards_bp)
    body = app.test_client().get("/api/dashboards/cuttle-performance").get_json()
    assert body["id"] == "cuttle-performance"
    assert body["status"] == "live"
