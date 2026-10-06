"""Cursor Agent per-segment timeout + auto-continue via --resume."""

from __future__ import annotations

import queue
from typing import Any, Dict, List
from unittest.mock import patch

from scripts.utilities.cursor_cli_tool import (
    _CURSOR_CONTINUE_PROMPT,
    _cursor_agent_max_segments,
    _cursor_agent_oneline_prompt,
    _cursor_agent_timeout_sec,
)


def test_timeout_sec_default_and_clamp(monkeypatch):
    monkeypatch.delenv("CUTTLE_CURSOR_AGENT_TIMEOUT_SEC", raising=False)
    assert _cursor_agent_timeout_sec() == 3600.0
    assert _cursor_agent_timeout_sec(900) == 900.0
    assert _cursor_agent_timeout_sec(10) == 60.0
    assert _cursor_agent_timeout_sec(99999) == 7200.0
    monkeypatch.setenv("CUTTLE_CURSOR_AGENT_TIMEOUT_SEC", "1200")
    assert _cursor_agent_timeout_sec() == 1200.0


def test_max_segments_default_and_clamp(monkeypatch):
    monkeypatch.delenv("CUTTLE_CURSOR_AGENT_MAX_SEGMENTS", raising=False)
    assert _cursor_agent_max_segments() == 4
    monkeypatch.setenv("CUTTLE_CURSOR_AGENT_MAX_SEGMENTS", "2")
    assert _cursor_agent_max_segments() == 2
    monkeypatch.setenv("CUTTLE_CURSOR_AGENT_MAX_SEGMENTS", "99")
    assert _cursor_agent_max_segments() == 10


def _isolate_session_map(monkeypatch, tmp_path):
    """Keep resume lookups/writes off the real src/data/workspace map."""
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: tmp_path / "cursor_cli_session_map.json",
    )


def test_auto_continue_after_timeout_assembles_one_reply(monkeypatch, tmp_path):
    """Timed-out segment then successful resume → single wrapped success reply."""
    monkeypatch.setenv("CUTTLE_CURSOR_AGENT_MAX_SEGMENTS", "4")
    _isolate_session_map(monkeypatch, tmp_path)
    calls: List[Dict[str, Any]] = []

    def fake_segment(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "final_text": "",
                "turns": ["Working on the LAN fix…"],
                "delta_buf": "Still digging.",
                "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "timed_out": True,
                "cancelled": False,
                "errored": False,
                "err": "",
                "returncode": None,
                "tool_count": 2,
                "proc": None,
            }
        return {
            "final_text": "Done — CORS and allowNavigation are fixed.",
            "turns": [],
            "delta_buf": "Done — CORS and allowNavigation are fixed.",
            "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "timed_out": False,
            "cancelled": False,
            "errored": False,
            "err": "",
            "returncode": 0,
            "tool_count": 5,
            "proc": None,
        }

    status_q: queue.Queue = queue.Queue()
    with patch("scripts.utilities.cursor_cli_tool._resolve_cursor_agent_argv", return_value=["C:\\fake\\agent.exe"]), patch(
        "scripts.utilities.cursor_cli_tool._run_cursor_agent_stream_segment", side_effect=fake_segment
    ), patch(
        "scripts.utilities.cursor_cli_session_store.save_cursor_resume_id",
    ):
        out = _cursor_agent_oneline_prompt(
            "fix phone LAN connect",
            workspace="C:\\Projects\\Cuttle",
            status_queue=status_q,
            timeout=120,
            chat_session_id="76",
        )

    assert out is not None
    assert not out.startswith("**Cursor Agent**")
    assert "Done — CORS and allowNavigation are fixed." in out
    assert "Cursor run" not in out
    assert "[FAIL]" not in out
    assert "could not auto-continue" not in out.lower()
    assert len(calls) == 2
    assert calls[0]["prompt"] == "fix phone LAN connect"
    assert calls[0]["resume_id"] is None
    assert calls[1]["prompt"] == _CURSOR_CONTINUE_PROMPT
    assert calls[1]["resume_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    statuses = []
    while not status_q.empty():
        kind, payload = status_q.get_nowait()
        if kind == "status":
            statuses.append(payload)
    assert any("Continuing long agent run" in s for s in statuses)


def test_timeout_without_session_id_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_CURSOR_AGENT_MAX_SEGMENTS", "4")
    _isolate_session_map(monkeypatch, tmp_path)

    def fake_segment(**kwargs):
        return {
            "final_text": "",
            "turns": [],
            "delta_buf": "",
            "session_id": None,
            "timed_out": True,
            "cancelled": False,
            "errored": False,
            "err": "",
            "returncode": None,
            "tool_count": 0,
            "proc": None,
        }

    status_q: queue.Queue = queue.Queue()
    with patch("scripts.utilities.cursor_cli_tool._resolve_cursor_agent_argv", return_value=["C:\\fake\\agent.exe"]), patch(
        "scripts.utilities.cursor_cli_tool._run_cursor_agent_stream_segment", side_effect=fake_segment
    ):
        out = _cursor_agent_oneline_prompt(
            "do something long",
            workspace="C:\\Projects\\Cuttle",
            status_queue=status_q,
            timeout=90,
            chat_session_id="76",
        )

    assert out is not None
    assert out.startswith("[FAIL]")
    assert "timed out after 90s" in out
