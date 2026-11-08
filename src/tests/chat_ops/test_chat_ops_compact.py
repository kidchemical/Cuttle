"""Agent-driven compaction of another chat (``python -m api.chat_cli context|compact``),
context fill in supervision check-ins and ``api.subagents status --context``."""

from __future__ import annotations

import json

import pytest

from tests.chat_ops.test_chat_ops import FakeHost, _ch, _request, _snap, chats, isolated_home  # noqa: F401

from api.chat_ops import local_auth, service, worker


class CompactHost(FakeHost):
    def __init__(self, *, result=None, refuse=None, **kw):
        super().__init__(**kw)
        self.result = result or {"success": True, "method": "compact", "status": {"success": True, "percent": 12}}
        self.refuse = refuse

    def __call__(self, uid, sid, method, path, data=None, *, stream=False):
        if path == "/api/agent-context/compact":
            self.calls.append((method, path, sid, data))
            if self.refuse:
                raise RuntimeError(self.refuse)
            return self.result
        return super().__call__(uid, sid, method, path, data, stream=stream)


def _ran(sid, agent="claude"):
    from api.cuttle_brain.handoff import record_last_agent
    record_last_agent(str(sid), agent, through_message_id=1)


def test_capability_covers_the_compact_route(chats):
    db, owner, _sup, target, _foreign = chats
    token = local_auth.issue(owner, target, "POST", "/api/agent-context/compact")
    with _request("/api/agent-context/compact", body={"session_id": f"db_session_{target}"}):
        user = db.verify_auth_session(token)
        assert user and user["id"] == owner


def test_compact_runs_on_the_host_with_the_chats_last_agent(chats):
    db, _owner, sup, target, _foreign = chats
    _ran(target, "claude")
    host = CompactHost()
    out = service.compact(_ch(target), _ch(sup), mode="fresh", db=db, request=host)
    assert out["ok"] and out["agent"] == "claude" and out["mode"] == "fresh"
    assert out["percent_after"] == 12
    method, path, sid, data = host.calls[-1]
    assert (method, path, sid) == ("POST", "/api/agent-context/compact", target)
    # Resume maps key the chat by its plain numeric id.
    assert data["session"] == str(target) and data["agent"] == "claude" and data["mode"] == "fresh"


def test_compact_refuses_a_busy_target_without_touching_it(chats):
    db, _owner, sup, target, _foreign = chats
    _ran(target)
    host = CompactHost(generating=True)
    out = service.compact(_ch(target), _ch(sup), db=db, request=host)
    assert not out["ok"] and "busy" in out["error"]
    assert all(c[1] != "/api/agent-context/compact" for c in host.calls)


def test_compact_reports_the_hosts_refusal(chats):
    db, _owner, sup, target, _foreign = chats
    _ran(target)
    out = service.compact(_ch(target), _ch(sup), db=db, request=CompactHost(refuse="No claude session to compact yet."))
    assert not out["ok"] and "No claude session" in out["error"]


def test_compact_needs_an_agent_and_the_same_owner(chats):
    db, _owner, sup, target, foreign = chats
    with pytest.raises(ValueError, match="harness agent"):
        service.compact(_ch(target), _ch(sup), db=db, request=CompactHost())
    with pytest.raises(ValueError, match="same owner"):
        service.compact(_ch(foreign), _ch(sup), db=db, request=CompactHost())
    with pytest.raises(ValueError, match="mode"):
        service.compact(_ch(target), _ch(sup), mode="summarize", db=db, request=CompactHost())


def test_context_reports_fill_of_the_chats_agent(chats, monkeypatch):
    db, _owner, sup, target, _foreign = chats
    _ran(target, "codex")
    seen = {}

    def fake_status(**kw):
        seen.update(kw)
        return {"success": True, "percent": 81.5, "used_tokens": 221_000, "limit_tokens": 272_000,
                "compact_available": True, "hint": "compact soon"}

    monkeypatch.setattr("api.agent_context.get_agent_context_status", fake_status)
    out = service.agent_context(_ch(target), _ch(sup), db=db)
    assert out["context"] == {"agent": "codex", "supported": True, "percent": 81.5, "used_tokens": 221_000,
                              "limit_tokens": 272_000, "compact_available": True, "hint": "compact soon"}
    assert seen["chat_session_id"] == str(target) and seen["agent_id"] == "codex"
    assert service.agent_context(_ch(sup), _ch(target), db=db)["context"] is None  # never ran an agent


