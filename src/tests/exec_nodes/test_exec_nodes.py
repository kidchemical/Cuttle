"""Execution nodes: pure model, chat binding, and a turn relayed to a node.

The relay tests run the real host adapter, mesh store, HTTP routes and node
runner against a fake agent adapter: no agent CLI and no network.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time

import pytest
from flask import Flask

from api.agent_harness.types import AgentManifest, AgentResult
from api.exec_nodes import model, node, remote, service, store
from api.experimental import flags

NODE = "laptop"
MANIFEST = AgentManifest(id="fake", label="Fake", slash="/fake")


# ── pure model ─────────────────────────────────────────────────────────────


def _worker(**over):
    base = {"worker_id": NODE, "hostname": "KCsLaptop", "os": "Windows-11-10.0", "online": True,
            "capabilities": {"agent_exec": True, "agents": ["claude", "fake"]}, "load": {}}
    return {**base, **over}


@pytest.mark.parametrize("worker,agent,state,needle", [
    (None, "", "unavailable", "not paired"),
    (_worker(online=False), "", "unavailable", "offline"),
    (_worker(capabilities={"agents": ["claude"]}), "", "unavailable", "not enabled"),
    (_worker(), "codex", "unavailable", "codex CLI is not installed"),
    (_worker(load={"agent_turns": 1}), "claude", "busy", "1 agent turn running"),
    (_worker(), "claude", "online", ""),
])
def test_node_state(worker, agent, state, needle):
    got = model.node_state(worker, agent_id=agent)
    assert got["state"] == state and needle in got["reason"]


def test_map_path_longest_prefix_and_windows_separators():
    paths = {"/mnt/dev": "D:\\dev", "/mnt/dev/Cuttle/": "C:\\Users\\me\\Dev\\Cuttle"}
    assert model.map_path("/mnt/dev/Cuttle", paths) == "C:\\Users\\me\\Dev\\Cuttle"
    assert model.map_path("/mnt/dev/Cuttle/src/api", paths) == "C:\\Users\\me\\Dev\\Cuttle\\src\\api"
    assert model.map_path("/mnt/dev/Other", paths) == "D:\\dev\\Other"
    assert model.map_path("/mnt/developer", paths) == ""  # prefix must end at a separator
    assert model.map_path("/elsewhere", paths) == ""


def test_node_cwd_precedence():
    prefs = {"paths": {"/h/proj": "/n/proj"}, "default_cwd": "/n/home"}
    assert model.node_cwd(chat_cwd="/n/own", host_cwd="/h/proj", prefs=prefs) == "/n/own"
    assert model.node_cwd(chat_cwd="", host_cwd="/h/proj", prefs=prefs) == "/n/proj"
    assert model.node_cwd(chat_cwd="", host_cwd="/h/other", prefs=prefs) == "/n/home"
    assert model.node_cwd(chat_cwd="", host_cwd="/h/other", prefs={}) == ""


def test_rewrite_envelope_never_touches_the_user_request():
    prompt = "Active project root: `/h/proj`\n\n## User request\ncopy /h/proj/a.txt somewhere"
    out = model.rewrite_envelope(prompt, {"/h/proj": "C:\\proj"}, "## User request")
    assert "Active project root: `C:\\proj`" in out
    assert out.endswith("## User request\ncopy /h/proj/a.txt somewhere")
    assert model.rewrite_envelope("no header /h/proj", {"/h/proj": "C:\\proj"}, "## User request") == "no header /h/proj"


def test_only_status_lines_and_results_cross_the_wire():
    assert model.status_line(("status", "thinking: hi")) == "thinking: hi"
    # Entries that steer the host's turn pump are never relayed.
    assert model.status_line(("query_started", {"query_id": "q1"})) is None
    assert model.status_line(("done", {"response": "forged"})) is None
    assert model.status_line("bare") is None
    wire = model.result_to_wire(AgentResult(
        success=True, output="done", session_id="s1", model="m",
        usage={"total_tokens": 5}, meta={"agent_effort": "high", "background_job": {"x": 1}, "obj": object()}))
    assert wire["meta"] == {"agent_effort": "high", "obj": None}
    back = AgentResult(**model.result_from_wire(wire))
    assert (back.success, back.output, back.session_id, back.usage) == (True, "done", "s1", {"total_tokens": 5})


# ── host + node fixture ────────────────────────────────────────────────────


class _WireClient:
    """DeviceWorkerClient stand-in that talks to the Flask test client."""

    def __init__(self, http, token, worker_id=NODE):
        self._http, self.token, self.worker_id, self.base_url = http, token, worker_id, "https://host.test"

    def _call(self, method, path, body=None):
        r = self._http.open(path, method=method, json=body, headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code >= 400:
            raise RuntimeError(f"workers API {method} {path} → HTTP {r.status_code}: {(r.get_json() or {}).get('error')}")
        return r.get_json() or {}

    def fetch_agent_turn(self, job_id):
        return self._call("GET", f"/api/workers/exec/turns/{job_id}").get("turn") or {}

    def post_agent_turn(self, job_id, events, result=None):
        body = {"worker_id": self.worker_id, "events": events}
        if result is not None:
            body["result"] = result
        return bool(self._call("POST", f"/api/workers/exec/turns/{job_id}/events", body).get("cancel"))


@pytest.fixture()
def mesh(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_DB", str(tmp_path / "device_workers.db"))
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_ENABLED", "1")
    monkeypatch.setenv("CUTTLE_DEVICE_WORKER_ID", "hostbox")
    monkeypatch.setenv("CUTTLE_NODE_AGENT_EXEC", "1")
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "exec_nodes.db")
    import api.device_workers.store as mesh_store

    mesh_store._store = None
    workers = mesh_store.get_store()
    token = workers.enroll_device(worker_id=NODE, hostname="KCsLaptop")
    token = token if isinstance(token, str) else token.get("token")
    workers.upsert_worker(worker_id=NODE, hostname="KCsLaptop", os_name="Windows-11-10.0",
                          capabilities={"agent_exec": True, "agents": ["fake"]}, storage={}, load={},
                          interactive_priority="low", ac_power=True, meta={})
    from api.device_workers.routes import workers_bp

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    flags.set_enabled("exec_nodes", True)
    node._active.clear()
    yield {"store": workers, "http": app.test_client(), "token": token}
    flags.set_enabled("exec_nodes", False)
    mesh_store._store = None


class _FakeAdapter:
    """Records what the node was asked to run; behaviour set per test."""

    def __init__(self, behaviour=None):
        self.calls = []
        self._behaviour = behaviour

    def available(self):
        return True

    def resolve_cwd(self, project_path):
        return project_path

    async def execute(self, prompt, *, cwd, resume, model, status_queue=None, chat_session_id=None,
                      timeout=600.0, cancel_event=None, reasoning_effort=None):
        self.calls.append({"prompt": prompt, "cwd": cwd, "resume": resume, "model": model,
                           "chat_session_id": chat_session_id, "effort": reasoning_effort})
        status_queue.put(("status", "thinking: on the node"))
        if self._behaviour:
            return await self._behaviour(cancel_event)
        return AgentResult(success=True, output="hello from the node", session_id="cli-123", model=model or "m1",
                           usage={"total_tokens": 7})


def _node_worker(mesh, adapter, monkeypatch, *, stop):
    """One claim-and-run pass of a node's worker, in a thread."""
    monkeypatch.setattr("api.agent_harness.catalog.get_agent", lambda agent_id, project_path=None: (MANIFEST, adapter))
    monkeypatch.setattr("api.device_workers.client.DeviceWorkerClient",
                        lambda token=None: _WireClient(mesh["http"], token or mesh["token"]))
    workers = mesh["store"]

    def loop():
        from api.device_workers.executor import execute_job

        while not stop.is_set():
            jobs = workers.claim_jobs(worker_id=NODE, capabilities={"agent_exec": True}, limit=1)
            for job in jobs:
                try:
                    workers.complete_job(job["id"], NODE, result=execute_job(job, auth_token=mesh["token"]))
                except Exception as exc:
                    workers.fail_job(job["id"], NODE, error=str(exc), retry=False)
                return
            time.sleep(0.05)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def _run_remote(tmp_path, **kwargs):
    adapter = remote.RemoteAdapter(_FakeAdapter(), MANIFEST, NODE, chat_cwd=str(tmp_path))
    statuses = queue.Queue()
    result = asyncio.run(adapter.execute(
        "ctx\n\n## User request\nsay hi", cwd="/host/proj", resume=None, model=None,
        status_queue=statuses, chat_session_id="41", timeout=30.0, **kwargs))
    return adapter, result, [statuses.get_nowait() for _ in range(statuses.qsize())]


