"""P0/P1 regressions from the CH-000545 / CH-000558–560 restart RCA.

These tests exist to *demonstrate* the bugs, then stay as locks after the fix:

P0
- Force cannot supersede a ``waiting_for_idle`` restart.
- ``waiting_for_idle`` survives a Flask/Cuttle PID change (24h stale window).
- Drain can report idle while live-work enumeration failed (false-idle kill).

P1
- Restart toast omits the blocking chat handle (CH-000560).
- Force on a locked when-idle card is ``already_locked``.
- First-message stream retry mints a second chat without ``chat_request_id``.
- Auth SQLite connections skip WAL / busy_timeout.
"""

from __future__ import annotations

import os
from pathlib import Path
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


def _busy_work(session="560"):
    return {
        "busy_sessions": [session],
        "live_process_sessions": [session],
        "executing_jobs": [],
        "tasks": [
            {
                "kind": "chat_run",
                "session_id": session,
                "query_id": "q-diag",
                "pipeline_name": "Cursor Auto",
                "has_live_process": True,
            }
        ],
        "deduped_job_count": 0,
        "active_count": 1,
        "is_idle": False,
    }


# ── P0: Force vs waiting_for_idle ─────────────────────────────────────────────


def test_force_supersedes_waiting_for_idle(restart_paths, monkeypatch):
    """CH-000545-10: Force on a when-idle wait used to return 'already in progress'."""
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "list_active_work", lambda *a, **k: _busy_work())
    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    monkeypatch.setattr(fr, "_ensure_when_idle_watcher", lambda: None)

    scheduled = fr.request_restart(mode="when-idle", session_id="545", chat_notify=False)
    assert scheduled["state"] == "waiting_for_idle"
    wait_id = scheduled["restart_id"]

    forced = fr.request_restart(
        mode="force", session_id="545", force_confirm=True, chat_notify=False
    )
    assert forced["success"] is True, forced
    assert forced.get("restart_scheduled") is True
    st = fr.read_status()
    assert st.get("mode") == "force"
    assert st.get("restart_id") != wait_id
    assert st.get("state") not in ("waiting_for_idle",)


def test_waiting_for_idle_is_stale_after_flask_pid_changes(restart_paths):
    """Full Cuttle restart left flask-restart-g11 waiting because stale=24h."""
    from api import flask_restart as fr

    fr.write_status(
        {
            "restart_id": "orphaned-wait",
            "state": "waiting_for_idle",
            "mode": "when-idle",
            "flask_pid": 1,
            "old_flask_pid": 1,
            "generation": 11,
            "session_id": "545",
        }
    )
    st = fr.read_status()
    assert fr._status_is_stale(st) is True
    snap = fr.status_snapshot()
    assert snap["status"].get("state") != "waiting_for_idle"
    assert snap["status"].get("cleared_stale") is True or snap["status"].get("superseded") is True


def test_same_process_waiting_for_idle_is_not_stale(restart_paths):
    from api import flask_restart as fr

    fr.write_status(
        {
            "restart_id": "live-wait",
            "state": "waiting_for_idle",
            "mode": "when-idle",
            "flask_pid": os.getpid(),
            "old_flask_pid": os.getpid(),
        }
    )
    assert fr._status_is_stale(fr.read_status()) is False


def test_when_idle_does_not_fire_when_live_work_enumeration_fails(
    restart_paths, monkeypatch
):
    """09:01 false-idle: swallowed registry errors looked like zero tasks."""
    from api import chat_delivery, chat_run_registry
    from api import flask_restart as fr
    import api.active_executions as ae

    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    monkeypatch.setattr(fr, "_ensure_when_idle_watcher", lambda: None)
    monkeypatch.setattr(chat_delivery, "busy_entries", lambda: [])
    monkeypatch.setattr(ae, "get_executing_jobs", lambda: [])

    def _boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(chat_run_registry, "active_run_session_ids", _boom)

    fr.write_status(
        {
            "restart_id": "wait-enum",
            "state": "waiting_for_idle",
            "mode": "when-idle",
            "session_id": "545",
            "flask_pid": os.getpid(),
            "old_flask_pid": os.getpid(),
        }
    )
    work = fr.list_active_work(exclude_session_id="545")
    assert work["is_idle"] is False
    assert work.get("enumeration_failed") is True
    assert fr.maybe_fire_when_idle() is None
    assert not restart_paths["request"].exists()


