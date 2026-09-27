"""Busy locks / run registry must not leave history spinners stuck forever."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest


def test_end_run_clears_entry_when_proc_already_exited():
    from api import chat_run_registry as reg

    # Isolate module state
    reg._runs.clear()
    sid = "zombie_proc_test"
    reg.begin_run(sid, query_id="q1")
    dead = MagicMock()
    dead.poll.return_value = 0  # exited
    dead.pid = None
    dead.returncode = 0
    reg.attach_process(sid, dead)

    reg.end_run(sid, query_id="q1")  # no proc= — old bug left the entry pinned
    assert not reg.has_live_process(sid)
    assert not any(sid in k or k.endswith(sid) for k in reg._runs)


def test_reconcile_clears_busy_without_live_work(monkeypatch):
    from api import chat_delivery as delivery

    delivery._busy.clear()
    delivery._pending.clear()

    monkeypatch.setattr(delivery, "_BUSY_ZOMBIE_GRACE", 0.01)
    monkeypatch.setattr(delivery, "_session_has_live_work", lambda _sid: False)

    assert delivery.try_begin("42")
    time.sleep(0.02)
    cleared = delivery.reconcile_zombie_busy()
    assert "42" in cleared
    assert not delivery.is_busy("42")


def test_try_begin_clears_stale_pending():
    from api import chat_delivery as delivery

    delivery._busy.clear()
    delivery._pending.clear()

    assert delivery.try_begin("90")
    delivery.store_result("90", {"success": True, "response": "old reply", "query_id": "old"})
    delivery.end("90")
    # Parked reply left over after the prior turn finished.
    assert delivery._pending  # noqa: presence check

    assert delivery.try_begin("90")
    assert delivery.take_result("90") is None
    delivery.end("90")


def test_take_result_aliases_cleared_together():
    from api import chat_delivery as delivery

    delivery._busy.clear()
    delivery._pending.clear()
    delivery.store_result("db_session_7", {"success": True, "response": "hi"})
    got = delivery.take_result("7")
    assert got and got.get("response") == "hi"
    assert delivery.take_result("db_session_7") is None


def test_turn_token_marks_superseded_run_stale():
    from api import chat_delivery as delivery

    delivery._busy.clear()
    delivery._pending.clear()
    delivery._last_turn.clear()

    assert delivery.try_begin("55")
    first = delivery.current_turn("55")
    assert first is not None
    assert delivery.is_stale_turn("55", first) is False

    # User pressed Stop, then sent again — a new turn owns the chat.
    delivery.end("55")
    assert delivery.try_begin("db_session_55")
    second = delivery.current_turn("55")
    assert second is not None and second > first
    assert delivery.is_stale_turn("55", first) is True
    assert delivery.is_stale_turn("55", second) is False

    # The abandoned worker finishing late must not release the live lock.
    delivery.end("55", turn=first)
    assert delivery.is_busy("55") is True
    delivery.end("55", turn=second)
    assert delivery.is_busy("55") is False


def test_cancel_current_turn_makes_in_flight_worker_stale():
    from api import chat_delivery as delivery

    delivery._busy.clear()
    delivery._pending.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()

    assert delivery.try_begin("199")
    token = delivery.current_turn("199")
    assert token is not None
    delivery.store_result("199", {"success": True, "response": "late"})

    cancelled = delivery.cancel_current_turn("199")
    assert cancelled == token
    assert delivery.is_busy("199") is False
    assert delivery.is_stale_turn("199", token) is True
    assert delivery.is_turn_cancelled("199") is True
    assert delivery.is_turn_cancelled("db_session_199") is True
    assert delivery.peek_result("199") is None

    # Next send starts a fresh turn and clears the sticky cancel.
    assert delivery.try_begin("199")
    next_token = delivery.current_turn("199")
    assert next_token is not None and next_token > token
    assert delivery.is_turn_cancelled("199") is False
    assert delivery.is_stale_turn("199", token) is True
    assert delivery.is_stale_turn("199", next_token) is False
    delivery.end("199")


def test_cancel_before_begin_run_is_sticky():
    from api import chat_delivery as delivery
    from api import chat_run_registry as reg

    delivery._busy.clear()
    delivery._pending.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()
    reg._runs.clear()

    assert delivery.try_begin("88")
    token = delivery.current_turn("88")
    info = reg.cancel_session_runs("88")
    assert info.get("cancelled") is True
    assert delivery.is_stale_turn("88", token) is True
    assert delivery.is_turn_cancelled("88") is True
    assert reg.is_run_cancelled("88") is True
    assert reg.is_run_cancelled("db_session_88") is True

    # CLI spawn after Stop must inherit the cancelled event.
    ev = reg.begin_run("88", query_id="late")
    assert ev.is_set()
    assert reg.is_run_cancelled("88") is True


def test_new_turn_after_stop_is_not_cancelled():
    from api import chat_delivery as delivery
    from api import chat_run_registry as reg

    delivery._busy.clear()
    delivery._pending.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()
    reg._runs.clear()

    assert delivery.try_begin("91")
    reg.cancel_session_runs("91")
    assert delivery.try_begin("91")
    ev = reg.begin_run("91", query_id="next")
    assert ev.is_set() is False
    assert delivery.is_turn_cancelled("91") is False
    assert reg.is_run_cancelled("91") is False
    delivery.end("91")
    reg.end_run("91", query_id="next")


def test_begin_run_clears_cancel_while_old_proc_still_alive():
    from api import chat_delivery as delivery
    from api import chat_run_registry as reg
    from unittest.mock import MagicMock

    delivery._busy.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()
    reg._runs.clear()

    assert delivery.try_begin("77")
    live = MagicMock()
    live.poll.return_value = None  # still running
    live.pid = None
    live.returncode = None
    reg.begin_run("77", query_id="q-old")
    reg.attach_process("77", live)
    reg.cancel_session_runs("77")
    assert reg.is_run_cancelled("77") is True

    # User sends again before the old CLI has exited.
    assert delivery.try_begin("77")
    ev = reg.begin_run("77", query_id="q-new")
    assert ev.is_set() is False
    assert reg.is_run_cancelled("77") is False
    delivery.end("77")
    reg.end_run("77", query_id="q-new")


def test_cancel_session_runs_resolves_ch_handle():
    from api import chat_delivery as delivery
    from api import chat_run_registry as reg
    from unittest.mock import MagicMock

    delivery._busy.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()
    reg._runs.clear()

    assert delivery.try_begin("201")
    dead = MagicMock()
    dead.poll.return_value = None
    dead.pid = None
    dead.returncode = None
    reg.begin_run("201", query_id="q1")
    reg.attach_process("201", dead)

    info = reg.cancel_session_runs("CH-000201")
    assert info.get("cancelled") is True
    assert reg.is_run_cancelled("201") is True
    delivery.end("201")


def test_turn_guard_is_inert_without_a_token():
    from api import chat_delivery as delivery

    delivery._busy.clear()
    delivery._last_turn.clear()
    assert delivery.is_stale_turn("77", None) is False
    assert delivery.current_turn("77") is None


def test_peek_result_does_not_consume():
    from api import chat_delivery as delivery

    delivery._busy.clear()
    delivery._pending.clear()
    delivery.store_result("42", {"success": True, "response": "multi-device"})
    peeked = delivery.peek_result("db_session_42")
    assert peeked and peeked.get("response") == "multi-device"
    peeked2 = delivery.peek_result("42")
    assert peeked2 and peeked2.get("response") == "multi-device"
    taken = delivery.take_result("42")
    assert taken and taken.get("response") == "multi-device"
    assert delivery.peek_result("42") is None