# ── binding + kernel seam ──────────────────────────────────────────────────


def test_flag_off_is_inert(monkeypatch):
    flags.set_enabled("exec_nodes", False)
    monkeypatch.setattr(store, "get_chat_node", lambda sid: pytest.fail("store read with the flag off"))
    local = _FakeAdapter()
    assert service.adapter_for_turn(MANIFEST, local, 41) is local


def test_unbound_chat_runs_on_the_host_and_leaves_no_row(mesh):
    local = _FakeAdapter()
    assert service.adapter_for_turn(MANIFEST, local, 41) is local
    assert service.adapter_for_turn(MANIFEST, local, None) is local  # headless caller, no chat
    assert store.swap_last_node("41", "fake", "host") is None
    assert service.binding(41)["node_id"] == "host"


def test_binding_and_node_switch_resets_the_agents_context_once(mesh, monkeypatch):
    moved, cleared = [], []
    monkeypatch.setattr("api.cuttle_brain.handoff.mark_workspace_moved", lambda sid, aid: moved.append((sid, aid)))
    monkeypatch.setattr("api.cuttle_brain.context_delta.clear_injected_snapshot",
                        lambda sid, aid=None, project_path=None: cleared.append((sid, aid)))
    with pytest.raises(service.ExecNodeError, match="node not found"):
        service.set_binding(41, "ghost")
    got = service.set_binding("CH-000041", NODE, "C:\\work")
    assert (got["node_id"], got["cwd"], got["node"]["label"], got["node"]["state"]) == (NODE, "C:\\work", "KCsLaptop", "online")

    local = _FakeAdapter()
    first = service.adapter_for_turn(MANIFEST, local, "db_session_41")
    assert isinstance(first, remote.RemoteAdapter) and first.node_id == NODE
    assert moved == [("db_session_41", "fake")] and cleared == [("db_session_41", "fake")]
    service.adapter_for_turn(MANIFEST, local, 41)  # same node again: nothing to reset
    assert len(moved) == 1

    service.set_binding(41, "host")
    assert service.adapter_for_turn(MANIFEST, local, 41) is local
    assert len(moved) == 2 and service.binding(41)["node_id"] == "host"


