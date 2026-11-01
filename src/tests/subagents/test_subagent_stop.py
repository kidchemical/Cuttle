"""Stop / cancel of sub-agent children whose CLI lives in another process.

The spawn CLI owns each child's harness CLI. Flask (Stop, ``/api/chat-cancel``,
the agent feed's End) may not signal it, so cancel records a stop request on
the child row and the owner's ``ChildStopWatch`` kills the run. A cancelled
serial step ends the chain instead of letting the next step start.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from api.auth_db import AuthDatabase
from api.subagents import service, stop_watch, store
from api.subagents.stop_watch import ChildStopWatch, stop_requested
from api.subagents.types import ChildRecord


def _seed(tmp_path: Path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    parent = db.create_chat_session(owner, "Parent chat")
    db.add_message(parent, "user", "port the features")
    return db, parent


def _child(status="running", stop_at=None) -> ChildRecord:
    return ChildRecord(id="c1", batch_id="b1", session_id=7, status=status, stop_requested_at=stop_at)


def _insert_rows(db, parent, titles, collect):
    from api.subagents.types import ChildSpec

    row = db.get_chat_session_by_id(parent)
    batch = store.insert_batch(
        db, parent_session_id=parent, user_id=row["user_id"], collect=collect, lifetime="one_shot"
    )
    for i, title in enumerate(titles):
        sid = db.create_subagent_chat_session(row["user_id"], parent_session_id=parent, session_name=title)
        store.insert_child(
            db, batch_id=batch.id, session_id=sid, sort_index=i,
            spec=ChildSpec(title=title, message=title), prompt=title,
        )
    return store.get_batch(db, batch.id)


# --- decision ---------------------------------------------------------------

def test_stop_request_counts_only_from_this_watch_on():
    assert stop_requested(_child(stop_at=10.0), since=10.0, in_flight=True)
    assert stop_requested(_child(stop_at=12.0), since=10.0, in_flight=False)
    # An earlier turn's Stop must not kill a conversational follow-up.
    assert not stop_requested(_child(stop_at=9.0), since=10.0, in_flight=True)
    assert not stop_requested(None, since=0.0, in_flight=True)


def test_cancelled_row_counts_while_the_turn_is_in_flight():
    assert stop_requested(_child(status="cancelled"), since=10.0, in_flight=True)
    assert not stop_requested(_child(status="cancelled"), since=10.0, in_flight=False)


# --- watch ------------------------------------------------------------------

def test_watch_kills_the_session_when_stop_is_requested(tmp_path: Path):
    db, parent = _seed(tmp_path)
    batch = _insert_rows(db, parent, ["Solo"], "all")
    child = batch.children[0]
    killed = []
    watch = ChildStopWatch(db, child.id, child.session_id, cancel=killed.append, has_live=lambda _s: True)
    assert watch.check() is True and not killed
    store.request_stop(db, child.id, at=watch.since + 1)
    assert watch.check() is False
    assert killed == [child.session_id] and watch.fired


def test_watch_outlives_the_turn_while_a_detached_cli_runs(tmp_path: Path):
    """A finished Claude turn can leave its CLI in a background wait; End must still kill it."""
    db, parent = _seed(tmp_path)
    child = _insert_rows(db, parent, ["Bg"], "all").children[0]
    store.update_child(db, child.id, status="done", result="reply", finished=True)
    live = {"v": True}
    killed = []
    watch = ChildStopWatch(db, child.id, child.session_id, cancel=killed.append, has_live=lambda _s: live["v"])
    watch.turn_finished()
    assert watch.check() is True  # CLI still alive: keep watching
    service.cancel_session(child.session_id, db=db)  # done row: status stays, stop is recorded
    assert store.get_child(db, child.id).status == "done"
    assert watch.check() is False and killed == [child.session_id]


def test_watch_ends_when_turn_is_over_and_nothing_is_alive(tmp_path: Path):
    db, parent = _seed(tmp_path)
    child = _insert_rows(db, parent, ["Quiet"], "all").children[0]
    watch = ChildStopWatch(db, child.id, child.session_id, cancel=lambda _s: None, has_live=lambda _s: False)
    watch.turn_finished()
    assert watch.check() is False and not watch.fired


# --- cancel + serial --------------------------------------------------------

def test_cancel_records_a_stop_request_even_on_a_finished_child(tmp_path: Path):
    db, parent = _seed(tmp_path)
    child = _insert_rows(db, parent, ["Done"], "all").children[0]
    store.update_child(db, child.id, status="done", finished=True)
    before = time.time()
    service.cancel_session(child.session_id, db=db)
    row = store.get_child(db, child.id)
    assert row.status == "done"
    assert row.stop_requested_at is not None and row.stop_requested_at >= before - 1


def test_cancelling_the_running_serial_step_cancels_the_rest(tmp_path: Path):
    db, parent = _seed(tmp_path)
    batch = _insert_rows(db, parent, ["One", "Two", "Three", "Four"], "serial")
    one, two, three, four = batch.children
    store.update_child(db, one.id, status="done", result="ok", finished=True)
    store.update_child(db, two.id, status="running", started=True)

    service.cancel_session(two.session_id, db=db)

    kids = {c.label: c.status for c in store.get_batch(db, batch.id).children}
    assert kids == {"One": "done", "Two": "cancelled", "Three": "cancelled", "Four": "cancelled"}
    assert store.get_batch(db, batch.id).status == "cancelled"
    # Nothing left to kick: the chain does not "come back".
    started = []
    service._kick_next_serial(db, store.get_batch(db, batch.id), runner=lambda **kw: started.append(kw))
    assert started == []


def test_ending_a_finished_step_does_not_cancel_the_chain(tmp_path: Path):
    """End on a done child (leftover background CLI) only stops that CLI."""
    db, parent = _seed(tmp_path)
    batch = _insert_rows(db, parent, ["One", "Two", "Three"], "serial")
    one, two, _three = batch.children
    store.update_child(db, one.id, status="done", result="ok", finished=True)
    store.update_child(db, two.id, status="running", started=True)
    service.cancel_session(one.session_id, db=db)
    kids = {c.label: c.status for c in store.get_batch(db, batch.id).children}
    assert kids == {"One": "done", "Two": "running", "Three": "pending"}


def test_end_from_another_process_stops_the_owner_run_and_the_chain(tmp_path: Path, monkeypatch):
    """The CH-001838 case: End lands in Flask while the spawn CLI runs the step.

    The step's runner blocks like a live CLI until the owner's watch kills the
    session; the next serial step must never start.
    """
    db, parent = _seed(tmp_path)
    monkeypatch.setattr(stop_watch, "POLL_SEC", 0.02)
    killed = threading.Event()
    killed_sessions = []

    def fake_cancel_runs(session_id):
        killed_sessions.append(int(session_id))
        killed.set()

    monkeypatch.setattr(stop_watch, "_cancel_runs", fake_cancel_runs)
    monkeypatch.setattr(stop_watch, "_has_live_process", lambda _sid: False)

    ran = []
    second_started = threading.Event()

    def runner(**kwargs):
        title = kwargs["spec"].title
        ran.append(title)
        if title == "Two":
            second_started.set()
            assert killed.wait(5), "owner watch never killed the run"
            return {"success": False, "error": "killed", "response": ""}
        return {"success": True, "response": f"{title} ok"}

    def end_from_feed():
        assert second_started.wait(5)
        two = next(c for c in store.list_batches_for_parent(db, parent)[0].children if c.label == "Two")
        service.cancel_session(two.session_id, db=db)  # what /api/chat-cancel does

    ender = threading.Thread(target=end_from_feed)
    ender.start()
    payload = service.spawn(
        parent_session_id=parent,
        children=[{"title": t, "message": t} for t in ("One", "Two", "Three")],
        collect="serial",
        wait=True,
        timeout=8,
        runner=runner,
        db=db,
    )
    ender.join(5)

    assert ran == ["One", "Two"]
    statuses = {c["label"]: c["status"] for c in payload["children"]}
    assert statuses == {"One": "done", "Two": "cancelled", "Three": "cancelled"}
    assert payload["status"] == "cancelled"
    two_sid = next(c["session_id"] for c in payload["children"] if c["label"] == "Two")
    assert killed_sessions == [two_sid]
