"""Live work tree: owners, nesting, chat background summary, routes."""

import pytest
from flask import Flask

from api.jobs import processes, work_tree
from api.jobs.routes import jobs_bp


def node(pid, chat, *, detached=False, parent_chat=None, children=(), t=1000.0):
    return {"pid": pid, "ppid": 1, "name": f"p{pid}", "status": "sleeping", "create_time": t, "cmdline": "x",
            "rss": 1, "cpu_sec": 0, "run_id": "r", "agent_id": "claude", "model": "", "chat_session_id": chat,
            "detached": detached, "parent_chat": parent_chat, "children": list(children)}


@pytest.fixture
def sources(monkeypatch):
    """Every source of the tree, replaced; tests fill in what they need."""
    state = {"forest": {}, "waits": [], "watches": {}, "schedules": {}, "runs": [], "meta": {}, "parents": {},
             "mesh": []}
    monkeypatch.setattr(processes, "forest", lambda max_age=None: state["forest"])
    monkeypatch.setattr(work_tree, "_waits", lambda: state["waits"])
    monkeypatch.setattr(work_tree, "_watches", lambda skip: state["watches"])
    monkeypatch.setattr(work_tree, "_schedules", lambda: state["schedules"])
    monkeypatch.setattr(work_tree, "_active_runs", lambda: state["runs"])
    monkeypatch.setattr(work_tree, "_chat_meta", lambda chats: state["meta"])
    monkeypatch.setattr(work_tree, "_subagent_parents", lambda chats: state["parents"])
    monkeypatch.setattr(work_tree, "_mesh_active", lambda: state["mesh"])
    monkeypatch.setattr(work_tree, "_feed_enabled", lambda: True)
    work_tree.invalidate()
    yield state
    work_tree.invalidate()


def test_detached_wait_and_processes_make_one_owner(sources):
    # The CH-001821 shape: turn finished, Claude CLI waiting, a nohup'd gate running.
    sources["forest"] = {"1821": {"roots": [node(17376, "1821", children=[node(2, "1821")]),
                                            node(214882, "1821", detached=True)],
                                  "process_count": 3, "detached_count": 1, "spawned_by": {}}}
    sources["waits"] = [{"job_id": "claude-bg-1821-x", "label": "Waiting on: fg12", "started_at": "2026-11-01T01:53:20",
                         "started_ts": 1793523200.0, "pid": 17376, "chat_session_id": "1821", "agent": "claude"}]
    sources["meta"] = {"1821": {"title": "Multiplayer orchestrator", "project": "/p"}}
    tree = work_tree.work_tree()
    [owner] = tree["owners"]
    assert owner["handle"] == "CH-001821" and owner["state"] == "waiting"
    assert owner["label"] == "Waiting on: fg12" and owner["title"] == "Multiplayer orchestrator"
    assert owner["process_count"] == 3 and owner["detached_count"] == 1
    assert [p["pid"] for p in owner["processes"]] == [17376, 214882]
    assert owner["processes"][1]["detached"] is True
    assert tree["totals"] == {"chats": 1, "processes": 3, "detached": 1}

    bg = work_tree.background_summary("db_session_1821")
    assert bg["label"] == "Waiting on: fg12" and bg["process_count"] == 3 and bg["detached_count"] == 1
    assert bg["working"] is True and bg["feed"] is True and bg["waits"][0]["job_id"] == "claude-bg-1821-x"
    assert bg["waits"][0]["started_at"] == 1793523200.0  # epoch wins over the naive local ISO string
    assert work_tree.background_summary(42) is None


def test_children_nest_under_parents_and_cycles_stay_visible(sources):
    sources["forest"] = {
        "1": {"roots": [node(10, "1")], "process_count": 1, "detached_count": 0, "spawned_by": {}},
        "2": {"roots": [node(30, "2", parent_chat="1")], "process_count": 1, "detached_count": 0, "spawned_by": {"1": 11}},
        "8": {"roots": [node(80, "8")], "process_count": 1, "detached_count": 0, "spawned_by": {}},
        "9": {"roots": [node(90, "9")], "process_count": 1, "detached_count": 0, "spawned_by": {}},
    }
    sources["runs"] = [{"query_id": "q3", "agent_id": "codex", "chat_session_id": "3", "started_at": 5}]
    sources["parents"] = {"3": "1", "8": "9", "9": "8"}  # 3: Flask-thread sub-agent; 8<->9: corrupt links
    tree = work_tree.work_tree()
    top = {o["chat_session_id"]: o for o in tree["owners"]}
    assert "2" not in top and "3" not in top
    kids = {c["chat_session_id"]: c for c in top["1"]["children"]}
    assert set(kids) == {"2", "3"} and kids["3"]["state"] == "running"
    assert kids["2"]["parent"] == {"chat_session_id": "1", "handle": "CH-000001", "via_pid": 11}
    assert {"8", "9"} <= set(top)  # a parent cycle never hides both chats


