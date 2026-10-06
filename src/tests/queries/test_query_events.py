"""Harness query-log event stream (Brain / thinking / tools)."""

from __future__ import annotations

import json

from api.query_events import (
    QueryStatusTee,
    bind_query_id,
    build_query_log_response,
    ingest_status_message,
    public_query_payload,
    record_thinking,
    reset_query_id,
)
from api.query_tracker import (
    finish_query_tracking,
    get_query_tracker,
    start_query_tracking,
)


def test_status_tee_records_thinking_and_tools():
    qid = start_query_tracking("hello", {"web_ui": True})
    token = bind_query_id(qid)
    try:
        q = QueryStatusTee(_Sink())
        q.put(("status", "thinking: pondering the maze"))
        q.put_nowait(("status", "tool 1: read src/foo.py"))
        q.put(("status", "Agent working… 12s (tool: read)"))
        data = get_query_tracker(qid).execution_data
        kinds = [e.get("kind") for e in data.get("events") or []]
        assert "thinking" in kinds
        assert "tool" in kinds
        assert not any(
            (e.get("kind") == "status" and "working" in str(e.get("text") or ""))
            for e in data["events"]
        )
        think = next(e for e in data["events"] if e["kind"] == "thinking")
        assert "pondering" in think["text"]
        tool = next(e for e in data["events"] if e["kind"] == "tool")
        assert "read" in (tool.get("summary") or "")
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


def test_thinking_coalesces():
    qid = start_query_tracking("t", {"web_ui": True})
    token = bind_query_id(qid)
    try:
        ingest_status_message("thinking: aaa")
        ingest_status_message("thinking: aaabbb")
        record_thinking("full block\nmore")
        events = [e for e in get_query_tracker(qid).execution_data["events"] if e["kind"] == "thinking"]
        assert len(events) == 1
        assert "full block" in events[0]["text"]
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


def test_codex_text_log_flushes_throttled_text_without_merging_items():
    from api.agent_harness.activity import ActivityEmitter
    from api.query_events import MAX_TEXT
    from api.agent_harness.activity import TextActivityLog

    qid = start_query_tracking("text snapshots", {"web_ui": True})
    token = bind_query_id(qid)
    try:
        log = TextActivityLog("codex")
        emitter = ActivityEmitter(QueryStatusTee(_Sink()), record_text_previews=False)
        log.start("writing", "a")
        log.delta("writing", "I", "a")
        log.save("writing", item_id="a")
        assert emitter.emit("writing: I")
        log.delta("writing", " finished before another status update.", "a")
        assert not emitter.emit("writing: I finished before another status update.")
        ingest_status_message("tool 1: read file")
        log.start("writing", "b")
        log.save("writing", "Second block", "b")
        log.delta("thinking", "x" * (MAX_TEXT + 50), "r")
        log.flush()
        events = get_query_tracker(qid).execution_data["events"]
        assert [e["text"] for e in events if e["kind"] == "writing"] == [
            "I finished before another status update.", "Second block",
        ]
        reasoning = next(e for e in events if e["kind"] == "thinking")
        assert reasoning["text"] == "x" * MAX_TEXT + "\n… *(truncated)*"
        assert any(e["kind"] == "tool" for e in events)
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


def test_tool_log_keeps_identical_calls_distinct_and_bounds_arguments():
    from api.agent_harness.activity import ActivityEmitter, ToolActivityLog
    from api.query_events import MAX_TOOL_ARGS
    qid = start_query_tracking("tool identity", {"web_ui": True})
    token = bind_query_id(qid)
    try:
        emitter = ActivityEmitter(QueryStatusTee(_Sink()), record_tool_previews=False)
        tools = ToolActivityLog("test", emitter)
        for ident in ("a", "b"):
            tools.record(ident, "Bash", {"command": "echo ok"})
        tools.record("a", phase="completed", result="ok")
        tools.record("b", phase="failed", result="error", failed=True)
        tools.record("c", "Read", {"path": "x" * (MAX_TOOL_ARGS + 100)})
        events = [e for e in get_query_tracker(qid).execution_data["events"] if e["kind"] == "tool"]
        assert len(events) == 3
        assert events[0]["phase"] == "completed" and not events[0]["failed"]
        assert events[1]["phase"] == "failed" and events[1]["failed"]
        assert "*(truncated)*" in events[2]["args"]["preview"]
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


def test_set_sent_and_api_payload(tmp_path, monkeypatch):
    qid = start_query_tracking("/cursor hi", {"web_ui": True, "slash_command": "/cursor"})
    tr = get_query_tracker(qid)
    tr.set_harness({"agent_id": "cursor", "label": "Cursor", "cwd": str(tmp_path)})
    tr.set_brain({"mode": "full", "layers": ["rules"], "prompt_chars": 12})
    tr.set_sent("ENVELOPE\n\n## User request\nhi", resume=False)
    payload, err, code = build_query_log_response(qid)
    assert err is None and code == 200
    assert payload["query_id"] == qid
    assert payload["sent"]["chars"] > 0
    assert payload["brain"]["mode"] == "full"
    assert any(e.get("kind") == "sent" for e in payload["events"])
    # After finish, stable JSON is readable
    finish_query_tracking(success=True)
    payload2, err2, code2 = build_query_log_response(qid)
    assert code2 == 200 and err2 is None
    assert payload2["executing"] is False
    pub = public_query_payload(payload2, executing=False)
    json.dumps(pub)


class _Sink:
    def put(self, item, *a, **k):
        return item

    def put_nowait(self, item):
        return item


def test_web_turn_metadata_cannot_be_misclassified_as_retired_ingress():
    qid = start_query_tracking('hello', {
        'web_ui': True,
        'command_type': 'claude',
        'channel_id': 'old-channel',
        'node_editor': True,
    })
    try:
        tracker = get_query_tracker(qid)
        assert tracker.execution_data['input_source']['source'] == 'Web UI'
        tracker._add_output_stage()
        stages = tracker.execution_data['execution_stages']
        output = next(stage for stage in stages if stage['type'] == 'output')
        assert output['details']['destination'] == 'Web UI Response'
        tracker.set_output_destination('Explicit destination', 'Preserved')
        assert tracker.execution_data['output_destination']['destination'] == 'Explicit destination'
    finally:
        finish_query_tracking(success=True)
