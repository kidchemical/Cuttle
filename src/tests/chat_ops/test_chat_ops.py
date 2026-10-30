"""Existing-chat agent operations (``api.chat_ops`` / ``python -m api.chat_cli send|status|…``)."""

from __future__ import annotations

import base64
import json
import time
from contextlib import contextmanager

import pytest
from flask import Flask

from api.auth_db import AuthDatabase
from api.chat_ops import local_auth, service, store, worker


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    from api.gizmos import tasks
    monkeypatch.setattr(tasks, "database", lambda db=None: db)


@pytest.fixture
def chats(tmp_path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    other = db.create_user("other@local", "Other", "local", password="x")
    supervisor = db.create_chat_session(owner, "supervisor")
    target = db.create_chat_session(owner, "worker")
    foreign = db.create_chat_session(other, "someone else")
    return db, owner, supervisor, target, foreign


class FakeHost:
    """Stands in for the loopback chat HTTP routes."""

    def __init__(self, *, generating=False, steered=True):
        self.generating = generating
        self.steered = steered
        self.calls = []

    def __call__(self, uid, sid, method, path, data=None, *, stream=False):
        self.calls.append((method, path, sid, data))
        if path == "/api/chat-live-status":
            return {"success": True, "generating": self.generating, "status": "working"}
        if path == "/api/chat-steer":
            return {"success": True, "steered": self.steered}
        if path == "/api/chat-cancel":
            return {"success": True, "cancelled": True}
        raise AssertionError(path)


def _ch(sid):
    return f"CH-{sid:06d}"


# --- scoped local capabilities ------------------------------------------------


@contextmanager
def _request(path, *, method="POST", body=None, addr="127.0.0.1"):
    app = Flask(__name__)
    kwargs = {"json": body} if method != "GET" else {"query_string": body}
    with app.test_request_context(path, method=method, environ_base={"REMOTE_ADDR": addr}, **kwargs):
        yield


def test_capability_authorizes_only_its_chat_route_and_loopback(chats):
    db, owner, _sup, target, _foreign = chats
    token = local_auth.issue(owner, target, "POST", "/api/chat")
    body = {"session_id": f"db_session_{target}"}

    with _request("/api/chat", body=body):
        user = db.verify_auth_session(token)
        assert user and user["id"] == owner
    with _request("/api/chat", body={"session_id": f"db_session_{target + 1}"}):
        assert db.verify_auth_session(token) is None
    with _request("/api/chat-steer", body=body):
        assert db.verify_auth_session(token) is None
    with _request("/api/chat", body=body, addr="192.168.1.20"):
        assert db.verify_auth_session(token) is None
    with _request("/api/settings", body=body):
        assert db.verify_auth_session(token) is None


def test_capability_rejects_tampering_and_expiry(chats, monkeypatch):
    db, owner, _sup, target, _foreign = chats
    token = local_auth.issue(owner, target, "POST", "/api/chat")
    encoded, signature = token[len(local_auth.PREFIX):].split(".")
    claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    claims["uid"] = owner + 1
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    body = {"session_id": f"db_session_{target}"}
    with _request("/api/chat", body=body):
        assert db.verify_auth_session(local_auth.PREFIX + forged + "." + signature) is None
        later = time.time() + 120
        monkeypatch.setattr(local_auth.time, "time", lambda: later)
        assert db.verify_auth_session(token) is None


def test_capability_refuses_unlisted_routes():
    with pytest.raises(ValueError):
        local_auth.issue(1, 1, "POST", "/api/settings")


# --- send / status ------------------------------------------------------------


def test_send_steers_a_live_turn(chats):
    db, _owner, sup, target, _foreign = chats
    host = FakeHost(generating=True, steered=True)
    out = service.send(_ch(target), "keep going", actor=_ch(sup), db=db, request=host)
    assert out["delivery"] == "steered"
    [op] = store.listing(sup)
    assert op["state"] == "delivered" and op["result"] == {"steered": True}
    assert ("POST", "/api/chat-steer", target, {"message": "keep going", "chat_op_id": op["id"]}) in host.calls
    assert op["id"] not in [o["id"] for o in store.due()]


def test_refused_steer_reuses_its_operation_as_the_queued_send(chats):
    db, _owner, sup, target, _foreign = chats
    out = service.send(_ch(target), "next", actor=_ch(sup), db=db, request=FakeHost(generating=True, steered=False))
    assert out["delivery"] == "queued" and out["operation"]["state"] == "pending"
    assert len(store.listing(sup)) == 1


def test_failed_steer_is_verified_not_resent_blindly(chats):
    db, _owner, sup, target, _foreign = chats

    def host(uid, sid, method, path, data=None, **kw):
        if path == "/api/chat-steer":
            raise ConnectionError("dropped")
        return FakeHost(generating=True)(uid, sid, method, path, data)

    out = service.send(_ch(target), "next", actor=_ch(sup), db=db, request=host)
    assert out["operation"]["state"] == "verifying"


def test_chat_op_rows_speak_as_cuttle_only_when_the_store_vouches(chats):
    from api.chat_metadata import chat_op_metadata
    _db, _owner, sup, target, _foreign = chats
    send = _queued(chats)
    meta = chat_op_metadata({"chat_op_id": send["id"]}, target)
    assert meta["speaker"] == "Cuttle" and meta["speaker_kind"] == "parent"
    assert meta["parent_handle"] == _ch(sup) and meta["parent_label"] == f"Sent by {_ch(sup)}"
    assert chat_op_metadata({"chat_op_id": send["id"]}, f"db_session_{target}")["speaker_kind"] == "parent"
    # Wrong chat or an id the store never issued: id only, no speaker claim.
    assert chat_op_metadata({"chat_op_id": send["id"]}, sup) == {"chat_op_id": send["id"]}
    assert chat_op_metadata({"chat_op_id": "b" * 32}, target) == {"chat_op_id": "b" * 32}
    checkin = store.create("checkin", sup, sup, {"text": "x", "schedule_id": "s"})
    assert chat_op_metadata({"chat_op_id": checkin["id"]}, sup)["parent_label"] == "Supervision check-in"


def test_send_queues_when_idle_or_steer_refused(chats):
    db, _owner, sup, target, _foreign = chats
    for host in (FakeHost(generating=False), FakeHost(generating=True, steered=False)):
        out = service.send(_ch(target), "next task", actor=_ch(sup), db=db, request=host)
        assert out["delivery"] == "queued"
        assert out["operation"]["state"] == "pending"
        assert out["operation"]["payload"] == {"text": "next task"}


def test_send_rejects_self_foreign_and_bubble_refs(chats):
    db, _owner, sup, target, foreign = chats
    host = FakeHost()
    with pytest.raises(ValueError, match="different chat"):
        service.send(_ch(sup), "hi", actor=_ch(sup), db=db, request=host)
    with pytest.raises(ValueError, match="same owner"):
        service.send(_ch(foreign), "hi", actor=_ch(sup), db=db, request=host)
    with pytest.raises(ValueError, match="session handle"):
        service.send(_ch(target) + "-3", "hi", actor=_ch(sup), db=db, request=host)


def test_actor_defaults_to_calling_chat(chats, monkeypatch):
    db, _owner, sup, target, _foreign = chats
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(sup))
    out = service.send(_ch(target), "hi", db=db, request=FakeHost())
    assert out["operation"]["actor"] == sup


