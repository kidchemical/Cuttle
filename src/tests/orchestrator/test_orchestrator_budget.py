"""Budget-aware orchestration: tier spawns skip out-of-usage agents, blocked
profiles are refused before a chat exists, child quota failures are remembered,
and the router's cooldowns / usage snapshot are shared across processes."""

from __future__ import annotations

import pytest

from api.agent_router import budget, quota
from api.agent_router.types import ExecutionTarget
from api.orchestrator import model, service
from api.subagents import service as sub
from api.subagents.service import SubagentError

from .test_orchestrator import _seed_db, orch  # noqa: F401  (fixture)

OUT_OF_USAGE = (
    "[FAIL] **Cursor Agent** (`agent`):\n```\nActionRequiredError: Increase limits for faster "
    "responses You're out of usage. Switch to Auto, or ask your admin to increase your limit.\n```"
)


def _bound(tmp_path, monkeypatch):
    db, owner, _other, sid, _ = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    service.save_playbook(owner, {
        "id": "budget", "name": "Budget",
        "tiers": [
            {"id": "worker", "label": "Grunt", "role": "worker",
             "members": [{"profile": "escalate"}, {"profile": "expert-codex"}, {"profile": "grunt"}]},
            {"id": "expert", "label": "Expert", "role": "expert", "members": [{"profile": "expert-claude"}]},
        ],
    }, db=db)
    service.bind(sid, "budget", "ship it", db=db)
    return db, owner, sid


def _runner(seen):
    def run(**kw):
        seen.append((kw["agent"], kw["spec"].model, kw["spec"].profile_id))
        return {"success": True, "response": "done"}
    return run


def _chats(db, owner):
    return len(db.get_user_chat_sessions(owner))


def test_pick_member_prefers_declared_order_then_headroom():
    assert model.pick_member(["ok", "ok"]) == 0
    assert model.pick_member(["blocked", "unknown", "ok"]) == 1
    assert model.pick_member(["low", "ok"]) == 1
    assert model.pick_member(["blocked", "low"]) == 1
    assert model.pick_member(["blocked", "blocked"]) is None
    pb = {"tiers": [{"id": "worker", "label": "Grunt", "role": "worker"}]}
    assert model.find_tier(pb, "grunt")["id"] == model.find_tier(pb, "WORKER")["id"] == "worker"
    assert model.find_tier(pb, "expert") is None


def test_tier_spawn_skips_an_out_of_usage_account(tmp_path, orch, monkeypatch):
    db, owner, sid = _bound(tmp_path, monkeypatch)
    quota.mark_exhausted(ExecutionTarget("cursor", "grok-4.6"), OUT_OF_USAGE)
    seen = []
    out = sub.spawn(parent_session_id=sid, db=db, runner=_runner(seen),
                    children=[{"title": "Fix", "tier": "worker", "message": "fix it"}])
    assert seen == [("codex", "gpt-6.1-sol", "expert-codex")]
    pick = out["tier_picks"][0]
    assert pick["tier"] == "worker" and pick["picked"] == "Expert-Codex"
    assert pick["skipped"][0]["name"] == "Escalate" and pick["skipped"][0]["state"] == "blocked"
    assert "out of usage until" in pick["skipped"][0]["why"]


def test_tier_spawn_reads_live_usage_and_keeps_order_when_healthy(tmp_path, orch, monkeypatch):
    db, owner, sid = _bound(tmp_path, monkeypatch)
    seen = []
    sub.spawn(parent_session_id=sid, db=db, runner=_runner(seen),
              children=[{"title": "A", "tier": "Grunt", "message": "x"}])
    assert seen[-1][0:2] == ("cursor", "grok-4.6")  # nothing known → first in order
    # Cursor premium blocked by its plan window (cached snapshot): Auto would still run,
    # but grok is premium → skipped; Codex nearly spent but budget awareness is off → still next.
    budget.set_accounts([budget.Account("cursor", premium_blocked=True, label="included API usage 100%"),
                         budget.Account("codex", headroom=0.05, label="weekly 95%")])
    sub.spawn(parent_session_id=sid, db=db, runner=_runner(seen),
              children=[{"title": "B", "tier": "worker", "message": "x"}])
    assert seen[-1][0] == "codex"
    # With router budget awareness on, the nearly-spent Codex drops behind Cursor Auto.
    monkeypatch.setattr(budget, "budget_settings",
                        lambda raw=None: {"enabled": True, "low_headroom": 0.15, "refresh_s": 300})
    sub.spawn(parent_session_id=sid, db=db, runner=_runner(seen),
              children=[{"title": "C", "tier": "worker", "message": "x"}])
    assert seen[-1][0:2] == ("cursor", "auto")


