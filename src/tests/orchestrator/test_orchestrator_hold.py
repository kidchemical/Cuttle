"""Orchestration hold: Pause / Stop all work / Resume over the whole orchestration."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from api.orchestrator import control, service, store
from tests.orchestrator.test_orchestrator import WEB, _seed_db, orch  # noqa: F401  (fixture)


@pytest.fixture()
def held_ctx(tmp_path, orch, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "output").mkdir(parents=True, exist_ok=True)
    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    service.bind(sid, "default", "ship it", db=db)
    calls = []

    def request(uid, session, method, path, body=None, **kw):
        calls.append((session, method, path))
        return {"success": True}

    return {"db": db, "owner": owner, "other": other, "sid": sid, "other_sid": other_sid,
            "request": request, "calls": calls}


def _supervise(ctx):
    from api.chat_ops import service as chat_ops

    sup = ctx["db"].create_chat_session(ctx["owner"], "supervisor")
    return chat_ops.supervise(ctx["sid"], sup, db=ctx["db"])["operation"]


def test_pause_stops_the_turn_pauses_supervision_and_marks_the_brief(held_ctx):
    from api.chat_ops import store as ops

    ctx, sid = held_ctx, held_ctx["sid"]
    op = _supervise(ctx)
    out = control.hold(sid, user_id=ctx["owner"], db=ctx["db"], request=ctx["request"])
    assert out["hold"] == "paused" and out["hold_at"]
    assert out["actions"]["supervision_paused"] == [op["id"]] and out["actions"]["turn_stopped"] is True
    assert "jobs_stopped" not in out["actions"] and "errors" not in out["actions"]
    assert (sid, "POST", "/api/chat-cancel") in ctx["calls"]
    assert ops.find(op["id"])["state"] == "paused"
    assert control.held(sid) == "paused" and control.held(ctx["other_sid"]) == ""
    block = service.brief_block(sid)
    assert block.startswith("**This orchestration is PAUSED by the user.**") and "## Orchestrator playbook" in block

    back = control.resume(sid, user_id=ctx["owner"], db=ctx["db"])
    assert back["hold"] == "" and back["actions"]["supervision_resumed"] == [op["id"]]
    assert ops.find(op["id"])["state"] == "pending"
    assert service.brief_block(sid).startswith("## Orchestrator playbook")
    assert [e["action"] for e in store.binding_events(session_id=sid)][-3:] == ["pause", "pause", "resume"]


def test_resume_leaves_supervision_the_user_paused_alone(held_ctx):
    from api.chat_ops import store as ops

    ctx, sid = held_ctx, held_ctx["sid"]
    op = _supervise(ctx)
    ops.set_paused(op["id"], op["actor"], True)  # the user's own pause, before the hold
    out = control.hold(sid, db=ctx["db"], request=ctx["request"])
    assert out["actions"]["supervision_paused"] == []
    assert control.resume(sid, db=ctx["db"])["actions"]["supervision_resumed"] == []
    assert ops.find(op["id"])["state"] == "paused"


def test_a_held_chat_gets_no_deliveries_until_resume(held_ctx, monkeypatch):
    from api.chat_ops import store as ops, worker

    ctx, sid = held_ctx, held_ctx["sid"]
    notice = ops.create("notice", sid, sid, {"text": "job finished", "label": "Detached job finished"})
    control.hold(sid, db=ctx["db"], request=ctx["request"])
    monkeypatch.setattr(worker.service, "context", lambda target, actor: (ctx["db"], actor, {"user_id": ctx["owner"], "id": sid}))
    ctx["calls"].clear()
    worker.dispatch(ops.find(notice["id"]), request=ctx["request"])
    row = ops.find(notice["id"])
    assert row["state"] == "pending" and row["result"]["waiting_on"] == ops.HELD_REASON
    assert ctx["calls"] == []  # not even a live-status read: nothing reaches the chat
    assert row["due"] > notice["due"]

    out = control.resume(sid, db=ctx["db"])
    assert out["actions"]["deliveries_released"] == 1
    assert ops.find(notice["id"])["due"] <= __import__("time").time()


def test_stop_ends_jobs_and_drops_wake_ups(held_ctx, monkeypatch):
    from api import job_watch as jw
    from api.chat_ops import store as ops

    ctx, sid = held_ctx, held_ctx["sid"]
    running = {"queried": True, "running": True, "active_state": "active", "sub_state": "running",
               "result": "", "exit": None, "pid": 4242}
    monkeypatch.setattr(jw, "unit_state", lambda unit: dict(running))
    monkeypatch.setattr(jw, "timer_state", lambda unit: {"waiting": False})
    monkeypatch.setattr(jw, "kill_status_pids", lambda pids: 0)
    systemctl = []
    monkeypatch.setattr(jw, "_systemctl", lambda args: systemctl.append(list(args)) or (0, ""))
    job = jw.register_unit_watch(unit="lane-build", chat_session_id=sid, resume_message="Start the next build.")
    notice = ops.create("notice", sid, sid, {"text": "earlier job finished"})
    send = ops.create("send", ctx["other_sid"], sid, {"text": "carry on"})

    out = control.hold(sid, stop=True, db=ctx["db"], request=ctx["request"])
    assert out["hold"] == "stopped"
    assert out["actions"]["jobs_stopped"] == [job["id"]] and out["actions"]["deliveries_dropped"] == 2
    assert ["stop", "lane-build"] in systemctl
    assert ops.find(notice["id"])["state"] == "cancelled" and ops.find(send["id"])["state"] == "cancelled"
    assert "STOPPED by the user" in service.brief_block(sid)

    # The stopped job's exit must not wake the orchestrator.
    monkeypatch.setattr(jw, "unit_state", lambda unit: {"queried": True, "running": False, "result": "success",
                                                         "exit": 0, "pid": 0})
    assert jw.poll_unit_watches()[0]["resume"] == "cancelled"
    assert [o for o in ops.listing(sid) if o["kind"] == "notice" and o["state"] == "pending"] == []


def test_a_held_orchestrator_cannot_spawn(held_ctx):
    from api.subagents import service as subagents

    ctx, sid = held_ctx, held_ctx["sid"]
    control.hold(sid, db=ctx["db"], request=ctx["request"])
    with pytest.raises(subagents.SubagentError, match="paused by the user"):
        subagents.spawn(parent_session_id=sid, children=[{"title": "A", "agent": "cursor", "message": "go"}],
                        wait=False, db=ctx["db"])


def test_one_failed_step_does_not_skip_the_others(held_ctx):
    ctx, sid = held_ctx, held_ctx["sid"]

    def broken(*a, **kw):
        raise RuntimeError("host unavailable")

    out = control.hold(sid, db=ctx["db"], request=broken)
    assert out["hold"] == "paused" and out["actions"]["errors"][0].startswith("turn_stopped:")


def test_only_the_owner_and_only_a_bound_chat(held_ctx):
    ctx = held_ctx
    with pytest.raises(service.OrchestratorError, match="another account"):
        control.hold(ctx["sid"], user_id=ctx["other"], db=ctx["db"], request=ctx["request"])
    with pytest.raises(service.OrchestratorError, match="not orchestrating"):
        control.hold(ctx["other_sid"], db=ctx["db"], request=ctx["request"])
    assert control.held("not-a-session") == ""


def test_cli_resume_refuses_the_held_chat_itself(held_ctx, monkeypatch, capsys):
    from api.orchestrator import __main__ as cli

    ctx, sid = held_ctx, held_ctx["sid"]
    control.hold(sid, db=ctx["db"], request=ctx["request"])
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(sid))
    assert cli.main(["resume", "--session", f"CH-{sid:06d}"]) == 2
    assert cli.main(["resume"]) == 2
    assert control.held(sid) == "paused"
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(ctx["other_sid"]))
    assert cli.main(["resume", "--session", f"CH-{sid:06d}"]) == 0
    assert control.held(sid) == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_js_chip_shows_the_hold_and_its_controls():
    script = """