def test_live_process_without_busy_lock_blocks_when_idle(restart_paths, monkeypatch):
    """Codex still running after the chat busy lock dropped must still drain-block."""
    from api import chat_delivery, chat_run_registry as crr
    from api import flask_restart as fr
    import api.active_executions as ae

    monkeypatch.setattr(chat_delivery, "busy_entries", lambda: [])
    monkeypatch.setattr(ae, "get_executing_jobs", lambda: [])
    crr._runs.clear()
    try:
        crr.begin_run("560", query_id="q-codex")

        class _Alive:
            def poll(self):
                return None

        crr.attach_process("560", _Alive())
        work = fr.list_active_work(exclude_session_id="545")
        assert work["is_idle"] is False
        assert any(str(t.get("session_id")) == "560" for t in work["tasks"])
    finally:
        crr._runs.clear()


def test_asyncio_cli_without_poll_still_counts_as_live():
    from api import chat_run_registry as crr

    crr._runs.clear()
    try:
        crr.begin_run("558", query_id="q-asyncio")

        class _AsyncioProc:
            pid = 424242
            returncode = None

        crr.attach_process("558", _AsyncioProc())
        assert crr.has_live_process("558") is True
        assert "558" in crr.active_run_session_ids()
    finally:
        crr._runs.clear()


# ── P1: toast names blocking chats ────────────────────────────────────────────


def test_waiting_toast_names_blocking_chat_handle():
    from api import flask_restart as fr

    toast = fr._restart_card_toast(
        mode="when-idle",
        state="waiting_for_idle",
        active_work=_busy_work("560"),
    )
    assert "CH-000560" in toast
    assert "1" in toast


# ── P1: first-message retry must not mint a second chat ───────────────────────


def test_same_chat_request_id_reuses_minted_session():
    from api.chat_turn_idempotency import mint_or_reuse, reset_for_tests

    reset_for_tests()

    class _DB:
        def __init__(self):
            self.created = []
            self._n = 557

        def create_chat_session(self, user_id, name=None):
            self._n += 1
            self.created.append(self._n)
            return self._n

    db = _DB()
    first, created_first = mint_or_reuse(
        db, user_id=1, chat_session_id=None, chat_request_id="ct_jam"
    )
    second, created_second = mint_or_reuse(
        db, user_id=1, chat_session_id=None, chat_request_id="ct_jam"
    )
    assert created_first is True
    assert created_second is False
    assert first == second == 558
    assert db.created == [558]


def test_different_chat_request_ids_mint_separate_sessions():
    from api.chat_turn_idempotency import mint_or_reuse, reset_for_tests

    reset_for_tests()

    class _DB:
        def __init__(self):
            self._n = 557

        def create_chat_session(self, user_id, name=None):
            self._n += 1
            return self._n

    db = _DB()
    a, _ = mint_or_reuse(db, user_id=1, chat_session_id=None, chat_request_id="ct_a")
    b, _ = mint_or_reuse(db, user_id=1, chat_session_id=None, chat_request_id="ct_b")
    assert a != b


def test_chat_page_stamps_request_id_and_session_before_stream_retry():
    src = (
        Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
    ).read_text(encoding="utf-8")
    assert "requestBody.chat_request_id" in src
    assert "requestBody.session_id = sid" in src
    # Retry must reuse the same body object (session_id + request id survive).
    assert "JSON.stringify({ ...requestBody, stream: useStream })" in src
    assert "retrying with stream=false" in src


def test_resolve_auth_session_reuses_chat_request_id(monkeypatch):
    """Two /api/chat POSTs with the same chat_request_id must share one mint."""
    from api import web_chat_api as wca
    from api.chat_turn_idempotency import reset_for_tests

    reset_for_tests()

    class _DB:
        def __init__(self):
            self.n = 0

        def verify_auth_session(self, token):
            return {"id": 9}

        def create_chat_session(self, user_id, name=None):
            self.n += 1
            return 700 + self.n

    db = _DB()
    monkeypatch.setattr(wca, "get_request_session_token", lambda: "tok")
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    with wca.app.test_request_context(
        "/api/chat",
        method="POST",
        json={"chat_request_id": "ct_dup", "message": "hi", "stream": False},
    ):
        _u1, s1, c1 = wca._resolve_auth_chat_session(None)
        _u2, s2, c2 = wca._resolve_auth_chat_session(None)
    assert c1 is True
    assert c2 is False
    assert s1 == s2 == 701
    assert db.n == 1


# ── P1: SQLite WAL + busy_timeout ─────────────────────────────────────────────


def test_auth_db_enables_wal_and_busy_timeout(tmp_path):
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    conn = db._get_connection()
    try:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert str(journal).lower() == "wal"
    assert int(timeout) >= 30000