def test_resume_ids_are_kept_per_node(mesh):
    here = remote.RemoteAdapter(_FakeAdapter(), MANIFEST, NODE)
    there = remote.RemoteAdapter(_FakeAdapter(), MANIFEST, "tower")
    here.save_resume("/host/proj", "41", "cli-laptop")
    assert here.load_resume("/host/proj", "41") == "cli-laptop"
    assert there.load_resume("/host/proj", "41") is None
    here.clear_resume("/host/proj", "41")
    assert here.load_resume("/host/proj", "41") is None


def test_node_list_puts_the_host_first(mesh):
    nodes = service.list_nodes("fake")
    assert [n["id"] for n in nodes] == ["host", NODE] and nodes[0]["host"] is True
    assert service.list_nodes("codex")[1]["state"] == "unavailable"
    service.set_node_prefs(NODE, label="Laptop", default_cwd="C:\\Users\\me", paths={"/h": "C:\\h", "": "x"})
    row = service.node(NODE)
    assert (row["label"], row["default_cwd"], row["paths"]) == ("Laptop", "C:\\Users\\me", {"/h": "C:\\h"})


# ── a turn relayed to the node ─────────────────────────────────────────────


def test_turn_runs_on_the_node_and_streams_back(mesh, monkeypatch, tmp_path):
    fake = _FakeAdapter()
    stop = threading.Event()
    worker = _node_worker(mesh, fake, monkeypatch, stop=stop)
    try:
        adapter, result, statuses = _run_remote(tmp_path)
    finally:
        stop.set()
        worker.join(timeout=5)

    assert (result.success, result.output, result.session_id) == (True, "hello from the node", "cli-123")
    assert ("status", "Running on KCsLaptop…") in statuses and ("status", "thinking: on the node") in statuses
    call = fake.calls[0]
    assert call["cwd"] == str(tmp_path) and call["chat_session_id"] is None and call["resume"] is None
    assert call["prompt"].startswith("[Cuttle execution node: this agent runs on `KCsLaptop` (Windows)")
    assert call["prompt"].endswith("## User request\nsay hi")
    # The prompt and reply never enter the mesh job, and the relay row is gone.
    job = mesh["store"].list_jobs(limit=1)[0]
    assert job["type"] == "agent_turn" and job["status"] == "succeeded"
    assert "say hi" not in str(job["params"]) and "hello from the node" not in str(job.get("result"))
    assert store.read_turn(job["id"]) == ([], None) and store.running_turns() == {}


