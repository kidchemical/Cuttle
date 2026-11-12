"""Model-catalog refresh deltas — what a refresh added and removed."""

from __future__ import annotations

import json
import subprocess

import pytest

from api import model_catalog_delta as delta
from api.agent_harness.agents.claude import adapter as claude_adapter
from api.agent_harness.agents.claude import model_catalog as cat
from scripts.utilities import claude_cli_session_store as store


def test_record_and_diff_reports_added_and_removed():
    first = delta.record_and_diff(
        "delta-agent", [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    )
    assert first["first"] is True and first["count"] == 2
    assert "First refresh" in delta.render_delta(first)

    second = delta.record_and_diff(
        "delta-agent", [{"id": "a", "label": "A"}, {"id": "c", "label": "C"}]
    )
    assert second["first"] is False
    assert [r["id"] for r in second["added"]] == ["c"]
    assert [r["id"] for r in second["removed"]] == ["b"]
    text = delta.render_delta(second)
    assert "➕ 1 added" in text and "➖ 1 removed" in text
    assert "`c`" in text and "`b`" in text

    third = delta.record_and_diff(
        "delta-agent", [{"id": "a", "label": "A"}, {"id": "c", "label": "C"}]
    )
    assert "No catalog changes" in delta.render_delta(third)


def test_record_and_diff_detects_alias_retarget():
    delta.record_and_diff(
        "alias-agent",
        [{"id": "haiku", "label": "Haiku 4.5", "resolved_model": "claude-haiku-4-5"}],
    )
    moved = delta.record_and_diff(
        "alias-agent",
        [{"id": "haiku", "label": "Haiku 5.5", "resolved_model": "claude-haiku-5-5"}],
    )
    assert moved["added"] == [] and moved["removed"] == []
    assert [c["id"] for c in moved["changed"]] == ["haiku"]
    text = delta.render_delta(moved)
    assert "✎ 1 changed" in text and "`haiku` now points to Haiku 5.5 (was Haiku 4.5)" in text


def test_alias_swap_between_listed_models_is_one_change_not_add_and_remove():
    """``fable`` moves 5.1 → 5.2 and the pinned row flips 5.2 → 5.1: same two models."""
    delta.record_and_diff("swap-agent", [
        {"id": "fable", "label": "Fable 5.1", "resolved_model": "claude-fable-5-1"},
        {"id": "claude-fable-5-2", "label": "Fable 5.2", "resolved_model": "claude-fable-5-2"},
    ])
    moved = delta.record_and_diff("swap-agent", [
        {"id": "fable", "label": "Fable 5.2", "resolved_model": "claude-fable-5-2"},
        {"id": "claude-fable-5-1", "label": "Fable 5.1", "resolved_model": "claude-fable-5-1"},
    ])
    assert moved["added"] == [] and moved["removed"] == []
    text = delta.render_delta(moved)
    assert "➕ 0 added · ➖ 0 removed · ✎ 1 changed" in text
    assert "`fable` now points to Fable 5.2 (was Fable 5.1)" in text
    assert "claude-fable-5-1" not in text and "claude-fable-5-2" not in text


def _init_stdout(models):
    return "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": "cuttle-models",
            "response": {"models": models, "commands": []},
        }}),
    ])


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "claude_session_map.json")
    monkeypatch.setattr("scripts.utilities.claude_cli_tool.claude_executable", lambda: "/fake/claude")
    monkeypatch.setattr(delta, "_snapshot_path", lambda agent: tmp_path / f"{agent}.json")
    import api.agent_harness.agent_defaults as ad
    monkeypatch.setattr(ad, "get_starred_model", lambda aid: None)
    monkeypatch.setattr(ad, "get_starred_effort", lambda aid: None)
    cat.clear_claude_catalog_cache()
    yield
    cat.clear_claude_catalog_cache()


def test_claude_refresh_reports_delta_on_the_slash_reply(monkeypatch):
    state = {
        "models": [
            {"value": "opus", "displayName": "Opus 5.5"},
            {"value": "claude-haiku-4-5", "displayName": "Haiku 4.5"},
        ]
    }

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout=_init_stdout(state["models"]), stderr=""
        )

    monkeypatch.setattr(cat.subprocess, "run", fake_run)
    adapter = claude_adapter.build_adapter()

    first = adapter.handle_meta("/model refresh", cwd=".", chat_session_id="1")
    assert "First refresh" in first.output

    state["models"] = [
        {"value": "opus", "displayName": "Opus 5.5"},
        {"value": "claude-haiku-5-5", "displayName": "Haiku 5.5"},
    ]
    second = adapter.handle_meta("/model refresh", cwd=".", chat_session_id="1")
    assert "➕ 1 added" in second.output and "➖ 1 removed" in second.output
    assert "`claude-haiku-5-5`" in second.output and "`claude-haiku-4-5`" in second.output
    assert second.meta["claude_catalog_added"] == ["claude-haiku-5-5"]
    assert second.meta["claude_catalog_removed"] == ["claude-haiku-4-5"]
