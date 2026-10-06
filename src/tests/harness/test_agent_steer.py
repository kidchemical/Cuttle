"""Mid-turn steering: registry, text rules, and the Codex / Muse server runners.

The runners are exercised against tiny fake ``codex app-server`` / ``muse serve``
scripts that speak just enough of each protocol to start a turn, accept a
``turn/steer`` while it is running, and fold the steer into the final reply.
"""

import asyncio
import concurrent.futures
import os
import stat
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.agent_harness import steer  # noqa: E402


def _future(value):
    fut = concurrent.futures.Future()
    fut.set_result(value)
    return fut


@pytest.fixture(autouse=True)
def _clean_registry():
    with steer._lock:
        steer._active.clear()
    yield
    with steer._lock:
        steer._active.clear()


def test_steer_text_strips_running_agent_prefix():
    assert steer.steer_text_for("/codex also add tests", "codex") == "also add tests"
    assert steer.steer_text_for("plain follow-up", "muse") == "plain follow-up"


@pytest.mark.parametrize(
    "message",
    ["/cursor do it", "/restart graceful", "/codex model gpt-5", "/codex effort high", "/codex", "  "],
)
def test_steer_text_rejects_routing_and_control_input(message):
    assert steer.steer_text_for(message, "codex") is None


def test_registry_normalizes_chat_handles_and_forwards_text():
    sent = []
    token = steer.register("db_session_77", "codex", lambda text: sent.append(text) or _future((True, None)))
    assert steer.active_agent(77) == "codex"
    assert steer.active_agent("CH-000077") == "codex"
    info = steer.steer(77, "/codex keep going")
    assert info == {"steered": True, "agent": "codex", "text": "keep going"}
    assert sent == ["keep going"]
    steer.unregister("77", token)
    assert steer.active_agent(77) is None


def test_registry_reports_rejection_and_missing_runs():
    assert steer.steer(5, "hello")["reason"] == "no_steerable_run"
    steer.register(5, "muse", lambda _t: _future((False, "turn is no longer active")))
    info = steer.steer(5, "hello")
    assert info["steered"] is False and info["reason"] == "rejected"
    assert "no longer active" in info["error"]


def test_unregister_ignores_stale_token():
    old = steer.register(9, "codex", lambda _t: _future((True, None)))
    steer.register(9, "muse", lambda _t: _future((True, None)))
    steer.unregister(9, old)
    assert steer.active_agent(9) == "muse"


def test_steer_enabled_reads_settings(monkeypatch):
    class _SM:
        def __init__(self, cfg):
            self.cfg = cfg

        def get_setting(self, key, default=None):
            return self.cfg if key == "agent_steer" else default

    import managers.settings_manager as sm

    monkeypatch.delenv("CUTTLE_AGENT_STEER", raising=False)
    monkeypatch.setattr(sm, "get_settings_manager", lambda: _SM({"codex": False}))
    assert steer.steer_enabled("codex") is False
    assert steer.steer_enabled("muse") is True
    assert steer.steer_enabled("cursor") is False
    monkeypatch.setenv("CUTTLE_AGENT_STEER", "0")
    assert steer.steer_enabled("muse") is False


_FAKE_CODEX = r'''
import json, sys, threading, queue
q = queue.Queue()
def rd():
    for line in sys.stdin:
        q.put(json.loads(line))
    q.put(None)
threading.Thread(target=rd, daemon=True).start()
def out(msg):
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
steer_text = None
while True:
    try:
        m = q.get(timeout=6)
    except queue.Empty:
        m = "tick"
    if m is None:
        break
    if m == "tick" or (isinstance(m, dict) and m.get("method") == "turn/steer"):
        if isinstance(m, dict):
            steer_text = m["params"]["input"][0]["text"]
            out({"id": m["id"], "result": {"turnId": "turn-1"}})
            out({"method": "item/completed", "params": {"threadId": "th-1", "turnId": "turn-1",
                 "item": {"type": "userMessage", "id": "u2", "content": [{"type": "text", "text": steer_text}]}}})
        out({"method": "item/completed", "params": {"threadId": "th-1", "turnId": "turn-1",
             "item": {"type": "agentMessage", "id": "a1", "text": "saw: %s" % steer_text}}})
        out({"method": "thread/tokenUsage/updated", "params": {"threadId": "th-1", "tokenUsage": {
             "total": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
             "last": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 9}, "modelContextWindow": 1000}}})
        out({"method": "turn/completed", "params": {"threadId": "th-1", "turn": {"id": "turn-1", "status": "completed"}}})
        continue
    method, mid = m.get("method"), m.get("id")
    if method == "initialize":
        out({"id": mid, "result": {}})
    elif method in ("thread/start", "thread/resume"):
        out({"id": mid, "result": {"thread": {"id": "th-1"}, "model": "fake-model"}})
    elif method == "turn/start":
        out({"id": mid, "result": {"turn": {"id": "turn-1"}}})
        out({"method": "turn/started", "params": {"threadId": "th-1", "turn": {"id": "turn-1"}}})
        out({"method": "item/started", "params": {"threadId": "th-1", "turnId": "turn-1",
             "item": {"type": "commandExecution", "id": "c1", "command": "sleep 5"}}})
'''

