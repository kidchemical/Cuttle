"""Event-driven supervision guards: chat activity re-checks rules, only violations wake."""
from __future__ import annotations

import json
import sqlite3
import subprocess

import pytest

from api import session_events
from api.auth_db import AuthDatabase
from api.chat_ops import rules, service, store, triggers, worker


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    from api.gizmos import tasks
    monkeypatch.setattr(tasks, "database", lambda db=None: db)
    monkeypatch.setattr(triggers, "_pending", {})


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "dev/core")
    _git(path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    _git(path, "branch", "master")
    _git(path, "checkout", "-q", "-b", "dev/feature")
    return path


@pytest.fixture
def chats(tmp_path, repo):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    supervisor = db.create_chat_session(owner, "supervisor")
    target = db.create_chat_session(owner, "orchestrator")
    child = db.create_chat_session(owner, "child")
    db.set_session_project(target, owner, project_name="repo", project_path=str(repo))
    conn = sqlite3.connect(tmp_path / "cuttle_auth.db")
    conn.execute("UPDATE chat_sessions SET parent_session_id=? WHERE id=?", (target, child))
    conn.commit()
    conn.close()
    return db, owner, supervisor, target, child


def _host(uid, sid, method, path, data=None, *, stream=False):
    assert path == "/api/chat-live-status"
    return {"success": True, "generating": True, "status": "working"}


def _schedule(chats, **kw):
    db, _owner, sup, target, _child = chats
    kw.setdefault("required_branch", "dev/feature")
    kw.setdefault("forbidden_branches", ["dev/core", "master"])
    return service.supervise(f"CH-{target:06d}", f"CH-{sup:06d}", every=900,
                             instructions="keep it on track", db=db, **kw)["operation"]


def _checkins(chats):
    return [o for o in store.listing(chats[2]) if o["kind"] == "checkin"]


def _commit_on(repo, branch):
    _git(repo, "checkout", "-q", branch)
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "oops")
    _git(repo, "checkout", "-q", "dev/feature")


def test_publish_reaches_listeners_and_isolates_failures():
    seen = []

    def broken(kind, sid, detail):
        raise RuntimeError("boom")

    def good(kind, sid, detail):
        seen.append((kind, sid, detail))

    off_broken = session_events.subscribe(broken)
    off_good = session_events.subscribe(good)
    try:
        session_events.publish("tool", "db_session_42", failed=False)
        session_events.publish("tool", "not-a-chat")
    finally:
        off_broken()
        off_good()
    assert seen == [("tool", 42, {"failed": False})]


def test_finished_tool_call_publishes_its_chat(monkeypatch):
    from api import query_events

    class Tracker:
        query_id = "q1"
        execution_data = {"user_context": {"chat_session_id": "7"}, "events": []}

        def persist_event(self, kind, payload):
            pass

        def _publish_live_snapshot(self):
            pass

    monkeypatch.setattr(query_events, "_tracker", lambda query_id=None: Tracker())
    monkeypatch.setattr(query_events, "add_event", lambda *a, **k: None)
    seen = []
    off = session_events.subscribe(lambda kind, sid, detail: seen.append((kind, sid)))
    try:
        query_events.record_agent_tool("t1", "Bash git status", phase="started")
        query_events.record_agent_tool("t1", "Bash git status", phase="completed")
    finally:
        off()
    assert seen == [("tool", 7)]


def test_listener_only_notes_the_chat():
    triggers.on_session_event("tool", 5, {})
    triggers.on_session_event("tool", 5, {})
    triggers.on_session_event("something_else", 6, {})
    assert triggers._drain() == {5: "tool"}
    assert triggers._drain() == {}


def test_rules_holding_cost_no_checkin(chats):
    sched = _schedule(chats)
    assert sched["payload"]["forbidden_tips"].keys() == {"dev/core", "master"}
    assert triggers.run_checks({chats[3]: "tool"}, db=chats[0], request=_host) == 0
    assert _checkins(chats) == []


def test_child_commit_on_forbidden_branch_wakes_supervisor_once(chats, repo):
    db, _owner, _sup, target, child = chats
    _schedule(chats)
    _commit_on(repo, "dev/core")
    # The event came from the orchestrator's sub-agent; the orchestrator's schedule checks.
    assert triggers.run_checks({child: "tool"}, db=db, request=_host) == 1
    texts = [c["payload"]["text"] for c in _checkins(chats)]
    assert len(texts) == 1 and "forbidden branch 'dev/core' moved" in texts[0]
    # The new tip is the baseline: the same commit is not reported again.
    assert triggers.run_checks({child: "tool"}, db=db, request=_host) == 0
    assert len(_checkins(chats)) == 1


def test_state_violation_wakes_once_until_it_clears(chats, repo):
    db, _owner, _sup, target, _child = chats
    sched = _schedule(chats)
    _git(repo, "checkout", "-q", "master")
    assert triggers.run_checks({target: "tool"}, db=db, request=_host) == 1
    assert triggers.run_checks({target: "tool"}, db=db, request=_host) == 0
    _git(repo, "checkout", "-q", "dev/feature")
    assert triggers.run_checks({target: "tool"}, db=db, request=_host) == 0
    assert store.get(sched["id"], chats[2])["result"]["guard_reported"] == []
    _git(repo, "checkout", "-q", "master")
    assert triggers.run_checks({target: "worktree_bound"}, db=db, request=_host) == 1
    assert len(_checkins(chats)) == 2


def test_unguarded_and_paused_schedules_are_skipped(chats, repo):
    db, _owner, sup, target, _child = chats
    plain = _schedule(chats, required_branch="", forbidden_branches=[])
    guarded = _schedule(chats)
    store.set_paused(guarded["id"], sup, True)
    _git(repo, "checkout", "-q", "master")
    assert triggers.run_checks({target: "tool"}, db=db, request=_host) == 0
    assert not rules.has_guards(plain["payload"])


def test_interval_check_reports_a_moved_branch_even_behind_a_queued_checkin(chats, repo, monkeypatch):
    db, _owner, sup, target, _child = chats
    sched = _schedule(chats)
    store.create("checkin", sup, sup, {"text": "earlier", "schedule_id": sched["id"]})
    _commit_on(repo, "master")
    worker._check_in(store.get(sched["id"], sup), db, sup, target, _host)
    texts = [c["payload"]["text"] for c in _checkins(chats)]
    assert any("forbidden branch 'master' moved" in t for t in texts)


def test_schedule_without_baseline_adopts_current_tips(chats, repo):
    db, _owner, sup, target, _child = chats
    sched = _schedule(chats)
    with store.connection() as conn:
        payload = dict(sched["payload"])
        payload.pop("forbidden_tips")
        conn.execute("UPDATE operations SET payload=? WHERE id=?", (json.dumps(payload), sched["id"]))
    assert triggers.run_checks({target: "tool"}, db=db, request=_host) == 0
    assert store.get(sched["id"], sup)["payload"]["forbidden_tips"].keys() == {"dev/core", "master"}
