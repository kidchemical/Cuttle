"""Claude snapshot reconciliation without running a vendor CLI."""

import json
import queue

import pytest

from api.query_events import bind_query_id, reset_query_id
from api.query_tracker import start_query_tracking, get_query_tracker, finish_query_tracking
from scripts.utilities.claude_stream import ClaudeStream


@pytest.mark.parametrize("scope", [None, "child-tool"])
def test_shifted_snapshots_keep_one_event_per_block(scope):
    qid = start_query_tracking("Claude reconciliation", {"web_ui": True})
    token = bind_query_id(qid)
    stream = ClaudeStream(queue.Queue(), lambda _: None)

    def feed(obj):
        if scope:
            obj["parent_tool_use_id"] = scope
        stream.feed(json.dumps(obj))

    def event(kind, **fields):
        feed({"type": "stream_event", "event": {"type": kind, **fields}})

    try:
        for message_id in ("m1", "m2"):
            event("message_start", message={"id": message_id})
            for index, kind, text in ((0, "thinking", "reason"), (1, "text", "same"), (2, "text", "same")):
                event("content_block_start", index=index, content_block={"type": kind, kind: ""})
                event("content_block_delta", index=index, delta={"type": f"{kind}_delta", kind: text})
                event("content_block_stop", index=index)
                if index == 1:
                    feed({"type": "assistant", "message": {"id": message_id, "content": [
                        {"type": "tool_use", "id": f"tool-{message_id}", "name": "Bash", "input": {}},
                    ]}})
            # Thinking is omitted, so snapshot indexes differ from wire indexes.
            snapshot = {"type": "assistant", "message": {"id": message_id, "content": [
                {"type": "text", "text": "same"}, {"type": "text", "text": "same"},
                {"type": "text", "text": "snapshot only"},
            ]}}
            feed(snapshot)
            feed(snapshot)
        stream.text.flush()
        events = get_query_tracker(qid).execution_data["events"]
        assert [e["text"] for e in events if e["kind"] == "writing"] == ["same", "same", "snapshot only"] * 2
        assert [e["text"] for e in events if e["kind"] == "thinking"] == ["reason"] * 2
        if scope is None:
            assert stream.partial_output() == "same\nsame\nsnapshot only"
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


def test_snapshot_only_output():
    stream = ClaudeStream(None, lambda _: None)
    stream.feed(json.dumps({"type": "assistant", "message": {"id": "m", "content": [
        {"type": "thinking", "thinking": "reason"}, {"type": "text", "text": "answer"},
    ]}}))
    assert stream.partial_output() == "answer"
    assert len(stream.text.buffers) == 2
