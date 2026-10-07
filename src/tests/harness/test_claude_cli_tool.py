"""Unit tests for modern Claude Code CLI tool + session store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.utilities import claude_cli_session_store as store
from scripts.utilities.claude_cli_tool import (
    ClaudeCliTool,
    ClaudeCodeTool,
    _parse_claude_json,
    claude_executable,
)


def test_parse_claude_json_result_and_usage():
    raw = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": "Hello from Claude",
            "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "usage": {"input_tokens": 1200, "output_tokens": 40},
            "total_cost_usd": 0.0123,
        }
    )
    parsed = _parse_claude_json(raw)
    assert parsed["output"] == "Hello from Claude"
    assert parsed["session_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert parsed["usage"]["prompt_tokens"] == 1200
    assert parsed["usage"]["completion_tokens"] == 40
    assert parsed["usage"]["context_tokens"] == 1200
    assert parsed["usage"]["cost"] == pytest.approx(0.0123)
    assert parsed["errors"] == []


def test_parse_claude_json_error_subtype():
    raw = json.dumps(
        {
            "subtype": "error",
            "error": "rate limited",
            "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        }
    )
    parsed = _parse_claude_json(raw)
    assert "rate limited" in parsed["errors"][0]


def test_claude_cost_prices_cache_per_concrete_model(monkeypatch):
    from api import model_pricing
    from scripts.utilities.claude_cli_tool import usage_for_query_report, _merge_results

    rates = {"opus-concrete": {"input": 4, "output": 20, "cache_read": .2, "cache_write": 5},
             "haiku-concrete": {"input": 1, "output": 5, "cache_read": .1, "cache_write": 1.25}}
    monkeypatch.setattr(model_pricing, "lookup_model_rates", rates.get)
    result = {
        "total_cost_usd": 83.985179,
        "modelUsage": {
            "opus-concrete": {"inputTokens": 48, "outputTokens": 16644,
                              "cacheReadInputTokens": 20323108, "cacheCreationInputTokens": 28271},
            "haiku-concrete": {"inputTokens": 1000000},
        },
    }
    parsed = _parse_claude_json(json.dumps(result))
    usage = parsed["usage"]
    assert usage["cost"] == pytest.approx(5.539049)
    assert usage["reported_cost"] == pytest.approx(83.985179)
    assert usage["cost_estimated"] is True
    report = usage_for_query_report(usage, "opus")
    display = model_pricing.enrich_usage_for_display(report, model="opus")
    assert display["cost_estimated"] is True
    assert display["reported_cost"] == usage["reported_cost"]
    merged = _merge_results([parsed, parsed])["usage"]
    assert merged["cost"] == pytest.approx(2 * usage["cost"])
    assert merged["reported_cost"] == pytest.approx(2 * usage["reported_cost"])


@pytest.mark.parametrize("row", [{"inputTokens": 10}, {"inputTokens": "invalid"}, None])
def test_claude_cost_keeps_reported_total_if_any_model_unpriceable(monkeypatch, row):
    from api import model_pricing
    monkeypatch.setattr(model_pricing, "lookup_model_rates", lambda model: None)
    usage = _parse_claude_json(json.dumps({
        "total_cost_usd": 12.5, "modelUsage": {"unknown": row},
    }))["usage"]
    assert usage["cost"] == 12.5
    assert not usage.get("cost_estimated")


def test_parse_claude_terminal_errors_and_empty_output():
    empty = _parse_claude_json(json.dumps({"type": "result", "result": ""}))
    assert empty["output"] == ""
    failed = _parse_claude_json(json.dumps({
        "type": "result", "subtype": "error_max_turns", "errors": ["Turn limit reached"],
    }))
    assert failed["errors"] == ["Turn limit reached"]
    assert failed["output"] == ""


def test_session_store_roundtrip(tmp_path, monkeypatch):
    map_file = tmp_path / "claude_cli_session_map.json"
    monkeypatch.setattr(store, "_map_file", lambda: map_file)

    cwd = str(tmp_path)
    assert store.load_claude_resume_id(cwd, "chat-1") is None
    store.save_claude_resume_id(
        cwd, "chat-1", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )
    assert (
        store.load_claude_resume_id(cwd, "chat-1")
        == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )
    store.save_claude_model("chat-1", "sonnet")
    assert store.load_claude_model("chat-1") == "sonnet"
    store.clear_claude_resume_id(cwd, "chat-1")
    assert store.load_claude_resume_id(cwd, "chat-1") is None


def test_compat_resolve_project_path():
    tool = ClaudeCodeTool(session_id="s1", model="haiku")
    unit_dir = str(Path(__file__).resolve().parent)
    resolved = tool._resolve_project_path(unit_dir, None)
    assert Path(resolved).exists()
    assert Path(resolved).is_absolute()


def test_claude_cli_tool_available_is_bool():
    assert isinstance(ClaudeCliTool().available, bool)
    # claude_executable may or may not find a binary; both are fine offline.
    exe = claude_executable()
    assert exe is None or isinstance(exe, str)


def test_execute_prompt_missing_cli(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        "scripts.utilities.claude_cli_tool.claude_executable", lambda: None
    )
    tool = ClaudeCliTool()

    async def _run():
        return await tool.execute_prompt(
            "hi", cwd=str(Path(__file__).resolve().parent)
        )

    result = asyncio.run(_run())
    assert result["success"] is False
    assert "not found" in (result.get("error") or "").lower()
