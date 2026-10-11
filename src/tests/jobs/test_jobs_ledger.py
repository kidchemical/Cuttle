"""Jobs slice: unified ledger + control + telemetry (pure, source-injected)."""

import json
import time

import pytest

import api.jobs.ledger as ledger
import api.jobs.service as service
from api.jobs.catalog import job_actions


class FakeRuns:
    def __init__(self, rows=None):
        self.rows = rows or []

    def recent_runs(self, limit=200, status=None):
        rows = self.rows if not status else [r for r in self.rows if r.get("status") == status]
        return rows[:limit]

    def run(self, qid):
        for r in self.rows:
            if r.get("query_id") == qid:
                return r
        return None


class FakeMesh:
    def __init__(self, jobs=None):
        self.jobs = jobs or []
        self.cancelled = []
        self.submitted = []

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
        self.cancelled.append(job_id)
        return {**j, "status": "cancelled"}

    def submit_job(self, **kwargs):
        self.submitted.append(kwargs)
        return {"success": True, "id": "new-job", **kwargs}


@pytest.fixture
def patched_stores(monkeypatch):
    runs = FakeRuns()
    mesh = FakeMesh()
    monkeypatch.setattr(ledger, "agent_events_store", lambda: runs)
    monkeypatch.setattr(ledger, "mesh_store", lambda: mesh)
    monkeypatch.setattr(service, "_agent_events_store", lambda: runs)
    monkeypatch.setattr(service, "_mesh_store", lambda: mesh)
    return runs, mesh


def run_row(qid, status="ok", started=1000, finished=1300, agent="cursor", model="gpt-4o", meta=None):
    metadata = {
        "harness": {"agent_id": agent, "model": model},
        "success": status == "ok",
        "total_tokens": 1000,
        "total_cost": 0.02,
        "finished_at": finished,
        "error_message": None if status == "ok" else "boom",
    }
    if meta:
        metadata.update(meta)
    return {
        "query_id": qid,
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "agent_id": agent,
        "model": model,
        "chat_session_id": 7,
        "project_root": "/repo",
        "metadata": metadata,
    }


def mesh_row(jid="mesh1", status="running", jtype="blender_render", created=2000):
    return {
        "id": jid,
        "type": jtype,
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
        "created_at": created,
        "updated_at": created,
        "started_at": created + 5,
        "completed_at": None,
    }


def test_normalize_status_and_actions():
    assert job_actions("run", "running") == ["cancel"]
    assert job_actions("run", "ok") == []
    assert job_actions("mesh", "queued") == ["cancel"]
    assert job_actions("mesh", "failed") == ["rerun"]
    assert job_actions("mesh", "cancelled") == ["rerun"]


def test_unified_jobs_merges_and_sorts(patched_stores):
    runs, mesh = patched_stores
    runs.rows = [run_row("r-old", started=1000, finished=1300), run_row("r-new", started=4000, finished=4100)]
    mesh.jobs = [mesh_row(jid="mesh1", status="running", created=3000), mesh_row(jid="mesh2", status="succeeded", created=500)]
    jobs = ledger.unified_jobs(limit=10)
    ids = [j["id"] for j in jobs]
    assert ids[0] == "r-new"  # newest
    assert {"r-old", "r-new", "mesh1", "mesh2"} <= set(ids)
    by_id = {j["id"]: j for j in jobs}
    assert by_id["r-old"]["kind"] == "run"
    assert by_id["r-old"]["status"] == "succeeded"
    assert by_id["r-old"]["duration_s"] == 300
    assert by_id["mesh1"]["kind"] == "mesh"
    assert by_id["mesh1"]["status"] == "running"
    assert by_id["mesh1"]["links"]["mesh"]


def test_unified_jobs_filters(patched_stores):
    runs, mesh = patched_stores
    runs.rows = [run_row("r1", status="running", started=1000), run_row("r2", status="failed", started=2000)]
    mesh.jobs = [mesh_row("m1", status="queued", created=1500)]
    active = ledger.unified_jobs(limit=10, active_only=True)
    assert {j["id"] for j in active} == {"r1", "m1"}
    runs_only = ledger.unified_jobs(limit=10, kind="run")
    assert all(j["kind"] == "run" for j in runs_only)


