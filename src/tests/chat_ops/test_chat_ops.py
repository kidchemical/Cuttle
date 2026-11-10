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

    def __init__(self, *, generating=False, steered=True, reason=None):
        self.generating = generating
        self.steered = steered
        self.reason = reason
        self.calls = []

    def __call__(self, uid, sid, method, path, data=None, *, stream=False):
        self.calls.append((method, path, sid, data))
        if path == "/api/chat-live-status":
            return {"success": True, "generating": self.generating, "status": "working"}
        if path == "/api/chat-steer":
            return {"success": True, "steered": self.steered, **({"reason": self.reason} if self.reason else {})}
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


def test_refused_steer_says_the_send_waits_for_the_live_turn(chats):
    db, _owner, sup, target, _foreign = chats
    host = FakeHost(generating=True, steered=False, reason="no_steerable_run")
    out = service.send(_ch(target), "pause now", actor=_ch(sup), db=db, request=host)
    assert out["delivery"] == "queued" and out["target_busy"] is True
    assert out["steer_reason"] == "no_steerable_run"
    assert "chat_cli stop" in out["hint"]
    idle = service.send(_ch(target), "later", actor=_ch(sup), db=db, request=FakeHost(generating=False))
    assert "target_busy" not in idle


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


def test_send_follows_the_chats_latest_agent_over_a_stale_badge(chats):
    db, owner, _sup, target, _foreign = chats
    row = {"id": target, "user_id": owner}
    db.set_composer_selection(target, owner, {"stickyChips": [{"prefix": "/codex", "label": "Codex"}]})
    db.add_message(target, "user", "/claude switch harness")
    db.add_message(target, "assistant", "ok")
    db.add_message(target, "user", "plain follow-up")
    assert service.pin_to_chat_agent(db, row, "go") == "/claude go"


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


def test_settings_change_is_told_once_in_the_next_check_in_even_mid_turn(chats, monkeypatch):
    """A pending settings edit wakes the supervisor once (a busy target too: that is
    when it may need steering); the check-in carries it and the record clears."""
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    service.update_supervision(sched["id"], owner, {"instructions": "new rules"}, db=db)
    assert store.find(sched["id"])["due"] <= __import__("time").time() + service.CHANGE_WAKE_SEC + 1
    checkins = _tick(chats, sched, _snap("busy"), monkeypatch)
    assert len(checkins) == 1
    text = checkins[0]["payload"]["text"]
    assert "Settings changed since your last check-in** (by you)" in text and "new rules" in text
    assert "pending_changes" not in store.find(sched["id"])["payload"]
    store.finish(checkins[0]["id"], "done", {})
    assert len(_tick(chats, sched, _snap("busy"), monkeypatch)) == 1  # nothing new: no second wake


def test_edit_landing_mid_check_survives_the_clear(chats):
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    first = store.merge_pending_changes(sched["id"], updated=["every"], kept=[], notes=[], source="you", at=1)
    store.merge_pending_changes(sched["id"], updated=["instructions"], kept=[], notes=[], source="you", at=2)
    assert store.clear_pending_changes(sched["id"], first["rev"]) is False
    assert store.find(sched["id"])["payload"]["pending_changes"]["updated"] == ["every", "instructions"]


def test_chat_op_rows_name_their_kind(chats):
    from api.chat_metadata import chat_op_metadata
    db, owner, sup, target, _foreign = chats
    op = store.create("checkin", sup, sup, {"text": "x"})
    meta = chat_op_metadata({"chat_op_id": op["id"]}, sup)
    assert meta["chat_op_kind"] == "checkin" and meta["parent_label"] == "Supervision check-in"


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


def test_template_nudge_sends_the_next_item_without_a_supervisor_turn(chats, monkeypatch):
    sched = _schedule(chats, nudge="template")
    sent = []
    monkeypatch.setattr(service, "send", lambda target, text, actor=None, **k: sent.append((target, text)))
    items = [{"id": "a", "text": "Rig", "done": True},
             {"id": "b", "text": "Blend", "done": False, "children": [{"id": "c", "text": "Idle loop"}]}]

    def snap(reply_id, done=1):
        out = _snap("idle_with_work", reply_id=reply_id, done=done)
        out["tasks"][0]["items"] = items
        return out
    assert _tick(chats, sched, snap(1), monkeypatch) == [] and len(sent) == 1
    assert "**Blend › Idle loop**" in sent[0][1]
    # It replied without checking anything off: one more nudge, then the supervisor is woken.
    assert _tick(chats, sched, snap(2), monkeypatch) == [] and len(sent) == 2
    assert len(_tick(chats, sched, snap(3), monkeypatch)) == 1 and len(sent) == 2
    outcomes = [c["outcome"] for c in store.checks(sched["id"])]
    assert outcomes.count("nudged") == 2
    # Errors still need judgment: never a template.
    sched2 = _schedule(chats, nudge="template")
    _tick(chats, sched2, _snap("error"), monkeypatch)
    assert len(sent) == 2


