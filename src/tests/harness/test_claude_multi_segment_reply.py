"""A Claude Code turn that writes, runs tools, then writes again delivers every text block.

The CLI's terminal ``result`` carries only the last message's text; the reply
used to be just that, so a long answer written before a final tool call was
lost (CH-001998-18). Runs against a fake ``claude`` script (no real CLI).
"""

import asyncio
import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.utilities.claude_stream import ClaudeStream, join_segments  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake CLI script uses a shebang")

ANSWER = "**dev/core is moved.** Here is the full answer with a table:\n\n| a | b |\n|---|---|\n| 1 | 2 |"
NOTE = "Saving a note for later."
CLOSING = "I also saved a note for future sessions."

# text -> Write -> text -> Bash -> text, streamed with partial messages plus
# per-block assistant snapshots like claude 2.x; result holds only CLOSING.
_FAKE_CLAUDE = r'''
import json, os, sys
steerable = "--input-format" in sys.argv
if steerable:
    sys.stdin.readline()
def out(msg):
    msg.setdefault("session_id", "cs-multi")
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
def ev(**event):
    out({"type": "stream_event", "event": event})
def text_message(mid, text):
    ev(type="message_start", message={"id": mid})
    ev(type="content_block_start", index=0, content_block={"type": "text", "text": ""})
    for i in range(0, len(text), 7):
        ev(type="content_block_delta", index=0, delta={"type": "text_delta", "text": text[i:i + 7]})
    ev(type="content_block_stop", index=0)
    out({"type": "assistant", "message": {"id": mid, "content": [{"type": "text", "text": text}]}})
def tool(mid, tid, name):
    out({"type": "assistant", "message": {"id": mid, "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": {}}]}})
    out({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}})
out({"type": "system", "subtype": "init", "model": "fake"})
text_message("m1", os.environ["FAKE_ANSWER"])
tool("m1", "t1", "Write")
text_message("m2", os.environ["FAKE_NOTE"])
tool("m2", "t2", "Bash")
text_message("m3", os.environ["FAKE_CLOSING"])
out({"type": "result", "subtype": "success", "result": os.environ["FAKE_CLOSING"],
     "usage": {"input_tokens": 10, "output_tokens": 5}})
'''


def _fake_bin(tmp_path):
    path = tmp_path / "claude"
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(_FAKE_CLAUDE), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@posix_only
@pytest.mark.parametrize("steerable", [False, True])
def test_multi_segment_turn_delivers_every_text_block_in_order(tmp_path, monkeypatch, steerable):
    import scripts.utilities.claude_cli_tool as mod

    monkeypatch.setenv("FAKE_ANSWER", ANSWER)
    monkeypatch.setenv("FAKE_NOTE", NOTE)
    monkeypatch.setenv("FAKE_CLOSING", CLOSING)
    monkeypatch.setattr(mod, "claude_executable", lambda: _fake_bin(tmp_path))
    monkeypatch.setattr(
        "scripts.utilities.claude_cli_session_store.save_claude_resume_id", lambda *a, **k: None
    )
    res = asyncio.run(mod.ClaudeCliTool().execute_prompt(
        "go", cwd=str(tmp_path), chat_session_id="7301" if steerable else None,
        timeout=60, steerable=steerable,
    ))
    assert res["success"] is True, res
    assert res["output"] == f"{ANSWER}\n\n{NOTE}\n\n{CLOSING}"


def _feed_text(stream, mid, text):
    stream.feed(json.dumps({"type": "assistant", "message": {"id": mid, "content": [{"type": "text", "text": text}]}}))


def _feed_result(stream, text):
    stream.feed(json.dumps({"type": "result", "subtype": "success", "result": text}))


def test_each_root_result_owns_only_the_segments_written_since_the_previous_one():
    stream = ClaudeStream(None, lambda _sid: None)
    _feed_text(stream, "m1", "first answer")
    _feed_text(stream, "m2", "first closer")
    _feed_result(stream, "first closer")
    _feed_text(stream, "m3", "follow-up answer")
    _feed_result(stream, "follow-up answer")
    assert stream.result_segments == [["first answer", "first closer"], ["follow-up answer"]]
    assert stream.pending_writing() == []


def test_join_keeps_result_text_the_stream_never_showed():
    assert join_segments(["a", "  ", "b"], "b") == "a\n\nb"
    assert join_segments(["a"], "error: quota") == "a\n\nerror: quota"
    assert join_segments([], "only result") == "only result"