def _tasks(db, owner, sid, done):
    db.upsert_chat_widget(
        widget_id="plan", user_id=owner, wtype="tasks", title="Tasks", scope="session",
        session_id=sid, project_path="",
        payload={"items": [{"id": "1", "text": "a", "done": done, "children": []}]},
    )


def test_status_states(chats):
    db, owner, sup, target, _foreign = chats
    assert service.status(_ch(target), _ch(sup), db=db, request=FakeHost(generating=True))["state"] == "busy"
    assert service.status(_ch(target), _ch(sup), db=db, request=FakeHost())["state"] == "idle"
    _tasks(db, owner, target, done=False)
    out = service.status(_ch(target), _ch(sup), db=db, request=FakeHost())
    assert out["state"] == "idle_with_work"
    assert out["tasks"][0]["done"] == 0 and out["tasks"][0]["total"] == 1


# --- outbox delivery ----------------------------------------------------------


class FakeStream:
    def __init__(self, status, events=()):
        self.status_code = status
        self._lines = [b"data: " + json.dumps(e).encode() for e in events]

    def iter_lines(self):
        yield b": flush"
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _queued(chats, text="go"):
    db, _owner, sup, target, _foreign = chats
    return service.send(_ch(target), text, actor=_ch(sup), db=db, request=FakeHost())["operation"]