def test_cli_compact_dispatches_and_exits_nonzero_on_refusal(monkeypatch, capsys):
    from api.chat_cli import cli

    calls = []

    def fake(handle, **kw):
        calls.append((handle, kw["mode"], kw["agent"]))
        return {"schema_version": 1, "ok": False, "error": "busy"}

    monkeypatch.setattr(service, "compact", fake)
    monkeypatch.setattr(cli, "_open_db", lambda path: object())
    code = cli.main(["compact", "CH-000007", "--mode", "fresh", "--agent", "cursor", "--json"])
    assert code == 1 and calls == [("CH-000007", "fresh", "cursor")]
    assert json.loads(capsys.readouterr().out)["error"] == "busy"


# --- supervision check-ins ------------------------------------------------------


def _cfg():
    return {"instructions": "Keep it moving", "agent": ""}


def test_checkin_suggests_compacting_a_full_target():
    snap = {**_snap("idle"), "context": {"agent": "cursor", "supported": True, "percent": 82.4,
                                          "compact_available": True}}
    text = worker.checkin_text(_cfg(), snap, [])
    assert "Context 82% full" in text and "chat_cli compact CH-000002`" in text


def test_checkin_suggests_fresh_when_the_cli_cannot_compact():
    snap = {**_snap("idle"), "context": {"agent": "antigravity", "supported": True, "percent": 90,
                                          "compact_available": False}}
    assert "compact CH-000002 --mode fresh`" in worker.checkin_text(_cfg(), snap, [])


@pytest.mark.parametrize("ctx", [None, {"agent": "cursor", "supported": True, "percent": 40},
                                 {"agent": "x", "supported": False}])
def test_checkin_stays_quiet_about_context_below_threshold(ctx):
    assert "Context " not in worker.checkin_text(_cfg(), {**_snap("idle"), "context": ctx}, [])


# --- sub-agent status -----------------------------------------------------------


def test_subagent_status_context_is_opt_in_per_child(chats, monkeypatch):
    from api.subagents import cli as sub_cli

    db, _owner, _sup, target, _foreign = chats
    monkeypatch.setattr(service, "context_fill", lambda db_, row: {"agent": "claude", "supported": True,
                                                                     "percent": 77, "row": row["id"]})
    payload = {"ok": True, "id": "b1", "children": [{"session_id": target, "handle": _ch(target)}]}
    sub_cli._attach_context(payload, db)
    assert payload["children"][0]["context"]["row"] == target

    nested = {"ok": True, "batches": [{"children": [{"session_id": target}, {"session_id": 999999}]}]}
    sub_cli._attach_context(nested, db)
    kids = nested["batches"][0]["children"]
    assert kids[0]["context"]["percent"] == 77 and kids[1]["context"] is None
    assert sub_cli.build_parser().parse_args(["status", "--parent", "CH-000001", "--context"]).context


# --- HTTP route mode switch -------------------------------------------------------


def test_compact_route_dispatches_mode(monkeypatch):
    from flask import Flask

    from api import agent_context as ac
    from api.agent_harness import routes

    monkeypatch.setattr(routes, "_require_actor", lambda session: ({"id": 1}, session, None))
    monkeypatch.setattr(ac, "compact_agent_context", lambda **kw: {"success": True, "method": "native"})
    monkeypatch.setattr(ac, "fresh_compact_agent_context", lambda **kw: {"success": True, "method": "fresh"})
    app = Flask(__name__)
    app.register_blueprint(routes.agent_harness_bp)
    client = app.test_client()
    post = lambda body: client.post("/api/agent-context/compact", json={"agent": "cursor", "session": "4", **body})
    assert post({}).get_json()["method"] == "native"
    assert post({"mode": "fresh"}).get_json()["method"] == "fresh"
    assert post({"mode": "bogus"}).status_code == 400


def test_checkin_defers_to_auto_compact_when_it_covers_the_target():
    snap = {**_snap("idle"), "context": {"agent": "claude", "supported": True, "percent": 81,
                                          "compact_available": True,
                                          "auto_compact": {"enabled": True, "percent": 60, "max_tokens": 250_000,
                                                           "milestone": True, "scope": "managed"}}}
    text = worker.checkin_text(_cfg(), snap, [])
    assert "Context 81% full" in text and "Cuttle auto-compacts it between turns at 60% or 250k tokens" in text
    assert "earlier at Tasks milestones" in text and "chat_cli compact" not in text
