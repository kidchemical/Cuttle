"""Claude Code compact: real fill after /compact, and no fork of a live run.

CH-002006: the compact ran as a second ``claude --resume`` while a detached
background wait still held the session. The boundary landed on disk (877k →
13k) but the live CLI kept sending its full window, and the gauge kept reading
the last bubble's pre-compact stamp, so the notice said 88% → 88%.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import agent_context as ac
from scripts.utilities import claude_cli_session_store as store

SID = "6be5dba2-8bb4-44a7-b44b-92a8c4b04766"


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    proj = tmp_path / "claude" / "projects" / "-proj"
    proj.mkdir(parents=True)
    return proj / f"{SID}.jsonl"


def _assistant(cache_read: int) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"usage": {"input_tokens": 2, "cache_read_input_tokens": cache_read,
                              "cache_creation_input_tokens": 0}},
    }, separators=(",", ":"))


def _boundary(post: int) -> str:
    return json.dumps({
        "type": "system", "subtype": "compact_boundary",
        "compactMetadata": {"trigger": "manual", "preTokens": 877_203, "postTokens": post},
    }, separators=(",", ":"))


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_transcript_fill_uses_post_tokens_after_boundary(claude_home):
    _write(claude_home, _assistant(876_000), _boundary(13_472))
    stats = store.claude_transcript_stats(SID)
    assert stats == {"compactions": 1, "context_tokens": 13_472, "model": ""}

    # A model call after the boundary is the live fill again.
    _write(claude_home, _assistant(876_000), _boundary(13_472), _assistant(20_000))
    assert store.claude_transcript_stats(SID)["context_tokens"] == 20_002


def test_compact_refused_while_live_run_holds_session(claude_home, monkeypatch, tmp_path):
    _write(claude_home, _assistant(876_000))
    monkeypatch.setattr("api.chat_run_registry.has_live_process", lambda _sid: True)
    monkeypatch.setattr(
        "scripts.utilities.claude_cli_tool.claude_executable", lambda: "/usr/bin/claude"
    )

    def _boom(*_a, **_k):
        raise AssertionError("must not spawn a second --resume writer")

    monkeypatch.setattr("scripts.utilities.claude_cli_tool.ClaudeCliTool.execute_prompt", _boom)
    out = ac._compact_claude(str(tmp_path), SID, 2006)
    assert out["success"] is False
    assert "still running" in out["error"]
    assert out["busy"] is True  # the composer keeps the compaction queued


def test_alias_model_is_sized_from_the_transcript(claude_home, monkeypatch, tmp_path):
    """The chat stores "fable"; the transcript names the 1M model that ran."""
    row = json.loads(_assistant(8_000))
    row["message"]["model"] = "claude-fable-5-2"
    _write(claude_home, json.dumps(row, separators=(",", ":")))
    assert store.claude_transcript_stats(SID)["model"] == "claude-fable-5-2"

    monkeypatch.setattr(store, "resolve_claude_resume", lambda *_a, **_k: (SID, str(tmp_path)))
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "fable")
    monkeypatch.setattr(
        ac, "lookup_catalog_context_limit",
        lambda model: 1_000_000 if model == "claude-fable-5-2" else None,
    )
    status = ac.get_agent_context_status(
        chat_session_id=2006, agent_id="claude", cwd=str(tmp_path), messages=[]
    )
    assert status["limit_tokens"] == 1_000_000
    assert status["limit_source"] != "agent_default"


def _fake_compact(claude_home, monkeypatch, *, writes_boundary: bool):
    monkeypatch.setattr("api.chat_run_registry.has_live_process", lambda _sid: False)
    monkeypatch.setattr(
        "scripts.utilities.claude_cli_tool.claude_executable", lambda: "/usr/bin/claude"
    )
    _write(claude_home, _assistant(876_000))

    async def _run(self, prompt, **_k):
        assert prompt == "/compact"
        if writes_boundary:
            with open(claude_home, "a", encoding="utf-8") as f:
                f.write(_boundary(13_472) + "\n")
        return {"success": True, "output": ""}

    monkeypatch.setattr("scripts.utilities.claude_cli_tool.ClaudeCliTool.execute_prompt", _run)


def test_compact_stamps_post_fill_and_gauge_prefers_it(claude_home, monkeypatch, tmp_path):
    _fake_compact(claude_home, monkeypatch, writes_boundary=True)
    out = ac._compact_claude(str(tmp_path), SID, 2006)
    assert out["success"] is True
    assert out["token_usage"]["context_tokens"] == 13_472
    snap = store.load_claude_context_snapshot(2006)
    assert snap["compacted"] is True and snap["context_tokens"] == 13_472

    # The last bubble still carries the pre-compact stamp; the snapshot wins.
    monkeypatch.setattr(ac, "_load_resume_id", lambda *_a, **_k: SID)
    monkeypatch.setattr(store, "resolve_claude_resume", lambda *_a, **_k: (SID, str(tmp_path)))
    monkeypatch.setattr(ac, "_resolve_model_for_agent", lambda *_a, **_k: "claude-opus-5-5")
    msgs = [{
        "id": 2, "role": "assistant", "content": "ok",
        "metadata": {"slash_command": {"chips": [{"prefix": "/claude "}]},
                     "usage": {"context_tokens": 876_243, "prompt_tokens": 18}},
    }]
    status = ac.get_agent_context_status(
        chat_session_id=2006, agent_id="claude", cwd=str(tmp_path), messages=msgs
    )
    assert status["used_tokens"] == 13_472
    assert status["percent"] < 10


def test_compact_without_boundary_is_not_success(claude_home, monkeypatch, tmp_path):
    _fake_compact(claude_home, monkeypatch, writes_boundary=False)
    out = ac._compact_claude(str(tmp_path), SID, 2006)
    assert out["success"] is False
    assert "no change" in out["error"]
