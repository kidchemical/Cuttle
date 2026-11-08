"""Auto-compact service, store and trigger (``api.auto_compact``)."""

from __future__ import annotations

import pytest

from api import chat_delivery, chat_live_status
from api.auth_db import AuthDatabase
from api.auto_compact import service, store, triggers


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    from api.gizmos import tasks
    monkeypatch.setattr(tasks, "database", lambda db=None: db)
    monkeypatch.setattr(service, "available", lambda: True)
    monkeypatch.setattr(service, "settings", lambda: service.pol.merge_settings({}))
    monkeypatch.setattr(service, "_fresh_available", lambda agent, cwd: True)


@pytest.fixture
def chat(tmp_path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "worker")
    return db, owner, sid


class Fill:
    """Stands in for ``api.agent_context``: a context reading plus compact calls."""

    def __init__(self, monkeypatch, percent=80.0, used=160_000, ok=True, after=12.0):
        self.percent, self.used, self.ok, self.after, self.calls = percent, used, ok, after, []
        from api import agent_context
        monkeypatch.setattr(agent_context, "get_agent_context_status", self.status)
        monkeypatch.setattr(agent_context, "compact_agent_context", lambda **kw: self.compact("native", **kw))
        monkeypatch.setattr(agent_context, "fresh_compact_agent_context", lambda **kw: self.compact("fresh", **kw))

    def status(self, **_kw):
        return {"success": True, "supported": True, "agent_id": "claude", "percent": self.percent,
                "used_tokens": self.used, "limit_tokens": 200_000, "token_source": "stream",
                "compact_available": True}

    def compact(self, mode, **kw):
        # What the CLI sees while it runs: the chat is held and shows a status.
        self.calls.append({"mode": mode, "busy": chat_delivery.is_busy(kw["chat_session_id"]),
                           "live": chat_live_status.get_live_status(kw["chat_session_id"]).get("status")})
        if not self.ok:
            return {"success": False, "error": "quota"}
        return {"success": True, "status": {"success": True, "percent": self.after}}


def reply(db, sid, text="done"):
    return db.add_message(sid, "assistant", text)


def evaluate(sid, db, **kw):
    kw.setdefault("agent", "claude")
    kw.setdefault("cwd", "/tmp")
    return service.evaluate(sid, db=db, **kw)


def system_rows(db, sid):
    return [m for m in db.get_messages(sid) if m.get("role") == "system"]


def test_flag_off_does_nothing(chat, monkeypatch):
    db, _, sid = chat
    monkeypatch.setattr(service, "available", lambda: False)
    assert evaluate(sid, db)["reason"] == "flag_off"


def test_compacts_over_threshold_holding_the_chat(chat, monkeypatch):
    db, _, sid = chat
    fill = Fill(monkeypatch)
    reply(db, sid)
    out = evaluate(sid, db)
    assert out["compact"] is True and out["reason"] == "compacted"
    assert fill.calls == [{"mode": "native", "busy": True, "live": "Auto-compacting Claude context (80%)…"}]
    # Released quietly: no turn_end for a turn that never ran.
    assert not chat_delivery.is_busy(sid) and not chat_live_status.get_live_status(sid).get("active")
    notice = system_rows(db, sid)[-1]
    assert notice["metadata"]["timeline_notice"] == {
        "type": "compact", "label": "Claude context auto-compacted", "detail": "80% → 12% · over 70%", "state": "done"}
    assert store.history(sid)[0]["outcome"] == "compacted"


def test_quiet_release_publishes_no_turn_end(chat, monkeypatch):
    db, _, sid = chat
    Fill(monkeypatch)
    reply(db, sid)
    seen = []
    from api import session_events
    unsubscribe = session_events.subscribe(lambda kind, s, d: seen.append(kind))
    try:
        evaluate(sid, db)
    finally:
        unsubscribe()
    assert "turn_end" not in seen


def test_busy_chat_is_left_alone(chat, monkeypatch):
    db, _, sid = chat
    fill = Fill(monkeypatch)
    reply(db, sid)
    assert chat_delivery.try_begin(sid)
    try:
        assert evaluate(sid, db)["reason"] == "busy"
    finally:
        chat_delivery.end(sid, publish=False)
    assert fill.calls == []


def test_one_compact_per_turn(chat, monkeypatch):
    db, _, sid = chat
    fill = Fill(monkeypatch)
    reply(db, sid)
    assert evaluate(sid, db)["compact"]
    # Same turn seen again (a retry, a second process): nothing new to compact.
    assert evaluate(sid, db)["reason"] == "just_compacted"
    fill.percent, fill.used = 30.0, 60_000
    reply(db, sid)
    assert evaluate(sid, db)["reason"] == "below_threshold"
    assert len(fill.calls) == 1


def test_ineffective_compact_escalates_to_fresh(chat, monkeypatch):
    db, _, sid = chat
    fill = Fill(monkeypatch)
    reply(db, sid)
    evaluate(sid, db)
    # The next turn still reads over the threshold: the summary did not help.
    reply(db, sid)
    out = evaluate(sid, db)
    assert out["compact"] and [c["mode"] for c in fill.calls] == ["native", "fresh"]
    assert "fresh session" in system_rows(db, sid)[-1]["metadata"]["timeline_notice"]["detail"]


