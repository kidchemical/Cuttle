"""Tests for agent context fill estimates + compact helpers."""

from __future__ import annotations

import json

import pytest

from api import agent_context as ac
from api import model_pricing as mp


def test_parse_context_limit_hint_brackets_and_labels():
    assert ac.parse_context_limit_hint("claude-opus-4-8[context=1m,effort=high]") == 1_000_000
    assert ac.parse_context_limit_hint("Claude Opus 5 1M Thinking") == 1_000_000
    assert ac.parse_context_limit_hint("something 200k context") == 200_000
    assert ac.parse_context_limit_hint("plain-model") is None


def test_context_percent_caps_at_100():
    assert ac.context_percent(50_000, 200_000) == 25.0
    assert ac.context_percent(250_000, 200_000) == 100.0
    assert ac.context_percent(0, 200_000) == 0.0
    assert ac.context_percent(10, 0) == 0.0


def test_normalize_agent_id():
    assert ac.normalize_agent_id("/cursor") == "cursor"
    assert ac.normalize_agent_id("Muse") == "muse"
    assert ac.normalize_agent_id("opencode ") == "opencode"
    assert ac.normalize_agent_id("codex") == "codex"
    assert ac.normalize_agent_id("/codex ") == "codex"
    assert ac.normalize_agent_id("hermes") == "hermes"
    assert ac.normalize_agent_id("antigravity") == "antigravity"
    assert ac.normalize_agent_id("claude") == "claude"
    assert ac.normalize_agent_id("deepseek") == "deepseek"
    assert ac.normalize_agent_id("gemini") is None


def test_resolve_context_limit_from_model_id(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)

    def _no_models():
        return []

    monkeypatch.setattr(
        "api.cursor_agent_commands.list_cursor_agent_models",
        _no_models,
        raising=False,
    )
    limit, source = ac.resolve_context_limit("cursor", "composer-2.5")
    assert limit == 1_000_000
    assert source == "agent_default"

    limit2, source2 = ac.resolve_context_limit(
        "cursor", "claude-sonnet-5-thinking-high[context=1m]"
    )
    assert limit2 == 1_000_000
    assert source2 == "model_id"

    limit3, source3 = ac.resolve_context_limit("muse", "muse-spark-1.3")
    assert limit3 == 1_000_000
    assert source3 == "agent_default"



def test_lookup_model_context_limit(pricing_cache_with_context):
    assert mp.lookup_model_context_limit("claude-sonnet-4-6") == 200_000
    assert mp.lookup_model_context_limit("missing-model-xyz") is None


def test_last_usage_from_messages_prefers_assistant_prompt_tokens():
    messages = [
        {"id": 1, "role": "user", "content": "hi", "metadata": {}},
        {
            "id": 2,
            "role": "assistant",
            "content": "ok",
            "metadata": {
                "slash_command": {"chips": [{"prefix": "/cursor "}]},
                "usage": {
                    "prompt_tokens": 12000,
                    "completion_tokens": 40,
                    "model": "auto",
                    "context_tokens": 18000,
                },
            },
        },
    ]
    tokens, model, usage, source = ac._last_usage_from_messages(
        list(reversed(messages)), agent_id="cursor"
    )
    assert tokens == 18000
    assert source == "peak"
    assert model == "auto"
    assert usage["prompt_tokens"] == 12000


def test_cursor_aggregated_usage_not_treated_as_full_window():
    usage = {
        "inputTokens": 209952,
        "outputTokens": 36539,
        "cacheReadTokens": 4127872,
        "cacheWriteTokens": 0,
    }
    tokens, source = ac._usage_context_fill_tokens(usage, agent_id="cursor")
    assert tokens == 0
    assert source == "aggregated"


