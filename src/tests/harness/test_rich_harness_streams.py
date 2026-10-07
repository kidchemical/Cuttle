"""Offline CLI contract tests for rich activity and authoritative results."""

import asyncio
import json
import os
import queue
import sqlite3
import stat
import sys
import threading

import pytest

from api.query_events import QueryStatusTee, bind_query_id, reset_query_id
from api.query_tracker import start_query_tracking, get_query_tracker, finish_query_tracking

BODY = "I will check every adapter. " * 30
THOUGHT = "Check the documented event contract. " * 20


def test_deepseek_step_usage_separates_billing_from_context():
    from api.agent_harness.agents.deepseek.stream import DeepSeekStream

    stream = DeepSeekStream(queue.Queue())
    for usage in [
        {"inputTokens": 100, "outputTokens": 5, "cacheReadTokens": 800, "cacheWriteTokens": 200},
        {"inputTokens": 0, "outputTokens": 2, "cacheReadTokens": 600, "cacheWriteTokens": 100},
    ]:
        stream.feed(json.dumps({"type": "status", "phase": "step_end", "usage": usage}))
    assert stream.usage["prompt_tokens"] == 100
    assert stream.usage["completion_tokens"] == 7
    assert stream.usage["cache_read_tokens"] == 1400
    assert stream.usage["cache_write_tokens"] == 300
    assert stream.usage["context_tokens"] == 700
    assert stream.usage["peak_context_tokens"] == 1100
    assert stream.usage["cache_inclusive"] is False
    stream.feed(json.dumps({"type": "status", "phase": "step_end", "usage": {}}))
    assert stream.usage["context_tokens"] == 700
    stream.feed(json.dumps({"type": "status", "phase": "step_end", "usage": {"inputTokens": 0}}))
    assert stream.usage["context_tokens"] == 0
    assert stream.usage["peak_context_tokens"] == 1100


def _claude_events():
    return [
        {"type": "system", "subtype": "init", "session_id": "claude-s", "model": "fake"},
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": THOUGHT}}},
        {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "I"}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": BODY[1:]}}},
        {"type": "stream_event", "event": {"type": "content_block_stop", "index": 1}},
        {"type": "stream_event", "event": {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "tool1", "name": "Bash", "input": {}}}},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"command":"echo ok"}'}}},
        {"type": "stream_event", "event": {"type": "content_block_stop", "index": 2}},
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "thinking", "thinking": THOUGHT}, {"type": "text", "text": BODY},
            {"type": "tool_use", "id": "tool1", "name": "Bash", "input": {"command": "echo ok"}},
        ]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tool1", "content": "ok", "is_error": True}]}},
    ]


