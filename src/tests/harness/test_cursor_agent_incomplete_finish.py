"""Cursor Agent auto-continue when a clean run ends on narration instead of an answer.

Regression for session 147 / query 4e3dd498: the run exited cleanly after 122s and
one tool call (a background subagent that hit a usage limit), so the reply Cuttle
persisted was the bridging sentence "I have enough for a concrete RCA and harness
plan." — `agent -p` exits when the model ends its turn, and nothing wakes it back up.
"""

from __future__ import annotations

import queue
from typing import Any, Dict, List
from unittest.mock import patch

from scripts.utilities.cursor_cli_tool import (
    _CURSOR_FINISH_PROMPT,
    _cursor_reply_looks_incomplete,
    _visible_cursor_answer,
)


# ── completeness heuristic ────────────────────────────────────────────────────

def test_promise_only_reply_is_incomplete():
    assert _cursor_reply_looks_incomplete(
        "I have enough for a concrete RCA and harness plan. Gemini is already "
        "half-wired — that makes it the right proof target."
    )


def test_short_completion_statement_is_complete():
    assert not _cursor_reply_looks_incomplete("Done — CORS and allowNavigation are fixed.")


def test_closing_let_me_know_is_not_a_promise():
    assert not _cursor_reply_looks_incomplete("Fixed the badge. Let me know if it recurs.")


def test_long_answer_opening_with_a_promise_is_complete():
    body = "I'll walk through the whole path. " + ("Details about the executor. " * 40)
    assert len(body) > 400
    assert not _cursor_reply_looks_incomplete(body)


def test_cut_mid_sentence_is_incomplete():
    assert _cursor_reply_looks_incomplete(
        "The registry resolves the harness, then the palette entry is built from the"
    )


def test_answer_ending_in_code_fence_is_complete():
    assert not _cursor_reply_looks_incomplete(
        "Run this:\n\n```bash\npytest src/tests/\n```"
    )


def test_empty_reply_after_tool_calls_is_incomplete():
    assert _cursor_reply_looks_incomplete("", tool_count=3)
    assert not _cursor_reply_looks_incomplete("", tool_count=0)


def test_visible_answer_ignores_think_block():
    assembled = "<think>\nI'll investigate.\n</think>\n\nAll three nodes are wired."
    assert _visible_cursor_answer(assembled) == "All three nodes are wired."


# ── auto-continue wiring ──────────────────────────────────────────────────────

def _isolate_session_map(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: tmp_path / "cursor_cli_session_map.json",
    )


def _segment(**over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "final_text": "",
        "turns": [],
        "delta_buf": "",
        "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "timed_out": False,
        "cancelled": False,
        "errored": False,
        "err": "",
        "returncode": 0,
        "tool_count": 1,
        "proc": None,
    }
    base.update(over)
    return base


def _run(monkeypatch, tmp_path, segments: List[Dict[str, Any]]):
    from scripts.utilities.cursor_cli_tool import _cursor_agent_oneline_prompt

    monkeypatch.setenv("CUTTLE_CURSOR_AGENT_MAX_SEGMENTS", "4")
    _isolate_session_map(monkeypatch, tmp_path)
    calls: List[Dict[str, Any]] = []

    def fake_segment(**kwargs):
        calls.append(kwargs)
        return segments[min(len(calls) - 1, len(segments) - 1)]

    status_q: queue.Queue = queue.Queue()
    with patch(
        "scripts.utilities.cursor_cli_tool._resolve_cursor_agent_argv", return_value=["C:\\fake\\agent.exe"]
    ), patch(
        "scripts.utilities.cursor_cli_tool._run_cursor_agent_stream_segment", side_effect=fake_segment
    ), patch(
        "scripts.utilities.cursor_cli_session_store.save_cursor_resume_id"
    ):
        out = _cursor_agent_oneline_prompt(
            "do an RCA and give me a game plan",
            workspace="C:\\Projects\\Cuttle",
            status_queue=status_q,
            timeout=120,
            chat_session_id="147",
        )

    statuses = []
    while not status_q.empty():
        kind, payload = status_q.get_nowait()
        if kind == "status":
            statuses.append(payload)
    return out, calls, statuses


def test_clean_exit_on_promise_triggers_finish_nudge(monkeypatch, tmp_path):
    promise = "I have enough for a concrete RCA and harness plan."
    answer = (
        "Here is the RCA.\n\n1. The registry needs three edits per harness.\n"
        "2. The palette entry is hand-written.\n3. Status events are per-adapter."
    )
    out, calls, statuses = _run(
        monkeypatch,
        tmp_path,
        [
            _segment(delta_buf=promise, final_text=promise),
            _segment(delta_buf=answer, final_text=answer, tool_count=6),
        ],
    )

    assert len(calls) == 2
    assert calls[1]["prompt"] == _CURSOR_FINISH_PROMPT
    assert calls[1]["resume_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert "Here is the RCA." in out
    # The promise becomes interim context, not the delivered reply.
    assert _visible_cursor_answer(out).startswith("Here is the RCA.")
    assert any("finish" in s.lower() for s in statuses)


def test_complete_reply_does_not_nudge(monkeypatch, tmp_path):
    answer = "Done — the registry, palette and status wiring are all updated."
    out, calls, _ = _run(
        monkeypatch, tmp_path, [_segment(delta_buf=answer, final_text=answer)]
    )
    assert len(calls) == 1
    assert answer in out


def test_nudge_is_capped_and_still_returns_text(monkeypatch, tmp_path):
    promise = "I'll dig into the adapters now."
    out, calls, _ = _run(
        monkeypatch, tmp_path, [_segment(delta_buf=promise, final_text=promise)]
    )
    # Initial run + at most _CURSOR_MAX_FINISH_NUDGES retries, then deliver what we have.
    assert len(calls) == 3
    assert promise in out