def _deliver(chats, op, response, monkeypatch):
    db = chats[0]
    original = service.context
    monkeypatch.setattr(service, "context", lambda target, actor=None, **kw: original(target, actor, db=db))
    host = FakeHost()

    def request(uid, sid, method, path, data=None, *, stream=False):
        return response if path == "/api/chat" else host(uid, sid, method, path, data)

    worker.dispatch(op, request=request)
    return store.get(op["id"], op["actor"])


def test_delivery_stops_reading_once_host_accepts_turn(chats, monkeypatch):
    op = _queued(chats)
    stream = FakeStream(200, [{"type": "status", "message": "Connecting..."},
                              {"type": "query_started", "query_id": "q1"}])
    got = _deliver(chats, op, stream, monkeypatch)
    assert got["state"] == "delivered" and got["result"]["query_id"] == "q1"


def test_delivery_busy_requeues_and_other_errors_fail(chats, monkeypatch):
    got = _deliver(chats, _queued(chats), FakeStream(200, [{"type": "busy"}]), monkeypatch)
    assert got["state"] == "pending"
    got = _deliver(chats, _queued(chats), FakeStream(409), monkeypatch)
    assert got["state"] == "pending"
    got = _deliver(chats, _queued(chats), FakeStream(500), monkeypatch)
    assert got["state"] == "failed"
    got = _deliver(chats, _queued(chats), FakeStream(200, []), monkeypatch)
    assert got["state"] == "verifying" and got["result"]["attempts"] == 1
    assert got["due"] >= time.time() + worker.VERIFY_GRACE_SEC - 5


def test_send_carries_its_operation_id(chats, monkeypatch):
    op = _queued(chats)
    seen = []
    stream = FakeStream(200, [{"type": "query_started", "query_id": "q1"}])
    db = chats[0]
    original = service.context
    monkeypatch.setattr(service, "context", lambda target, actor=None, **kw: original(target, actor, db=db))

    def request(uid, sid, method, path, data=None, *, stream_=False, **kw):
        if path == "/api/chat":
            seen.append(data)
            return stream
        return FakeHost()(uid, sid, method, path, data)

    worker.dispatch(op, request=request)
    assert seen[0]["chat_op_id"] == op["id"]


def test_send_runs_on_the_target_chats_agent_not_the_router(chats):
    from api.subagents.spec import ChildSpec
    from api.subagents.store import insert_child
    db, owner, _sup, target, _foreign = chats
    row = {"id": target, "user_id": owner}
    pin = lambda text: service.pin_to_chat_agent(db, row, text)
    assert pin("go") == "go"
    assert pin("/codex go") == "/codex go"
    insert_child(db, batch_id="b1", session_id=target, sort_index=0,
                 spec=ChildSpec(title="w", message="m", agent="muse"), prompt="m")
    assert pin("go") == "/muse go"
    db.set_composer_selection(target, owner, {"stickyChips": [{"prefix": "/cursor", "label": "Cursor"}]})
    assert pin("go") == "/cursor go"
    db.set_composer_selection(target, owner, {"stickyChips": [], "stickyCleared": True})
    assert pin("go") == "go"


def test_restart_verifies_sends_and_resumes_schedules(chats):
    db, _owner, sup, target, _foreign = chats
    send = _queued(chats)
    sched = service.supervise(_ch(target), _ch(sup), every=60, instructions="watch", db=db)["operation"]
    assert store.claim(send["id"]) and store.claim(sched["id"])
    store.recover_after_restart()
    after = store.get(send["id"], sup)
    assert after["state"] == "verifying" and after["due"] <= time.time()
    assert store.get(sched["id"], sup)["state"] == "pending"
    assert send["id"] in [o["id"] for o in store.due()]


def _verify(chats, op, monkeypatch, *, generating=False):
    db = chats[0]
    original = service.context
    monkeypatch.setattr(service, "context", lambda target, actor=None, **kw: original(target, actor, db=db))
    worker.dispatch(store.find(op["id"]), request=FakeHost(generating=generating))
    return store.find(op["id"])


def _lost(chats, monkeypatch, attempts=1):
    op = _queued(chats)
    store.claim(op["id"])
    store.finish(op["id"], "verifying", {"attempts": attempts}, due=0)
    return op