def _answer(chats, checkin, query_id, content, **meta):
    db, _owner, sup, _target, _foreign = chats
    store.finish(checkin["id"], "delivered", {"query_id": query_id})
    db.add_message(sup, "assistant", content, {"query_id": query_id, **meta})


def test_failed_check_in_wakes_the_supervisor_again(chats, monkeypatch):
    sched = _schedule(chats)
    first = _tick(chats, sched, _snap("error"), monkeypatch)
    _answer(chats, first[0], "q1", "codex_error: connection reset", slash_command_failed=True)
    again = _tick(chats, sched, _snap("error"), monkeypatch)
    assert len(again) == 2  # the unchanged picture was never really seen
    _answer(chats, again[0], "q2", "Sent the worker a resume.")
    assert len(_tick(chats, sched, _snap("error"), monkeypatch)) == 2  # answered: quiet again


def test_check_in_still_running_is_not_a_failure(chats, monkeypatch):
    sched = _schedule(chats)
    first = _tick(chats, sched, _snap("error"), monkeypatch)
    _answer(chats, first[0], "q1", "", in_progress=True)
    assert len(_tick(chats, sched, _snap("error"), monkeypatch)) == 1


def test_supervisor_out_of_usage_waits_for_the_reset(chats, monkeypatch):
    from datetime import datetime
    sched = _schedule(chats)
    clock = [datetime(2026, 11, 8, 0, 30).timestamp()]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    monkeypatch.setattr(worker, "_git_head", lambda path: "abc")
    first = _tick(chats, sched, _snap("error"), monkeypatch)
    _answer(chats, first[0], "q1", "You've hit your usage limit. Upgrade to Pro or try again at 4:46 AM.",
            slash_command_failed=True)
    reset = datetime(2026, 11, 8, 4, 46).timestamp()
    clock[0] += 900
    assert len(_tick(chats, sched, _snap("error"), monkeypatch)) == 1
    op = store.get(sched["id"], chats[2])
    assert op["result"]["skipped"] == "supervisor_quota" and op["due"] <= reset
    clock[0] = reset + 1
    assert len(_tick(chats, sched, _snap("error"), monkeypatch)) == 2


def test_reset_at_reads_a_time_of_day_as_the_next_one():
    from datetime import datetime
    from api.agent_router import quota
    now = datetime(2026, 11, 8, 0, 30).timestamp()
    assert quota.reset_at("try again at 4:46 AM.", now) == datetime(2026, 11, 8, 4, 46).timestamp()
    assert quota.reset_at("try again at 12:10 AM", now) == datetime(2026, 11, 9, 0, 10).timestamp()
    assert quota.reset_at("try again at Oct 5th, 2026 12:55 AM", now) is None  # past
    assert quota.reset_at("no reset here", now) is None


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
        service.supervise(_ch(target), _ch(sup), instructions="x" * 2001, db=db)
    with pytest.raises(ValueError):
        service.supervise(_ch(target), _ch(sup), involve_user="sometimes", db=db)
    with pytest.raises(ValueError):
        service.supervise(_ch(target), _ch(sup), nudge="loud", db=db)
    with pytest.raises(ValueError):
        service.supervise(_ch(sup), _ch(sup), instructions="x", db=db)


def test_supervise_focus_is_optional_and_settings_carry_the_brief(chats):
    """No Focus needed: the check-in states the job and each host-enforced setting."""
    db, _owner, sup, target, _foreign = chats
    out = service.supervise(_ch(target), _ch(sup), forbidden_branches=["main"], involve_user="never", db=db)
    cfg = out["operation"]["payload"]
    assert cfg["instructions"] == "" and cfg["involve_user"] == "never" and cfg["nudge"] == "agent"
    assert out["warnings"] == []
    snap = {"handle": _ch(target), "state": "idle", "tasks": [{"id": "t", "title": "Plan", "done": 1, "total": 2}]}
    text = worker.checkin_text(cfg, snap, [])
    assert "**Your job:**" in text and '"Plan"' in text and "never touch `main`" in text
    assert "Never involve the user" in text and "**Focus**" not in text
    focused = worker.checkin_text({**cfg, "instructions": "Animation first."}, snap, [])
    assert "**Focus** (the user's notes for this job):\nAnimation first." in focused