def test_failures_back_off_and_notice_once(chat, monkeypatch):
    db, _, sid = chat
    fill = Fill(monkeypatch, ok=False)
    reply(db, sid)
    assert evaluate(sid, db)["reason"] == "failed"
    reply(db, sid)
    assert evaluate(sid, db)["reason"] == "failed"   # waited 1 turn
    reply(db, sid)
    assert evaluate(sid, db)["reason"] == "backoff"  # streak 2 waits 2 turns
    notices = [m for m in system_rows(db, sid) if m["metadata"].get("auto_compact")]
    assert len(notices) == 1 and notices[0]["metadata"]["timeline_notice"]["state"] == "error"
    assert len(fill.calls) == 2


def test_override_disables_and_resets_streak(chat, monkeypatch):
    db, _, sid = chat
    Fill(monkeypatch)
    store.patch_state(sid, fail_streak=4)
    service.set_chat_override(sid, {"enabled": False}, db=db)
    reply(db, sid)
    assert evaluate(sid, db)["reason"] == "disabled"
    assert store.get_state(sid)["fail_streak"] == 0
    view = service.set_chat_override(sid, {}, db=db)
    assert view["override"] == {} and view["policy"]["enabled"] is True


def test_milestone_compacts_at_lower_threshold(chat, monkeypatch):
    db, owner, sid = chat
    from api.gizmos import tasks
    fill = Fill(monkeypatch, percent=45.0, used=90_000)
    tasks.create(gizmo_id="plan", items=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
                 session_id=sid, user_id=owner, db=db)
    reply(db, sid)
    assert evaluate(sid, db)["reason"] == "below_threshold"  # first look is the baseline
    tasks.patch("plan", {"set_done": ["a"]}, user_id=owner, session_id=sid, db=db)
    reply(db, sid)
    out = evaluate(sid, db)
    assert out["compact"] and out["decision"]["trigger"] == "milestone"
    assert "Tasks milestone" in system_rows(db, sid)[-1]["metadata"]["timeline_notice"]["detail"]
    reply(db, sid)
    assert evaluate(sid, db)["reason"] == "below_threshold"  # milestone consumed
    assert len(fill.calls) == 1


def test_managed_scope_for_supervised_chats(chat, monkeypatch):
    db, owner, sid = chat
    Fill(monkeypatch, percent=65.0, used=130_000)
    reply(db, sid)
    assert evaluate(sid, db)["reason"] == "below_threshold"  # ordinary chat: 70%
    from api.chat_ops import store as ops
    sup = db.create_chat_session(owner, "supervisor")
    ops.create("supervise", sup, sid, {"every": 900, "instructions": "x"})
    assert service.chat_policy(sid, db=db)["managed_reason"] == "supervised"
    assert service.chat_policy(sup, db=db)["managed_reason"] == "supervisor"
    reply(db, sid)
    assert evaluate(sid, db)["compact"]  # managed: 60%


def test_subagent_children_compact_in_preflight_only(chat, monkeypatch):
    db, owner, sid = chat
    fill = Fill(monkeypatch)
    child = db.create_chat_session(owner, "child")
    monkeypatch.setattr(db, "get_chat_session_by_id",
                        lambda s, _orig=db.get_chat_session_by_id: {**_orig(s), "parent_session_id": sid}
                        if int(s) == child else _orig(s))
    reply(db, child)
    assert evaluate(child, db)["reason"] == "preflight_only"
    out = service.preflight(child, agent="claude", cwd="/tmp", db=db)
    assert out["compact"] and fill.calls[0]["busy"] is False  # the child turn owns the chat


def test_lease_blocks_a_second_compact(chat, monkeypatch):
    db, _, sid = chat
    fill = Fill(monkeypatch)
    reply(db, sid)
    assert store.claim_lease(sid, "other-process", 60)
    assert evaluate(sid, db)["reason"] == "leased"
    store.release_lease(sid, "other-process")
    assert fill.calls == []


def test_dry_run_changes_nothing(chat, monkeypatch):
    db, _, sid = chat
    fill = Fill(monkeypatch)
    reply(db, sid)
    out = evaluate(sid, db, dry_run=True)
    assert out["would_compact"] and fill.calls == [] and not store.get_state(sid).get("turns")


def test_trigger_waits_quiet_period_and_retries_live_cli():
    calls = []
    results = iter([{"reason": "live_cli"}, {"reason": "compacted"}])

    def fake(sid, trigger, count_turn):
        calls.append((sid, count_turn))
        return next(results)

    triggers._due.clear()
    triggers.arm(7, delay=20, now=1000)
    assert triggers.run_due(1010, evaluate=fake) == [] and calls == []
    assert triggers.run_due(1020, evaluate=fake) == [{"reason": "live_cli"}]
    assert triggers.run_due(1079, evaluate=fake) == []
    triggers.run_due(1080, evaluate=fake)
    assert calls == [(7, True), (7, False)] and not triggers._due


def test_trigger_ignores_events_with_flag_off(monkeypatch):
    triggers._due.clear()
    monkeypatch.setattr(service, "available", lambda: False)
    triggers.on_session_event("turn_end", 9, {})
    assert 9 not in triggers._due
    monkeypatch.setattr(service, "available", lambda: True)
    triggers.on_session_event("tool", 9, {})
    assert 9 not in triggers._due
    triggers.on_session_event("turn_end", 9, {})
    assert 9 in triggers._due
    triggers._due.clear()
