"""Live-status store behavior (P4-1 baseline).

Pins the current ``api.web_chat_api`` live-status contract BEFORE the
store moves to the owned ``api.chat_live_status`` service: set/get
roundtrip across session-id key variants, field preservation,
TTL expiry (normal + Connecting fast path), clear, active-id listing
with stale eviction, and the Stop cancel guard. Post-extraction the
same assertions must hold through the service and the compatibility
wrappers; store identity across both modules is asserted separately.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

import api.web_chat_api as wca
from api import chat_delivery as delivery


def setup_function(_fn=None):
    delivery._busy.clear()
    delivery._pending.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()
    with wca._chat_live_status_lock:
        wca._chat_live_status.clear()


def _plant(session_id, *, active=True, status="Working", age_s=0):
    keys = wca._live_status_keys(session_id)
    entry = {
        "active": active,
        "status": status,
        "updated_at": time.time() - age_s,
        "report_url": None,
        "query_id": None,
    }
    with wca._chat_live_status_lock:
        for k in keys:
            wca._chat_live_status[k] = entry
    return keys


def test_set_get_roundtrip_across_key_variants():
    wca.set_chat_live_status("db_session_12", "Working", active=True)
    got_bare = wca.get_chat_live_status("12")
    got_prefixed = wca.get_chat_live_status("db_session_12")
    assert got_bare["active"] is True and got_bare["status"] == "Working"
    assert got_prefixed == got_bare


def test_old_turn_cannot_overwrite_or_clear_new_turn_status():
    from api import chat_live_status as live

    live.set_live_status("db_session_12", "old", query_id="old-query", turn=10)
    live.set_live_status("12", "new", turn=11)
    # New turn does not inherit the old query link or identity.
    assert live.get_live_status("12")["query_id"] is None
    live.set_live_status("12", "late old tool", turn=10)
    live.clear_live_status("db_session_12", turn=10)
    assert live.get_live_status("12")["status"] == "new"
    live.clear_live_status("12", turn=11)
    assert not live.get_live_status("12")["active"]


@pytest.mark.parametrize("cleanup", ["clear", "read_expiry", "list_expiry"])
def test_old_writer_cannot_republish_after_newer_status_is_removed(cleanup):
    """Freshness check passes, then the writer stalls until a newer turn ends."""
    import threading
    from api import chat_live_status as live

    checked = threading.Event()
    resume = threading.Event()
    errors = []

    def checked_before_resend(sid):
        checked.set()
        assert resume.wait(3)
        return False  # valid at the time of the old check

    def old_writer():
        try:
            live.set_live_status(
                "db_session_12", "old late tool", turn=10,
                is_cancelled=checked_before_resend,
            )
        except Exception as exc:
            errors.append(exc)

    writer = threading.Thread(target=old_writer)
    writer.start()
    try:
        assert checked.wait(3)
        live.set_live_status("12", "new work", turn=11)
        if cleanup == "clear":
            live.clear_live_status("12", turn=11)
        else:
            with live._LOCK:
                live._STORE["12"]["updated_at"] = time.time() - live.TTL_SECONDS - 1
            if cleanup == "read_expiry":
                live.get_live_status("12")
            else:
                live.active_live_session_ids()
        resume.set()
        writer.join(3)
        assert not writer.is_alive()
        assert not errors
        status = live.get_live_status("12")
        assert not status["active"]
        assert status["status"] is None
        assert "12" not in live.active_live_session_ids()
    finally:
        resume.set()
        writer.join(3)
        live.clear_live_status("12")


def test_set_preserves_prior_fields_and_defaults_connecting():
    wca.set_chat_live_status("s1", active=True)
    first = wca.get_chat_live_status("s1")
    assert first["status"] == "Connecting..."
    wca.set_chat_live_status("s1", "Step two", report_url="http://r", query_id="q")
    second = wca.get_chat_live_status("s1")
    assert second["status"] == "Step two"
    assert second["report_url"] == "http://r" and second["query_id"] == "q"
    wca.set_chat_live_status("s1", None, active=True)
    third = wca.get_chat_live_status("s1")
    assert third["status"] == "Step two"
    assert third["report_url"] == "http://r"


def test_get_evicts_expired_active_row():
    _plant("old", age_s=45 * 60 + 5)
    got = wca.get_chat_live_status("old")
    assert got == {
        "active": False, "status": None, "updated_at": None,
        "report_url": None, "query_id": None,
    }
    with wca._chat_live_status_lock:
        assert not any("old" in k for k in wca._chat_live_status)


def test_connecting_rows_expire_fast():
    _plant("conn", status="Connecting...", age_s=95)
    assert wca.get_chat_live_status("conn")["active"] is False
    _plant("conn2", status="Connecting...", age_s=30)
    assert wca.get_chat_live_status("conn2")["active"] is True


def test_clear_removes_all_key_variants():
    wca.set_chat_live_status("db_session_12", "Working", active=True)
    wca.clear_chat_live_status("12")
    assert wca.get_chat_live_status("db_session_12")["active"] is False
    assert wca.get_chat_live_status("12")["active"] is False


def test_active_ids_lists_bare_sessions_and_skips_idle():
    wca.set_chat_live_status("db_session_7", "Working", active=True)
    wca.set_chat_live_status("s8", "done", active=False)
    _plant("stale", age_s=45 * 60 + 5)
    ids = wca.active_live_session_ids()
    assert "7" in ids
    assert "s8" not in ids and "stale" not in ids


def test_set_active_noops_while_turn_cancelled():
    from api.chat_delivery import _session_keys

    for k in _session_keys("cancelled-chat"):
        delivery._cancel_sticky[k] = 1
    try:
        wca.set_chat_live_status("cancelled-chat", "late worker", active=True)
        assert wca.get_chat_live_status("cancelled-chat")["active"] is False
        # Inactive bookkeeping writes still apply (no republish risk).
        wca.set_chat_live_status("cancelled-chat", "done", active=False)
        assert wca.get_chat_live_status("cancelled-chat")["status"] == "done"
    finally:
        delivery._cancel_sticky.clear()


def test_unknown_session_reads_idle():
    assert wca.get_chat_live_status("nope")["active"] is False


def test_service_owns_store_without_monolith_import():
    import api.chat_live_status as svc

    src = Path(svc.__file__).read_text(encoding="utf-8")
    assert "web_chat_api" not in src
    assert "chat_delivery" not in src


def test_store_identity_across_service_and_wrapper():
    import api.chat_live_status as svc

    assert wca._chat_live_status is svc._STORE
    assert wca._chat_live_status_lock is svc._LOCK
    svc.set_live_status("ident-a", "via service", active=True)
    assert wca.get_chat_live_status("ident-a")["status"] == "via service"
    wca.set_chat_live_status("ident-b", "via wrapper", active=True)
    assert svc.get_live_status("ident-b")["status"] == "via wrapper"
    wca.clear_chat_live_status("ident-a")
    assert svc.get_live_status("ident-a")["active"] is False


def test_module_cache_shares_one_store_instance():
    import sys

    import api.chat_live_status as first

    second = sys.modules["api.chat_live_status"]
    assert first is second
    assert first._STORE is second._STORE


def test_orchestrator_parent_progress_writes_inactive_row():
    from api.agent_router.supervised.orchestrator import _emit_parent_progress
    import api.chat_live_status as svc

    _emit_parent_progress("svc-parent-1", "Worker running…")
    try:
        row = svc.get_live_status("svc-parent-1")
        assert row["active"] is False
        assert row["status"] == "Worker running…"
    finally:
        svc.clear_live_status("svc-parent-1")


def test_cancelled_wrapper_guard_matches_service_default():
    import api.chat_live_status as svc
    from api.chat_delivery import _session_keys

    for k in _session_keys("guard-chat"):
        delivery._cancel_sticky[k] = 1
    try:
        # Wrapper injects the cancel predicate: republish suppressed.
        wca.set_chat_live_status("guard-chat", "late", active=True)
        assert wca.get_chat_live_status("guard-chat")["active"] is False
        # Bare service without a predicate keeps prior behavior (writes).
        svc.set_live_status("guard-chat", "late", active=True)
        assert svc.get_live_status("guard-chat")["active"] is True
    finally:
        delivery._cancel_sticky.clear()


def test_live_badge_preserved_for_status_updates_replaced_on_fallback_and_fenced():
    from api import chat_live_status as live
    codex = {'chips': [{'label': 'Codex - GPT-6.1-Sol', 'meta': '/codex'}]}
    claude = {'chips': [{'label': 'Claude - Opus 5.5', 'meta': '/claude'}]}
    live.set_live_status('12', turn=1, slash_command=codex)
    live.set_live_status('12', 'Working', turn=1)
    assert live.get_live_status('12')['slash_command'] == codex
    live.set_live_status('12', turn=1, slash_command=claude)
    assert live.get_live_status('12')['slash_command'] == claude
    live.set_live_status('12', turn=2)
    assert live.get_live_status('12')['slash_command'] is None
    live.set_live_status('12', turn=1, slash_command=codex)
    assert live.get_live_status('12')['slash_command'] is None
    live.clear_live_status('12', turn=2)
    assert not live.get_live_status('12')['active']
