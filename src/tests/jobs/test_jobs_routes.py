"""Jobs HTTP transport: auth, gating, and dispatch shapes."""

import time

import pytest
from flask import Flask

from api.jobs import ledger, service
from api.jobs.routes import jobs_bp


@pytest.fixture
def client(owner_session, monkeypatch):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(jobs_bp)
    return owner_session.sign_in(app.test_client())


def test_unauthenticated_401():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(jobs_bp)
    res = app.test_client().get("/api/jobs")
    assert res.status_code == 401


def test_gated_off(client, monkeypatch):
    # jobs_surface now defaults on; force it off to exercise the gate.
    monkeypatch.setattr("api.experimental.is_enabled", lambda flag: False)
    assert client.get("/api/jobs").status_code == 403
    assert client.get("/api/jobs/stats").status_code == 403
    # In-flight powers the shell badge and stays available regardless of the flag.
    assert client.get("/api/jobs/in-flight").status_code == 200


def test_list_and_stats(client, monkeypatch):
    monkeypatch.setattr("api.experimental.is_enabled", lambda flag: True)
    runs = FakeRuns()
    mesh = FakeMesh()
    monkeypatch.setattr(ledger, "agent_events_store", lambda: runs)
    monkeypatch.setattr(ledger, "mesh_store", lambda: mesh)
    monkeypatch.setattr(service, "_agent_events_store", lambda: runs)
    monkeypatch.setattr(service, "_mesh_store", lambda: mesh)
    runs.rows = [run_row("q1", status="ok")]
    mesh.jobs = [mesh_row("m1", status="running")]

    jobs = client.get("/api/jobs").get_json()["jobs"]
    assert {j["id"] for j in jobs} == {"q1", "m1"}
    assert client.get("/api/jobs?kind=run").get_json()["jobs"][0]["kind"] == "run"
    assert client.get("/api/jobs/stats").get_json()["runs"]["total"] == 1
    inflight = client.get("/api/jobs/in-flight").get_json()["executing_jobs"]
    assert "m1" in {j["id"] for j in inflight}
    assert all(set(j) >= {"kind", "id", "label", "status"} for j in inflight)


def test_stats_timeline_buckets(monkeypatch):
    runs = FakeRuns()
    mesh = FakeMesh()
    monkeypatch.setattr(service, "_agent_events_store", lambda: runs)
    monkeypatch.setattr(service, "_mesh_store", lambda: mesh)
    now = time.time()

    def run_at(qid, status, age_min):
        row = run_row(qid, status=status)
        row["started_at"] = now - age_min * 60
        row["finished_at"] = now - age_min * 60 + 5
        return row

    runs.rows = [
        run_at("a", "ok", 30),
        run_at("b", "failed", 30),
        run_at("c", "ok", 90),
    ]
    out = service.job_stats(hours=24)
    tl = out["timeline"]
    assert tl["bucket_hours"] == 1
    assert len(tl["buckets"]) == 24
    assert sum(b["ok"] + b["failed"] for b in tl["buckets"]) == 3
    assert tl["buckets"][-1]["ok"] == 1 and tl["buckets"][-1]["failed"] == 1
    assert tl["buckets"][-2]["ok"] == 1
    assert "mesh_timeline" in out and out["mesh_timeline"]["buckets"]


def test_cancel_route(client, monkeypatch):
    monkeypatch.setattr(service, "_mesh_store", lambda: FakeMesh([mesh_row("m1", status="running")]))
    monkeypatch.setattr("api.experimental.is_enabled", lambda flag: True)
    res = client.post("/api/jobs/mesh/m1/cancel", json={})
    assert res.status_code == 200
    assert res.get_json()["success"]


class FakeRuns:
    def __init__(self, rows=None):
        self.rows = rows or []

    def recent_runs(self, limit=200, status=None):
        rows = self.rows if not status else [r for r in self.rows if r.get("status") == status]
        return rows[:limit]


class FakeMesh:
    def __init__(self, jobs=None):
        self.jobs = jobs or []

    def list_jobs(self, limit=200):
        return self.jobs[:limit]

    def get_job(self, jid):
        for j in self.jobs:
            if j.get("id") == jid:
                return j
        return None

    def cancel_job(self, job_id, *, reason="cancelled"):
        j = self.get_job(job_id)
        if not j or j.get("status") in ("succeeded", "failed", "cancelled"):
            return None
        return {**j, "status": "cancelled"}


def run_row(qid, status="ok"):
    base = time.time()
    return {
        "query_id": qid,
        "status": status,
        "started_at": base - 300,
        "finished_at": base - 200,
        "agent_id": "cursor",
        "model": "gpt-4o",
        "chat_session_id": 7,
        "project_root": "/repo",
        "metadata": {"harness": {"agent_id": "cursor", "model": "gpt-4o"}, "success": status == "ok"},
    }


def mesh_row(jid="mesh1", status="running"):
    return {
        "id": jid,
        "type": "blender_render",
        "status": status,
        "priority": 50,
        "submitted_by": "bob",
        "claimed_by": "w1",
        "target_worker_id": None,
        "params": {"frame_start": 1, "frame_end": 10},
        "requirements": {},
        "result": None,
        "progress": None,
        "error": None,
        "attempts": 1,
        "created_at": 2000,
        "updated_at": 2000,
        "started_at": 2005,
        "completed_at": None,
    }