def test_focus_restating_settings_is_warned_not_rejected(chats):
    db, owner, sup, target, _foreign = chats
    text = ("Use opus for reviews. Never merge or push protected branches. Do not involve the user. "
            "Run `python -m api.jobs work-tree --json` every check-in. Review at every milestone. "
            "Check in every 10 minutes.")
    out = service.supervise(_ch(target), _ch(sup), instructions=text, db=db)
    joined = " ".join(out["warnings"])
    for hint in ("--model", "--forbid-branch", "--involve", "runbook", "milestone", "--every"):
        assert hint in joined, hint
    assert service.supervise(_ch(target), _ch(sup), instructions="Commit a checkpoint about every 90 min.",
                             db=db)["warnings"] == []  # the worker's own cadence is a job note
    edited = service.update_supervision(out["operation"]["id"], owner, {"instructions": "Animation first."}, db=db)
    assert edited["updated"] == ["instructions"] and edited["warnings"] == []
    # The dialog always sends the policy fields: their defaults are not an edit of an older schedule.
    again = service.update_supervision(out["operation"]["id"], owner, {"involve_user": "blocked", "nudge": "agent"}, db=db)
    assert again["updated"] == []


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


def test_start_supervisor_pins_model_effort_and_seeds_the_composer_chip(chats, monkeypatch):
    db, owner, _sup, target, _foreign = chats
    pinned = []
    monkeypatch.setattr("api.agent_harness.agent_models.check_agent_pin",
                        lambda a, m="", e="", **k: None if m == "terra" else f"unknown model {m}")
    monkeypatch.setattr("api.subagents.identity.pin_child_session",
                        lambda sid, spec, project_path="": pinned.append((sid, spec.agent, spec.model, spec.effort)))
    out = service.start_supervisor(_ch(target), user_id=owner, agent="codex", model="terra", effort="high",
                                   instructions="watch", db=db)
    sup = out["operation"]["actor"]
    assert pinned == [(sup, "codex", "terra", "high")]
    chips = db.get_composer_selection(sup, owner)["stickyChips"]
    assert chips[0]["prefix"].strip() == "/codex" and chips[0]["category"] == "codex"
    with pytest.raises(ValueError, match="unknown model"):
        service.start_supervisor(_ch(target), user_id=owner, agent="codex", model="nope", instructions="x", db=db)
    with pytest.raises(ValueError, match="Pick an agent"):
        service.start_supervisor(_ch(target), user_id=owner, agent="", instructions="x", db=db)


def _list(db, owner, wid, *, sid=None, scope="session", project="", done=False):
    db.upsert_chat_widget(widget_id=wid, user_id=owner, wtype="tasks", title=wid, scope=scope,
                          session_id=sid, project_path=project,
                          payload={"items": [{"id": "1", "text": "a", "done": done, "children": []}]})


def test_supervision_follows_only_the_picked_tasks_lists(chats):
    db, owner, sup, target, _foreign = chats
    db.set_session_project(target, owner, project_id=7, project_name="Game", project_path="/games/x")
    _list(db, owner, "own", sid=target)
    _list(db, owner, "shared", scope="project", project="/games/x")
    db.set_session_project(sup, owner, project_id=7, project_name="Game", project_path="/games/x")
    _list(db, owner, "elsewhere", sid=sup, done=True)
    cands = service.supervise_defaults(_ch(target), owner, db=db)["tasks"]
    assert [(t["id"], t["selected"]) for t in cands][0] == ("own", True)
    assert {t["id"]: t["selected"] for t in cands} == {"own": True, "shared": False, "elsewhere": False}
    # Default: every visible list (own + project-scoped).
    assert {t["id"] for t in service.status(_ch(target), _ch(sup), db=db, request=FakeHost())["tasks"]} == {"own", "shared"}
    op = service.supervise(_ch(target), _ch(sup), instructions="w", task_ids=["elsewhere"], db=db)["operation"]
    pick = service.task_pick(op["payload"])
    out = service.status(_ch(target), _ch(sup), db=db, request=FakeHost(), task_pick=pick)
    assert [t["id"] for t in out["tasks"]] == ["elsewhere"] and out["state"] == "idle"
    # A list the target creates after the pick is still followed; project lists are not.
    _list(db, owner, "later", sid=target)
    _list(db, owner, "later-project", scope="project", project="/games/x")
    out = service.status(_ch(target), _ch(sup), db=db, request=FakeHost(), task_pick=pick)
    assert {t["id"] for t in out["tasks"]} == {"elsewhere", "later"} and out["state"] == "idle_with_work"
    empty = service.supervise(_ch(target), _ch(sup), instructions="w", task_ids=[], db=db)["operation"]
    # An explicit empty pick follows no list (all current ones were offered).
    assert service.status(_ch(target), _ch(sup), db=db, request=FakeHost(),
                          task_pick=service.task_pick(empty["payload"]))["tasks"] == []