def test_inflight_uses_running_runs_and_active_mesh(patched_stores, monkeypatch):
    runs, mesh = patched_stores
    runs.rows = [run_row("live1", status="running", started=5000, agent="codex")]
    mesh.jobs = [mesh_row("m1", status="running", created=4000), mesh_row("m2", status="succeeded", created=3000)]
    monkeypatch.setattr(
        "api.active_executions.get_executing_jobs",
        lambda: [{"query_id": "live1", "pipeline_name": "Codex"}],
    )
    jobs = ledger.inflight()
    ids = {j["id"] for j in jobs}
    assert "live1" in ids
    assert "m1" in ids
    assert "m2" not in ids
    live = next(j for j in jobs if j["id"] == "live1")
    assert live["label"] == "codex"


def test_cancel_run_dispatches_to_session(patched_stores, monkeypatch):
    runs, _ = patched_stores
    runs.rows = [run_row("r1", status="running", started=1000)]
    calls = {}

    def fake_cancel(session_id):
        calls["sid"] = session_id
        return {"cancelled": True, "query_ids": ["r1"]}

    monkeypatch.setattr("api.chat_run_registry.cancel_session_runs", fake_cancel)
    out = service.cancel_job("run", "r1")
    assert calls["sid"] == 7
    assert out["success"]


def test_cancel_run_rejects_finished(patched_stores):
    runs, _ = patched_stores
    runs.rows = [run_row("r1", status="ok", started=1000, finished=1100)]
    with pytest.raises(service.JobNotCancellableError):
        service.cancel_job("run", "r1")


def test_cancel_mesh_passes_through(patched_stores):
    _, mesh = patched_stores
    mesh.jobs = [mesh_row("m1", status="running", created=1000)]
    out = service.cancel_job("mesh", "m1")
    assert out["success"]
    assert mesh.cancelled == ["m1"]


def test_rerun_mesh_resubmits_spec(patched_stores):
    _, mesh = patched_stores
    mesh.jobs = [mesh_row("m1", status="failed", created=1000)]
    out = service.rerun_mesh_job("m1")
    assert out["success"]
    assert mesh.submitted
    spec = mesh.submitted[0]
    assert spec["job_type"] == "blender_render"
    assert spec["params"]["frame_start"] == 1
    assert spec["submitted_by"] == "bob"
    # Terminal jobs stay rerunnable (idempotent re-submission).
    service.rerun_mesh_job("m1")
    assert len(mesh.submitted) == 2
    with pytest.raises((service.JobNotFoundError, service.JobNotRerunnableError)):
        service.rerun_mesh_job("not-a-job")


def test_stats_aggregates(patched_stores):
    runs, mesh = patched_stores
    base = time.time()
    runs.rows = [
        run_row("a", status="ok", started=base - 300, finished=base - 200, agent="cursor", meta={"total_cost": 0.01}),
        run_row("b", status="ok", started=base - 200, finished=base - 100, agent="cursor", meta={"total_cost": 0.03}),
        run_row("c", status="failed", started=base - 100, finished=base, agent="codex", meta={"total_cost": 0.02}),
    ]
    mesh.jobs = [mesh_row(jid="mesh1", status="running", created=base - 100), mesh_row(jid="mesh2", status="succeeded", created=base - 200)]
    stats = service.job_stats(hours=24)
    assert stats["runs"]["total"] == 3
    assert stats["runs"]["succeeded"] == 2
    assert stats["runs"]["failed"] == 1
    assert stats["runs"]["success_rate"] == pytest.approx(0.667)
    assert stats["mesh"]["active"] == 1
    assert stats["mesh"]["succeeded"] == 1
    assert stats["by_agent"][0]["agent"] == "cursor"  # 2 runs > 1
    assert stats["by_agent"][0]["total_cost"] == pytest.approx(0.04)


def test_jsonish_metadata_survives(patched_stores):
    runs, _ = patched_stores
    row = run_row("q1", status="ok", started=1000, finished=1100, agent="cursor")
    row["metadata"] = json.loads(json.dumps(row["metadata"]))  # round-trip a real store decode
    runs.rows = [row]
    jobs = ledger.unified_jobs(limit=10)
    assert next(j for j in jobs if j["id"] == "q1")["meta"]["cost"] == 0.02