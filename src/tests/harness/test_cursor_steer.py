"""Cursor mid-turn steering: a held steer ends the ``-p`` segment at a safe boundary
and runs as the next ``--resume`` segment of the same turn."""

from __future__ import annotations

import json
import sys
import textwrap
import threading
import time
from typing import Any, Dict, List
from unittest.mock import patch

from api.agent_harness import steer as steer_registry
from api.agent_harness.agents.cursor.segment_steer import CursorSteer
from scripts.utilities.cursor_cli_tool import _cursor_agent_oneline_prompt

SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _isolate_session_map(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: tmp_path / "cursor_cli_session_map.json",
    )


def _seg(**over: Any) -> Dict[str, Any]:
    base = {
        "final_text": "", "turns": [], "delta_buf": "", "session_id": SID,
        "timed_out": False, "cancelled": False, "errored": False, "err": "",
        "returncode": 0, "tool_count": 0, "proc": None, "steer_cut": False,
    }
    base.update(over)
    return base


def test_steer_waits_for_tool_result_and_new_output():
    owner = CursorSteer("cursor-unit")
    owner.begin_segment("do it")
    ok, _err = owner.send("also marigold").result(timeout=1)
    assert ok
    assert not owner.should_cut()  # no session id yet: nothing to resume
    owner.on_event("system", "init", {"session_id": SID})
    owner.on_event("tool_call", "started", {"call_id": "t1"})
    assert not owner.should_cut()  # never interrupt a running tool
    owner.on_event("tool_call", "completed", {"call_id": "t1"})
    assert not owner.should_cut()  # result not yet in the session
    owner.on_event("thinking", "delta", {"text": "ok"})
    assert owner.should_cut()


def test_closed_turn_refuses_sends_so_the_client_queues():
    owner = CursorSteer("cursor-unit")
    assert owner.finish_or_take() is None
    ok, err = owner.send("late").result(timeout=1)
    assert not ok and "no longer active" in err


def test_unsent_steer_is_reported_undelivered():
    owner = CursorSteer("cursor-unit")
    owner.send("never ran").result(timeout=1)
    owner.close()
    assert owner.delivery.undelivered == ["never ran"]
    assert "never ran" in (owner.closing_notice() or "")


def test_steer_cut_resumes_with_steer_text_outside_the_segment_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_CURSOR_AGENT_MAX_SEGMENTS", "1")
    _isolate_session_map(monkeypatch, tmp_path)
    owner = CursorSteer("cursor-loop")
    calls: List[Dict[str, Any]] = []

    def fake_segment(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            kwargs["steer"].send("use marigold").result(timeout=1)
            return _seg(turns=["Working…"], steer_cut=True)
        return _seg(final_text="Done, marigold.", delta_buf="Done, marigold.")

    with patch("scripts.utilities.cursor_cli_tool._resolve_cursor_agent_argv", return_value=["agent"]), \
            patch("scripts.utilities.cursor_cli_tool._run_cursor_agent_stream_segment", side_effect=fake_segment), \
            patch("scripts.utilities.cursor_cli_session_store.save_cursor_resume_id"):
        out = _cursor_agent_oneline_prompt("start", workspace=str(tmp_path), timeout=120,
                                           chat_session_id="cursor-loop", steer=owner)

    assert len(calls) == 2
    assert calls[1]["prompt"] == "use marigold"
    assert calls[1]["resume_id"] == SID
    assert "Done, marigold." in out


def test_steer_sent_while_segment_finishes_runs_before_the_turn_closes(monkeypatch, tmp_path):
    _isolate_session_map(monkeypatch, tmp_path)
    owner = CursorSteer("cursor-loop")
    calls: List[Dict[str, Any]] = []

    def fake_segment(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            kwargs["steer"].send("one more thing").result(timeout=1)
            return _seg(final_text="First answer.", delta_buf="First answer.")
        return _seg(final_text="Second answer.", delta_buf="Second answer.")

    with patch("scripts.utilities.cursor_cli_tool._resolve_cursor_agent_argv", return_value=["agent"]), \
            patch("scripts.utilities.cursor_cli_tool._run_cursor_agent_stream_segment", side_effect=fake_segment), \
            patch("scripts.utilities.cursor_cli_session_store.save_cursor_resume_id"):
        out = _cursor_agent_oneline_prompt("start", workspace=str(tmp_path), timeout=120,
                                           chat_session_id="cursor-loop", steer=owner)

    assert [c["prompt"] for c in calls] == ["start", "one more thing"]
    assert "Second answer." in out
    ok, _ = owner.send("after").result(timeout=1)
    assert not ok


_FAKE_AGENT = r'''
import json, os, sys, time
args = sys.argv[1:]
log = os.environ["FAKE_AGENT_LOG"]
gate = os.environ["FAKE_AGENT_GATE"]
prompt = args[-1]
with open(log, "a") as fh:
    fh.write(json.dumps(args) + "\n")
def out(evt):
    sys.stdout.write(json.dumps({**evt, "session_id": "%s"}) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "model": "Fake"})
out({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
if "--resume" in args:
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Steered: " + prompt + "."}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "Steered: " + prompt + "."})
    sys.exit(0)
out({"type": "tool_call", "subtype": "started", "call_id": "t1", "tool_call": {"shellToolCall": {"args": {"command": "sleep"}}}})
while not os.path.exists(gate):
    time.sleep(0.02)
out({"type": "tool_call", "subtype": "completed", "call_id": "t1", "tool_call": {"shellToolCall": {"args": {"command": "sleep"}, "result": {"success": {}}}}})
for _ in range(200):
    out({"type": "thinking", "subtype": "delta", "text": "hm "})
    time.sleep(0.02)
out({"type": "result", "subtype": "success", "is_error": False, "result": "unsteered"})
''' % SID


def test_real_segment_holds_steer_through_tool_then_resumes(monkeypatch, tmp_path):
    _isolate_session_map(monkeypatch, tmp_path)
    fake = tmp_path / "agent_fake.py"
    fake.write_text(textwrap.dedent(_FAKE_AGENT), encoding="utf-8")
    log, gate = tmp_path / "calls.jsonl", tmp_path / "gate"
    monkeypatch.setenv("FAKE_AGENT_LOG", str(log))
    monkeypatch.setenv("FAKE_AGENT_GATE", str(gate))

    chat = "cursor-e2e-steer"
    owner = CursorSteer(chat)
    owner.register()
    result: Dict[str, Any] = {}

    def _turn():
        with patch("scripts.utilities.cursor_cli_tool._resolve_cursor_agent_argv",
                   return_value=[sys.executable, str(fake)]):
            result["out"] = _cursor_agent_oneline_prompt(
                "run the tool", workspace=str(tmp_path), timeout=60,
                chat_session_id=chat, steer=owner,
            )

    worker = threading.Thread(target=_turn)
    worker.start()
    try:
        deadline = time.time() + 10
        while not log.exists() and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
        sent = steer_registry.steer(chat, "/cursor use marigold")
        assert sent["steered"] is True and sent["agent"] == "cursor"
        time.sleep(0.3)
        assert len(log.read_text(encoding="utf-8").splitlines()) == 1  # tool still running: no cut
        gate.write_text("go", encoding="utf-8")
        worker.join(timeout=20)
    finally:
        gate.write_text("go", encoding="utf-8")
        worker.join(timeout=5)
        owner.close()

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 2
    assert calls[1][-1] == "use marigold"
    assert calls[1][calls[1].index("--resume") + 1] == SID
    assert "Steered: use marigold" in result["out"]
    assert "unsteered" not in result["out"]
    assert owner.received_count == 1
    assert owner.closing_notice() is None