def test_update_supervision_edits_in_place_and_tells_the_supervisor(chats, monkeypatch):
    db, owner, sup, target, _foreign = chats
    pinned = []
    monkeypatch.setattr("api.agent_harness.agent_models.check_agent_pin",
                        lambda a, m="", e="", **k: None if m in ("", "terra") else f"unknown model {m}")
    monkeypatch.setattr("api.subagents.identity.pin_child_session",
                        lambda sid, spec, project_path="": pinned.append((sid, spec.agent, spec.model, spec.effort)))
    op = service.supervise(_ch(target), _ch(sup), instructions="watch", agent="cursor", every=900, db=db)["operation"]
    service.set_paused(op["id"], owner, True, indefinite=True, db=db)
    # Unchanged fields (blank model on an older schedule included) are not edits.
    assert service.update_supervision(op["id"], owner, {"every": 900, "model": "", "instructions": "watch"},
                                      db=db)["updated"] == []
    out = service.update_supervision(op["id"], owner, {
        "every": 300, "agent": "codex", "model": "terra", "effort": "high", "instructions": "new rules",
        "forbidden_branches": "main, prod", "task_ids": ["plan"]}, db=db)
    assert out["updated"] == ["agent", "effort", "every", "forbidden_branches", "instructions", "model", "task_ids"]
    cfg = out["operation"]["payload"]
    assert cfg["agent"] == "codex" and cfg["model"] == "terra" and cfg["every"] == 300
    assert cfg["forbidden_branches"] == ["main", "prod"] and cfg["task_ids"] == ["plan"]
    assert out["operation"]["state"] == "paused"  # editing never resumes
    assert pinned == [(sup, "codex", "terra", "high")]
    assert db.get_composer_selection(sup, owner)["stickyChips"][0]["category"] == "codex"
    # An edit never spends a supervisor turn of its own: the user gets a timeline
    # notice, and the change waits on the schedule for its next check-in.
    assert [o for o in store.listing(sup) if o["kind"] == "checkin"] == []
    notices = [m for m in db.get_messages(sup) if m["role"] == "system"]
    assert len(notices) == 1
    meta = notices[0]["metadata"]
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta["timeline_notice"]["type"] == "supervision" and meta["kind"] == "supervision-change"
    assert "Focus" in meta["timeline_notice"]["detail"] and "by you" in meta["timeline_notice"]["detail"]
    service.update_supervision(op["id"], owner, {"every": 600}, db=db)
    pending = store.find(op["id"])["payload"]["pending_changes"]
    assert pending["rev"] == 2 and pending["sources"] == ["you"]
    assert pending["updated"] == ["agent", "effort", "every", "forbidden_branches", "instructions", "model",
                                  "task_ids"]
    section = "\n".join(service.changes_section(store.find(op["id"])["payload"], _ch(target)))
    assert "Settings changed since your last check-in** (by you)" in section and "Focus" in section
    with pytest.raises(ValueError, match="unknown model"):
        service.update_supervision(op["id"], owner, {"model": "nope"}, db=db)
    with pytest.raises(ValueError, match="also in the forbidden"):
        service.update_supervision(op["id"], owner, {"required_branch": "prod"}, db=db)
    with pytest.raises(ValueError, match="interval"):
        service.update_supervision(op["id"], owner, {"every": 5}, db=db)
    service.cancel(op["id"], owner, db=db)
    with pytest.raises(ValueError, match="live supervision"):
        service.update_supervision(op["id"], owner, {"every": 600}, db=db)


