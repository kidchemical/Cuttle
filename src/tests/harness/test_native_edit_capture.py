"""Native edit capture for Cursor and Muse (offline fixtures of recorded shapes).

Shapes verified against recorded agent-events payloads (2026-10-07/08):
Cursor ``editToolCall.result.success`` carries ``diffString`` plus full
before/after file text; Muse ``serve`` toolCall items carry a ``patchRef`` to a
``*-tool_patch.json`` file with structured hunks. No CLI is executed.
"""
import json

from api.agent_harness.activity import ActivityEmitter, ToolActivityLog


CURSOR_EDIT = {
    "editToolCall": {
        "args": {"path": "/repo/src/a.py", "streamContent": "y\n"},
        "result": {"success": {"path": "/repo/src/a.py", "linesAdded": 1, "linesRemoved": 0,
                               "diffString": "--- a//repo/src/a.py\n+++ b//repo/src/a.py\n@@ -1 +1,2 @@\n x\n+y\n",
                               "beforeFullFileContent": "x\n", "afterFullFileContent": "x\ny\n"}},
    },
    "toolCallId": "call-1",
}


def test_cursor_edit_results_yield_native_diffs():
    from scripts.utilities.cursor_cli_tool import _cursor_native_edits

    (edit,) = _cursor_native_edits(CURSOR_EDIT)
    assert edit["path"] == "/repo/src/a.py" and edit["change"] == "modify" and "+y" in edit["patch"]
    created = json.loads(json.dumps(CURSOR_EDIT))
    created["editToolCall"]["result"]["success"]["beforeFullFileContent"] = ""
    assert _cursor_native_edits(created)[0]["change"] == "add"
    failed = {"editToolCall": {"args": {}, "result": {"error": {"message": "nope"}}}}
    assert _cursor_native_edits(failed) == [] and _cursor_native_edits({"shellToolCall": {"result": {}}}) == []


def _muse_item(path, availability="available"):
    return {"kind": "toolCall", "itemId": "item-1", "tool": "edit_file", "status": "completed",
            "patchRef": {"kind": "tool_patch", "availability": availability, "path": str(path)}}


def test_muse_tool_patch_files_yield_structured_hunks(tmp_path):
    from scripts.utilities.muse_serve_turn import _muse_patch_edits

    patch = tmp_path / "call_1-tool_patch.json"
    hunks = [{"oldStart": 4, "oldLines": 1, "newStart": 4, "newLines": 2, "lines": ["-a", "+b", "+c"]}]
    patch.write_text(json.dumps({"files": [{"path": "/repo/src/t.py", "hunks": hunks}]}))
    assert _muse_patch_edits(_muse_item(patch)) == [{"path": "/repo/src/t.py", "patch": hunks}]
    assert _muse_patch_edits(_muse_item(patch, "expired")) == []
    other = tmp_path / "notes.json"
    other.write_text(patch.read_text())
    assert _muse_patch_edits(_muse_item(other)) == []  # only the CLI's own tool_patch files
    broken = tmp_path / "call_2-tool_patch.json"
    broken.write_text("{not json")
    assert _muse_patch_edits(_muse_item(broken)) == []


def test_native_edit_skips_the_step_snapshot(monkeypatch):
    calls, events = [], []
    import api.agent_events.snapshots as snapshots
    import api.query_events as query_events
    monkeypatch.setattr(snapshots, "record_step", lambda qid, tool: calls.append(tool))
    monkeypatch.setattr(query_events, "record_agent_tool", lambda *a, **k: None)
    monkeypatch.setattr(query_events, "add_event", lambda kind, **payload: events.append((kind, payload)))
    tools = ToolActivityLog("muse", ActivityEmitter(None, agent_label="Muse Code"))
    tools.record_edit("t1", "/repo/a.py", [{"lines": ["+x"]}])
    tools.record("t1", "edit_file", {"path": "a.py"}, phase="completed")
    tools.record("t2", "bash", {"command": "sed -i s/a/b/ a.py"}, phase="completed")
    assert calls == [f"{tools.source_id}:t2"]
    kind, payload = events[0]
    assert kind == "edit" and payload["source"] == "native" and payload["tool_id"] == f"{tools.source_id}:t1"