def test_schedules_alone_do_not_light_the_activity_dot(sources):
    sources["schedules"] = {"1822": [{"id": "op1", "label": "Supervise CH-001821", "detail": "every 15m",
                                      "status": "running", "target": "CH-001821", "cancellable": True}]}
    sources["forest"] = {"5": {"roots": [node(50, "5")], "process_count": 1, "detached_count": 0, "spawned_by": {}}}
    [sup, procs] = sorted(work_tree.work_tree()["owners"], key=lambda o: o["chat_session_id"])
    assert sup["state"] == "scheduled" and sup["label"] == "Supervise CH-001821 · every 15m"
    assert work_tree.background_session_ids() == {"5"}
    bg = work_tree.background_summary(1822)
    assert bg["working"] is False and bg["schedules"][0]["label"] == "Supervise CH-001821"
    assert bg["schedules"][0]["target"] == "CH-001821"  # the card links to the supervised chat
    rows = [{"id": 5, "generating": False}, {"id": 1822}, {"id": 7}, {"id": 5, "generating": True}]
    work_tree.mark_background(rows)
    assert [r["background"] for r in rows] == [True, False, False, False]


def test_a_failing_source_degrades_to_nothing(sources, monkeypatch):
    monkeypatch.setattr(processes, "forest", lambda max_age=None: (_ for _ in ()).throw(OSError("no /proc")))
    work_tree.invalidate()
    assert work_tree.background_session_ids() == set()
    assert work_tree.background_summary(1) is None


def test_live_status_extras_carries_background(sources):
    from api.chat_live_extras import live_status_extras

    sources["waits"] = [{"job_id": "j", "label": "Waiting on: build", "chat_session_id": "7"}]
    extras = live_status_extras("7")
    assert set(extras) == {"subagents", "background"}
    assert extras["background"]["label"] == "Waiting on: build"
    assert live_status_extras("8")["background"] is None


@pytest.fixture
def client(owner_session):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(jobs_bp)
    return owner_session.sign_in(app.test_client())


def test_routes_are_owner_only():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(jobs_bp)
    anon = app.test_client()
    assert anon.get("/api/jobs/work-tree").status_code == 401
    assert anon.post("/api/jobs/processes/123/end", json={"create_time": 1}).status_code == 401
    assert anon.post("/api/jobs/chats/1/end-processes").status_code == 401


def test_work_tree_and_end_routes(client, sources, monkeypatch):
    sources["forest"] = {"4": {"roots": [node(40, "4")], "process_count": 1, "detached_count": 0, "spawned_by": {}}}
    body = client.get("/api/jobs/work-tree").get_json()
    assert body["schema_version"] == 1 and body["owners"][0]["handle"] == "CH-000004"

    calls = []

    def fake_end(pid, create_time):
        calls.append((pid, create_time))
        if pid == 1:
            raise PermissionError("process 1 is protected")
        if pid == 2:
            raise LookupError("process 2 is no longer running")
        return {"pid": pid, "name": "sleep", "chat_session_id": "4", "ended": 1, "still_running": []}

    monkeypatch.setattr(processes, "end_process", fake_end)
    ok = client.post("/api/jobs/processes/40/end", json={"create_time": 1000.0})
    assert ok.status_code == 200 and ok.get_json()["ended"] == 1 and calls[-1] == (40, 1000.0)
    assert client.post("/api/jobs/processes/1/end", json={"create_time": 1}).status_code == 403
    assert client.post("/api/jobs/processes/2/end", json={"create_time": 1}).status_code == 404

    monkeypatch.setattr(processes, "end_chat_processes", lambda chat: {"chat_session_id": chat, "ended": [], "errors": []})
    res = client.post("/api/jobs/chats/4/end-processes")
    assert res.status_code == 200 and res.get_json()["chat_session_id"] == "4"