def test_edit_route_and_cli(client, chats, monkeypatch, capsys):
    db, owner, sup, target, _foreign = chats
    _flag(monkeypatch, True)
    op = service.supervise(_ch(target), _ch(sup), instructions="watch", db=db)["operation"]
    res = client.post(f"/api/chat-ops/operations/{op['id']}/edit", json={"every": 1200})
    assert res.get_json()["success"] and res.get_json()["updated"] == ["every"]
    from api.chat_cli.cli import build_parser
    args = build_parser().parse_args(["edit-operation", op["id"], "--as", _ch(sup), "--tasks", "a,b", "--json"])
    monkeypatch.setattr("api.chat_cli.cli._open_db", lambda _p: db)
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["operation"]["payload"]["task_ids"] == ["a", "b"]


def test_mark_supervision_links_history_rows(chats, monkeypatch):
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    monkeypatch.setattr("api.experimental.is_enabled", lambda f: f == "chat_supervision")
    rows = [{"id": sup}, {"id": target}, {"id": 999}]
    service.mark_supervision(rows)
    assert rows[0]["supervises_session_ids"] == [target] and rows[0]["supervisor_session_id"] is None
    assert rows[1]["supervisor_session_id"] == sup and rows[1]["supervises_session_ids"] == []
    assert rows[2]["supervisor_session_id"] is None
    service.set_paused(sched["id"], owner, True, db=db)  # a paused schedule still links
    service.mark_supervision(rows)
    assert rows[1]["supervisor_session_id"] == sup
    service.cancel(sched["id"], owner, db=db)
    service.mark_supervision(rows)
    assert rows[1]["supervisor_session_id"] is None and rows[0]["supervises_session_ids"] == []
    _schedule(chats)
    monkeypatch.setattr("api.experimental.is_enabled", lambda f: False)
    service.mark_supervision(rows)
    assert rows[1]["supervisor_session_id"] is None


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
    bare = client.post("/api/chat-ops/supervise", json={"target": _ch(target), "agent": "cursor"}).get_json()
    assert bare["success"] and bare["operation"]["payload"]["instructions"] == ""  # Focus is optional
    bad = client.post("/api/chat-ops/supervise", json={"target": _ch(target), "agent": "cursor",
                                                       "involve_user": "sometimes"})
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


# --- stall detection: what counts as progress, backoff, wording --------------


def test_new_reply_or_subagent_reply_counts_as_progress(chats, monkeypatch):
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    clock = [1000.0]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    monkeypatch.setattr(worker, "_git_head", lambda path: "abc")
    _tick(chats, sched, _snap("busy"), monkeypatch)
    clock[0] += worker.STALL_SEC - 60
    assert _tick(chats, sched, _snap("busy", reply_id=2), monkeypatch) == []  # its own new reply
    clock[0] += worker.STALL_SEC - 60
    child = db.create_chat_session(owner, "lane")
    conn = db._get_connection()
    conn.execute("UPDATE chat_sessions SET parent_session_id=? WHERE id=?", (target, child))
    conn.commit()
    conn.close()
    db.add_message(child, "assistant", "lane reporting")
    assert _tick(chats, sched, _snap("busy", reply_id=2), monkeypatch) == []  # a sub-agent spoke
    clock[0] += worker.STALL_SEC + 60
    assert len(_tick(chats, sched, _snap("busy", reply_id=2), monkeypatch)) == 1


def test_waiting_background_stall_reads_as_a_wait_and_backs_off(chats, monkeypatch):
    sched = _schedule(chats)
    clock = [1000.0]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    monkeypatch.setattr(worker, "_git_head", lambda path: "abc")
    snap = _snap("waiting_background")
    snap["live"]["background_wait"] = [{"job_id": "claude-bg-2-abc", "started_ts": 900.0}]
    _tick(chats, sched, snap, monkeypatch)
    clock[0] += worker.STALL_SEC + 60
    [first] = _tick(chats, sched, snap, monkeypatch)
    text = first["payload"]["text"]
    assert "waiting on background work" in text and "retry loop" not in text
    store.finish(first["id"], "done", {})
    # Second stall without progress needs twice the window.
    clock[0] += worker.STALL_SEC + 60
    assert len(_tick(chats, sched, snap, monkeypatch)) == 1
    clock[0] += worker.STALL_SEC
    checkins = _tick(chats, sched, snap, monkeypatch)
    assert len(checkins) == 2
    assert all("No progress for" in c["payload"]["text"] for c in checkins)
    for c in checkins:
        store.finish(c["id"], "done", {})
    # A newly started wait is progress: the window and the backoff reset.
    snap["live"]["background_wait"] = [{"job_id": "claude-bg-2-def", "started_ts": clock[0]}]
    clock[0] += 2 * worker.STALL_SEC
    assert len(_tick(chats, sched, snap, monkeypatch)) == 2
    clock[0] += worker.STALL_SEC + 60
    assert len(_tick(chats, sched, snap, monkeypatch)) == 3


