"""A chat whose turn ended while its CLI waits on background work reports waiting_background."""
from __future__ import annotations

import json
import os

import pytest

from api.agent_harness import background_wait


@pytest.fixture
def watch_dir(tmp_path, monkeypatch):
    import api.job_watch as jw
    monkeypatch.setattr(jw, "output_dir", lambda: tmp_path)
    return tmp_path


def _status(path, sid, *, state="running", pid=None):
    (path / f"claude-bg-{sid}-abc123-status.json").write_text(json.dumps({
        "state": state, "label": "Waiting on: build", "chat_session_id": str(sid),
        "run_id": f"claude-bg-{sid}-abc123", "pid": os.getpid() if pid is None else pid,
        "started_at": "2026-10-31T17:05:08"}))


def test_running_wait_with_live_pid_is_found(watch_dir):
    _status(watch_dir, 42)
    waits = background_wait.running_on_disk(42)
    assert [w["label"] for w in waits] == ["Waiting on: build"]
    assert background_wait.running_on_disk(43) == []


def test_finished_or_dead_waits_do_not_count(watch_dir, monkeypatch):
    import api.job_watch as jw
    _status(watch_dir, 42, state="done")
    assert background_wait.running_on_disk(42) == []
    _status(watch_dir, 42, pid=999999)
    monkeypatch.setattr(jw, "pid_alive", lambda pid: False)
    assert background_wait.running_on_disk(42) == []