def test_every_agent_blocked_fails_before_any_chat(tmp_path, orch, monkeypatch):
    db, owner, sid = _bound(tmp_path, monkeypatch)
    budget.set_accounts([budget.Account("claude", blocked=True, label="weekly 100%")])
    before = _chats(db, owner)
    with pytest.raises(SubagentError, match=r"every agent in the Expert tier is out of budget.*No chat was created"):
        sub.spawn(parent_session_id=sid, db=db, runner=_runner([]),
                  children=[{"title": "Review", "tier": "expert", "message": "x"}])
    assert _chats(db, owner) == before
    with pytest.raises(SubagentError, match="no tier 'nope'"):
        sub.spawn(parent_session_id=sid, db=db, runner=_runner([]),
                  children=[{"title": "X", "tier": "nope", "message": "x"}])


def test_blocked_profile_is_refused_with_the_tiers_runnable_agent(tmp_path, orch, monkeypatch):
    db, owner, sid = _bound(tmp_path, monkeypatch)
    quota.mark_exhausted(ExecutionTarget("cursor", "grok-4.6"), OUT_OF_USAGE)
    before = _chats(db, owner)
    with pytest.raises(SubagentError, match=r'out of budget.*No chat was created; the Grunt tier can run '
                                            r'Expert-Codex — spawn with "tier": "worker"'):
        sub.spawn(parent_session_id=sid, db=db, runner=_runner([]),
                  children=[{"title": "Fix", "profile": "escalate", "message": "x"}])
    assert _chats(db, owner) == before
    # Outside an orchestrator chat there is no tier to suggest.
    plain = db.create_chat_session(owner, "plain")
    with pytest.raises(SubagentError, match="choose another agent or wait for the reset"):
        sub.spawn(parent_session_id=plain, db=db, runner=_runner([]),
                  children=[{"title": "Fix", "agent": "cursor", "model": "grok-4.6", "message": "x"}])


def test_tier_and_profile_together_are_rejected():
    with pytest.raises(ValueError, match="either a tier or a profile"):
        sub.parse_children([{"title": "x", "tier": "worker", "profile": "grunt", "message": "m"}])


def test_child_quota_failure_marks_the_account(tmp_path, orch, monkeypatch):
    db, owner, sid = _bound(tmp_path, monkeypatch)

    def out_of_usage(**kw):
        return {"success": False, "type": "cursor_error", "error": OUT_OF_USAGE, "response": OUT_OF_USAGE}

    sub.spawn(parent_session_id=sid, db=db, runner=out_of_usage,
              children=[{"title": "Fix", "tier": "worker", "message": "x"}])
    assert quota.is_exhausted(ExecutionTarget("cursor", "grok-4.6"))
    assert not quota.is_exhausted(ExecutionTarget("cursor", "auto"))  # premium only


def test_cooldowns_and_usage_snapshot_are_shared_across_processes():
    quota.mark_exhausted(ExecutionTarget("codex", "gpt-6.1-sol"), "You've hit your usage limit")
    # Another process: empty memory, same home.
    quota._EXHAUSTED.clear()
    quota._FILE_SEEN = None
    assert quota.is_exhausted(ExecutionTarget("codex", "gpt-6.1-sol"))
    quota.clear("codex")
    quota._FILE_SEEN = None
    assert not quota.is_exhausted(ExecutionTarget("codex", "gpt-6.1-sol"))

    budget.set_accounts([budget.Account("claude", blocked=True, label="5-hour 100%")])
    with budget._LOCK:
        budget._ACCOUNTS.clear()
        budget._FETCHED_AT = 0.0
    assert budget.cached_snapshot()["claude"].blocked  # read from the cache file, no fetch
    budget.clear()
    assert budget.cached_snapshot() == {}