@pytest.mark.skipif(os.name == "nt", reason="fake CLI uses a shebang")
@pytest.mark.parametrize("agent", ["claude", "deepseek", "antigravity"])
@pytest.mark.parametrize("ending", ["complete", "timeout", "cancelled", "no_result"])
def test_rich_cli_streams_and_interruptions(tmp_path, monkeypatch, agent, ending):
    interrupted = ending in ("timeout", "cancelled")
    if agent == "claude":
        events = _claude_events()
        terminal = {"type": "result", "result": BODY, "session_id": "claude-s", "usage": {"input_tokens": 10, "output_tokens": 3}}
        flags = ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    elif agent == "deepseek":
        events = [
            {"type": "status", "phase": "turn_start"},
            {"type": "thinking", "text": THOUGHT}, {"type": "text", "text": BODY},
            {"type": "tool_call", "callId": "tool1", "tool": "bash", "input": {"command": "echo ok"}},
            {"type": "tool_result", "callId": "tool1", "status": "error", "result": "ok"},
        ]
        terminal = {"type": "final", "text": BODY}
        flags = ["--profile", "headless", "--json"]
    else:
        events = [
            {"event": "init", "conversation_id": "agy-s", "init": {}},
            {"event": "step_update", "step_update": {"step_index": 1, "step_type": "agent_response", "state": "ACTIVE", "text_delta": "I"}},
            {"event": "step_update", "step_update": {"step_index": 1, "step_type": "agent_response", "state": "DONE", "text_delta": BODY[1:]}},
            {"event": "step_update", "step_update": {"step_index": 2, "step_type": "tool", "state": "ACTIVE", "tool_name": "bash", "tool_info": {"parameters": {"command": "echo ok"}}}},
            {"event": "step_update", "step_update": {"step_index": 2, "step_type": "tool", "state": "DONE", "tool_name": "bash", "tool_info": {"output": "ok", "error": {"message": "failure"}}}},
        ]
        terminal = {"event": "result", "result": {"conversation_id": "agy-s", "status": "SUCCESS", "response": BODY, "usage": {"input_tokens": 10, "output_tokens": 3}}}
        flags = ["--output-format", "stream-json"]
    if ending == "complete":
        events.append(terminal)
        if agent == "claude":
            # Child metadata must never replace the parent result or resume id.
            events.append({"type": "result", "parent_tool_use_id": "child", "session_id": "child-s", "result": "CHILD"})
    exe = tmp_path / "fake-cli"
    script = f"#!{sys.executable}\nimport sys, time\n"
    script += f"assert all(flag in sys.argv for flag in {flags!r})\n"
    script += f"print({json.dumps(chr(10).join(json.dumps(e) for e in events))}, flush=True)\n"
    if interrupted:
        script += "time.sleep(30)\n"
    exe.write_text(script, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    saved = []
    qid = start_query_tracking("rich CLI", {"web_ui": True})
    token = bind_query_id(qid)
    statuses = queue.Queue()
    cancel_event = threading.Event()

    async def run_with_cancel(awaitable):
        if ending != "cancelled":
            return await awaitable
        task = asyncio.create_task(awaitable)
        while True:
            message = await asyncio.wait_for(asyncio.to_thread(statuses.get), timeout=5)
            consumed.append(message)
            if message[1].startswith("tool failed:"):
                break
        assert not task.done(), "Activity must arrive while the CLI is still running"
        cancel_event.set()
        result = await asyncio.wait_for(task, timeout=5)
        for message in consumed:
            statuses.put(message)
        return result

    consumed = []
    try:
        if agent == "claude":
            from scripts.utilities import claude_cli_tool as mod
            monkeypatch.setattr(mod, "claude_executable", lambda: str(exe))
            monkeypatch.setattr("scripts.utilities.claude_cli_session_store.save_claude_resume_id", lambda *a: saved.append(a[-1]))
            result = asyncio.run(run_with_cancel(mod.ClaudeCliTool().execute_prompt(
                "go", cwd=str(tmp_path), chat_session_id="test", status_queue=QueryStatusTee(statuses),
                cancel_event=cancel_event, timeout=0.2 if ending == "timeout" else 5,
            )))
            ok, output = result["success"], result["output"]
            assert result["claude_session_id"] == "claude-s"
            assert set(saved) == {"claude-s"}
            if ending == "complete":
                assert result["usage"]["prompt_tokens"] == 10
            if ending == "cancelled":
                assert result["cancelled"]
        else:
            if agent == "deepseek":
                from api.agent_harness.agents.deepseek import adapter as mod
                monkeypatch.setattr(mod, "dsh_argv", lambda: [str(exe)])
            else:
                from api.agent_harness.agents.antigravity import adapter as mod
                monkeypatch.setattr(mod, "antigravity_executable", lambda: str(exe))
                async def authenticated(*a):
                    return None
                monkeypatch.setattr(mod, "_authentication_preflight", authenticated)
            result = asyncio.run(run_with_cancel(mod.Adapter().execute(
                "go", cwd=str(tmp_path), resume=None, model=None, status_queue=QueryStatusTee(statuses),
                cancel_event=cancel_event, timeout=0.2 if ending == "timeout" else 5,
            )))
            ok, output = result.success, result.output
            if ending == "cancelled":
                assert result.meta["cancelled"]
        assert ok is (ending == "complete")
        assert BODY.strip() in output
        assert '"event"' not in output and '"stream_event"' not in output
        data = get_query_tracker(qid).execution_data["events"]
        assert [e["text"] for e in data if e["kind"] == "writing"] == [BODY]
        if agent != "antigravity":
            assert [e["text"] for e in data if e["kind"] == "thinking"] == [THOUGHT]
        tools = [e for e in data if e["kind"] == "tool"]
        assert len(tools) == 1 and tools[0]["failed"]
        assert tools[0]["args"] == {"command": "echo ok"}
        assert "ok" in tools[0]["text"]
        lines = [statuses.get()[1] for _ in range(statuses.qsize())]
        assert any(line.startswith("tool 1:") for line in lines)
        assert any(line.startswith("writing:") for line in lines)
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


def test_hermes_progress_is_scoped_to_invocation_and_new_rows(tmp_path):
    from scripts.utilities.hermes_activity import HermesActivity
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, reasoning_content TEXT, tool_calls TEXT, tool_name TEXT, tool_call_id TEXT)")
        conn.execute("INSERT INTO messages VALUES (1, 'ours', 'assistant', 'OLD', '', NULL, NULL, NULL)")
    qid = start_query_tracking("hermes progress", {"web_ui": True})
    token = bind_query_id(qid)
    try:
        progress = HermesActivity(db, QueryStatusTee(queue.Queue()), "ours")
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO messages VALUES (2, 'other', 'assistant', 'OTHER', '', NULL, NULL, NULL)")
            calls = json.dumps([{"id": "call1", "function": {"name": "bash", "arguments": '{"command":"echo ok"}'}}])
            conn.execute("INSERT INTO messages VALUES (3, 'ours', 'assistant', ?, ?, ?, NULL, NULL)", (BODY, THOUGHT, calls))
            conn.execute("INSERT INTO messages VALUES (4, 'ours', 'tool', 'ok', '', NULL, 'bash', 'call1')")
        progress.poll()
        progress.poll()
        events = get_query_tracker(qid).execution_data["events"]
        assert [e["text"] for e in events if e["kind"] == "writing"] == [BODY]
        assert [e["text"] for e in events if e["kind"] == "thinking"] == [THOUGHT]
        assert len([e for e in events if e["kind"] == "tool"]) == 1
        assert next(e for e in events if e["kind"] == "tool")["phase"] == "completed"
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)
