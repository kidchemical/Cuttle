"""Tests for models.dev pricing cache + usage enrichment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import model_pricing as mp


@pytest.fixture()
def pricing_cache(tmp_path, monkeypatch):
    cache = tmp_path / "models_dev_pricing_cache.json"
    monkeypatch.setattr(mp, "_cache_path", lambda: cache)
    monkeypatch.setattr(mp, "_index", {})
    monkeypatch.setattr(mp, "_context_index", {})
    monkeypatch.setattr(mp, "_index_loaded_at", 0.0)

    entries = [
        {
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "input": 3.0,
            "output": 15.0,
            "cache_read": 0.3,
            "cache_write": 3.75,
        },
        {
            "provider": "openai",
            "model_id": "gpt-5.6-sol",
            "name": "GPT-5.6 Sol",
            "input": 4.0,
            "output": 20.0,
            "cache_read": 0.4,
            "cache_write": 5.0,
        },
        {
            "provider": "meta",
            "model_id": "muse-spark-1.3",
            "name": "Muse Spark 1.3",
            "input": 1.25,
            "output": 4.25,
        },
        {
            "provider": "bothub",
            "model_id": "muse-spark-1.3",
            "name": "Muse Spark 1.3 (bothub)",
            "input": 9.0,
            "output": 9.0,
        },
    ]
    cache.write_text(
        json.dumps(
            {
                "version": mp._CACHE_VERSION,
                "fetched_at": 1_700_000_000.0,
                "source": "test",
                "count": len(entries),
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    # Avoid background network during tests.
    monkeypatch.setattr(mp, "_schedule_background_refresh", lambda **_kwargs: None)
    return cache


def test_lookup_prefers_canonical_provider(pricing_cache):
    rates = mp.lookup_model_rates("muse-spark-1.3")
    assert rates is not None
    assert rates["provider"] == "meta"
    assert rates["input"] == 1.25


def test_estimate_cost_usd(pricing_cache):
    # 1M in + 1M out → 3 + 15 = 18
    cost = mp.estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert cost == pytest.approx(18.0)


def test_estimate_cost_usd_inclusive_cache(pricing_cache):
    # Codex/OpenAI style: cached is subset of prompt.
    # 100k uncached @ $4 + 900k cached @ $0.40 + 10k out @ $20
    cost = mp.estimate_cost_usd(
        "gpt-5.6-sol",
        1_000_000,
        10_000,
        cache_read_tokens=900_000,
    )
    assert cost == pytest.approx(0.4 + 0.36 + 0.2)


def test_estimate_cost_usd_exclusive_cache(pricing_cache):
    # Anthropic/Cursor style: prompt is uncached; cache billed separately.
    # 100k @ $3 + 900k cache_read @ $0.30 + 10k out @ $15
    cost = mp.estimate_cost_usd(
        "claude-sonnet-4-6",
        100_000,
        10_000,
        cache_read_tokens=900_000,
    )
    assert cost == pytest.approx(0.3 + 0.27 + 0.15)


def test_enrich_includes_cache_and_adjusts_cost(pricing_cache):
    out = mp.enrich_usage_for_display(
        {
            "prompt_tokens": 2_860_849,
            "completion_tokens": 23_455,
            "cached_input_tokens": 2_732_928,
        },
        model="gpt-5.6-sol",
    )
    assert out is not None
    assert out["cache_read_tokens"] == 2_732_928
    assert out["cost_estimated"] is True
    # Inclusive: (2860849-2732928)*4/1e6 + 2732928*0.4/1e6 + 23455*20/1e6
    assert out["cost"] == pytest.approx(2.074, abs=0.01)
    naive = mp.estimate_cost_usd("gpt-5.6-sol", 2_860_849, 23_455)
    assert naive == pytest.approx(11.912, abs=0.01)
    assert out["cost"] < naive * 0.3


def test_skip_auto_model(pricing_cache):
    assert mp.lookup_model_rates("auto") is None
    assert mp.estimate_cost_usd("Auto", 1000, 100) is None


def test_enrich_prefers_cli_cost(pricing_cache):
    out = mp.enrich_usage_for_display(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cost": 0.42,
        },
        model="claude-sonnet-4-6",
    )
    assert out is not None
    assert out["cost"] == 0.42
    assert out["cost_estimated"] is False


def test_enrich_estimates_when_missing(pricing_cache):
    out = mp.enrich_usage_for_display(
        {"input_tokens": 1_000_000, "output_tokens": 0},
        model="claude-sonnet-4-6",
    )
    assert out is not None
    assert out["cost"] == pytest.approx(3.0)
    assert out["cost_estimated"] is True


def test_enrich_cursor_token_keys(pricing_cache):
    out = mp.enrich_usage_for_display(
        {"inputTokens": 10, "outputTokens": 5},
        model="auto",
    )
    assert out is not None
    assert out["prompt_tokens"] == 10
    assert out["completion_tokens"] == 5
    assert "cost" not in out


def test_usage_meta_from_assistant_result(pricing_cache):
    from api.web_chat_api import _usage_meta_from_assistant_result

    meta = _usage_meta_from_assistant_result(
        {
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            "agent_model": "claude-sonnet-4-6",
        }
    )
    assert meta is not None
    assert meta["prompt_tokens"] == 1000
    assert meta["completion_tokens"] == 200
    assert meta["cost_estimated"] is True
    assert meta["cost"] > 0


def test_usage_meta_from_cursor_run(pricing_cache):
    from api.web_chat_api import _usage_meta_from_assistant_result

    meta = _usage_meta_from_assistant_result(
        {
            "cursor_run": {
                "reported_model": "auto",
                "usage": {"inputTokens": 1200, "outputTokens": 340},
            }
        }
    )
    assert meta is not None
    assert meta["prompt_tokens"] == 1200
    assert meta["completion_tokens"] == 340
    assert "cost" not in meta


def test_parse_catalog_from_fixture():
    fixture = Path(__file__).resolve().parents[2] / "temp" / "models_dev_api.json"
    if not fixture.is_file():
        pytest.skip("models.dev fixture not present")
    catalog = json.loads(fixture.read_text(encoding="utf-8-sig"))
    entries = mp._parse_catalog(catalog)
    assert len(entries) > 100
    assert any(e["model_id"] == "claude-sonnet-4-6" for e in entries)