_FAKE_MUSE = r'''
import json, sys, threading, queue
q = queue.Queue()
def rd():
    for line in sys.stdin:
        q.put(json.loads(line))
    q.put(None)
threading.Thread(target=rd, daemon=True).start()
def out(msg):
    msg["jsonrpc"] = "2.0"
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
steer_text = None
while True:
    try:
        m = q.get(timeout=6)
    except queue.Empty:
        m = "tick"
    if m is None:
        break
    if m == "tick" or (isinstance(m, dict) and m.get("method") == "turn/steer"):
        if isinstance(m, dict):
            steer_text = m["params"]["input"][0]["text"]
            out({"id": m["id"], "result": {"commandId": m["params"]["commandId"], "status": "accepted", "turnId": "t-1"}})
            out({"method": "item/completed", "params": {"sessionId": "s-1", "item": {
                 "itemId": "u2", "kind": "userMessage", "steered": True, "text": steer_text, "turnId": "t-1"}}})
        out({"method": "item/completed", "params": {"sessionId": "s-1", "item": {
             "itemId": "a1", "kind": "agentMessage", "text": "saw: %s" % steer_text, "turnId": "t-1"}}})
        out({"method": "turn/completed", "params": {"sessionId": "s-1", "turnId": "t-1", "terminal": "completed",
             "usage": {"inputTokens": 10, "outputTokens": 2}}})
        continue
    method, mid = m.get("method"), m.get("id")
    if method == "initialize":
        out({"id": mid, "result": {}})
    elif method in ("session/start", "session/resume"):
        out({"id": mid, "result": {"session": {"sessionId": "s-1", "modelId": "fake"}, "viewCursor": "v"}})
    elif method in ("session/setApprovalMode", "session/setModel"):
        out({"id": mid, "result": {"status": "accepted"}})
    elif method == "turn/start":
        out({"id": mid, "result": {"turnId": "t-1", "disposition": "started", "status": "accepted"}})
        out({"method": "item/started", "params": {"sessionId": "s-1", "item": {
             "itemId": "c1", "kind": "toolCall", "tool": "bash", "args": "{\"command\": \"sleep 5\"}",
             "status": "inProgress", "turnId": "t-1"}}})
'''