def test_resumed_turn_sends_no_node_note(mesh, monkeypatch, tmp_path):
    fake = _FakeAdapter()
    stop = threading.Event()
    worker = _node_worker(mesh, fake, monkeypatch, stop=stop)
    try:
        adapter = remote.RemoteAdapter(_FakeAdapter(), MANIFEST, NODE, chat_cwd=str(tmp_path))
        asyncio.run(adapter.execute("next", cwd="/host/proj", resume="cli-123", model="m9",
                                    chat_session_id="41", timeout=30.0, reasoning_effort="high"))
    finally:
        stop.set()
        worker.join(timeout=5)
    assert fake.calls[0] == {"prompt": "next", "cwd": str(tmp_path), "resume": "cli-123", "model": "m9",
                             "chat_session_id": None, "effort": "high"}


def test_stop_reaches_the_node_cli(mesh, monkeypatch, tmp_path):
    async def wait_for_cancel(cancel_event):
        for _ in range(400):
            if cancel_event.is_set():
                return AgentResult(success=False, error="cancelled", session_id="cli-9")
            await asyncio.sleep(0.05)
        return AgentResult(success=True, output="never cancelled")

    fake = _FakeAdapter(wait_for_cancel)
    stop = threading.Event()
    worker = _node_worker(mesh, fake, monkeypatch, stop=stop)
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    try:
        _adapter, result, _statuses = _run_remote(tmp_path, cancel_event=cancel)
    finally:
        stop.set()
        worker.join(timeout=5)
    # The node's own result comes back, so the CLI session id survives a Stop.
    assert (result.success, result.error, result.session_id) == (False, "cancelled", "cli-9")


def test_unavailable_node_refuses_without_queueing(mesh, tmp_path):
    mesh["store"].upsert_worker(worker_id=NODE, hostname="KCsLaptop", os_name="Windows-11",
                                capabilities={"agent_exec": False}, storage={}, load={},
                                interactive_priority="low", ac_power=True, meta={})
    _adapter, result, _statuses = _run_remote(tmp_path)
    assert result.success is False and "is unavailable" in result.output and "did not run" in result.output
    assert mesh["store"].list_jobs() == []


def test_node_that_never_claims_times_out_cleanly(mesh, monkeypatch, tmp_path):
    monkeypatch.setattr(remote, "START_TIMEOUT_SECONDS", 1.0)
    _adapter, result, _statuses = _run_remote(tmp_path)
    assert result.success is False and "did not start this turn" in result.output
    assert mesh["store"].list_jobs(limit=1)[0]["status"] == "cancelled" and store.running_turns() == {}


def test_missing_working_directory_is_reported_by_the_node(mesh, monkeypatch, tmp_path):
    fake = _FakeAdapter()
    stop = threading.Event()
    worker = _node_worker(mesh, fake, monkeypatch, stop=stop)
    try:
        adapter = remote.RemoteAdapter(_FakeAdapter(), MANIFEST, NODE, chat_cwd=str(tmp_path / "nope"))
        result = asyncio.run(adapter.execute("hi", cwd="/host/proj", resume=None, model=None,
                                             chat_session_id="41", timeout=30.0))
    finally:
        stop.set()
        worker.join(timeout=5)
    assert result.success is False and "Working directory not found on this node" in result.output
    assert fake.calls == []


def test_no_directory_known_for_the_node_refuses(mesh):
    adapter = remote.RemoteAdapter(_FakeAdapter(), MANIFEST, NODE)
    result = asyncio.run(adapter.execute("hi", cwd="/host/proj", resume=None, model=None, chat_session_id="41"))
    assert result.success is False and "No working directory" in result.output


# ── security ───────────────────────────────────────────────────────────────


