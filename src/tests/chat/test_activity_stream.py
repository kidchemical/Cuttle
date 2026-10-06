"""Activity push stream: live-status changes wake subscribers."""
from __future__ import annotations

import threading
import time

from flask import Flask

from api import activity_stream
from api import chat_live_status as live


def test_set_and_clear_bump_change_version():
    start = live.change_version()
    live.set_live_status("9101", "Calling Claude Code…")
    after_set = live.change_version()
    live.clear_live_status("9101")
    assert start < after_set < live.change_version()


def test_wait_for_change_wakes_on_set_and_times_out_quietly():
    since = live.change_version()
    assert live.wait_for_change(since, 0.05) == since

    threading.Timer(0.05, lambda: live.set_live_status("9102", "Thinking")).start()
    began = time.monotonic()
    assert live.wait_for_change(since, 5.0) != since
    assert time.monotonic() - began < 2.0
    live.clear_live_status("9102")


def _client(monkeypatch, user):
    err = None if user else ({"success": False}, 401)
    monkeypatch.setattr(activity_stream, "require_authenticated", lambda: (user, err))
    app = Flask(__name__)
    app.register_blueprint(activity_stream.activity_bp)
    return app.test_client()


def test_stream_requires_auth(monkeypatch):
    assert _client(monkeypatch, None).get("/api/activity/stream").status_code == 401


def test_stream_pushes_counter_only_on_change(monkeypatch):
    resp = _client(monkeypatch, {"id": 1}).get("/api/activity/stream", buffered=False)
    assert resp.mimetype == "text/event-stream"
    chunks = resp.response
    first = next(chunks).decode()
    assert first.startswith("event: activity")

    threading.Timer(0.05, lambda: live.set_live_status("9103", "Secret status")).start()
    pushed = next(chunks).decode()
    resp.close()
    live.clear_live_status("9103")
    assert pushed.startswith("event: activity")
    assert "9103" not in pushed and "Secret" not in pushed
