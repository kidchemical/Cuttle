"""Hermes / Claude / DeepSeek / Antigravity context-gauge honesty tests."""

from __future__ import annotations

from api import agent_context as ac


def test_hermes_transcript_occupancy(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "deepseek/flash")
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_session_store.resolve_hermes_resume",
        lambda *_a, **_k: ("20260922_193739_f292a8", "C:/Projects/Cuttle"),
    )
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_session_store.load_hermes_context_snapshot",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_session_store.save_hermes_context_snapshot",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_tool.load_hermes_context_occupancy",
        lambda *_a, **_k: {"context_tokens": 3904, "model": "deepseek/flash"},
    )
    msgs = [
        {
            "id": 2,
            "role": "assistant",
            "content": "ok",
            "metadata": {
                "slash_command": {"chips": [{"prefix": "/hermes "}]},
                "usage": {
                    "prompt_tokens": 2_000_000,
                    "cache_read_tokens": 1_800_000,
                    "completion_tokens": 10,
                    "model": "deepseek/flash",
                },
            },
        }
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="hermes", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["token_source"] == "hermes_transcript"
    assert st["used_tokens"] == 3904
    assert st["compact_available"] is True
    assert st["percent"] < 10


def test_deepseek_gauge_one_shot_no_compact(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: None)
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "deepseek-v4-flash")
    msgs = [
        {
            "id": 2,
            "role": "assistant",
            "content": "ok",
            "metadata": {
                "slash_command": {"chips": [{"prefix": "/deepseek "}]},
                "usage": {
                    "prompt_tokens": 12_000,
                    "completion_tokens": 400,
                    "context_tokens": 12_000,
                    "model": "deepseek-v4-flash",
                },
            },
        }
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="deepseek", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["success"] is True
    assert st["supported"] is True
    assert st["used_tokens"] == 12_000
    assert st["compact_available"] is False
    assert "one-shot" in (st.get("hint") or "").lower()


def test_claude_aggregate_distrusted(monkeypatch):
    monkeypatch.setattr(ac, "lookup_catalog_context_limit", lambda _m: 200_000)
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "sonnet")
    monkeypatch.setattr(
        "scripts.utilities.claude_cli_session_store.resolve_claude_resume",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "scripts.utilities.claude_cli_session_store.load_claude_context_snapshot",
        lambda *_a, **_k: None,
    )
    msgs = [
        {
            "id": 2,
            "role": "assistant",
            "content": "ok",
            "metadata": {
                "slash_command": {"chips": [{"prefix": "/claude "}]},
                "usage": {
                    "prompt_tokens": 2_860_849,
                    "cache_read_tokens": 2_732_928,
                    "completion_tokens": 100,
                    "model": "sonnet",
                },
            },
        }
    ]
    st = ac.get_agent_context_status(
        chat_session_id=1, agent_id="claude", cwd="C:/Projects/Cuttle", messages=msgs
    )
    assert st["token_source"] == "aggregated"
    assert st["used_tokens"] == 0
    assert st["percent"] == 0.0
    assert st["compact_available"] is True