def _child_of(chats, text):
    db, owner, _sup, target, _foreign = chats
    child = db.create_chat_session(owner, "lane")
    conn = db._get_connection()
    conn.execute("UPDATE chat_sessions SET parent_session_id=? WHERE id=?", (target, child))
    conn.commit()
    conn.close()
    db.add_message(child, "assistant", text)
    return child


def test_lane_finishing_wakes_the_supervisor_of_an_idle_orchestrator(chats, monkeypatch):
    """CH-002006: idle with open Tasks, its lane finished, every check said "unchanged"."""
    db = chats[0]
    sched = _schedule(chats)
    monkeypatch.setattr(worker, "_git_head", lambda path: "abc")
    child = _child_of(chats, "lane running")
    [first] = _tick(chats, sched, _snap("idle_with_work"), monkeypatch)
    store.finish(first["id"], "done", {})
    assert len(_tick(chats, sched, _snap("idle_with_work"), monkeypatch)) == 1  # seen: no new wake
    db.add_message(child, "assistant", "lane done: REPORT.md")
    assert len(_tick(chats, sched, _snap("idle_with_work"), monkeypatch)) == 2


def test_idle_with_work_stall_wakes_the_supervisor_again_and_backs_off(chats, monkeypatch):
    sched = _schedule(chats)
    clock = [1000.0]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    monkeypatch.setattr(worker, "_git_head", lambda path: "abc")
    snap = _snap("idle_with_work")
    [first] = _tick(chats, sched, snap, monkeypatch)
    store.finish(first["id"], "done", {})
    clock[0] += worker.STALL_SEC - 60
    assert len(_tick(chats, sched, snap, monkeypatch)) == 1  # unchanged, not yet stalled
    clock[0] += 120
    checkins = _tick(chats, sched, snap, monkeypatch)
    assert len(checkins) == 2
    text = checkins[0]["payload"]["text"]
    assert "No progress for" in text and "Tasks items still open" in text and "retry loop" not in text
    store.finish(checkins[0]["id"], "done", {})
    clock[0] += worker.STALL_SEC + 60  # backoff: the next stall needs twice the window
    assert len(_tick(chats, sched, snap, monkeypatch)) == 2
    clock[0] += worker.STALL_SEC
    assert len(_tick(chats, sched, snap, monkeypatch)) == 3


# --- stop holds queued sends -------------------------------------------------


def test_stop_cancels_sends_queued_for_the_target(chats):
    db, _owner, sup, target, _foreign = chats
    host = FakeHost(generating=True, steered=False, reason="not steerable")
    service.send(_ch(target), "later 1", actor=_ch(sup), db=db, request=host)
    service.send(_ch(target), "later 2", actor=_ch(sup), db=db, request=host)
    assert len(store.due()) == 2
    out = service.stop(_ch(target), actor=_ch(sup), db=db, request=host)
    assert out["cancelled"] is True
    assert sorted(o["text"] for o in out["cancelled_sends"]) == ["later 1", "later 2"]
    assert store.due() == []
    assert all(o["state"] == "cancelled" for o in store.listing(sup) if o["kind"] == "send")
    assert ("POST", "/api/chat-cancel", target, None) in host.calls


# --- a paused schedule cannot be silently forgotten ---------------------------


def test_pause_is_timed_by_default_and_auto_resumes(chats):
    db, owner, sup, _target, _foreign = chats
    sched = _schedule(chats)
    paused = service.set_paused(sched["id"], owner, True, db=db)
    assert paused["state"] == "paused"
    assert paused["result"]["paused_at"] <= time.time()
    # Default: 4 h, not indefinite — the pause ends by itself.
    assert time.time() + service.DEFAULT_PAUSE_SEC - 5 < paused["result"]["paused_until"]
    assert store.resume_expired() == []  # not yet due
    assert store.resume_expired(now=paused["result"]["paused_until"] + 1)[0]["state"] == "pending"
    back = store.get(sched["id"], sup)
    assert back["state"] == "pending" and back["due"] <= time.time() + 1
    assert back["result"]["paused_until"] is None
    assert store.checks(sched["id"])[0]["outcome"] == "resumed"


