"""Muse per-turn usage from session logs (model_completed diff) + cost estimate.

``muse exec --json`` no longer emits usage on stdout and the MSP view has no
cost, so the adapter diffs the session log's ``model_completed`` events and
estimates USD when the CLI did not report a cost.
"""

from __future__ import annotations

import asyncio
import json

from scripts.utilities import muse_cli_session_store as store
from scripts.utilities.muse_serve_turn import _usage_from_turn

SID = "01a9f00d-0000-7000-8000-000000000001"


def _write_session_log(root, events):
    day_dir = root / "sessions" / "2026" / "09" / "28" / SID
    (day_dir / "subagent" / "aa").mkdir(parents=True, exist_ok=True)
    (day_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events["main"]) + "\n", encoding="utf-8"
    )
    (day_dir / "subagent" / "aa" / "session.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events["sub"]) + "\n", encoding="utf-8"
    )


def _completed(input_tokens, output_tokens, cached=0, cache_read=0, cache_write=0, reasoning=0):
    return {
        "payload": {
            "event": {
                "kind": "model_completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": cached,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "reasoning_tokens": reasoning,
                },
            }
        }
    }


def _noise():
    return {"payload": {"event": {"kind": "resource_usage_sampled", "usage": {"rss_self_bytes": 1}}}}


def _muse_data_home(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "muse_data_home", lambda: tmp_path)
    return tmp_path


def test_read_muse_session_usage_aggregates_main_and_subagents(tmp_path, monkeypatch):
    _muse_data_home(monkeypatch, tmp_path)
    _write_session_log(
        tmp_path,
        {
            "main": [_noise(), _completed(28668, 139, cached=11505, cache_read=11505, reasoning=16)],
            "sub": [_completed(1000, 200)],
        },
    )
    usage = store.read_muse_session_usage(SID)
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 29668
    assert usage["output_tokens"] == 339
    assert usage["cache_read_tokens"] == 11505
    assert usage["cache_write_tokens"] == 0
    assert usage["reasoning_tokens"] == 16


def test_read_muse_session_usage_baseline_diff_is_one_turn(tmp_path, monkeypatch):
    _muse_data_home(monkeypatch, tmp_path)
    _write_session_log(
        tmp_path,
        {"main": [_completed(5000, 300)], "sub": []},
    )
    baseline = store.read_muse_session_usage(SID)
    assert baseline["calls"] == 1

    # A resumed turn appends two more model calls to the same session log.
    log = tmp_path / "sessions" / "2026" / "09" / "28" / SID / "session.jsonl"
    extra = [_completed(12000, 400, cache_read=11000)]
    with open(log, "a", encoding="utf-8") as f:
        for e in extra:
            f.write(json.dumps(e) + "\n")

    turn = store.read_muse_session_usage(SID, baseline=baseline)
    assert turn["calls"] == 1
    assert turn["input_tokens"] == 12000
    assert turn["output_tokens"] == 400
    assert turn["cache_read_tokens"] == 11000


def test_read_muse_session_usage_missing_log_is_empty(tmp_path, monkeypatch):
    _muse_data_home(monkeypatch, tmp_path)
    assert store.read_muse_session_usage(SID) == {}
    assert store.read_muse_session_usage(None) == {}
    assert store.read_muse_session_usage("not-a-uuid") == {}


def test_usage_from_turn_maps_cost_and_cache_splits():
    out = _usage_from_turn(
        {
            "inputTokens": 1000,
            "outputTokens": 50,
            "cacheReadTokens": 900,
            "cacheWriteTokens": 10,
            "reasoningTokens": 20,
        },
        prompt_tokens=1234,
    )
    assert out["input_tokens"] == 1000
    assert out["output_tokens"] == 50
    assert out["total_tokens"] == 1050
    assert out["cache_read_tokens"] == 900
    assert out["cache_write_tokens"] == 10
    assert out["reasoning_tokens"] == 20
    assert out["context_tokens"] == 1234
    assert "cost" not in out

    assert _usage_from_turn({"costUsd": 0.25}, 0)["cost"] == 0.25
    assert _usage_from_turn({"cost": 0.5}, 0)["cost"] == 0.5
    assert _usage_from_turn({"costMicros": 1500}, 0)["cost"] == 0.0015


def _patch_cli_tool(monkeypatch, raw, sid):
    from scripts.utilities import muse_cli_tool as tool

    class _FakeTool:
        provider = None
        yolo = False

        def __init__(self, *args, **kwargs):
            self.model = kwargs.get("model")

        async def execute_prompt(self, *args, **kwargs):
            return dict(raw)

    monkeypatch.setattr(tool, "MuseCliTool", _FakeTool)
    monkeypatch.setattr(tool, "resolve_muse_default_model", lambda: "muse-spark-1.3-contributor")
    monkeypatch.setattr("api.agent_harness.steer.steer_enabled", lambda _a: False)


def test_adapter_estimates_cost_when_cli_omits_usage(tmp_path, monkeypatch):
    _muse_data_home(monkeypatch, tmp_path)
    _write_session_log(
        tmp_path,
        {"main": [_completed(50000, 500, cache_read=40000, cache_write=1000)], "sub": []},
    )
    _patch_cli_tool(
        monkeypatch,
        {"success": True, "output": "done", "usage": {}, "muse_session_id": SID},
        SID,
    )
    monkeypatch.setattr(
        "api.model_pricing.estimate_cost_usd",
        lambda model, pt, ct, cache_read_tokens=0, cache_write_tokens=0, cache_inclusive=None: 0.0042,
    )

    from api.agent_harness.agents.muse.adapter import build_adapter

    result = asyncio.run(
        build_adapter().execute(
            "task",
            cwd=str(tmp_path),
            resume=None,
            model="muse-spark-1.3-contributor",
            chat_session_id=None,
        )
    )
    assert result.success is True
    assert result.usage["prompt_tokens"] == 50000
    assert result.usage["completion_tokens"] == 500
    assert result.usage["total_tokens"] == 50500
    assert result.usage["cache_read_tokens"] == 40000
    assert result.usage["cache_write_tokens"] == 1000
    assert result.usage["cost"] == 0.0042
    assert result.usage["cost_estimated"] is True


def test_adapter_keeps_cli_reported_cost_over_estimate(tmp_path, monkeypatch):
    _muse_data_home(monkeypatch, tmp_path)
    _write_session_log(
        tmp_path,
        {"main": [_completed(50000, 500, cache_read=40000)], "sub": []},
    )
    _patch_cli_tool(
        monkeypatch,
        {
            "success": True,
            "output": "done",
            "usage": {"input_tokens": 100, "output_tokens": 10, "cost": 0.0019},
            "muse_session_id": SID,
        },
        SID,
    )

    called = []
    monkeypatch.setattr(
        "api.model_pricing.estimate_cost_usd",
        lambda *a, **k: called.append(a) or 9.99,
    )

    from api.agent_harness.agents.muse.adapter import build_adapter

    result = asyncio.run(
        build_adapter().execute(
            "task",
            cwd=str(tmp_path),
            resume=None,
            model="muse-spark-1.3-contributor",
            chat_session_id=None,
        )
    )
    assert result.success is True
    # Session-log diff still wins for tokens; CLI cost wins over the estimate.
    assert result.usage["prompt_tokens"] == 50000
    assert result.usage["cost"] == 0.0019
    assert "cost_estimated" not in result.usage
    assert not called
