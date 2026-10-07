"""Muse MSP context reader + agent_context integration."""

from __future__ import annotations

import json

from api import agent_context as ac
from scripts.utilities import muse_cli_session_store as store


def test_read_muse_msp_context_from_offline_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "muse_msp_view_dir", lambda _sid: tmp_path)
    (tmp_path / "snapshot-1.json").write_text(json.dumps({
        "continuation": {
            "state": {"context_anchor": 27333},
            "view_materialization": {"current_state": {"tokenUsage": {
                "totalTokens": 27333,
                "usage": {"inputTokens": 27158, "outputTokens": 175},
            }}},
        },
    }))
    ctx = store.read_muse_msp_context("offline-session")
    assert ctx is not None
    assert ctx["context_tokens"] == 27333
    assert ctx["source"] == "msp_view"


def test_muse_msp_missing_anchor_does_not_count_cache_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "muse_msp_view_dir", lambda _sid: tmp_path)
    (tmp_path / "snapshot-1.json").write_text(json.dumps({
        "continuation": {"state": {}, "view_materialization": {
            "current_state": {"tokenUsage": {
                "usage": {"inputTokens": 10000, "cachedTokens": 9000},
            }},
        }},
    }))
    ctx = store.read_muse_msp_context("offline-session")
    assert ctx["context_tokens"] == 10000


def test_muse_msp_zero_anchor_uses_native_total(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "muse_msp_view_dir", lambda _sid: tmp_path)
    (tmp_path / "snapshot-1.json").write_text(json.dumps({
        "continuation": {"state": {"context_anchor": 0}, "view_materialization": {
            "current_state": {"tokenUsage": {
                "totalTokens": 10100,
                "usage": {"inputTokens": 10000, "outputTokens": 100, "cachedTokens": 9000},
            }},
        }},
    }))
    assert store.read_muse_msp_context("offline-session")["context_tokens"] == 10100


def test_get_agent_context_status_uses_muse_msp(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(
        ac,
        "_load_resume_id",
        lambda *_a, **_k: "01a0c7d5-b94b-7ad2-9a91-994010a9e6d5",
    )
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.read_muse_msp_context",
        lambda _sid: {
            "context_tokens": 27333,
            "prompt_tokens": 27158,
            "completion_tokens": 175,
            "total_tokens": 27333,
            "model": "muse-spark-1.3-contributor",
            "source": "msp_view",
        },
    )
    status = ac.get_agent_context_status(
        chat_session_id=493,
        agent_id="muse",
        cwd="C:/Projects/Cuttle",
        messages=[{
            "role": "assistant",
            "metadata": {"usage": {
                "context_tokens": 800_000,
                "prompt_tokens": 3_000_000,
                "cache_read_tokens": 4_000_000,
            }},
        }],
    )
    assert status["success"] is True
    assert status["used_tokens"] == 27333
    assert status["token_source"] == "msp_view"
    assert status["model"] == "muse-spark-1.3-contributor"
    assert status["percent"] > 0
    assert status["limit_tokens"] == 1_000_000