def test_uncertain_send_found_in_history_is_delivered_not_resent(chats, monkeypatch):
    db, _owner, _sup, target, _foreign = chats
    op = _lost(chats, monkeypatch)
    db.add_message(target, "user", "go", metadata={"chat_op_id": op["id"]})
    got = _verify(chats, op, monkeypatch)
    assert got["state"] == "delivered" and got["result"]["recovered"] is True


def test_uncertain_send_missing_from_history_is_resent(chats, monkeypatch):
    db, _owner, _sup, target, _foreign = chats
    op = _lost(chats, monkeypatch)
    db.add_message(target, "user", "go", metadata={"chat_op_id": "f" * 32})
    assert _verify(chats, op, monkeypatch, generating=True)["state"] == "verifying"  # wait for idle
    got = _verify(chats, op, monkeypatch)
    assert got["state"] == "pending" and got["result"]["attempts"] == 1


def test_uncertain_send_gives_up_after_max_attempts(chats, monkeypatch):
    op = _lost(chats, monkeypatch, attempts=worker.MAX_SEND_ATTEMPTS)
    got = _verify(chats, op, monkeypatch)
    assert got["state"] == "unknown" and "attempts" in got["result"]["error"]


def test_user_row_stores_only_well_formed_op_ids(chats):
    from api.chat_turn_persist import persist_auth_user_message, persist_user_turn
    db, _owner, _sup, target, _foreign = chats
    oid = "a" * 32
    merge = lambda meta, *_a: meta  # noqa: E731
    persist_user_turn(db, target, message_text="hi", history_text="hi", badge_fn=lambda *a, **k: None,
                      project_merge_fn=merge, request_data={"chat_op_id": oid})
    persist_auth_user_message(db, target, "/cursor hi", project_merge_fn=merge,
                              request_data={"chat_op_id": "not-an-id"})
    rows = db.get_messages(target)
    assert rows[0]["metadata"] == {"chat_op_id": oid}
    assert not (rows[1].get("metadata") or {}).get("chat_op_id")


# --- supervision check-ins ----------------------------------------------------


def _snap(state, *, branch="dev/multiplayer", reply_id=1, done=0):
    return {"handle": "CH-000002", "state": state, "branch": branch, "execution_path": "/galaxy/wt",
            "warnings": [], "live": {"status": "working"}, "latest_reply": {"id": reply_id, "content": "…"},
            "tasks": [{"id": "plan", "title": "Tasks", "done": done, "total": 3}]}


def _tick(chats, sched, snap, monkeypatch):
    db, _owner, sup, target, _foreign = chats
    monkeypatch.setattr(service, "snapshot", lambda *a, **k: snap)
    worker._check_in(store.get(sched["id"], sup), db, sup, target, FakeHost())
    return [o for o in store.listing(sup) if o["kind"] == "checkin"]


def _schedule(chats, **kw):
    db, _owner, sup, target, _foreign = chats
    return service.supervise(_ch(target), _ch(sup), every=900, instructions="keep it on track",
                             db=db, **kw)["operation"]


def test_busy_target_costs_no_supervisor_turn(chats, monkeypatch):
    sched = _schedule(chats)
    assert _tick(chats, sched, _snap("busy"), monkeypatch) == []
    assert store.get(sched["id"], chats[2])["state"] == "pending"


def test_busy_target_without_progress_wakes_supervisor(chats, monkeypatch):
    sched = _schedule(chats)
    clock = [1000.0]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    monkeypatch.setattr(worker, "_git_head", lambda path: "abc")
    assert _tick(chats, sched, _snap("busy"), monkeypatch) == []
    clock[0] += worker.STALL_SEC - 60
    assert _tick(chats, sched, _snap("busy"), monkeypatch) == []
    clock[0] += 120
    checkins = _tick(chats, sched, _snap("busy"), monkeypatch)
    assert len(checkins) == 1 and "No progress for" in checkins[0]["payload"]["text"]
    store.finish(checkins[0]["id"], "done", {})
    clock[0] += 60  # stall window restarts after a wake
    assert len(_tick(chats, sched, _snap("busy"), monkeypatch)) == 1