def test_pause_durations_and_explicit_indefinite(chats):
    db, owner, _sup, _target, _foreign = chats
    sched = _schedule(chats)
    hour = service.set_paused(sched["id"], owner, True, hours=1, db=db)["result"]["paused_until"]
    assert 3500 < hour - time.time() < 3700
    when = time.time() + 7200
    assert service.set_paused(sched["id"], owner, True, until=when, db=db)["result"]["paused_until"] == when
    forever = service.set_paused(sched["id"], owner, True, indefinite=True, db=db)
    assert forever["result"]["paused_until"] is None
    assert store.resume_expired(now=time.time() + 10 * 86400) == []  # nothing resumes it
    with pytest.raises(ValueError, match="1 minute and 30 days"):
        service.set_paused(sched["id"], owner, True, hours=0.001, db=db)
    with pytest.raises(ValueError, match="1 minute and 30 days"):
        service.set_paused(sched["id"], owner, True, hours=24 * 40, db=db)
    with pytest.raises(ValueError, match="epoch seconds"):
        service.set_paused(sched["id"], owner, True, until="soon", db=db)


def test_paused_schedule_view_says_since_when(chats):
    db, owner, sup, _target, _foreign = chats
    sched = _schedule(chats)
    service.set_paused(sched["id"], owner, True, hours=1, db=db)
    view = service.overview(_ch(sup), owner, db=db)["supervising"][0]
    assert view["state"] == "paused" and view["paused_at"] <= time.time()
    assert view["paused_until"] > time.time()
    service.set_paused(sched["id"], owner, True, indefinite=True, db=db)
    view = service.overview(_ch(sup), owner, db=db)["supervising"][0]
    assert view["paused_until"] is None and view["paused_at"]


def test_supervision_digest_tells_both_chats_about_a_pause(chats):
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    assert f"active — you supervise {_ch(target)}" in service.supervision_digest(sup)
    assert f"{_ch(sup)} supervises this chat" in service.supervision_digest(target)
    assert service.supervision_digest(999) == ""
    service.set_paused(sched["id"], owner, True, indefinite=True, db=db)
    mine = service.supervision_digest(sup)
    assert "**PAUSED since" in mine and "no auto-resume" in mine
    assert f"resume-operation {sched['id']}" in mine
    theirs = service.supervision_digest(target)
    assert "**PAUSED since" in theirs and f"{_ch(sup)} supervises this chat" in theirs
    service.set_paused(sched["id"], owner, True, hours=2, db=db)
    assert "auto-resumes" in service.supervision_digest(sup)


def test_supervision_digest_tells_the_supervisor_how_to_change_its_model(chats):
    """A supervisor once told the user the CLI could not change its check-in model."""
    _db, _owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    mine = service.supervision_digest(sup)
    assert f"edit-operation {sched['id']} --model" in mine and f"--as {_ch(sup)}" in mine
    assert "edit-operation" not in service.supervision_digest(target)


