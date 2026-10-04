"""Automated tests for drain-first Flask restart protocol (no live daemon/Flask kill)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def restart_paths(tmp_path, monkeypatch):
    status = tmp_path / "cuttle_flask_restart_status.json"
    request = tmp_path / "cuttle_flask_restart_request.json"
    events = tmp_path / "flask_restart_events.jsonl"
    monkeypatch.setattr("api.flask_restart.STATUS_PATH", status)
    monkeypatch.setattr("api.flask_restart.REQUEST_PATH", request)
    monkeypatch.setattr("api.flask_restart.EVENTS_PATH", events)
    monkeypatch.setattr("api.flask_restart.PROJECT_ROOT", tmp_path)
    yield {"status": status, "request": request, "events": events, "root": tmp_path}


def _idle_work(*_args, **_kwargs):
    return {
        "busy_sessions": [],
        "live_process_sessions": [],
        "executing_jobs": [],
        "tasks": [],
        "deduped_job_count": 0,
        "active_count": 0,
        "is_idle": True,
    }


@pytest.mark.parametrize('callback', ['direct', 'delivery_end', 'delivery_cancel'])
def test_foreign_process_cannot_fire_flask_pending_restart(restart_paths, monkeypatch, callback):
    """A pytest/CLI child has an empty registry, not the host's idle state."""
    from api import chat_delivery, flask_restart as fr

    pending = fr.write_status({
        'restart_id': 'host-waiting', 'state': 'waiting_for_idle',
        'session_id': '849', 'flask_pid': os.getpid() + 100000,
        'generation': fr.live_flask_generation(),
    })
    attempts = []
    monkeypatch.setattr(fr, 'list_active_work', _idle_work)
    monkeypatch.setattr(fr, '_begin_handoff', lambda *args: attempts.append(args))
    if callback == 'direct':
        fr.maybe_fire_when_idle()
    elif callback == 'delivery_end':
        chat_delivery.end('test-child')
    else:
        chat_delivery.cancel_current_turn('test-child')
    assert attempts == [], 'Child process scheduled the host restart using its own idle registry'
    assert fr.read_status() == pending
    assert not restart_paths['request'].exists()


def _busy_work(session="99"):
    return {
        "busy_sessions": [session],
        "live_process_sessions": [session],
        "executing_jobs": [{"query_id": "q1", "pipeline_name": "Cuttle_Main"}],
        "tasks": [
            {
                "kind": "chat_run",
                "session_id": session,
                "query_id": "q1",
                "pipeline_name": "Cuttle_Main",
                "has_live_process": True,
            }
        ],
        "deduped_job_count": 1,
        "active_count": 1,
        "is_idle": False,
    }


def test_idle_graceful_persists_ack_and_writes_daemon_request(restart_paths, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", _idle_work)
    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)

    result = fr.request_restart(mode="graceful", session_id="12", user_source="test")
    assert result["success"] is True
    assert result["restart_scheduled"] is True
    rid = result["restart_id"]
    assert restart_paths["request"].exists()
    req = json.loads(restart_paths["request"].read_text(encoding="utf-8"))
    assert req["restart_id"] == rid
    st = fr.read_status()
    assert st["restart_id"] == rid
    assert st["state"] in ("preparing", "acknowledged")
    assert st["delivery"] == fr.DELIVERY_SENT
    assert st["ack_persisted"] is True


def test_card_restart_keeps_ack_out_of_the_transcript(restart_paths, monkeypatch):
    """Action-card restarts report on the card, so no chat bubbles are written."""
    from api import flask_restart as fr

    busy = {"on": True}

    def _work(*_a, **_k):
        return _busy_work("5") if busy["on"] else _idle_work()

    monkeypatch.setattr(fr, "list_active_work", _work)
    monkeypatch.setattr(fr, "_ensure_when_idle_watcher", lambda: None)
    persisted = []
    monkeypatch.setattr(
        fr,
        "_persist_session_message",
        lambda session_id, text, **kw: persisted.append((session_id, text)) or True,
    )

    result = fr.request_restart(mode="when-idle", session_id="134", chat_notify=False)
    assert result["state"] == "waiting_for_idle"
    assert persisted == []

    busy["on"] = False
    fr.maybe_fire_when_idle()
    st = fr.read_status()
    assert st["chat_notify"] is False
    assert st["ack_persisted"] is False
    assert persisted == []