def test_busy_target_with_progress_stays_quiet(chats, monkeypatch):
    sched = _schedule(chats)
    clock = [1000.0]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    monkeypatch.setattr(worker, "_git_head", lambda path: "abc")
    _tick(chats, sched, _snap("busy"), monkeypatch)
    clock[0] += worker.STALL_SEC - 60
    _tick(chats, sched, _snap("busy", done=1), monkeypatch)
    clock[0] += worker.STALL_SEC - 60
    assert _tick(chats, sched, _snap("busy", done=1), monkeypatch) == []


def test_idle_target_wakes_supervisor_once_until_it_changes(chats, monkeypatch):
    sched = _schedule(chats)
    checkins = _tick(chats, sched, _snap("idle_with_work"), monkeypatch)
    assert len(checkins) == 1
    assert "api.chat_cli send CH-000002" in checkins[0]["payload"]["text"]
    assert checkins[0]["target"] == chats[2]
    store.finish(checkins[0]["id"], "done", {})
    assert len(_tick(chats, sched, _snap("idle_with_work"), monkeypatch)) == 1
    assert len(_tick(chats, sched, _snap("idle_with_work", reply_id=2), monkeypatch)) == 2


def test_branch_violation_wakes_supervisor_and_keeps_schedule(chats, monkeypatch):
    sched = _schedule(chats, required_branch="dev/multiplayer", forbidden_branches=["dev/core"])
    checkins = _tick(chats, sched, _snap("busy", branch="dev/core"), monkeypatch)
    assert len(checkins) == 1
    assert "forbidden branch 'dev/core'" in checkins[0]["payload"]["text"]
    assert store.get(sched["id"], chats[2])["state"] == "pending"


def test_cancel_wins_over_an_in_flight_check_in(chats):
    db, _owner, sup, _target, _foreign = chats
    sched = _schedule(chats)
    assert store.claim(sched["id"])
    assert store.cancel(sched["id"], sup)["state"] == "cancelled"
    store.finish(sched["id"], "pending", {}, due=time.time())
    assert store.get(sched["id"], sup)["state"] == "cancelled"


def test_supervise_validates_inputs(chats):
    db, _owner, sup, target, _foreign = chats
    with pytest.raises(ValueError):
        service.supervise(_ch(target), _ch(sup), every=10, instructions="x", db=db)
    with pytest.raises(ValueError):
        service.supervise(_ch(target), _ch(sup), instructions=" ", db=db)
    with pytest.raises(ValueError):
        service.supervise(_ch(sup), _ch(sup), instructions="x", db=db)


# --- CLI ----------------------------------------------------------------------