def test_context_envelope_carries_the_pause(chats, monkeypatch):
    db, owner, sup, _target, _foreign = chats
    sched = _schedule(chats)
    service.set_paused(sched["id"], owner, True, indefinite=True, db=db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    from api.cuttle_brain import context_compiler

    block = context_compiler._runtime_block(inventory={}, chat_session_id=_ch(sup))
    assert "Supervision schedules on this chat" in block
    assert "**PAUSED since" in block


def test_a_forgotten_pause_notifies_once(chats, monkeypatch):
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    service.set_paused(sched["id"], owner, True, indefinite=True, db=db)
    toasts = []
    monkeypatch.setattr(worker, "_target_idle_with_work", lambda op, db=None: True)
    kwargs = {"notify": lambda msg, variant="info": toasts.append((msg, variant)), "db": db}

    assert worker.stale_pause_alerts(**kwargs) == []  # paused minutes ago, not hours
    late = time.time() + 7 * 3600
    assert worker.stale_pause_alerts(now=late, **kwargs)[0]["target"] == target
    assert "paused for 7 h" in toasts[0][0] and toasts[0][1] == "warning"
    assert worker.stale_pause_alerts(now=late, **kwargs) == []  # once per pause
    assert store.checks(sched["id"])[0]["outcome"] == "paused_alert"

    # A timed pause is never alerted: it comes back by itself.
    service.set_paused(sched["id"], owner, False, db=db)
    service.set_paused(sched["id"], owner, True, hours=4, db=db)
    assert worker.stale_pause_alerts(now=late, **kwargs) == []
    # Nor is one whose target has nothing left open.
    service.set_paused(sched["id"], owner, True, indefinite=True, db=db)
    monkeypatch.setattr(worker, "_target_idle_with_work", lambda op, db=None: False)
    assert worker.stale_pause_alerts(now=late, **kwargs) == []
    monkeypatch.setattr(worker, "paused_alert_hours", lambda: 0)
    monkeypatch.setattr(worker, "_target_idle_with_work", lambda op, db=None: True)
    assert worker.stale_pause_alerts(now=late, **kwargs) == []  # setting 0 disables


def test_idle_with_open_tasks_is_what_makes_a_pause_stale(chats):
    db, owner, _sup, target, _foreign = chats
    sched = _schedule(chats)
    op = store.get(sched["id"], chats[2])
    from api.gizmos import tasks as tasks_gizmo

    assert worker._target_idle_with_work(op, db=db) is False
    tasks_gizmo.create(gizmo_id="plan", user_id=owner, session_id=target, title="Plan",
                       items=[{"id": "a", "text": "ship it"}], db=db)
    assert worker._target_idle_with_work(op, db=db) is True


def test_route_and_cli_pause_durations(client, chats, monkeypatch):
    _flag(monkeypatch, True)
    from api.chat_cli.cli import main
    db, _owner, sup, _target, _foreign = chats
    sched = _schedule(chats)
    body = client.post(f"/api/chat-ops/operations/{sched['id']}/pause", json={"hours": 1}).get_json()
    assert 3500 < body["operation"]["result"]["paused_until"] - time.time() < 3700
    body = client.post(f"/api/chat-ops/operations/{sched['id']}/pause",
                       json={"indefinite": True}).get_json()
    assert body["operation"]["result"]["paused_until"] is None
    bad = client.post(f"/api/chat-ops/operations/{sched['id']}/pause", json={"hours": 999})
    assert bad.status_code == 400
    client.post(f"/api/chat-ops/operations/{sched['id']}/resume")

    db_path = str(db.db_path)
    assert main(["pause-operation", sched["id"], "--hours", "2", "--db", db_path,
                 "--as", _ch(sup), "--json"]) == 0
    assert store.get(sched["id"], sup)["result"]["paused_until"] > time.time() + 7000
    assert main(["pause-operation", sched["id"], "--indefinite", "--db", db_path,
                 "--as", _ch(sup), "--json"]) == 0
    assert store.get(sched["id"], sup)["result"]["paused_until"] is None


def test_send_to_a_busy_chat_rotates_and_records_its_wait(chats, monkeypatch):
    """A parked send keeps its place in line (oldest first would starve
    check-ins) and says since when it waits."""
    db, _owner, sup, target, _foreign = chats
    op = _queued(chats)
    original = service.context
    monkeypatch.setattr(service, "context", lambda t, actor=None, **kw: original(t, actor, db=db))
    before = time.time()
    worker.dispatch(op, request=FakeHost(generating=True))
    got = store.get(op["id"], sup)
    assert got["state"] == "pending" and got["due"] >= before + worker.BUSY_RETRY_SEC - 1
    since = got["result"]["waiting_since"]
    assert since >= before and got["result"]["waiting_on"] == "target_turn"
    worker.dispatch(got, request=FakeHost(generating=True))
    assert store.get(op["id"], sup)["result"]["waiting_since"] == since  # the first skip, not the latest
    [row] = service.undelivered(target, now=since + 120)
    assert (row["from"], row["to"], row["waiting_sec"]) == (_ch(sup), _ch(target), 120)


def test_undelivered_send_wakes_the_supervisor_once_even_mid_turn(chats, monkeypatch):
    sched = _schedule(chats)
    stuck = {"id": "op1", "from": "CH-000002", "to": "CH-000009", "waiting_on": "target_turn",
             "waiting_sec": worker.STUCK_SEND_SEC + 60, "text": "redo the pistol walks"}
    fresh = {**stuck, "id": "op0", "waiting_sec": 30}
    assert _tick(chats, sched, {**_snap("busy"), "undelivered": [fresh]}, monkeypatch) == []
    checkins = _tick(chats, sched, {**_snap("busy"), "undelivered": [stuck]}, monkeypatch)
    assert len(checkins) == 1
    text = checkins[0]["payload"]["text"]
    assert "Not delivered (1)" in text and "CH-000002 → CH-000009" in text and "redo the pistol walks" in text
    store.finish(checkins[0]["id"], "done", {})
    again = _tick(chats, sched, {**_snap("busy"), "undelivered": [stuck]}, monkeypatch)
    assert [c["id"] for c in again] == [checkins[0]["id"]]  # told once
    assert store.checks(sched["id"])[1]["outcome"] == "undelivered"
