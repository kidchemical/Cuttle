"""A turn's assistant row and inspector exist from its first frame (api.chat_turn_row).

Real ``/api/chat`` ingress, real SQLite, the REAL harness kernel and the real
``claude`` / ``codex`` adapters — only each adapter's ``execute`` is a fake that
holds the turn open, so the live state can be read mid-turn:

- the live turn has its assistant row (id + query id) and a working inspector
  before it finishes, and the reply lands in that same row;
- a stopped or failed turn finalises the same row (no empty orphan);
- supervision check-ins and unit-watch resumes (chat-ops deliveries) get rows;
- a background continuation gets its own row while it works;
- a row left behind by a crash heals on the next history read.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from api import chat_delivery, chat_live_status
from api import web_chat_api as wca
from api.agent_harness.types import AgentResult

AGENTS = ("claude", "codex")


@pytest.fixture
def authed(monkeypatch, tmp_path):
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "turn_row_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    monkeypatch.setattr("api.chat_titler.schedule_session_autoname", lambda *a, **k: None)
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    return client, db, owner


class Gate:
    """A fake ``execute`` for a real adapter: waits until released, then replies."""

    def __init__(self, reply="held reply", *, fail=False, raise_exc=False):
        self.reply, self.fail, self.raise_exc = reply, fail, raise_exc
        self.entered = threading.Event()
        self.release = threading.Event()
        self.seen = {}

    def install(self, monkeypatch, agent_id, db=None):
        from api.agent_harness import catalog, kernel

        manifest, adapter = catalog.get_agent(agent_id)
        gate = self

        async def execute(prompt, **kwargs):
            sid = kwargs.get("chat_session_id")
            if db is not None and sid:
                gate.seen["rows"] = db.in_progress_messages(int(sid))
            gate.entered.set()
            gate.release.wait(20)
            if gate.raise_exc:
                raise RuntimeError("adapter blew up")
            return AgentResult(success=not gate.fail, output="" if gate.fail else gate.reply,
                               error="boom" if gate.fail else None)

        monkeypatch.setattr(adapter, "available", lambda: True)
        monkeypatch.setattr(adapter, "execute", execute)
        monkeypatch.setattr(kernel, "get_agent", lambda aid, project_path=None: (manifest, adapter))
        return self


def _sse(chunks):
    for chunk in chunks:
        for line in chunk.decode("utf-8", "replace").splitlines():
            if line.startswith("data: "):
                yield json.loads(line[6:])


def _start_stream(client, sid, text):
    res = client.post("/api/chat", json={"message": text, "session_id": sid}, buffered=False)
    return res, _sse(iter(res.response))


def _until(events, kind):
    for ev in events:
        if ev.get("type") == kind:
            return ev
    raise AssertionError(f"stream ended before {kind}")


def _drain(events):
    return list(events)


def _assistants(db, sid, **kw):
    return [m for m in db.get_messages(sid, **kw) if m["role"] == "assistant"]


@pytest.mark.parametrize("agent", AGENTS)
def test_live_turn_has_row_and_inspector_before_it_finishes(authed, monkeypatch, agent):
    client, db, owner = authed
    sid = db.create_chat_session(owner)
    gate = Gate(f"{agent} answer").install(monkeypatch, agent, db)
    res, events = _start_stream(client, sid, f"/{agent} hello")
    try:
        started = _until(events, "query_started")
        mid, qid = started["message_id"], started["query_id"]
        assert mid and qid and started["report_url"] == f"/query_log.html?id={qid}"
        assert gate.entered.wait(10)
        # The harness kernel tracks this run under the row's query id.
        assert gate.seen["rows"] and gate.seen["rows"][0]["metadata"]["query_id"] == qid

        # The row exists now: hidden from readers, shown to history as in progress.
        assert _assistants(db, sid) == []
        hist = client.get(f"/api/auth/sessions/{sid}/messages").get_json()["messages"]
        live_rows = [m for m in hist if m.get("in_progress")]
        assert [m["id"] for m in live_rows] == [mid] and live_rows[0]["query_id"] == qid
        # CH-…-N for the live bubble resolves to the same row.
        assert db.get_message_by_share_index(sid, 2)["id"] == mid
        # Live status hands every device the bubble's row and inspector.
        live = client.get(f"/api/chat-live-status?session_id={sid}").get_json()
        assert live["message_id"] == mid and live["query_id"] == qid
        # The inspector answers, executing, before the turn ends.
        log = client.get(f"/api/query-log/{qid}")
        assert log.status_code == 200, log.get_json()
        assert log.get_json()["executing"] is True

        gate.release.set()
        tail = _drain(events)
    finally:
        gate.release.set()
        res.close()
    final = next(e for e in tail if e.get("type") == "response")
    assert final["message_id"] == mid and final["query_id"] == qid
    [row] = _assistants(db, sid)
    assert row["id"] == mid and row["content"] == f"{agent} answer"
    assert row["metadata"]["query_id"] == qid and not row["metadata"].get("in_progress")
    after = client.get(f"/api/query-log/{qid}")
    assert after.status_code == 200 and after.get_json()["executing"] is False
    attention = db.get_attention(sid, owner)
    assert attention["reply_id"] == mid  # "new reply" moved when the reply landed


@pytest.mark.parametrize("agent", AGENTS)
def test_stopped_turn_finalises_its_own_row(authed, monkeypatch, agent):
    client, db, owner = authed
    sid = db.create_chat_session(owner)
    gate = Gate("late answer").install(monkeypatch, agent, db)
    res, events = _start_stream(client, sid, f"/{agent} long job")
    try:
        started = _until(events, "query_started")
        assert gate.entered.wait(10)
        stop = client.post("/api/chat-cancel", json={"session_id": str(sid)})
        assert stop.status_code == 200
        gate.release.set()
        _drain(events)
    finally:
        gate.release.set()
        res.close()
    deadline = time.time() + 5
    while time.time() < deadline and chat_delivery.is_busy(sid):
        time.sleep(0.05)
    rows = client.get(f"/api/auth/sessions/{sid}/messages").get_json()["messages"]
    assistants = [m for m in rows if m["role"] == "assistant"]
    assert [m["id"] for m in assistants] == [started["message_id"]]  # same row, no extra card
    assert assistants[0]["content"].startswith("**Agent stopped before completion.**")
    assert "late answer" not in assistants[0]["content"]
    assert not assistants[0].get("in_progress")
    assert assistants[0]["query_id"] == started["query_id"]


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("how", ["fail", "raise"])
def test_failed_turn_finalises_its_own_row(authed, monkeypatch, agent, how):
    client, db, owner = authed
    sid = db.create_chat_session(owner)
    gate = Gate(fail=how == "fail", raise_exc=how == "raise").install(monkeypatch, agent, db)
    gate.release.set()
    res = client.post("/api/chat", json={"message": f"/{agent} break", "session_id": sid})
    body = res.get_data(as_text=True)
    started = next(e for e in _sse([body.encode()]) if e.get("type") == "query_started")
    rows = db.get_messages(sid, include_in_progress=True)
    assistants = [m for m in rows if m["role"] == "assistant"]
    assert [m["id"] for m in assistants] == [started["message_id"]]
    assert assistants[0]["content"].strip()  # the error text or the failed card
    assert not assistants[0]["metadata"].get("in_progress")
    assert assistants[0]["metadata"]["query_id"] == started["query_id"]


def test_sync_lane_gets_the_row_too(authed, monkeypatch):
    client, db, owner = authed
    sid = db.create_chat_session(owner)
    gate = Gate("sync answer").install(monkeypatch, "codex", db)
    gate.release.set()
    body = client.post("/api/chat", json={"message": "/codex hi", "session_id": sid,
                                          "stream": False}).get_json()
    [row] = _assistants(db, sid)
    assert gate.seen["rows"] and gate.seen["rows"][0]["id"] == row["id"]
    assert body["message_id"] == row["id"] and body["query_id"] == row["metadata"]["query_id"]


# ── turns without a user typing: chat-ops deliveries ─────────────────────────


class _Stream:
    def __init__(self, response):
        self.status_code = response.status_code
        self._lines = response.get_data().splitlines()

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _transport(client):
    """api.chat_ops.transport over the test client (same session scoping)."""
    def request(uid, sid, method, path, data=None, *, stream=False):
        payload = {**(data or {}), "session_id": f"db_session_{sid}"}
        if method == "GET":
            return client.get(path, query_string=payload).get_json()
        return _Stream(client.post(path, json=payload))
    return request


@pytest.mark.parametrize("kind", ["notice", "checkin"])
def test_resume_and_checkin_deliveries_get_rows(authed, monkeypatch, kind):
    """A unit-watch resume (notice) and a supervision check-in run through the
    ordinary ingress: their reply row exists while the agent works."""
    from api.chat_ops import store, worker

    client, db, owner = authed
    sid = db.create_chat_session(owner)
    db.add_message(sid, "user", "/claude earlier work", metadata=None)
    gate = Gate(f"{kind} handled").install(monkeypatch, "claude", db)
    gate.release.set()
    op = store.create(kind, sid, sid, {"text": "/claude The detached job exited. Continue."})
    worker.dispatch(op, request=_transport(client))
    assert store.find(op["id"])["state"] in ("delivered", "done")
    assert gate.seen["rows"], "the reply row existed while the agent ran"
    live_row = gate.seen["rows"][0]
    [row] = _assistants(db, sid)
    assert row["id"] == live_row["id"] and row["content"] == f"{kind} handled"
    assert row["metadata"]["query_id"] == live_row["metadata"]["query_id"]
    user = [m for m in db.get_messages(sid) if m["role"] == "user"][-1]
    assert user["metadata"]["chat_op_id"] == op["id"]


# ── background continuation ──────────────────────────────────────────────────


def test_background_followup_gets_its_own_row_while_it_works(authed, tmp_path):
    from api.agent_harness.background_wait import BackgroundWait
    from api.chat_turn_persist import make_assistant_saver

    client, db, owner = authed
    sid = db.create_chat_session(owner)
    first = db.add_message(sid, "assistant", "first reply", metadata={"query_id": "abcd1234"})
    saver = make_assistant_saver(
        chat_session_id=sid, db=db, resolve_project=lambda d: "",
        assistant_meta_fn=lambda r: {"query_id": r.get("query_id")},
        project_merge_fn=lambda meta, *a: meta, schedule_autoname=lambda *a: None,
        project_root=tmp_path)
    wait = BackgroundWait(sid, "claude")
    wait.start(["build"], pid=None)
    wait.bind(saver, {"query_id": "abcd1234", "success": True})
    wait.working("Answering the build result")
    [live] = db.in_progress_messages(sid)
    assert live["id"] > first and live["metadata"]["query_id"] == "abcd1234"  # still that run
    assert chat_live_status.get_live_status(sid)["message_id"] == live["id"]
    wait.followup("build passed, continuing")
    rows = _assistants(db, sid)
    assert [r["id"] for r in rows] == [first, live["id"]]
    assert rows[1]["content"] == "build passed, continuing" and not rows[1]["metadata"].get("in_progress")
    # A working phase that ends with no answer leaves nothing behind.
    wait.working("thinking again")
    assert db.in_progress_messages(sid)
    wait.finish("done", "finished")
    assert db.in_progress_messages(sid) == [] and len(_assistants(db, sid)) == 2
    chat_live_status.clear_live_status(sid)


# ── crash / restart mid-turn ─────────────────────────────────────────────────


def test_orphan_row_from_a_dead_turn_heals_on_history_read(authed):
    from api import chat_turn_row

    client, db, owner = authed
    sid = db.create_chat_session(owner)
    db.add_message(sid, "user", "/claude work", metadata=None)
    row = chat_turn_row.open_row(db, sid, turn=1)
    chat_turn_row.reset_for_tests()  # the process that owned the turn is gone
    msgs = client.get(f"/api/auth/sessions/{sid}/messages").get_json()["messages"]
    [asst] = [m for m in msgs if m["role"] == "assistant"]
    assert asst["id"] == row["message_id"] and not asst.get("in_progress")
    assert asst["content"].startswith("**Agent interrupted before completion.**")
    assert asst["query_id"] == row["query_id"]
    assert db.in_progress_messages(sid) == []


def test_history_read_never_heals_a_turn_running_in_another_process(authed, monkeypatch):
    """A sub-agent turn runs in the `api.subagents spawn` CLI, not Flask.

    Flask's open set does not know it, so a history read stamped the live
    turn "interrupted" mid-run (CH-002266-2)."""
    import subprocess
    import sys
    import psutil
    from api import chat_turn_row

    client, db, owner = authed
    sid = db.create_chat_session(owner)
    row = chat_turn_row.open_row(db, sid, turn=1)
    chat_turn_row.reset_for_tests()  # the turn is not open in this process
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        run = {"status": "running", "metadata": {"capture_owner": {
            "pid": other.pid, "started_at": psutil.Process(other.pid).create_time()}}}
        monkeypatch.setattr(chat_turn_row, "_run", lambda qid: run)
        assert chat_turn_row.heal_orphans(db, sid) == []
        assert db.in_progress_messages(sid)
    finally:
        other.kill()
        other.wait()
    # Once that process is gone the row is a real orphan and heals.
    assert chat_turn_row.heal_orphans(db, sid) == [row["message_id"]]


def test_history_read_during_the_reply_save_never_stamps_the_interrupted_card(authed, monkeypatch):
    """A poll landing between finalize's start and its commit healed the live row.

    finalize used to drop the turn from the open set before writing; a history
    read in that gap saw an in-progress row with no turn behind it, built the
    interrupted card and committed it after the reply (the reply was lost)."""
    from api import chat_turn_row
    from api.auth_db import AuthDatabase

    client, db, owner = authed
    sid = db.create_chat_session(owner)
    db.add_message(sid, "user", "/claude work", metadata=None)
    row = chat_turn_row.open_row(db, sid, turn=1)
    real_update = AuthDatabase.update_message_content
    healed = {}

    def update_with_poll(self, message_id, content, metadata=None):
        if content == "the real reply" and "polled" not in healed:
            healed["polled"] = True  # a history read arrives mid-save
            healed["rows"] = chat_turn_row.heal_orphans(db, sid)
        return real_update(self, message_id, content, metadata=metadata)

    monkeypatch.setattr(AuthDatabase, "update_message_content", update_with_poll)
    mid = chat_turn_row.finalize(db, sid, "the real reply", {"query_id": row["query_id"]})
    assert mid == row["message_id"] and healed["rows"] == []
    client.get(f"/api/auth/sessions/{sid}/messages")  # a later read settles nothing
    [asst] = _assistants(db, sid)
    assert asst["content"] == "the real reply"
    assert chat_turn_row.current(sid) is None
    # A close racing the same turn after the save is a no-op.
    chat_turn_row.close(db, sid, status="cancelled", turn=1)
    assert _assistants(db, sid)[0]["content"] == "the real reply"


def test_skipped_turn_drops_its_row_only_when_newest(authed):
    from api import chat_turn_row

    client, db, owner = authed
    sid = db.create_chat_session(owner)
    row = chat_turn_row.open_row(db, sid, turn=1)
    chat_turn_row.close(db, sid, status="skipped", turn=1)
    assert db.get_messages(sid, include_in_progress=True) == []
    row = chat_turn_row.open_row(db, sid, turn=2)
    db.add_message(sid, "user", "sent meanwhile", metadata=None)
    chat_turn_row.close(db, sid, status="skipped", turn=2)
    [asst] = _assistants(db, sid)
    assert asst["id"] == row["message_id"]  # later rows keep their CH-…-N
    assert asst["content"].startswith("**Agent stopped before completion.**")


def test_stale_turn_never_closes_a_newer_row(authed):
    from api import chat_turn_row

    _client, db, owner = authed
    sid = db.create_chat_session(owner)
    chat_turn_row.open_row(db, sid, turn=7)
    chat_turn_row.close(db, sid, status="cancelled", turn=6)
    assert chat_turn_row.current(sid)["turn"] == 7 and db.in_progress_messages(sid)


def test_answered_card_keeps_the_bubbles_query_id(authed):
    """Locking a question card rewrote the whole metadata (raw JSON read as empty)."""
    from api import action_forms

    _client, db, owner = authed
    sid = db.create_chat_session(owner)
    content = ('<cuttle_action_form_pending id="f1">\n{"title": "Q", "options": []}\n'
               '</cuttle_action_form_pending>')
    mid = db.add_message(sid, "assistant", content, metadata={"query_id": "q1234567", "project_path": "/p"})
    meta = action_forms._message_meta(db.get_message_by_id(mid))
    assert meta == {"query_id": "q1234567", "project_path": "/p", "turn_id": "q1234567"}