def test_cli_lists_and_cancels_operations(chats, tmp_path, capsys):
    from api.chat_cli.cli import main
    db, _owner, sup, _target, _foreign = chats
    op = _queued(chats)
    db_path = str(tmp_path / "cuttle_auth.db")
    assert main(["operations", "--db", db_path, "--as", _ch(sup), "--json"]) == 0
    assert [o["id"] for o in json.loads(capsys.readouterr().out)["operations"]] == [op["id"]]
    assert main(["cancel-operation", op["id"], "--db", db_path, "--as", _ch(sup), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["operation"]["state"] == "cancelled"


def test_cli_reports_validation_errors_without_traceback(chats, tmp_path, capsys):
    from api.chat_cli.cli import main
    _db, _owner, sup, _target, _foreign = chats
    code = main(["send", _ch(sup), "--text", "hi", "--db", str(tmp_path / "cuttle_auth.db"),
                 "--as", _ch(sup), "--json"])
    assert code == 2
    assert "different chat" in json.loads(capsys.readouterr().out)["error"]


# --- supervision UI support ---------------------------------------------------


def test_checks_are_recorded_for_the_history(chats, monkeypatch):
    sched = _schedule(chats)
    _tick(chats, sched, _snap("busy"), monkeypatch)
    _tick(chats, sched, _snap("idle_with_work"), monkeypatch)
    _tick(chats, sched, _snap("busy", branch="dev/core"), monkeypatch)
    history = store.checks(sched["id"])
    assert [h["outcome"] for h in history] == ["skipped", "woke", "skipped"]  # newest first
    assert history[-1]["detail"]["reason"] == "busy"
    sched = _schedule(chats, forbidden_branches=["dev/core"])
    _tick(chats, sched, _snap("busy", branch="dev/core"), monkeypatch)
    assert store.checks(sched["id"])[0]["outcome"] == "violation"


def test_history_is_pruned(chats):
    sched = _schedule(chats)
    for _ in range(store.HISTORY_KEEP + 5):
        store.record_check(sched["id"], "skipped", {"reason": "busy"})
    assert len(store.checks(sched["id"], limit=500)) == store.HISTORY_KEEP


def test_pause_stops_checks_and_resume_checks_now(chats):
    _db, _owner, sup, _target, _foreign = chats
    sched = _schedule(chats)
    assert store.set_paused(sched["id"], sup, True)["state"] == "paused"
    assert sched["id"] not in [o["id"] for o in store.due()]
    store.finish(sched["id"], "pending", {}, due=0)  # an in-flight check finishing
    assert store.get(sched["id"], sup)["state"] == "paused"
    resumed = store.set_paused(sched["id"], sup, False)
    assert resumed["state"] == "pending" and resumed["due"] <= time.time()
    assert store.cancel(store.set_paused(sched["id"], sup, True)["id"], sup)["state"] == "cancelled"


def test_agent_prefix_and_start_now_briefing(chats, monkeypatch):
    db, _owner, sup, target, _foreign = chats
    op = service.supervise(_ch(target), _ch(sup), instructions="watch", agent="cursor",
                           start_now=True, db=db)["operation"]
    assert op["payload"]["agent"] == "cursor" and op["due"] <= time.time()
    checkins = _tick(chats, op, _snap("busy"), monkeypatch)
    assert len(checkins) == 1  # the briefing wakes even though the target is busy
    assert checkins[0]["payload"]["text"].startswith("/cursor **Supervision check-in for CH-000002**")
    store.finish(checkins[0]["id"], "done", {})
    assert len(_tick(chats, op, _snap("busy"), monkeypatch)) == 1  # later busy checks skip
    with pytest.raises(ValueError, match="Unknown agent"):
        service.supervise(_ch(target), _ch(sup), instructions="x", agent="nope", db=db)


def test_start_supervisor_creates_a_briefed_chat_in_the_target_project(chats):
    db, owner, _sup, target, foreign = chats
    db.set_session_project(target, owner, project_id=7, project_name="Game", project_path="/games/x")
    out = service.start_supervisor(_ch(target), user_id=owner, agent="cursor", instructions="watch", db=db)
    new = db.get_chat_session(out["operation"]["actor"], owner)
    assert new["session_name"] == f"Supervisor · {_ch(target)}"
    assert new["project_path"] == "/games/x"
    assert out["operation"]["payload"]["brief_on_start"] is True
    with pytest.raises(ValueError):
        service.start_supervisor(_ch(foreign), user_id=owner, agent="cursor", instructions="x", db=db)


def test_overview_splits_supervising_supervised_and_queued(chats):
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    store.record_check(sched["id"], "skipped", {"reason": "busy", "state": "busy"})
    _queued(chats, "next step")
    mine = service.overview(_ch(sup), owner, db=db)
    theirs = service.overview(_ch(target), owner, db=db)
    assert [s["target"] for s in mine["supervising"]] == [_ch(target)]
    assert mine["supervising"][0]["history"][0]["outcome"] == "skipped"
    assert mine["supervised_by"] == [] and mine["queued"] == []
    assert [s["supervisor"] for s in theirs["supervised_by"]] == [_ch(sup)]
    assert [q["text"] for q in theirs["queued"]] == ["next step"]


def test_operation_controls_require_the_owner(chats):
    db, owner, _sup, _target, _foreign = chats
    other = db.get_user_by_id(owner)["id"] + 1
    sched = _schedule(chats)
    with pytest.raises(LookupError):
        service.cancel(sched["id"], other, db=db)
    with pytest.raises(LookupError):
        service.overview(_ch(chats[3]), other, db=db)
    assert service.set_paused(sched["id"], owner, True, db=db)["state"] == "paused"
    with pytest.raises(ValueError):
        service.set_paused(_queued(chats)["id"], owner, True, db=db)


@pytest.fixture
def client(chats, monkeypatch):
    from flask import Flask
    from api.chat_ops import routes
    db, owner, *_ = chats
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(routes, "current_user", lambda: {"id": owner})
    monkeypatch.setattr("api.http_authz.current_user", lambda: {"id": owner})
    app = Flask(__name__)
    app.register_blueprint(routes.chat_ops_bp)
    return app.test_client()


def _flag(monkeypatch, on):
    monkeypatch.setattr("api.chat_ops.routes.is_enabled", lambda f: on and f == "chat_supervision")


def test_routes_are_disabled_while_the_flag_is_off(client, chats, monkeypatch):
    _flag(monkeypatch, False)
    body = client.get(f"/api/chat-ops/sessions/{chats[2]}").get_json()
    assert body == {"success": False, "disabled": True, "error": "Chat supervision is disabled"}
    assert client.post("/api/chat-ops/supervise", json={}).get_json()["disabled"] is True


def test_routes_overview_control_and_start(client, chats, monkeypatch):
    _flag(monkeypatch, True)
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    body = client.get(f"/api/chat-ops/sessions/{sup}").get_json()
    assert body["success"] and body["supervising"][0]["id"] == sched["id"]
    paused = client.post(f"/api/chat-ops/operations/{sched['id']}/pause").get_json()
    assert paused["operation"]["state"] == "paused"
    assert client.post(f"/api/chat-ops/operations/{sched['id']}/explode").status_code == 404
    assert client.post("/api/chat-ops/operations/nope/cancel").status_code == 404
    started = client.post("/api/chat-ops/supervise", json={
        "target": _ch(target), "agent": "cursor", "every": 600, "instructions": "watch",
        "forbidden_branches": "main, master"}).get_json()
    assert started["success"] and started["operation"]["payload"]["forbidden_branches"] == ["main", "master"]
    bad = client.post("/api/chat-ops/supervise", json={"target": _ch(target), "agent": "cursor"})
    assert bad.status_code == 400


def test_jobs_ledger_lists_chat_ops_only_with_the_flag(chats, monkeypatch):
    from api.jobs import ledger, service as jobs_service
    sched = _schedule(chats)
    send = _queued(chats)
    monkeypatch.setattr("api.experimental.is_enabled", lambda f: False)
    assert ledger.chat_op_jobs() == []
    monkeypatch.setattr("api.experimental.is_enabled", lambda f: f == "chat_supervision")
    jobs = {j["id"]: j for j in ledger.chat_op_jobs()}
    assert jobs[sched["id"]]["status"] == "running" and jobs[sched["id"]]["label"].startswith("Supervise CH-")
    assert jobs[send["id"]]["status"] == "queued" and jobs[send["id"]]["actions"] == ["cancel"]
    assert jobs_service.cancel_job("chat_op", send["id"])["result"]["state"] == "cancelled"
    with pytest.raises(jobs_service.JobNotCancellableError):
        jobs_service.cancel_job("chat_op", send["id"])


def test_readable_reply_unwraps_postprocessing_and_strips_markup():
    wrapped = ('<cuttle_original_response>' + json.dumps("<think>\nplan\n</think>\n\nBuild **failed**.\n\nNext: rerun.")
               + '</cuttle_original_response>\n\nTL;DR: failed.')
    assert service.readable_reply(wrapped) == "Build **failed**.\n\nNext: rerun."
    card = 'Pick one.\n<cuttle_action_form>{"title": "Deploy?", "mode": "choice"}</cuttle_action_form>'
    assert service.readable_reply(card) == "Pick one.\n[card: Deploy?]"
    assert service.readable_reply("<cuttle_original_response>not json</cuttle_original_response>\n\nTail") == "Tail"


def test_checkin_text_is_markdown_without_raw_json():
    snap = _snap("idle_with_work", done=1)
    snap["latest_reply"] = {"text": "Line one\n\nLine two"}
    cfg = {"agent": "cursor", "instructions": "Keep going.", "model_tiers": {"worker": ["cursor auto"]}}
    text = worker.checkin_text(cfg, snap, ["target is on forbidden branch 'main'"])
    assert text.startswith("/cursor **Supervision check-in for CH-000002**: idle with open Tasks items.")
    assert "Branch `dev/multiplayer` · checkout `/galaxy/wt`" in text
    assert "- target is on forbidden branch 'main'" in text and "- Tasks: 1/3 done" in text
    assert "> Line one\n>\n> Line two" in text and "- worker: cursor auto" in text
    assert "{" not in text and "\\n" not in text
    snap["latest_reply"] = {"text": "x" * (worker.CHECKIN_REPLY_CHARS + 10)}
    assert "(start)" in worker.checkin_text(cfg, snap, [])