def test_cursor_stamped_peak_from_cache_sum_distrusted():
    """CH-000496-style: peak stamped as inn+cacheRead must not show as 1M fill."""
    usage = {
        "inputTokens": 109965,
        "outputTokens": 15109,
        "cacheReadTokens": 921344,
        "cacheWriteTokens": 0,
        "context_tokens": 1031309,
        "peak_context_tokens": 1031309,
        "prompt_tokens": 109965,
    }
    tokens, source = ac._usage_context_fill_tokens(usage, agent_id="cursor")
    assert tokens == 0
    assert source == "aggregated"


def test_get_agent_context_status_from_messages(monkeypatch):
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.read_muse_msp_context",
        lambda _sid: None,
    )
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 200_000)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "resume-uuid")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "auto")
    msgs = [
        {
            "id": 2,
            "role": "assistant",
            "content": "ok",
            "metadata": {
                "slash_command": {"chips": [{"prefix": "/muse "}]},
                "usage": {"context_tokens": 100_000, "prompt_tokens": 300_000, "completion_tokens": 10},
            },
        }
    ]
    status = ac.get_agent_context_status(
        chat_session_id=1,
        agent_id="muse",
        cwd="C:/Projects/Cuttle",
        messages=msgs,
    )
    assert status["success"] is True
    assert status["used_tokens"] == 100_000
    assert status["limit_tokens"] == 200_000
    assert status["percent"] == 50.0
    assert status["has_resume"] is True
    assert status["compact_available"] is True


def test_get_agent_context_status_rejects_cursor_aggregate(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: "resume-uuid")
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "Auto")
    monkeypatch.setattr(ac, "_cursor_recent_usage", lambda *_a, **_k: (0, "Auto", "aggregated"))
    msgs = [
        {
            "id": 2,
            "role": "assistant",
            "content": "ok",
            "metadata": {
                "slash_command": {"chips": [{"prefix": "/cursor "}]},
                "usage": {
                    "prompt_tokens": 209952,
                    "completion_tokens": 36539,
                    "model": "Auto",
                },
                "cursor_run": {
                    "reported_model": "Auto",
                    "usage": {
                        "inputTokens": 209952,
                        "outputTokens": 36539,
                        "cacheReadTokens": 4127872,
                        "cacheWriteTokens": 0,
                    },
                },
            },
        }
    ]
    status = ac.get_agent_context_status(
        chat_session_id=489,
        agent_id="cursor",
        cwd="C:/Projects/Cuttle",
        messages=msgs,
    )
    assert status["success"] is True
    assert status["used_tokens"] == 0
    assert status["percent"] == 0.0
    assert status["limit_tokens"] == 1_000_000
    assert status["token_source"] == "aggregated"
    assert "multi-step" in (status.get("hint") or "").lower()


@pytest.fixture()
def pricing_cache_with_context(tmp_path, monkeypatch):
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
            "context": 200_000,
        },
    ]
    cache.write_text(
        json.dumps(
            {
                "version": mp._CACHE_VERSION,
                "fetched_at": 9_999_999_999.0,
                "source": "test",
                "count": len(entries),
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    mp.ensure_models_dev_pricing(refresh_if_stale=False)
    return cache


@pytest.mark.parametrize("agent_id", ["cursor", "codex", "claude", "hermes", "antigravity", "muse"])
@pytest.mark.parametrize("stamp", ["context_tokens", "peak_context_tokens", "contextTokens"])
@pytest.mark.parametrize("cache_key", ["cacheReadTokens", "cache_read_tokens", "cached_input_tokens"])
def test_explicit_context_snapshot_survives_large_cache_bill(agent_id, stamp, cache_key):
    usage = {"prompt_tokens": 209952, cache_key: 4127872, stamp: 50000}
    assert ac._usage_context_fill_tokens(usage, agent_id=agent_id) == (50000, "peak")


@pytest.mark.parametrize("stamp", ["context_tokens", "peak_context_tokens", "contextTokens"])
def test_cursor_aggregate_sum_stamp_aliases_rejected(stamp):
    usage = {"prompt_tokens": 109965, "cached_input_tokens": 921344, stamp: 1031309}
    assert ac._usage_context_fill_tokens(usage, agent_id="cursor") == (0, "aggregated")