def _fake_bin(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _steer_when_live(chat_id, message, results):
    def _go():
        for _ in range(100):
            if steer.active_agent(chat_id):
                break
            time.sleep(0.05)
        results.append(steer.steer(chat_id, message))

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t


pytestmark_posix = pytest.mark.skipif(os.name == "nt", reason="fake server scripts use a shebang")


@pytestmark_posix
def test_codex_app_server_turn_accepts_steer(tmp_path, monkeypatch):
    import scripts.utilities.codex_app_server_turn as mod

    monkeypatch.setattr(mod, "codex_executable", lambda: _fake_bin(tmp_path, "codex", _FAKE_CODEX))
    monkeypatch.setattr(
        "scripts.utilities.codex_cli_session_store.save_codex_resume_id", lambda *a, **k: None
    )
    snaps = []
    monkeypatch.setattr(
        "scripts.utilities.codex_cli_session_store.save_codex_context_snapshot",
        lambda sid, snap: snaps.append((sid, snap)),
    )
    results = []
    t = _steer_when_live(4242, "/codex add BANANA", results)
    res = asyncio.run(
        mod.run_codex_turn_app_server(
            "go", cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None,
            chat_session_id="db_session_4242", timeout=30,
        )
    )
    t.join(5)
    assert results and results[0]["steered"] is True
    assert res["success"] is True
    assert res["output"] == "saw: add BANANA"
    assert res["steered"] == 1
    assert res["codex_session_id"] == "th-1"
    assert res["usage"]["input_tokens"] == 10
    assert res["token_usage"]["context_tokens"] == 9
    assert res["undelivered_steers"] == []
    assert snaps and snaps[0][1]["context_tokens"] == 9
    assert snaps[0][1]["model_context_window"] == 1000
    assert steer.active_agent(4242) is None


_FAKE_CODEX_LOST_STEER = r'''
import json, sys
def out(msg):
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line)
    method, mid = m.get("method"), m.get("id")
    if method == "initialize":
        out({"id": mid, "result": {}})
    elif method in ("thread/start", "thread/resume"):
        out({"id": mid, "result": {"thread": {"id": "th-1"}}})
    elif method == "turn/start":
        out({"id": mid, "result": {"turn": {"id": "turn-1"}}})
        out({"method": "item/reasoning/summaryTextDelta", "params": {"threadId": "th-1", "delta": "Planning the adapters"}})
    elif method == "turn/steer":
        out({"id": mid, "result": {"turnId": "turn-1"}})
        out({"method": "item/agentMessage/delta", "params": {"threadId": "th-1", "delta": "partial"}})
        out({"method": "item/completed", "params": {"threadId": "th-1", "turnId": "turn-1",
             "item": {"type": "agentMessage", "id": "a1", "text": "partial"}}})
        out({"method": "turn/completed", "params": {"threadId": "th-1",
             "turn": {"id": "turn-1", "status": "interrupted"}}})
'''


@pytestmark_posix
def test_codex_turn_reports_steer_it_never_read(tmp_path, monkeypatch):
    import queue

    import scripts.utilities.codex_app_server_turn as mod

    monkeypatch.setattr(mod, "codex_executable", lambda: _fake_bin(tmp_path, "codex", _FAKE_CODEX_LOST_STEER))
    monkeypatch.setattr(
        "scripts.utilities.codex_cli_session_store.save_codex_resume_id", lambda *a, **k: None
    )
    results = []
    t = _steer_when_live(4444, "/codex you stopped, continue", results)
    statuses = queue.Queue()
    res = asyncio.run(
        mod.run_codex_turn_app_server(
            "go", cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None,
            status_queue=statuses, chat_session_id="4444", timeout=30,
        )
    )
    t.join(5)
    assert results and results[0]["steered"] is True
    assert res["success"] is False
    assert res["undelivered_steers"] == ["you stopped, continue"]
    assert res["steered"] == 0
    assert "before reading your follow-up" in res["output"]
    assert "you stopped, continue" in res["output"]
    lines = []
    while not statuses.empty():
        lines.append(statuses.get()[1])
    assert any(s.startswith("thinking: Planning the adapters") for s in lines)
    assert any(s.startswith("steer queued: you stopped, continue") for s in lines)
    assert any(s.startswith("writing: partial") for s in lines)


@pytestmark_posix
def test_codex_inspector_keeps_complete_fast_blocks(tmp_path, monkeypatch):
    import queue
    from api.query_events import QueryStatusTee, bind_query_id, reset_query_id
    from api.query_tracker import start_query_tracking, get_query_tracker, finish_query_tracking
    import scripts.utilities.codex_app_server_turn as mod

    script = r'''
import json, sys
def out(msg):
    print(json.dumps(msg), flush=True)
def event(method, **params):
    out({"method": method, "params": {"threadId": "th-fast", "turnId": "turn-fast", **params}})
for line in sys.stdin:
    m = json.loads(line)
    method, mid = m.get("method"), m.get("id")
    if method == "initialize":
        out({"id": mid, "result": {}})
    elif method == "thread/start":
        out({"id": mid, "result": {"thread": {"id": "th-fast"}}})
    elif method == "turn/start":
        out({"id": mid, "result": {"turn": {"id": "turn-fast"}}})
        event("item/started", item={"id": "r", "type": "reasoning"})
        event("item/reasoning/summaryTextDelta", itemId="r", delta="Plan first. ")
        event("item/reasoning/summaryTextDelta", itemId="r", delta="Keep the entire summary. " * 20)
        event("item/completed", item={"id": "r", "type": "reasoning", "summary": []})
        for ident, body in [("a", "I will inspect everything. " * 30), ("b", "Done.")]:
            event("item/started", item={"id": ident, "type": "agentMessage", "text": ""})
            event("item/agentMessage/delta", itemId=ident, delta=body[0])
            event("item/agentMessage/delta", itemId=ident, delta=body[1:])
            event("item/completed", item={"id": ident, "type": "agentMessage", "text": body})
        event("turn/completed", turn={"id": "turn-fast", "status": "completed"})
'''
    monkeypatch.setattr(mod, "codex_executable", lambda: _fake_bin(tmp_path, "codex", script))
    monkeypatch.setattr("scripts.utilities.codex_cli_session_store.save_codex_resume_id", lambda *a, **k: None)
    qid = start_query_tracking("fast blocks", {"web_ui": True})
    token = bind_query_id(qid)
    statuses = queue.Queue()
    try:
        result = asyncio.run(mod.run_codex_turn_app_server(
            "go", cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None,
            status_queue=QueryStatusTee(statuses), timeout=10,
        ))
        assert result["success"]
        events = get_query_tracker(qid).execution_data["events"]
        assert [e["text"] for e in events if e["kind"] == "writing"] == [
            "I will inspect everything. " * 30, "Done.",
        ]
        assert [e["text"] for e in events if e["kind"] == "thinking"] == [
            "Plan first. " + "Keep the entire summary. " * 20,
        ]
        lines = []
        while not statuses.empty():
            lines.append(statuses.get()[1])
        assert "writing: Done." in lines
        assert "writing: I" in lines
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


def test_agent_context_skips_live_fetch_while_codex_turn_runs(monkeypatch):
    import api.agent_context as ac
    import scripts.utilities.codex_app_server as cas
    import scripts.utilities.codex_cli_session_store as store

    calls = []
    monkeypatch.setattr(store, "load_codex_context_snapshot", lambda sid: None)
    monkeypatch.setattr(store, "save_codex_context_snapshot", lambda sid, snap: None)
    monkeypatch.setattr(
        cas, "fetch_codex_thread_token_usage",
        lambda *a, **k: calls.append(k) or {"success": False},
    )
    steer.register(5151, "codex", lambda _t: _future((True, None)))
    ac._codex_apply_live_or_snapshot(
        chat_session_id=5151, cwd="/tmp", resume_id="th-1", tokens=0,
        token_source="none", limit=0, limit_source="none", live=True,
    )
    assert calls == []
    with steer._lock:
        steer._active.clear()
    ac._codex_apply_live_or_snapshot(
        chat_session_id=5151, cwd="/tmp", resume_id="th-1", tokens=0,
        token_source="none", limit=0, limit_source="none", live=True,
    )
    assert calls and calls[0]["timeout"] == 15.0


@pytestmark_posix
def test_codex_app_server_turn_falls_back_when_server_missing(tmp_path, monkeypatch):
    import scripts.utilities.codex_app_server_turn as mod

    monkeypatch.setattr(mod, "codex_executable", lambda: None)
    res = asyncio.run(
        mod.run_codex_turn_app_server("go", cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None)
    )
    assert res["fallback"] is True


@pytestmark_posix
def test_muse_serve_turn_accepts_steer(tmp_path, monkeypatch):
    import scripts.utilities.muse_serve_turn as mod

    monkeypatch.setattr(mod, "_which_muse_native", lambda: _fake_bin(tmp_path, "muse", _FAKE_MUSE))
    monkeypatch.setattr(
        "scripts.utilities.muse_cli_session_store.save_muse_resume_id", lambda *a, **k: None
    )
    results = []
    t = _steer_when_live(4343, "/muse add BANANA", results)
    res = asyncio.run(
        mod.run_muse_turn_serve(
            "go", cwd=str(tmp_path), resume="s-1", model="m", reasoning_effort="low",
            chat_session_id="4343", timeout=30,
        )
    )
    t.join(5)
    assert results and results[0]["steered"] is True
    assert res["success"] is True
    assert res["output"] == "saw: add BANANA"
    assert res["steered"] == 1
    assert res["muse_session_id"] == "s-1"
    assert res["usage"]["input_tokens"] == 10
    assert steer.active_agent(4343) is None


def test_muse_command_id_is_uuid7():
    from scripts.utilities.muse_serve_turn import command_id

    cid = command_id()
    assert cid[14] == "7"


@pytestmark_posix
@pytest.mark.parametrize("transport", ["serve", "exec"])
def test_muse_inspector_keeps_fast_text_blocks(tmp_path, monkeypatch, transport):
    import queue
    from api.query_events import QueryStatusTee, bind_query_id, reset_query_id
    from api.query_tracker import start_query_tracking, get_query_tracker, finish_query_tracking
    import scripts.utilities.muse_cli_tool as cli
    import scripts.utilities.muse_serve_turn as serve

    script = r'''
import json, sys
body = "I will inspect the entire result. " * 30
summary = "Check every adapter. " * 20
def out(msg):
    print(json.dumps(msg), flush=True)
if sys.argv[1] == "exec":
    for text in ("I", body[1:]):
        out({"payload_type": "run.output.delta", "payload": {"text": text}})
    out({"payload_type": "run.reasoning.delta", "payload": {"text": summary}})
    out({"payload_type": "run.terminal.completed", "payload": {"terminal": "completed", "text": body}})
else:
    def event(method, **params):
        out({"jsonrpc": "2.0", "method": method, "params": {"sessionId": "s-fast", "turnId": "t-fast", **params}})
    for line in sys.stdin:
        m = json.loads(line)
        method, mid = m.get("method"), m.get("id")
        if method == "initialize":
            out({"id": mid, "result": {}})
        elif method in ("session/start", "session/resume"):
            out({"id": mid, "result": {"session": {"sessionId": "s-fast", "modelId": "fake"}, "viewCursor": "v"}})
        elif method in ("session/setApprovalMode", "session/setModel"):
            out({"id": mid, "result": {"status": "accepted"}})
        elif method == "turn/start":
            out({"id": mid, "result": {"turnId": "t-fast", "disposition": "started", "status": "accepted"}})
            for ident, kind, text in [("r", "reasoning", summary), ("a", "agentMessage", body)]:
                event("item/started", item={"itemId": ident, "kind": kind, "text": ""})
                event("item/delta", itemId=ident, field="text", delta=text[0])
                event("item/delta", itemId=ident, field="text", delta=text[1:])
                event("item/completed", item={"itemId": ident, "kind": kind, "text": text})
            event("turn/completed", terminal="completed")
'''
    exe = _fake_bin(tmp_path, "muse-fast", script)
    monkeypatch.setattr(cli, "_which_muse_native", lambda: exe)
    monkeypatch.setattr(serve, "_which_muse_native", lambda: exe)
    monkeypatch.setattr("scripts.utilities.muse_cli_session_store.save_muse_resume_id", lambda *a, **k: None)
    qid = start_query_tracking("muse fast text", {"web_ui": True})
    token = bind_query_id(qid)
    try:
        statuses = QueryStatusTee(queue.Queue())
        if transport == "serve":
            result = asyncio.run(serve.run_muse_turn_serve(
                "go", cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None,
                status_queue=statuses, timeout=10,
            ))
        else:
            result = asyncio.run(cli.MuseCliTool().execute_prompt(
                "go", cwd=str(tmp_path), provider="echo", status_queue=statuses, timeout=10,
            ))
        assert result["success"], result
        events = get_query_tracker(qid).execution_data["events"]
        assert [e["text"].strip() for e in events if e["kind"] == "writing"] == [
            ("I will inspect the entire result. " * 30).strip(),
        ]
        assert [e["text"].strip() for e in events if e["kind"] == "thinking"] == [
            ("Check every adapter. " * 20).strip(),
        ]
    finally:
        reset_query_id(token)
        finish_query_tracking(success=True)


def test_chat_steer_endpoint_persists_steered_user_message(tmp_path, monkeypatch):
    """POST /api/chat-steer persists the user row with steered metadata.

    The chat UI renders its "steer" badge from ``metadata.steered`` /
    ``metadata.steered_agent`` (both the live bubble and history reload),
    so this pins the server half of that contract.
    """
    from api import auth_db as auth_db_mod
    from api import web_chat_api as wca

    db_path = tmp_path / "steer-badge.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")

    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "steer badge")
    auth_token = db.create_auth_session(owner)
    reg = steer.register(sid, "codex", lambda _t: _future((True, None)))
    assert reg is not None
    try:
        client = wca.app.test_client()
        client.set_cookie("session_token", auth_token)
        resp = client.post(
            "/api/chat-steer", json={"session_id": sid, "message": "keep going"}
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["steered"] is True
        assert body["agent"] == "codex"
        rows = [m for m in db.get_messages(sid) if m["role"] == "user"]
        assert rows, "steered user message must be persisted"
        assert rows[-1]["content"] == "keep going"
        meta = rows[-1].get("metadata") or {}
        assert meta.get("steered") is True
        assert meta.get("steered_agent") == "codex"
    finally:
        steer.unregister(sid, reg)


# Mirrors claude 2.1 stream-json input: replays each stdin user line, folds a
# steer that arrives before the tool finishes into the same result, answers a
# later one as an extra turn, and exits on EOF. MODE=late finishes the first
# turn with the steer already in the pipe; MODE=deaf never reads the steer.
_FAKE_CLAUDE = r'''
import json, os, sys, threading, queue
mode = os.environ.get("FAKE_CLAUDE_MODE", "mid")
q = queue.Queue()
def rd():
    for line in sys.stdin:
        q.put(json.loads(line))
    q.put(None)
threading.Thread(target=rd, daemon=True).start()
def out(msg):
    msg.setdefault("session_id", "cs-1")
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
def replay(m):
    out({"type": "user", "isReplay": True, "parent_tool_use_id": None, "message": m["message"]})
def result(text, cost):
    out({"type": "result", "subtype": "success", "result": text, "total_cost_usd": cost,
         "usage": {"input_tokens": 10, "output_tokens": 2}})
first = q.get()
out({"type": "system", "subtype": "init", "model": "fake"})
replay(first)
out({"type": "assistant", "message": {"id": "m1", "content": [
     {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "sleep 5"}}]}})
steer = q.get(timeout=6)
if mode == "deaf":
    result("done", 0.01)
    sys.exit(0)
if mode == "late":
    result("first answer", 0.01)
if steer is not None:
    replay(steer)
    result("saw: %s" % steer["message"]["content"], 0.02)
while q.get(timeout=6) is not None:
    pass
'''


def _run_fake_claude(tmp_path, monkeypatch, mode, chat_id, message):
    import scripts.utilities.claude_cli_tool as mod

    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    monkeypatch.setattr(mod, "claude_executable", lambda: _fake_bin(tmp_path, "claude", _FAKE_CLAUDE))
    monkeypatch.setattr(
        "scripts.utilities.claude_cli_session_store.save_claude_resume_id", lambda *a, **k: None
    )
    results = []
    t = _steer_when_live(chat_id, message, results)
    res = asyncio.run(
        mod.ClaudeCliTool().execute_prompt(
            "go", cwd=str(tmp_path), chat_session_id=str(chat_id), timeout=30, steerable=True,
        )
    )
    t.join(5)
    return res, results


@pytestmark_posix
def test_claude_turn_accepts_mid_turn_steer(tmp_path, monkeypatch):
    res, results = _run_fake_claude(tmp_path, monkeypatch, "mid", 5151, "/claude add BANANA")
    assert results and results[0] == {"steered": True, "agent": "claude", "text": "add BANANA"}
    assert res["success"] is True
    assert res["output"] == "saw: add BANANA"
    assert res["steered"] == 1
    assert res["undelivered_steers"] == []
    assert res["claude_session_id"] == "cs-1"
    assert steer.active_agent(5151) is None


@pytestmark_posix
def test_claude_late_steer_extra_turn_merges_into_reply(tmp_path, monkeypatch):
    res, results = _run_fake_claude(tmp_path, monkeypatch, "late", 5252, "and PINEAPPLE")
    assert results and results[0]["steered"] is True
    assert res["success"] is True
    assert res["output"] == "first answer\n\nsaw: and PINEAPPLE"
    assert res["usage"]["prompt_tokens"] == 20
    assert res["usage"]["cost"] == pytest.approx(0.03)
    assert res["steered"] == 1


@pytestmark_posix
def test_claude_turn_reports_steer_it_never_read(tmp_path, monkeypatch):
    res, results = _run_fake_claude(tmp_path, monkeypatch, "deaf", 5353, "too late")
    assert results and results[0]["steered"] is True
    assert res["steered"] == 0
    assert res["undelivered_steers"] == ["too late"]
    assert "before reading your follow-up" in res["output"]
    assert "- too late" in res["output"]


_FAKE_CLAUDE_BACKGROUND = r'''
import json, os, sys, threading, time, queue
mode = os.environ.get("FAKE_CLAUDE_MODE", "finishes")
q = queue.Queue()
def rd():
    for line in sys.stdin:
        q.put(json.loads(line))
    q.put(None)
threading.Thread(target=rd, daemon=True).start()
def out(msg):
    msg.setdefault("session_id", "cs-bg")
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
def result(text):
    out({"type": "result", "subtype": "success", "result": text, "total_cost_usd": 0.01,
         "usage": {"input_tokens": 10, "output_tokens": 2}})
q.get()
out({"type": "system", "subtype": "init", "model": "fake"})
task = {"task_id": "b1", "task_type": "local_bash", "description": "watch CI"}
out({"type": "system", "subtype": "background_tasks_changed", "tasks": [task]})
result("started the watcher")
if mode == "finishes":
    time.sleep(0.5)
    try:
        if q.get_nowait() is None:
            sys.exit(0)  # stdin closed: the CLI exits and the task dies
    except queue.Empty:
        pass
    out({"type": "system", "subtype": "background_tasks_changed", "tasks": []})
    out({"type": "system", "subtype": "task_notification", "task_id": "b1", "status": "completed"})
    out({"type": "system", "subtype": "init", "model": "fake"})
    result("CI is green")
# Like the real CLI: exit on stdin EOF (that kills any still-running task).
while q.get(timeout=20) is not None:
    pass
'''


def _run_fake_claude_background(tmp_path, monkeypatch, mode, chat_id):
    import scripts.utilities.claude_cli_tool as mod

    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    monkeypatch.setattr(mod, "claude_executable", lambda: _fake_bin(tmp_path, "claude", _FAKE_CLAUDE_BACKGROUND))
    monkeypatch.setattr(
        "scripts.utilities.claude_cli_session_store.save_claude_resume_id", lambda *a, **k: None
    )
    return asyncio.run(
        mod.ClaudeCliTool().execute_prompt(
            "go", cwd=str(tmp_path), chat_session_id=str(chat_id), timeout=120, steerable=True,
        )
    )


@pytestmark_posix
def test_claude_background_task_follow_up_merges_into_reply(tmp_path, monkeypatch):
    """A root result with a background task pending keeps stdin open; the
    task's notification turn answers in the same reply instead of dying."""
    res = _run_fake_claude_background(tmp_path, monkeypatch, "finishes", 5454)
    assert res["success"] is True
    assert res["output"] == "started the watcher\n\nCI is green"
    assert res["usage"]["cost"] == pytest.approx(0.02)
    assert steer.active_agent(5454) is None


@pytestmark_posix
def test_claude_background_wait_is_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_BACKGROUND_WAIT_SEC", "1")
    res = _run_fake_claude_background(tmp_path, monkeypatch, "never", 5555)
    assert res["success"] is True
    assert res["output"].startswith("started the watcher")
    assert "Stopped waiting for background work" in res["output"]
    assert "- watch CI" in res["output"]


@pytestmark_posix
def test_claude_background_wait_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_BACKGROUND_WAIT_SEC", "0")
    res = _run_fake_claude_background(tmp_path, monkeypatch, "finishes", 5656)
    assert res["output"] == "started the watcher"


def test_claude_is_steerable_by_default(monkeypatch):
    import managers.settings_manager as sm

    class _SM:
        def get_setting(self, key, default=None):
            return default

    monkeypatch.delenv("CUTTLE_AGENT_STEER", raising=False)
    monkeypatch.setattr(sm, "get_settings_manager", lambda: _SM())
    assert steer.steer_enabled("claude") is True
    assert steer.steer_text_for("/claude effort high", "claude") is None
