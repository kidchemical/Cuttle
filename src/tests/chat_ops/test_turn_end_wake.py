"""A finished agent turn wakes its supervisor early when the chat stopped needing attention.

``api.chat_delivery.end`` publishes ``turn_end``; ``triggers.wake_on_turn_end``
pulls the schedule's next check to now for awaiting_input / idle_with_work /
error, so the ordinary check-in runs within seconds instead of after the
interval. Busy and background-waiting chats never wake it; one early wake per
schedule per 120 s; the interval stays the backstop.
"""
from __future__ import annotations

import time

import pytest

from api import chat_delivery, session_events
from api.auth_db import AuthDatabase
from api.chat_ops import service, store, triggers


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    from api.gizmos import tasks
    monkeypatch.setattr(tasks, "database", lambda db=None: db)
    monkeypatch.setattr(triggers, "_turn_ends", set())
    monkeypatch.setattr(triggers, "_early_wakes", {})


@pytest.fixture
def chats(tmp_path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    supervisor = db.create_chat_session(owner, "supervisor")
    target = db.create_chat_session(owner, "orchestrator")
    other = db.create_chat_session(owner, "other")
    return db, owner, supervisor, target, other


def _idle(uid, sid, method, path, data=None, *, stream=False):
    assert path == "/api/chat-live-status"
    return {"success": True, "generating": False}


def _schedule(chats, target=None):
    db, _owner, sup, tgt, _other = chats
    op = service.supervise(f"CH-{target or tgt:06d}", f"CH-{sup:06d}", every=900,
                           instructions="keep it moving", db=db)["operation"]
    # As after its first check: next one an interval away.
    store.finish(op["id"], "pending", {}, due=time.time() + 900)
    return store.find(op["id"])


def _state(monkeypatch, state):
    seen = []

    def fake(target, actor=None, *, db=None, request=None, task_pick=None):
        seen.append(int(target))
        return {"state": state}

    monkeypatch.setattr(service, "status", fake)
    return seen


def _due(op):
    return store.find(op["id"])["due"]


@pytest.mark.parametrize("state", ["awaiting_input", "idle_with_work", "error"])
def test_stopped_states_pull_the_check_to_now(chats, monkeypatch, state):
    op = _schedule(chats)
    _state(monkeypatch, state)
    now = time.time()
    assert triggers.wake_on_turn_end({chats[3]}, now=now) == [op["id"]]
    assert _due(op) == pytest.approx(now)
    assert op["id"] in [o["id"] for o in store.due()]  # the outbox pump runs the normal check-in next
    [check] = [c for c in store.checks(op["id"]) if c["outcome"] == "early_wake"]
    assert check["detail"] == {"state": state, "trigger": "turn_end"}


@pytest.mark.parametrize("state", ["busy", "waiting_background", "idle", "cancelled"])
def test_working_waiting_or_idle_chats_never_wake_early(chats, monkeypatch, state):
    op = _schedule(chats)
    before = _due(op)
    _state(monkeypatch, state)
    assert triggers.wake_on_turn_end({chats[3]}) == []
    assert _due(op) == before


def test_one_early_wake_per_schedule_per_debounce_window(chats, monkeypatch):
    op = _schedule(chats)
    _state(monkeypatch, "awaiting_input")
    t0 = time.time()
    assert triggers.wake_on_turn_end({chats[3]}, now=t0) == [op["id"]]
    store.finish(op["id"], "pending", {}, due=t0 + 900)  # the check-in ran
    assert triggers.wake_on_turn_end({chats[3]}, now=t0 + 60) == []
    assert _due(op) == pytest.approx(t0 + 900)
    assert triggers.wake_on_turn_end({chats[3]}, now=t0 + triggers.EARLY_WAKE_DEBOUNCE_SEC + 1) == [op["id"]]


def test_paused_and_other_chats_schedules_are_left_alone(chats, monkeypatch):
    db, owner, sup, target, other = chats
    paused = _schedule(chats)
    store.set_paused(paused["id"], sup, True)
    elsewhere = _schedule(chats, target=other)
    before = _due(elsewhere)
    seen = _state(monkeypatch, "awaiting_input")
    assert triggers.wake_on_turn_end({target}) == []
    assert store.find(paused["id"])["state"] == "paused"
    assert _due(elsewhere) == before and seen == []  # no state read for unrelated chats


def test_a_check_in_already_running_is_not_pulled(chats, monkeypatch):
    op = _schedule(chats)
    assert store.claim(op["id"])  # dispatching
    seen = _state(monkeypatch, "awaiting_input")
    assert triggers.wake_on_turn_end({chats[3]}) == [] and seen == []


def test_a_due_schedule_is_not_moved_or_counted(chats, monkeypatch):
    op = _schedule(chats)
    now = time.time()
    store.finish(op["id"], "pending", {}, due=now - 5)
    _state(monkeypatch, "error")
    assert triggers.wake_on_turn_end({chats[3]}, now=now) == []
    assert _due(op) == pytest.approx(now - 5)
    assert triggers._early_wakes == {}


def test_the_real_state_reader_skips_a_chat_waiting_on_a_detached_job(chats, monkeypatch):
    """A pending unit watch resumes the chat by itself: waiting_background, no wake."""
    from api import job_watch as jw

    db, owner, sup, target, _other = chats
    op = _schedule(chats)
    db.upsert_chat_widget(widget_id="plan", user_id=owner, wtype="tasks", title="Tasks", scope="session",
                          session_id=target, project_path="",
                          payload={"items": [{"id": "1", "text": "build", "done": False, "children": []}]})
    assert service.status(f"CH-{target:06d}", f"CH-{sup:06d}", db=db, request=_idle)["state"] == "idle_with_work"
    monkeypatch.setattr(jw, "unit_state", lambda unit: {"queried": True, "running": True, "pid": 1})
    jw.register_unit_watch(unit="ch-rig-build", chat_session_id=f"CH-{target:06d}")
    out = service.status(f"CH-{target:06d}", f"CH-{sup:06d}", db=db, request=_idle)
    assert out["state"] == "waiting_background"
    assert out["live"]["background_wait"][0]["unit"] == "ch-rig-build"
    before = _due(op)
    assert triggers.wake_on_turn_end({target}, db=db, request=_idle) == []
    assert _due(op) == before
    # Watch gone (fired): open Tasks again, so the supervisor is woken early.
    for record in jw.unit_watch_records():
        jw.Path(record["record_path"]).unlink()
    assert triggers.wake_on_turn_end({target}, db=db, request=_idle) == [op["id"]]


def test_turn_end_event_is_queued_for_the_trigger_thread(monkeypatch):
    triggers.on_session_event("turn_end", 1821, {})
    triggers.on_session_event("turn_end", 1821, {})
    assert triggers._drain_turn_ends() == {1821}
    assert triggers._drain_turn_ends() == set()


# ── the publisher: chat_delivery.end ─────────────────────────────────────────


@pytest.fixture
def events():
    got = []

    def listener(kind, sid, detail):
        if kind == "turn_end":
            got.append(sid)

    unsubscribe = session_events.subscribe(listener)
    yield got
    unsubscribe()


def test_releasing_a_turn_publishes_turn_end_once(events):
    sid = "db_session_918273"
    assert chat_delivery.try_begin(sid)
    token = chat_delivery.current_turn(sid)
    chat_delivery.end(sid, turn=token)
    chat_delivery.end(sid, turn=token)  # belt-and-suspenders second end is silent
    assert events == [918273]


def test_a_stale_end_for_an_older_turn_publishes_nothing(events):
    sid = "918274"
    assert chat_delivery.try_begin(sid)
    old = chat_delivery.current_turn(sid)
    chat_delivery.end(sid, turn=old)
    assert chat_delivery.try_begin(sid)
    events.clear()
    chat_delivery.end(sid, turn=old)  # the slot belongs to the newer turn
    assert events == [] and chat_delivery.is_busy(sid)
    chat_delivery.end(sid, turn=chat_delivery.current_turn(sid))
    assert events == [918274]