def test_relay_accepts_only_the_owning_nodes_bearer(mesh):
    store.open_turn("job1", NODE, "41", {"contract": 1, "prompt": "secret prompt"})
    other = mesh["store"].enroll_device(worker_id="tower", hostname="Tower")
    other = other if isinstance(other, str) else other.get("token")
    http = mesh["http"]
    assert http.get("/api/workers/exec/turns/job1").status_code == 401
    assert http.get("/api/workers/exec/turns/job1", headers={"Authorization": "Bearer nope"}).status_code == 401
    wrong = {"Authorization": f"Bearer {other}"}
    assert http.get("/api/workers/exec/turns/job1", headers=wrong).status_code == 410
    assert http.post("/api/workers/exec/turns/job1/events", json={"events": [], "result": {"success": True}},
                     headers=wrong).status_code == 410
    assert store.read_turn("job1")[1]["result"] is None
    right = {"Authorization": f"Bearer {mesh['token']}"}
    assert http.get("/api/workers/exec/turns/job1", headers=right).get_json()["turn"]["prompt"] == "secret prompt"


def test_agent_turns_cannot_be_submitted_over_http_and_need_node_opt_in(mesh, monkeypatch):
    from api.device_workers.executor import JobExecError, execute_job, validate_job_submission

    ok, err = validate_job_submission("agent_turn", {"agent_id": "claude"})
    assert ok is False and "unsupported type" in err
    monkeypatch.setenv("CUTTLE_NODE_AGENT_EXEC", "0")
    with pytest.raises(JobExecError, match="not enabled on this machine"):
        execute_job({"id": "j", "type": "agent_turn", "params": {}}, auth_token=mesh["token"])
    assert node.advertise() == {"agent_exec": False, "agents": []}


def test_ui_routes_need_the_owner(mesh):
    assert mesh["http"].get("/api/workers/exec/nodes").status_code in (401, 403)
    assert mesh["http"].put("/api/workers/exec/chats/41", json={"node_id": NODE}).status_code in (401, 403)


def test_worker_only_claims_agent_turns_while_a_slot_is_free(mesh, monkeypatch):
    from api.device_workers.worker_loop import _claim_caps

    ads = {"capabilities": {"agent_exec": True, "shell": True}, "storage": {}}
    assert _claim_caps(ads)["agent_exec"] is True
    gate = threading.Event()
    node.start("a", gate.wait)
    node.start("b", gate.wait)
    try:
        assert node.running() == 2 and _claim_caps(ads)["agent_exec"] is False
        assert _claim_caps(ads)["shell"] is True
    finally:
        gate.set()
    for thread in list(node._active.values()):
        thread.join(timeout=2)
    assert node.running() == 0


# ── CLI + sub-agent children ───────────────────────────────────────────────


def test_cli_lists_sets_and_shows(mesh, capsys):
    from api.exec_nodes.__main__ import main

    assert main(["list", "--agent", "fake"]) == 0
    out = capsys.readouterr().out
    assert "this Cuttle host" in out and "KCsLaptop — Windows  [laptop]  online" in out
    assert main(["node", NODE, "--default-cwd", "C:\\Users\\me", "--map", "/h/proj=C:\\proj"]) == 0
    assert "/h/proj → C:\\proj" in capsys.readouterr().out
    assert main(["set", NODE, "--session", "CH-000041"]) == 0
    assert "From its next turn this chat runs on KCsLaptop" in capsys.readouterr().out
    assert main(["show", "--session", "41", "--json"]) == 0
    assert '"node_id": "laptop"' in capsys.readouterr().out
    assert main(["set", "ghost", "--session", "41"]) == 2
    assert "node not found" in capsys.readouterr().err


def test_subagent_child_spec_carries_its_node():
    from api.subagents.spec import parse_child

    assert parse_child({"title": "w", "message": "go", "node": "laptop"}).node == "laptop"
    assert parse_child({"title": "w", "message": "go"}).node == ""


# ── chat header chip (pure model, node) ────────────────────────────────────


