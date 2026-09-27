"""Dashboards catalog + DeepSWE normalize/cache/NEW flags."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

from api.dashboards import catalog, deepswe, service
from api.dashboards.cli import main as cli_main
from api.dashboards.routes import dashboards_bp


SAMPLE = {
    "generated_at": "2026-09-22T06:27:15+00:00",
    "n_tasks_in_set": 113,
    "rows": [
        {
            "model": "gpt-6-astra",
            "harness": "mini-swe-agent",
            "provider": "openai",
            "reasoning_effort": "xhigh",
            "config": "mini_swe_agent_gpt_6_astra_xhigh",
            "pass_at_1": 0.74115,
            "mean_cost_usd": 4.4291,
            "mean_duration_seconds": 1132.4,
            "mean_output_tokens": 29557,
            "mean_input_tokens": 1456927,
            "mean_agent_steps": 28.75,
            "ci_lo": 0.7125,
            "ci_hi": 0.7698,
        },
        {
            "model": "gpt-6-astra",
            "harness": "mini-swe-agent",
            "provider": "openai",
            "reasoning_effort": "medium",
            "config": "mini_swe_agent_gpt_6_astra_medium",
            "pass_at_1": 0.728,
            "mean_cost_usd": 3.08,
            "mean_duration_seconds": 884.0,
            "mean_agent_steps": 22.0,
        },
    ],
}

DISCOVERY = {
    "data": {
        "window_days": 7,
        "updates": [],
        "latest_available": [
            {
                "benchmark_id": "senior_swe_bench",
                "name": "Senior SWE-Bench",
                "latest_result_at": "2026-09-07T00:00:00.000Z",
                "urls": {"page": "https://benchmarklist.com/benchmarks/senior_swe_bench/"},
            }
        ],
    }
}


def _fetcher(url: str):
    if "deepswe" in url:
        return SAMPLE
    if "benchmarklist" in url:
        return DISCOVERY
    raise AssertionError(url)


def test_catalog_includes_model_benchmarks():
    ids = {d["id"] for d in catalog.list_dashboards()}
    assert "model-benchmarks" in ids
    assert catalog.get_dashboard("nope") is None


def test_normalize_and_new_flags(tmp_path: Path):
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    board = deepswe.fetch_and_cache(
        cache_dir=tmp_path,
        force=True,
        fetcher=_fetcher,
        now=now,
    )
    assert len(board["rows"]) == 2
    row = board["rows"][0]
    assert row["label"] == "gpt-6-astra [xhigh]"
    assert abs(row["score"] - 74.115) < 0.001
    assert row["is_new"] is False
    assert board["new_count"] == 0

    later = now + timedelta(hours=1)
    extra = {
        **SAMPLE,
        "rows": SAMPLE["rows"] + [{
            "model": "mimo-v2-7-flash",
            "harness": "mini-swe-agent",
            "provider": "xiaomi",
            "reasoning_effort": "high",
            "config": "mini_swe_agent_mimo_flash",
            "pass_at_1": 0.70,
            "mean_cost_usd": 1.2,
            "mean_duration_seconds": 400.0,
        }],
    }

    def fetcher2(url: str):
        if "deepswe" in url:
            return extra
        return _fetcher(url)

    board2 = deepswe.fetch_and_cache(
        cache_dir=tmp_path, force=True, fetcher=fetcher2, now=later
    )
    by_id = {r["id"]: r for r in board2["rows"]}
    assert by_id["mini_swe_agent_gpt_6_astra_xhigh"]["is_new"] is False
    assert by_id["mini_swe_agent_mimo_flash"]["is_new"] is True
    assert board2["new_count"] == 1


def test_infer_provider_and_effort_order():
    assert deepswe.infer_provider("gemini-3-8-flash", "") == "google"
    assert deepswe.infer_provider("claude-opus-5", "") == "anthropic"
    assert deepswe.infer_provider("gpt-5-6-sol", "") == "openai"
    assert deepswe.infer_provider("gpt-6-astra", "openai") == "openai"
    assert deepswe.sort_efforts(["xhigh", "low", "max", "medium", "high", "unspecified"]) == [
        "unspecified", "low", "medium", "high", "xhigh", "max",
    ]


def test_stale_cache_on_fetch_error(tmp_path: Path):
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    deepswe.fetch_and_cache(cache_dir=tmp_path, force=True, fetcher=_fetcher, now=now)

    def boom(_url: str):
        raise RuntimeError("offline")

    stale_now = now + timedelta(hours=8)
    out = deepswe.fetch_and_cache(
        cache_dir=tmp_path, force=True, fetcher=boom, now=stale_now
    )
    assert out["stale"] is True
    assert out["rows"]


def test_service_payload(tmp_path: Path):
    payload = service.model_benchmarks(
        cache_dir=tmp_path, force=True, fetcher=_fetcher
    )
    assert payload["success"] is True
    assert payload["stats"]["configs"] == 2
    assert payload["discovery"]["ok"] is True
    assert payload["discovery"]["latest"][0]["benchmark_id"] == "senior_swe_bench"


def test_aggregate_metrics_use_best_score_and_leave_single_source_spread_empty():
    from api.dashboards.integrations import aggregate_rows

    rows = aggregate_rows([
        {"rows": [
            {"model": "Example Model", "score": 80, "mean_cost_usd": 4,
             "mean_duration_seconds": None, "source_name": "Board A", "provider": "openai"},
            {"model": "Example Model", "score": 85, "mean_cost_usd": 6,
             "mean_duration_seconds": None, "source_name": "Board A", "provider": "openai"},
            {"model": "Solo Model", "score": 50, "mean_cost_usd": 1,
             "mean_duration_seconds": 10, "source_name": "Board A", "provider": "openai"},
        ]},
        {"rows": [
            {"model": "example-model", "score": 65, "mean_cost_usd": 2,
             "mean_duration_seconds": 30, "source_name": "Board B", "provider": "openai"},
        ]},
    ])
    by_model = {row["label"]: row for row in rows}
    combined = by_model["Example Model"]
    assert combined["benchmark_count"] == 2
    assert combined["score"] == 75
    assert combined["score_spread"] == 10
    assert combined["mean_cost_usd"] == 4
    assert combined["mean_duration_seconds"] == 30
    assert by_model["Solo Model"]["benchmark_count"] == 1
    assert by_model["Solo Model"]["score_spread"] is None


def test_flask_routes(tmp_path: Path, monkeypatch):
    from api.dashboards import benchmarklist as bl

    monkeypatch.setattr(deepswe, "default_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(deepswe, "get_json", _fetcher)
    monkeypatch.setattr(bl, "get_json", _fetcher)
    app = Flask(__name__)
    app.register_blueprint(dashboards_bp)
    client = app.test_client()
    hub = client.get("/api/dashboards").get_json()
    assert hub["dashboards"][0]["id"] == "model-benchmarks"
    missing = client.get("/api/dashboards/nope")
    assert missing.status_code == 404
    body = client.get("/api/dashboards/model-benchmarks").get_json()
    assert body["stats"]["configs"] == 2
    assert body["rows"][0]["model"] == "gpt-6-astra"


def test_cli_list(capsys):
    assert cli_main(["list"]) == 0
    out = capsys.readouterr().out
    assert "model-benchmarks" in out
    assert "cuttle-performance" in out
