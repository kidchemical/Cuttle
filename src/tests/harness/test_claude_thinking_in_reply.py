"""Claude Code thinking rides in the reply as a collapsed ``<think>`` block.

The model sometimes writes its user-facing answer inside a thinking block, then
closes the turn with one short text line (CH-001998-18); without the thinking
in the reply that answer was unrecoverable.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.utilities.claude_stream import ClaudeStream, with_thinking  # noqa: E402


def _assistant(stream, mid, *blocks):
    stream.feed(json.dumps({"type": "assistant", "message": {"id": mid, "content": list(blocks)}}))


def _result(stream, text):
    stream.feed(json.dumps({"type": "result", "subtype": "success", "result": text}))


def test_thinking_is_scoped_to_the_result_it_led_to():
    stream = ClaudeStream(None, lambda _sid: None)
    _assistant(stream, "m1", {"type": "thinking", "thinking": "", "signature": "x"},
               {"type": "thinking", "thinking": "You're back on dev/core.", "signature": "y"})
    _assistant(stream, "m2", {"type": "text", "text": "I also saved a note."})
    _result(stream, "I also saved a note.")
    _assistant(stream, "m3", {"type": "thinking", "thinking": "Follow-up reasoning"})
    _result(stream, "Follow-up answer")
    assert stream.thinking_for_results(0, 1) == ["You're back on dev/core."]
    assert stream.thinking_for_results(1, 2) == ["Follow-up reasoning"]
    assert stream.thinking_for_results(0, None) == ["You're back on dev/core.", "Follow-up reasoning"]


def test_subagent_thinking_is_not_the_reply():
    stream = ClaudeStream(None, lambda _sid: None)
    stream.feed(json.dumps({"type": "assistant", "parent_tool_use_id": "t1",
                            "message": {"id": "s1", "content": [{"type": "thinking", "thinking": "child"}]}}))
    assert stream.thinking_for_results(0, None) == []


def test_with_thinking_wraps_and_neutralises_tags():
    assert with_thinking([], "answer") == "answer"
    assert with_thinking(["a", " ", "b"], "answer") == "<think>\na\n\nb\n</think>\n\nanswer"
    assert "&lt;/think>" in with_thinking(["quoting </think> here"], "x")
