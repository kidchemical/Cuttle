"""Full inspector text stays independent of short live status previews."""

import asyncio
import io
import json
import os
import queue
import stat
import sys

import pytest

from api.query_events import QueryStatusTee, bind_query_id, reset_query_id
from api.query_tracker import start_query_tracking, get_query_tracker, finish_query_tracking


def _texts(qid, kind):
    return [e["text"] for e in get_query_tracker(qid).execution_data["events"] if e["kind"] == kind]


def test_cursor_preserves_complete_blocks_across_tools(monkeypatch, tmp_path):
    from scripts.utilities import cursor_cli_tool as mod

    body = "I will check each file. " * 30
    events = [
        {"type": "assistant", "message": {"content": "I"}},
        {"type": "assistant", "message": {"content": body}},
        {"type": "tool_call", "subtype": "started", "tool_call": {}},
        {"type": "thinking", "subtype": "delta", "text": "Full reasoning. " * 30},
        {"type": "thinking", "subtype": "completed"},
        {"type": "assistant", "message": {"content": "D"}},
        {"type": "result", "result": "Done."},
    ]

    class Proc:
        stdout = io.StringIO("\n".join(json.dumps(e) for e in events))
        stderr = io.StringIO("")
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: Proc())
    qid = start_query_tracking("cursor text", {"web_ui": True})
    token = bind_query_id(qid)
    try:
        result = mod._run_cursor_agent_stream_segment(
            agent_argv=["agent"], prompt="go", cwd=str(tmp_path), resume_id=None,
            timeout=10, status_queue=None, chat_session_id=None, cancel_event=None,
            emit=lambda *a, **k: False, cancelled=lambda: False, save_cursor_resume_id=None,
        )
        assert result["final_text"] == "Done."
        assert _texts(qid, "writing") == [body, "Done."]
        assert _texts(qid, "thinking") == ["Full reasoning. " * 30]
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
def test_opencode_preserves_text_snapshots_per_item(monkeypatch, tmp_path):
    from api.agent_harness.agents.opencode import adapter as mod

    body = "I will check each file. " * 30
    events = [
        {"type": "text", "part": {"id": "a", "text": "I"}},
        {"type": "reasoning", "part": {"id": "r", "text": "Full reasoning. " * 30}},
        {"type": "text", "part": {"id": "a", "text": body}},
        {"type": "text", "part": {"id": "b", "text": "Done."}},
    ]
    exe = tmp_path / "opencode"
    exe.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\nprint({json.dumps(chr(10).join(json.dumps(e) for e in events))})\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(mod, "opencode_executable", lambda: str(exe))
    monkeypatch.setattr(mod, "load_opencode_model", lambda *a: None)
    monkeypatch.setattr(mod, "load_opencode_effort", lambda *a: None)
    monkeypatch.setattr("api.agent_harness.agent_defaults.get_starred_model", lambda *a: None)
    monkeypatch.setattr("api.agent_harness.agent_defaults.get_starred_effort", lambda *a: None)
    qid = start_query_tracking("opencode text", {"web_ui": True})
    token = bind_query_id(qid)
    try:
        result = asyncio.run(mod.Adapter().execute(
            "go", cwd=str(tmp_path), resume=None, model=None,
            status_queue=QueryStatusTee(queue.Queue()), timeout=10,
        ))
        assert result.success
        assert _texts(qid, "writing") == [body, "Done."]
        assert _texts(qid, "thinking") == ["Full reasoning. " * 30]
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)
