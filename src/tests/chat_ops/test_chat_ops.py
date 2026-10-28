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
    assert ("POST", "/api/chat-steer", target, {"message": "keep going"}) in host.calls
    assert store.listing(sup) == []


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
    assert got["state"] == "unknown"


def test_restart_never_replays_a_send_but_resumes_schedules(chats):
    db, _owner, sup, target, _foreign = chats
    send = _queued(chats)
    sched = service.supervise(_ch(target), _ch(sup), every=60, instructions="watch", db=db)["operation"]
    assert store.claim(send["id"]) and store.claim(sched["id"])
    store.recover_after_restart()
    assert store.get(send["id"], sup)["state"] == "unknown"
    assert store.get(sched["id"], sup)["state"] == "pending"


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