const assert = require('assert');
const M = require(%s);
const base = {bound: true, playbook_id: 'fast', playbook_name: 'Fast'};
const run = M.chip(base);
assert.equal(run.text, 'Orchestrating · Fast'); assert.equal(run.hold, '');
assert.deepEqual(run.actions, ['pause', 'halt']);
const paused = M.chip({...base, hold: 'paused'});
assert.equal(paused.text, 'Paused · Fast'); assert.equal(paused.stateLabel, 'Paused');
assert.deepEqual(paused.actions, ['resume', 'halt']); assert.ok(paused.holdNote.includes('Resume'));
const stopped = M.chip({...base, hold: 'stopped'});
assert.equal(stopped.text, 'Stopped · Fast'); assert.deepEqual(stopped.actions, ['resume']);
assert.equal(M.chip({...base, hold: 'bogus'}).hold, '');
assert.equal(M.actionSummary('halt', {actions: {agents_stopped: 1, jobs_stopped: ['a', 'b'], deliveries_dropped: 1,
  supervision_paused: ['x']}}), 'Orchestration stopped: 1 agent batch stopped, 2 jobs stopped, 1 wake-up dropped, supervision paused');
assert.equal(M.actionSummary('pause', {actions: {errors: ['turn_stopped: x']}}), 'Orchestration paused (1 step failed)');
assert.equal(M.actionSummary('resume', {actions: {supervision_resumed: ['x'], deliveries_released: 2}}),
  'Orchestration resumed: supervision resumed, 2 waiting wake-ups delivered');
""" % json.dumps(str(WEB / "js" / "chat" / "chat_orchestration_model.js"))
    subprocess.run(["node", "-e", script], check=True)
    panel = (WEB / "js" / "chat" / "chat_orchestration.js").read_text(encoding="utf-8")
    for needle in ("/hold", "/resume", "Stop all work", "End orchestration"):
        assert needle in panel


def test_hold_and_resume_routes(tmp_path, monkeypatch, orch):  # noqa: F811
    from tests.orchestrator.test_orchestrator import _client

    ctx = _client(tmp_path, monkeypatch)
    client, sid = ctx["client"], ctx["sid"]
    assert client.post(f"/api/orchestrator/bindings/{sid}/hold", json={}).status_code == 400  # not orchestrating
    client.put("/api/agent-profiles/worker", json={"name": "Worker", "agent": "cursor", "model": "auto"})
    client.put("/api/orchestrator/playbooks/mine",
               json={"name": "Mine", "tiers": [{"id": "w", "role": "worker", "profiles": ["worker"]}]})
    service.bind(sid, "mine", "goal")
    monkeypatch.setattr(control, "_stop_turn", lambda sid, db, request: True)
    held_now = client.post(f"/api/orchestrator/bindings/{sid}/hold", json={"stop": True}).get_json()
    assert held_now["success"] and held_now["hold"] == "stopped"
    assert client.get(f"/api/orchestrator/bindings/{sid}").get_json()["hold"] == "stopped"
    assert client.post(f"/api/orchestrator/bindings/{sid}/resume", json={}).get_json()["hold"] == ""
    assert client.post(f"/api/orchestrator/bindings/{ctx['other_sid']}/hold", json={}).status_code == 400