def test_graceful_rejects_when_busy(restart_paths, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", lambda *a, **k: _busy_work("7"))
    persisted = []

    def _persist(session_id, text, **kwargs):
        persisted.append((session_id, text))
        return True

    monkeypatch.setattr(fr, "_persist_session_message", _persist)
    result = fr.request_restart(mode="graceful", session_id="7")
    assert result["success"] is False
    assert result["state"] == "rejected"
    assert result.get("suggest") == "when-idle"
    assert not restart_paths["request"].exists()
    assert persisted and "postponed" in persisted[0][1].lower()


def test_when_idle_queues_then_fires_after_idle(restart_paths, monkeypatch):
    from api import flask_restart as fr

    busy = {"on": True}

    def _work(*_a, **_k):
        return _busy_work("5") if busy["on"] else _idle_work()

    monkeypatch.setattr(fr, "list_active_work", _work)
    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    # Don't start the background watcher thread in tests
    monkeypatch.setattr(fr, "_ensure_when_idle_watcher", lambda: None)

    result = fr.request_restart(mode="when-idle", session_id="5")
    assert result["success"] is True
    assert result["state"] == "waiting_for_idle"
    assert not restart_paths["request"].exists()

    busy["on"] = False
    fired = fr.maybe_fire_when_idle()
    assert fired is not None
    assert result["restart_id"] == fired.get("restart_id") or fr.read_status()["restart_id"]
    assert restart_paths["request"].exists()
    st = fr.read_status()
    assert st["state"] in ("preparing", "acknowledged")


def test_force_requires_confirm(restart_paths, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", lambda *a, **k: _busy_work("1"))
    result = fr.request_restart(mode="force", session_id="1", force_confirm=False)
    assert result["success"] is False
    assert result.get("needs_confirm") is True
    assert not restart_paths["request"].exists()


def test_force_with_confirm_marks_interrupted(restart_paths, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", lambda *a, **k: _busy_work("3"))
    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    result = fr.request_restart(mode="force", session_id="3", force_confirm=True)
    assert result["success"] is True
    st = fr.read_status()
    assert st.get("interrupted_tasks")
    assert any(t.get("status") == "interrupted" for t in st["interrupted_tasks"])
    assert restart_paths["request"].exists()


def test_concurrent_restart_rejected(restart_paths, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", _idle_work)
    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    first = fr.request_restart(mode="graceful", session_id="9")
    assert first["success"] is True
    # Leave status in-flight
    fr.transition(first["restart_id"], "stopping_old_flask")
    second = fr.request_restart(mode="graceful", session_id="9")
    assert second["success"] is False
    assert "already in progress" in (second.get("error") or "").lower()


def test_stale_health_checking_does_not_block_restart(restart_paths, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", _idle_work)
    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    fr.write_status(
        {
            "restart_id": "respawn-stale",
            "state": "health_checking",
            "new_flask_pid": 999999,
            "updated_at": "2026-08-26T06:25:53.408381+00:00",
        }
    )
    result = fr.request_restart(mode="graceful", session_id="9")
    assert result["success"] is True
    st = fr.read_status()
    assert st.get("state") != "health_checking"
    assert st.get("restart_id") != "respawn-stale"


def test_completion_message_dedupe_by_restart_id(restart_paths, monkeypatch):
    from api import flask_restart as fr

    rid = fr.new_restart_id()
    fr.write_status(
        {
            "restart_id": rid,
            "state": "healthy",
            "new_flask_pid": 123,
            "generation": 2,
            "health_ms": 100,
            "client_notified": False,
        }
    )
    fr.mark_outcome_visible(rid)
    st = fr.read_status()
    assert st["client_notified"] is True
    fr.mark_outcome_visible(rid)  # second call no-op
    assert fr.read_status()["client_notified"] is True
    msg = fr.build_completion_message(st)
    assert rid in msg
    assert "complete" in msg.lower()


def test_daemon_consume_request(restart_paths, monkeypatch):
    from api import flask_restart as fr

    rid = fr.new_restart_id()
    fr.write_daemon_request(rid, "graceful", meta={"session_id": "1"})
    assert restart_paths["request"].exists()
    req = fr.consume_daemon_request()
    assert req["restart_id"] == rid
    assert not restart_paths["request"].exists()
    assert fr.consume_daemon_request() is None


def test_parse_restart_slash():
    from api.flask_restart import parse_restart_slash, handle_restart_slash

    assert parse_restart_slash("/restart")[0] == "status"
    assert parse_restart_slash("/restart graceful")[0] == "graceful"
    assert parse_restart_slash("/restart when-idle")[0] == "when-idle"
    mode, opts = parse_restart_slash("/restart force --yes")
    assert mode == "force"
    assert opts.get("force_confirm") is True
    assert parse_restart_slash("hello") is None


def test_health_timeout_does_not_report_success(restart_paths, monkeypatch):
    """Simulated daemon health failure → timed_out, not healthy."""
    from api import flask_restart as fr

    rid = fr.new_restart_id()
    fr.write_status({"restart_id": rid, "state": "health_checking"})
    fr.transition(rid, "timed_out", error="health check timed out", patch={"health_ms": 45000})
    st = fr.read_status()
    assert st["state"] == "timed_out"
    assert "complete" not in fr.build_completion_message(st).lower() or "timed_out" in fr.build_completion_message(st)


def test_executor_response_persisted_before_request_file(restart_paths, monkeypatch):
    """Ack persistence happens before daemon request (order guarantee)."""
    from api import flask_restart as fr

    order = []

    def _persist(*a, **k):
        order.append("persist")
        return True

    real_write = fr.write_daemon_request

    def _write(rid, mode, meta=None):
        order.append("request")
        return real_write(rid, mode, meta)

    monkeypatch.setattr(fr, "list_active_work", _idle_work)
    monkeypatch.setattr(fr, "_persist_session_message", _persist)
    monkeypatch.setattr(fr, "write_daemon_request", _write)
    fr.request_restart(mode="graceful", session_id="42")
    assert order == ["persist", "request"]


def test_overlapping_flask_instances_prevented_by_lock_semantics(restart_paths, monkeypatch):
    """Second in-flight restart is rejected while first is stopping/starting."""
    from api import flask_restart as fr
    import os

    monkeypatch.setattr(fr, "list_active_work", _idle_work)
    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    a = fr.request_restart(mode="graceful", session_id="1")
    # Use this process pid so stale-pid detection does not clear the in-flight record.
    fr.transition(a["restart_id"], "starting_new_flask", patch={"new_flask_pid": os.getpid()})
    b = fr.request_restart(mode="graceful", session_id="1")
    assert b["success"] is False


def test_web_status_endpoint_shape(restart_paths, monkeypatch):
    """status_snapshot returns fields the client polls."""
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", _idle_work)
    snap = fr.status_snapshot()
    assert snap["success"] is True
    assert "active_work" in snap
    assert "daemon_request_pending" in snap


def test_status_snapshot_heals_stale_health_checking(restart_paths, monkeypatch):
    """GET status must not leave cards looping on a dead health_checking record."""
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", _idle_work)
    fr.write_status(
        {
            "restart_id": "respawn-stale-ui",
            "state": "health_checking",
            "new_flask_pid": 999999,
            "updated_at": "2026-08-26T06:25:53.408381+00:00",
            "source": "exit_watch",
            "mode": "respawn",
        }
    )
    snap = fr.status_snapshot()
    st = snap["status"]
    assert st.get("state") in ("failed", "healthy", "cancelled", "timed_out", "rejected")
    assert st.get("state") != "health_checking"
    assert st.get("cleared_stale") is True or st.get("superseded") is True


def test_restart_chooser_standardizes_agent_variations_but_not_mixed_actions():
    from api.flask_restart import canonicalize_restart_form
    first = {'title': 'A', 'options': [{'id': 'idle', 'label': 'Wait', 'action': 'flask.restart', 'params': {'mode': 'when-idle'}}]}
    second = {'title': 'B', 'options': [{'id': 'go', 'label': 'Now', 'action': 'flask.restart', 'params': {'mode': 'graceful'}}]}
    a, b = canonicalize_restart_form(first), canonicalize_restart_form(second)
    assert a['title'] == b['title'] == 'Restart Flask (daemon-owned)'
    assert [(o['label'], o['action'], o['params']) for o in a['options']] == [(o['label'], o['action'], o['params']) for o in b['options']]
    assert next(o for o in a['options'] if o['params'].get('mode') == 'when-idle')['id'] == 'idle'
    mixed = dict(first, options=first['options'] + [{'id': 'push', 'action': 'git.push'}])
    assert canonicalize_restart_form(mixed) == mixed
