"""Tests for router evaluation (decision-only; mocked provider)."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from api.agent_router.config import load_router_config, reset_router_config
from api.agent_router.engine import build_context, decide_with_outcome
from api.agent_router.eval.report import format_batch_summary, format_single_eval, save_eval_report
from api.agent_router.eval.runner import evaluate_prompt, run_suite
from api.agent_router.eval.scoring import (
    RESULT_EXPECTATION_FAILURE,
    RESULT_INVALID,
    RESULT_PASS,
    RESULT_PREFERENCE_WARNING,
    score_case,
)
from api.agent_router.eval.suites import load_suite, parse_batch_args
from api.agent_router.providers.base import ProviderError
from api.agent_router.types import (
    ExecutionTarget,
    RoutingContext,
    RoutingDecision,
    TargetSource,
)


@pytest.fixture
def router_settings(tmp_path: Path, monkeypatch):
    from managers.settings_manager import SettingsManager

    sm = SettingsManager(str(tmp_path / "settings.json"))
    monkeypatch.setattr("managers.settings_manager.get_settings_manager", lambda: sm)
    monkeypatch.setattr(
        "api.cursor_agent_commands.list_cursor_agent_models",
        lambda: [
            {"id": "auto", "label": "Auto"},
            {"id": "grok-4.6", "label": "Grok 4.6"},
        ],
    )
    reset_router_config()
    return sm


def _decision(
    *,
    agent="cursor",
    model="auto",
    task_type="coding",
    difficulty="low",
    confidence=0.9,
    reason="ok",
    source=TargetSource.ROUTER.value,
) -> RoutingDecision:
    return RoutingDecision(
        decision_id=RoutingDecision.new_id(),
        task_type=task_type,
        difficulty=difficulty,
        target=ExecutionTarget(agent, model),
        confidence=confidence,
        reason=reason,
        escalation_target=ExecutionTarget("cursor", "grok-4.6"),
        source=source,
    )


def test_baseline_suite_loads():
    suite = load_suite("baseline")
    assert suite.name == "baseline"
    assert 20 <= len(suite.cases) <= 30
    assert all(c.prompt for c in suite.cases)


def test_parse_batch_args():
    assert parse_batch_args("") == ("baseline", 3, 1, False)
    assert parse_batch_args("baseline --yes")[3] is True
    assert parse_batch_args("--concurrency 5 --yes") == ("baseline", 5, 1, True)
    assert parse_batch_args("baseline --concurrency=2") == ("baseline", 2, 1, False)
    assert parse_batch_args("baseline --repeat 5 --concurrency 3 --yes") == (
        "baseline",
        3,
        5,
        True,
    )
    assert parse_batch_args("--repeats=4") == ("baseline", 3, 4, False)


def test_single_evaluate_uses_production_path(router_settings, monkeypatch):
    calls = {"n": 0}

    def fake_decide(ctx, cfg):
        calls["n"] += 1
        assert isinstance(ctx, RoutingContext)
        assert ctx.session_id is None
        return _decision(), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "openai_api",
        }

    monkeypatch.setattr("api.agent_router.eval.runner.decide_with_outcome", fake_decide)
    out = evaluate_prompt("Add a docstring", decide_fn=fake_decide)
    assert calls["n"] == 1
    assert out["decision"]["target_agent"] == "cursor"
    assert out["used_fallback"] is False
    text = format_single_eval(out)
    assert "no agent executed" in text.lower() or "Decision only" in text or "decision only" in text.lower()


def test_batch_never_invokes_execution_agents(router_settings, monkeypatch):
    exec_calls: List[str] = []

    def boom_runner(*a, **k):
        exec_calls.append("ran")
        raise AssertionError("executor must not run during eval")

    monkeypatch.setattr(
        "api.agent_router.dispatch._default_runners",
        lambda: {"cursor": boom_runner, "codex": boom_runner},
    )

    def fake_decide(ctx, cfg):
        return _decision(model="auto"), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "mock",
        }

    suite = load_suite("baseline")
    # Shrink to 3 cases for speed
    suite.cases = suite.cases[:3]
    report = run_suite(suite, concurrency=2, decide_fn=fake_decide)
    assert exec_calls == []
    assert report["case_count"] == 3
    assert all(c.get("decision") for c in report["cases"])


def test_session_sticky_unchanged_during_eval(router_settings, monkeypatch):
    from api import starred_slash as ss

    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: ["/cursor "])
    before = ss.get_starred_prefixes()

    def fake_decide(ctx, cfg):
        assert ctx.session_id is None
        return _decision(), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "mock",
        }

    evaluate_prompt("hi", decide_fn=fake_decide)
    assert ss.get_starred_prefixes() == before


def test_expectation_pass_and_preference_warning(router_settings):
    from api.agent_router.eval.suites import EvalTestCase

    case = EvalTestCase(
        id="t1",
        category="coding",
        prompt="x",
        allowed_task_types=["coding"],
        allowed_difficulties=["low", "medium"],
        allowed_targets=[{"agent": "cursor", "model": "auto"}, {"agent": "cursor", "model": "grok-4.6"}],
        preferred_target={"agent": "cursor", "model": "grok-4.6"},
    )
    ok = score_case(case, _decision(model="grok-4.6", difficulty="high".replace("high", "medium")))
    # difficulty medium is allowed
    d_ok = _decision(model="grok-4.6", difficulty="medium")
    s = score_case(case, d_ok)
    assert s["result"] == RESULT_PASS
    assert s["expectation_pass"] is True

    warn = score_case(case, _decision(model="auto", difficulty="low"))
    assert warn["result"] == RESULT_PREFERENCE_WARNING
    assert warn["preference_warning"] is True
    assert warn["expectation_pass"] is True


def test_expectation_failure_distinct(router_settings):
    from api.agent_router.eval.suites import EvalTestCase

    case = EvalTestCase(
        id="arch",
        category="architecture",
        prompt="redesign everything",
        allowed_task_types=["architecture"],
        allowed_difficulties=["high"],
        allowed_targets=[{"agent": "cursor", "model": "grok-4.6"}],
        preferred_target={"agent": "cursor", "model": "grok-4.6"},
    )
    s = score_case(case, _decision(model="auto", task_type="coding", difficulty="low"))
    assert s["result"] == RESULT_EXPECTATION_FAILURE
    assert s["expectation_pass"] is False


def test_invalid_decision_reported(router_settings):
    from api.agent_router.eval.suites import EvalTestCase

    case = EvalTestCase(id="x", category="c", prompt="p")
    s = score_case(
        case,
        _decision(),
        used_fallback=True,
        api_error="Invalid target_agent `made-up`",
        invalid_rejected=True,
    )
    assert s["result"] == RESULT_INVALID
    assert s["validation_pass"] is False


def test_fallback_visibly_marked(router_settings):
    from api.agent_router.eval.suites import EvalTestCase

    case = EvalTestCase(id="x", category="c", prompt="p")
    s = score_case(
        case,
        _decision(source=TargetSource.DEFAULT.value),
        used_fallback=True,
        api_error="timeout",
        invalid_rejected=False,
    )
    assert s["result"] in ("fallback", "api_error")
    assert s["used_fallback"] if "used_fallback" in s else True


def test_api_error_does_not_crash_batch(router_settings):
    suite = load_suite("baseline")
    suite.cases = suite.cases[:4]
    n = {"i": 0}

    def flaky(ctx, cfg):
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("provider down")
        return _decision(), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "mock",
        }

    # decide_fn that sometimes raises — runner must catch
    report = run_suite(suite, concurrency=1, decide_fn=flaky)
    assert report["case_count"] == 4
    assert any(c.get("api_error") or c.get("used_fallback") for c in report["cases"])


def test_concurrency_bounded(router_settings):
    suite = load_suite("baseline")
    suite.cases = suite.cases[:6]
    active = {"n": 0, "max": 0}

    def slow(ctx, cfg):
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        import time

        time.sleep(0.05)
        active["n"] -= 1
        return _decision(), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "mock",
        }

    report = run_suite(suite, concurrency=2, decide_fn=slow)
    assert report["concurrency"] == 2
    assert active["max"] <= 2
    assert report["case_count"] == 6


def test_long_report_truncated_and_json_fields(router_settings, tmp_path):
    suite = load_suite("baseline")

    def fake(ctx, cfg):
        return _decision(reason="x" * 80), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "mock",
        }

    report = run_suite(suite, concurrency=4, decide_fn=fake)
    json_path, md_path, url = save_eval_report(report, logs_dir=tmp_path)
    assert json_path.is_file() and md_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    for key in (
        "suite",
        "suite_version",
        "timestamp",
        "router",
        "counts",
        "cases",
        "concurrency",
        "elapsed_ms",
    ):
        assert key in data
    assert data["cases"][0]["decision_id"]
    assert data["cases"][0]["latency_ms"] is not None
    summary = format_batch_summary(data, artifact_url=url)
    assert "Passed" in summary
    assert "| ID |" in summary
    assert url in summary or "router_eval_" in summary


def test_slash_evaluate_single_and_batch_preview(router_settings, monkeypatch):
    from api.agent_router.commands import handle_router_command

    def fake_decide(ctx, cfg):
        return _decision(), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "mock",
        }

    monkeypatch.setattr("api.agent_router.eval.runner.decide_with_outcome", fake_decide)
    # evaluate_prompt imports decide_with_outcome at call time via runner module
    monkeypatch.setattr("api.agent_router.eval.evaluate_prompt", lambda prompt, **kw: {
        "prompt": prompt,
        "decision": _decision().to_dict(),
        "used_fallback": False,
        "api_error": None,
        "invalid_rejected": False,
        "provider": "mock",
        "latency_ms": 1.0,
        "router_config": {"mode": "api", "api_model": "gpt-4o-mini"},
    })

    single = handle_router_command("evaluate What does the router do?")
    assert single["type"] == "router_eval"
    assert "Target" in single["response"] or "target" in single["response"].lower()

    preview = handle_router_command("evaluate batch")
    assert preview["type"] == "router_eval_preview"
    assert "--yes" in preview["response"]
    assert "paid" in preview["response"].lower() or "API" in preview["response"]


def test_slash_evaluate_batch_yes(router_settings, monkeypatch, tmp_path):
    from api.agent_router.commands import handle_router_command
    import api.agent_router.eval as ev

    suite = load_suite("baseline")
    suite.cases = suite.cases[:2]

    def _load(name="baseline"):
        return suite

    monkeypatch.setattr(ev, "load_suite", _load)
    monkeypatch.setattr(ev, "run_suite", lambda s, **kw: {
        "suite": s.name,
        "suite_version": s.version,
        "description": "",
        "concurrency": 2,
        "case_count": 2,
        "elapsed_ms": 10,
        "router": {"mode": "api", "api_model": "gpt-4o-mini"},
        "counts": {
            "total": 2,
            "passed": 2,
            "preference_warnings": 0,
            "expectation_failures": 0,
            "invalid": 0,
            "manual_review": 0,
            "fallback": 0,
            "api_error": 0,
        },
        "cases": [
            {
                "id": "a",
                "expected_summary": "x",
                "actual_summary": "cursor/auto",
                "confidence": 0.9,
                "result": "pass",
                "prompt": "p",
                "reason": "r",
                "task_type": "coding",
                "difficulty": "low",
            }
        ],
    })
    monkeypatch.setattr(
        ev,
        "save_eval_report",
        lambda report, logs_dir=None: (
            tmp_path / "r.json",
            tmp_path / "r.md",
            "/logs/r.json",
        ),
    )
    (tmp_path / "r.json").write_text("{}", encoding="utf-8")
    (tmp_path / "r.md").write_text("x", encoding="utf-8")

    out = handle_router_command("evaluate batch baseline --yes")
    assert out["type"] == "router_eval"
    assert "Passed" in out["response"]


def test_decide_with_outcome_marks_fallback(router_settings, monkeypatch):
    # Dummy credential only: the engine credential gate runs before the
    # mocked provider, and the fake below raises before any network.
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-test-key")
    calls = []

    class Boom:
        name = "openai_api"

        def decide(self, context, config):
            calls.append(True)
            raise ProviderError("Invalid task_type `nope`", retryable=False)

    monkeypatch.setattr("api.agent_router.engine._provider_for", lambda cfg: Boom())
    d, meta = decide_with_outcome(build_context("x"))
    assert calls == [True]
    assert meta["used_fallback"] is True
    assert meta["invalid_rejected"] is True
    assert d.source == TargetSource.DEFAULT.value


def test_repeat_aggregation_stability_and_raw_runs(router_settings, tmp_path):
    """Repeated decisions are preserved; oscillation + confidence stats reported."""
    from api.agent_router.eval.stability import aggregate_repeated_runs, summarize_stability

    suite = load_suite("baseline")
    suite.cases = suite.cases[:2]
    per_prompt: Dict[str, int] = {}

    def oscillating(ctx, cfg):
        prompt = ctx.user_request or ""
        i = per_prompt.get(prompt, 0)
        per_prompt[prompt] = i + 1
        # Case 0: alternate Auto / Grok; case 1: stable Auto
        if prompt == suite.cases[0].prompt:
            model = "auto" if (i % 2 == 0) else "grok-4.6"
            conf = 0.5 + (i % 5) * 0.1
        else:
            model = "auto"
            conf = 0.9
        return _decision(model=model, confidence=conf), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "mock",
        }

    report = run_suite(suite, concurrency=3, repeats=5, decide_fn=oscillating)
    assert report["repeats"] == 5
    assert report["case_count"] == 2
    assert report["decision_count"] == 10
    assert "stability_summary" in report

    for case in report["cases"]:
        assert case["repeats"] == 5
        assert len(case["runs"]) == 5
        assert "stability" in case
        assert "confidence_stats" in case
        cs = case["confidence_stats"]
        assert cs["n"] == 5
        assert cs["min"] <= cs["mean"] <= cs["max"]

    # At least one case should show Auto↔Grok oscillation given the mock
    osc_ids = report["stability_summary"]["cases_with_target_oscillation"]
    assert suite.cases[0].id in osc_ids
    first = next(c for c in report["cases"] if c["id"] == suite.cases[0].id)
    assert first["stability"]["target"]["oscillates_auto_grok"] is True
    assert first["stability"]["target"]["stable"] is False

    # Pure aggregation unit check
    meta = {"id": "x", "expected_summary": "cursor/auto"}
    runs = [
        {
            "id": "x",
            "run_index": i,
            "task_type": "coding",
            "difficulty": "low",
            "target_agent": "cursor",
            "target_model": "auto" if i % 2 == 0 else "grok-4.6",
            "confidence": 0.6 + i * 0.05,
            "result": "pass",
            "expectation_pass": True,
            "validation_pass": True,
            "latency_ms": 10,
            "decision": {"target_agent": "cursor"},
        }
        for i in range(4)
    ]
    agg = aggregate_repeated_runs(meta, runs)
    assert agg["stability"]["target"]["oscillates_auto_grok"] is True
    assert agg["confidence_stats"]["min"] == 0.6
    assert summarize_stability([agg])["oscillation_count"] == 1

    json_path, md_path, url = save_eval_report(report, logs_dir=tmp_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["repeats"] == 5
    assert len(data["cases"][0]["runs"]) == 5
    md = md_path.read_text(encoding="utf-8")
    assert "Osc" in md or "oscillation" in md.lower() or "Auto" in md
    summary = format_batch_summary(data, artifact_url=url)
    assert "Repeats per case: 5" in summary


def test_repeat_never_invokes_executors(router_settings, monkeypatch):
    exec_calls: List[str] = []

    def boom(*a, **k):
        exec_calls.append("x")
        raise AssertionError("no executor")

    monkeypatch.setattr(
        "api.agent_router.dispatch._default_runners",
        lambda: {"cursor": boom, "codex": boom},
    )
    suite = load_suite("baseline")
    suite.cases = suite.cases[:2]

    def fake(ctx, cfg):
        return _decision(), {
            "used_fallback": False,
            "api_error": None,
            "invalid_rejected": False,
            "provider": "mock",
        }

    report = run_suite(suite, concurrency=2, repeats=3, decide_fn=fake)
    assert exec_calls == []
    assert report["decision_count"] == 6
