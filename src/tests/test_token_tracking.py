"""Query tracking records token usage for AI tool calls (and works without it)."""

import json
import time

from api.query_tracker import finish_query_tracking, start_query_tracking
from api.query_tracker import track_tool_call as _track_tool_call


def _finished_tool_calls():
    report_path, json_path = finish_query_tracking(success=True)
    assert report_path and json_path
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f).get("tool_calls") or []


def test_tool_call_with_tokens():
    start_query_tracking(
        user_input='/claude "Create a hello world file"',
        user_context={"display_name": "TestUser", "id": 12345, "command_type": "claude"},
    )
    tokens = {"total_tokens": 1234, "prompt_tokens": 400, "completion_tokens": 834}
    _track_tool_call(
        tool_name="claude_code",
        parameters={"prompt": "Create a hello world file", "project": "sandbox"},
        start_time=time.time(),
        success=True,
        result="Successfully created hello_world.py",
        model="claude-3-haiku",
        tokens=tokens,
        cost=0.001234,
    )

    calls = [c for c in _finished_tool_calls() if c.get("tokens")]
    assert calls, "tool call lost its token information"
    assert calls[0]["tokens"]["total_tokens"] == 1234
    assert calls[0].get("model") == "claude-3-haiku"


def test_tool_call_without_tokens():
    start_query_tracking(
        user_input="/screenshot main",
        user_context={"display_name": "TestUser", "id": 12345},
    )
    _track_tool_call(
        tool_name="screenshot",
        parameters={"target": "main"},
        start_time=time.time(),
        success=True,
        result="Screenshot captured",
    )

    calls = _finished_tool_calls()
    assert calls, "regular tool call was not tracked"
    assert not calls[0].get("tokens")