def test_chat_chip_model_and_page_wiring():
    import json
    import shutil
    import subprocess
    from pathlib import Path

    web = Path(__file__).resolve().parents[2] / "web"
    page = (web / "chat_page.html").read_text(encoding="utf-8")
    assert page.index("/js/chat/chat_exec_node_model.js") < page.index("/js/chat/chat_exec_node.js")
    assert 'id="chatExecNodeSlot"' in page
    root = (web / "js" / "chat" / "chat_page.js").read_text(encoding="utf-8")
    assert "CuttleChatExecNode.create(" in root and "execNode.setSession(sessionId)" in root
    if not shutil.which("node"):
        pytest.skip("node not installed")
    script = r"""
const M = require(%s);
const assert = require('assert');
const host = { id: 'host', label: 'KCsMainFrame', os: 'Linux', host: true, state: 'online', reason: '' };
const lap = { id: 'laptop', label: 'KCsLaptop', os: 'Windows', host: false, state: 'busy',
              reason: '1 agent turn running', default_cwd: 'C:\\Users\\me' };
const off = { id: 'tower', label: 'Tower', os: 'Windows', host: false, state: 'unavailable', reason: 'offline' };
assert.strictEqual(M.chip(null), null);
assert.strictEqual(M.chip({ node_id: 'host', node: host }), null);
const c = M.chip({ node_id: 'laptop', node: lap });
assert.deepStrictEqual([c.text, c.warn, c.busy], ['KCsLaptop', false, true]);
assert.strictEqual(c.compact, 'Node · busy');
assert.ok(c.title.includes('KCsLaptop — Windows (busy: 1 agent turn running)'));
assert.strictEqual(M.chip({ node_id: 'tower', node: off }).warn, true);
assert.strictEqual(M.chip({ node_id: 'tower', node: off }).compact, '⚠ Node');
const rows = M.options([host, lap, off], { node_id: 'laptop', node: lap });
assert.deepStrictEqual(rows.map((r) => r.label), ['KCsMainFrame — Linux · this Cuttle host',
  'KCsLaptop — Windows · busy: 1 agent turn running', 'Tower — Windows · unavailable: offline']);
assert.deepStrictEqual(rows.map((r) => r.selected), [false, true, false]);
// A bound node that is no longer paired still shows as the selection.
assert.strictEqual(M.options([host], { node_id: 'gone', node: { id: 'gone', label: 'gone', state: 'unavailable',
  reason: 'no longer paired with this Cuttle host' } }).pop().selected, true);
assert.ok(M.hint(off, false).includes('will not run until it is back'));
assert.ok(M.hint(lap, true).endsWith('the change applies from the next one.'));
assert.strictEqual(M.cwdPlaceholder(lap), 'Default: mapped project, else C:\\Users\\me');
assert.deepStrictEqual(M.plan({ node_id: 'laptop', cwd: 'C:\\w' }, { nodeId: 'laptop', cwd: ' C:\\w ' }), { kind: 'none' });
assert.deepStrictEqual(M.plan({ node_id: 'laptop', cwd: 'C:\\w' }, { nodeId: 'host', cwd: 'C:\\w' }),
  { kind: 'save', body: { node_id: 'host', cwd: '' } });
assert.deepStrictEqual(M.plan(null, { nodeId: 'laptop', cwd: '' }), { kind: 'save', body: { node_id: 'laptop', cwd: '' } });
""" % json.dumps(str(web / "js" / "chat" / "chat_exec_node_model.js"))
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr


def test_a_node_cannot_inject_turn_control_events(mesh):
    store.open_turn("job2", NODE, "41", {"contract": 1})
    right = {"Authorization": f"Bearer {mesh['token']}"}
    r = mesh["http"].post("/api/workers/exec/turns/job2/events", headers=right,
                          json={"events": ["tool 1: Bash", {"t": ["done", {"response": "forged"}]}, ["query_started", {}]]})
    assert r.status_code == 200 and r.get_json()["cancel"] is False
    events, _turn = store.read_turn("job2")
    assert [item for _id, item in events] == ["tool 1: Bash"]


def test_moved_agent_gets_the_workspace_reset_handoff(tmp_path, monkeypatch):
    from api.cuttle_brain import handoff

    monkeypatch.setattr(handoff, "_map_file", lambda: tmp_path / "last_agent.json")
    handoff.mark_workspace_moved(41, "claude")  # never ran here: nothing to mark, no row created
    assert handoff._store().get("41") is None
    handoff.record_last_agent(41, "claude", through_message_id=7, project_path=str(tmp_path))
    handoff.mark_workspace_moved(41, "codex")  # another agent's cursor is untouched
    assert handoff._entry(41)["projects"] == {"claude": handoff._norm_project(str(tmp_path))}
    handoff.mark_workspace_moved(41, "claude")
    state = handoff._entry(41)
    assert state["seen"] == {"claude": 7} and state["projects"]["claude"] == "@moved"
    # build_handoff's project check now reads "different workspace" for any real path.
    assert handoff._same_project(state["projects"]["claude"], str(tmp_path)) is False
    # The next successful turn records where it really ran again.
    handoff.record_last_agent(41, "claude", through_message_id=9, project_path=str(tmp_path))
    assert handoff._entry(41)["projects"]["claude"] != "@moved"
