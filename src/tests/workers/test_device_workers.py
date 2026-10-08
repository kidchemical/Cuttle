"""Tests for host-first device workers mesh (W1 + file_copy)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture()
def worker_db(tmp_path, monkeypatch):
    db = tmp_path / "device_workers.db"
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_DB", str(db))
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_ENABLED", "1")
    monkeypatch.delenv("CUTTLE_DEVICE_WORKERS_TOKEN", raising=False)
    # Reset singleton
    import api.device_workers.store as store_mod

    store_mod._store = None
    yield store_mod.get_store()
    store_mod._store = None


def test_register_and_list_workers(worker_db):
    w = worker_db.upsert_worker(
        worker_id="worker-a",
        hostname="WORKER-A",
        os_name="Windows",
        capabilities={"filesystem": True, "blender": True},
        load={"cpu_pct": 10},
        interactive_priority="low",
        ac_power=True,
        meta={"cuttle_version": "0.2.5"},
    )
    assert w["worker_id"] == "worker-a"
    assert w["cuttle_version"] == "0.2.5"
    listed = worker_db.list_workers(stale_after=60)
    assert len(listed) == 1
    assert listed[0]["online"] is True
    assert listed[0]["capabilities"]["blender"] is True
    assert listed[0]["cuttle_version"] == "0.2.5"

    # Heartbeat with empty meta must preserve version + RTT
    worker_db.patch_worker_meta("worker-a", {"last_rtt_ms": 42.5, "last_rtt_at": time.time()})
    worker_db.upsert_worker(
        worker_id="worker-a",
        hostname="WORKER-A",
        os_name="Windows",
        capabilities={"filesystem": True},
        meta={"cuttle_version": "0.2.5"},
    )
    again = worker_db.list_workers(stale_after=60)[0]
    assert again["cuttle_version"] == "0.2.5"
    assert again["last_rtt_ms"] == 42.5


def test_remove_worker_revokes_enroll(worker_db):
    worker_db.upsert_worker(
        worker_id="smoke-laptop",
        hostname="SMOKE",
        capabilities={"filesystem": True},
    )
    enrolled = worker_db.enroll_device(worker_id="smoke-laptop", hostname="SMOKE")
    assert enrolled["token"]
    assert worker_db.lookup_enrolled_token(enrolled["token"]) == "smoke-laptop"

    result = worker_db.remove_worker("smoke-laptop", revoke_enroll=True)
    assert result["removed"] is True
    assert result["enroll_revoked"] is True
    assert worker_db.get_worker("smoke-laptop") is None
    assert worker_db.lookup_enrolled_token(enrolled["token"]) is None
    assert worker_db.list_workers() == []


def test_platform_remove_worker_refuses_host(worker_db, monkeypatch):
    from api.device_workers import platform as plat

    monkeypatch.setattr(plat, "worker_id", lambda: "kcstower")
    monkeypatch.setattr(plat, "device_workers_enabled", lambda: True)
    worker_db.upsert_worker(worker_id="kcstower", hostname="tower")
    worker_db.upsert_worker(worker_id="kcslaptop", hostname="laptop")

    denied = plat.remove_worker("kcstower")
    assert denied["success"] is False
    assert "host" in denied["error"].lower()
    assert worker_db.get_worker("kcstower") is not None

    ok = plat.remove_worker("kcslaptop")
    assert ok["success"] is True
    assert worker_db.get_worker("kcslaptop") is None


def test_list_workers_marks_version_mismatch(worker_db, monkeypatch):
    from api.device_workers import platform as plat
    from api.device_workers import capabilities as caps

    monkeypatch.setattr(caps, "cuttle_version", lambda: "0.2.6")
    monkeypatch.setattr(caps, "cuttle_git_rev", lambda: "aaa1111")
    monkeypatch.setattr(plat, "worker_id", lambda: "kcstower")
    monkeypatch.setattr(plat, "device_workers_enabled", lambda: True)
    worker_db.upsert_worker(
        worker_id="kcstower",
        hostname="tower",
        capabilities={"filesystem": True},
        meta={"cuttle_version": "0.2.6", "cuttle_git_rev": "aaa1111"},
    )
    worker_db.upsert_worker(
        worker_id="kcslaptop",
        hostname="laptop",
        capabilities={"filesystem": True, "cuttle_self_update": True},
        meta={"cuttle_version": "0.2.5", "cuttle_git_rev": "aaa1111"},
    )
    result = plat.list_workers()
    assert result["host_cuttle_version"] == "0.2.6"
    assert result["host_git_rev"] == "aaa1111"
    by_id = {w["worker_id"]: w for w in result["workers"]}
    assert by_id["kcslaptop"]["version_mismatch"] is True
    assert by_id["kcslaptop"]["git_mismatch"] is False
    assert by_id["kcslaptop"]["needs_update"] is True
    assert "kcslaptop" in result["outdated_workers"]
    assert by_id["kcstower"]["version_mismatch"] is False
    # Host is always slot 1; order is stable (not last_seen).
    assert result["workers"][0]["worker_id"] == "kcstower"
    assert result["workers"][0]["slot"] == 1
    assert by_id["kcstower"]["slot"] == 1
    assert by_id["kcslaptop"]["slot"] == 2


def test_list_workers_stable_slots_ignore_last_seen(worker_db, monkeypatch):
    """Heartbeats must not reshuffle the Devices table."""
    import time

    from api.device_workers import platform as plat
    from api.device_workers import capabilities as caps

    monkeypatch.setattr(caps, "cuttle_version", lambda: "0.2.25")
    monkeypatch.setattr(caps, "cuttle_git_rev", lambda: "abc")
    monkeypatch.setattr(plat, "worker_id", lambda: "kcstower")
    monkeypatch.setattr(plat, "device_workers_enabled", lambda: True)

    worker_db.upsert_worker(
        worker_id="kcstower",
        hostname="tower",
        capabilities={"filesystem": True},
        meta={"cuttle_version": "0.2.25"},
    )
    time.sleep(0.02)
    worker_db.upsert_worker(
        worker_id="kcslaptop",
        hostname="laptop",
        capabilities={"filesystem": True},
        meta={"cuttle_version": "0.2.25"},
    )
    # Make laptop "fresher" than host — old ORDER BY last_seen DESC would put it first.
    worker_db.upsert_worker(
        worker_id="kcslaptop",
        hostname="laptop",
        capabilities={"filesystem": True},
        meta={"cuttle_version": "0.2.25"},
    )

    first = plat.list_workers()
    assert [w["worker_id"] for w in first["workers"]] == ["kcstower", "kcslaptop"]
    assert [w["slot"] for w in first["workers"]] == [1, 2]

    worker_db.upsert_worker(
        worker_id="kcslaptop",
        hostname="laptop",
        capabilities={"filesystem": True},
        meta={"cuttle_version": "0.2.25"},
    )
    second = plat.list_workers()
    assert [w["worker_id"] for w in second["workers"]] == ["kcstower", "kcslaptop"]
    assert [w["slot"] for w in second["workers"]] == [1, 2]


def test_stale_process_flags_needs_update(worker_db, monkeypatch):
    """Version match + boot behind disk ⇒ stale_process / needs_update."""
    from api.device_workers import platform as plat
    from api.device_workers import capabilities as caps

    monkeypatch.setattr(caps, "cuttle_version", lambda: "0.2.25")
    monkeypatch.setattr(caps, "cuttle_git_rev", lambda: "bbbbbbbbbbbb")
    monkeypatch.setattr(plat, "worker_id", lambda: "kcstower")
    monkeypatch.setattr(plat, "device_workers_enabled", lambda: True)

    worker_db.upsert_worker(
        worker_id="kcstower",
        hostname="tower",
        capabilities={"filesystem": True},
        meta={
            "cuttle_version": "0.2.25",
            "cuttle_git_rev": "bbbbbbbbbbbb",
            "boot_git_rev": "bbbbbbbbbbbb",
        },
    )
    worker_db.upsert_worker(
        worker_id="kcslaptop",
        hostname="laptop",
        capabilities={"filesystem": True, "cuttle_self_update": True},
        meta={
            "cuttle_version": "0.2.25",
            "cuttle_git_rev": "bbbbbbbbbbbb",  # disk matches host
            "boot_git_rev": "aaaaaaaaaaaa",  # process still on older boot
        },
    )
    result = plat.list_workers()
    by_id = {w["worker_id"]: w for w in result["workers"]}
    assert by_id["kcslaptop"]["git_mismatch"] is False
    assert by_id["kcslaptop"]["version_mismatch"] is False
    assert by_id["kcslaptop"]["stale_process"] is True
    assert by_id["kcslaptop"]["needs_update"] is True
    assert "kcslaptop" in result["outdated_workers"]
    assert by_id["kcstower"]["stale_process"] is False


def test_process_stale_and_refresh_boot(monkeypatch):
    from api.device_workers import capabilities as caps

    caps._BOOT_GIT_REV = "oldoldoldold"
    caps._BOOT_AT = 1.0
    caps._GIT_REV_CACHE = (0.0, "")
    monkeypatch.setattr(caps, "cuttle_git_rev", lambda force=False: "newnewnewnew")
    assert caps.process_stale() is True
    assert caps.refresh_boot_marker() == "newnewnewnew"
    assert caps.process_stale() is False


def test_list_workers_marks_git_mismatch_without_version_bump(worker_db, monkeypatch):
    """Git push without bumping electron/package.json still flags Clients."""
    from api.device_workers import platform as plat
    from api.device_workers import capabilities as caps

    monkeypatch.setattr(caps, "cuttle_version", lambda: "0.2.18")
    monkeypatch.setattr(caps, "cuttle_git_rev", lambda: "e00d8c5d1146")
    monkeypatch.setattr(plat, "worker_id", lambda: "kcstower")
    monkeypatch.setattr(plat, "device_workers_enabled", lambda: True)
    worker_db.upsert_worker(
        worker_id="kcstower",
        hostname="tower",
        capabilities={"filesystem": True},
        meta={"cuttle_version": "0.2.18", "cuttle_git_rev": "e00d8c5d1146"},
    )
    worker_db.upsert_worker(
        worker_id="kcslaptop",
        hostname="laptop",
        capabilities={"filesystem": True, "cuttle_self_update": True},
        meta={"cuttle_version": "0.2.18", "cuttle_git_rev": "oldc0ffee000"},
    )
    result = plat.list_workers()
    by_id = {w["worker_id"]: w for w in result["workers"]}
    assert by_id["kcslaptop"]["version_mismatch"] is False
    assert by_id["kcslaptop"]["git_mismatch"] is True
    assert by_id["kcslaptop"]["needs_update"] is True
    assert by_id["kcslaptop"]["cuttle_git_rev"] == "oldc0ffee000"
    assert "kcslaptop" in result["outdated_workers"]


def test_list_workers_flags_missing_git_rev_on_updatable_client(worker_db, monkeypatch):
    """Old Client sidecars (no git rev in meta) still get needs_update after Host push."""
    from api.device_workers import platform as plat
    from api.device_workers import capabilities as caps

    monkeypatch.setattr(caps, "cuttle_version", lambda: "0.2.18")
    monkeypatch.setattr(caps, "cuttle_git_rev", lambda: "e00d8c5d1146")
    monkeypatch.setattr(plat, "worker_id", lambda: "kcstower")
    monkeypatch.setattr(plat, "device_workers_enabled", lambda: True)
    worker_db.upsert_worker(
        worker_id="kcstower",
        hostname="tower",
        capabilities={"filesystem": True},
        meta={"cuttle_version": "0.2.18", "cuttle_git_rev": "e00d8c5d1146"},
    )
    worker_db.upsert_worker(
        worker_id="kcslaptop",
        hostname="laptop",
        capabilities={"filesystem": True, "cuttle_self_update": True},
        meta={"cuttle_version": "0.2.18"},
    )
    result = plat.list_workers()
    by_id = {w["worker_id"]: w for w in result["workers"]}
    assert by_id["kcslaptop"]["git_mismatch"] is True
    assert by_id["kcslaptop"]["needs_update"] is True


def test_git_revs_differ_prefix():
    from api.device_workers.capabilities import git_revs_differ

    assert git_revs_differ("e00d8c5", "e00d8c5d11464c8e") is False
    assert git_revs_differ("e00d8c5d1146", "oldc0ffee000") is True
    assert git_revs_differ("", "e00d8c5") is False


def test_claim_complete_ping(worker_db):
    job = worker_db.submit_job(job_type="ping", params={"echo": "hi"})
    assert job["status"] == "queued"
    claimed = worker_db.claim_jobs(worker_id="host", capabilities={"filesystem": True}, limit=1)
    assert len(claimed) == 1
    assert claimed[0]["status"] == "claimed"
    assert worker_db.heartbeat_job(claimed[0]["id"], "host")
    assert worker_db.complete_job(claimed[0]["id"], "host", result={"pong": True})
    done = worker_db.get_job(claimed[0]["id"])
    assert done["status"] == "succeeded"
    assert done["result"]["pong"] is True


def test_target_worker_id_filters_claim(worker_db):
    worker_db.submit_job(
        job_type="ping",
        params={},
        target_worker_id="worker-a",
    )
    assert worker_db.claim_jobs(worker_id="tower", capabilities={}) == []
    claimed = worker_db.claim_jobs(worker_id="worker-a", capabilities={})
    assert len(claimed) == 1


def test_requirements_blender(worker_db):
    worker_db.submit_job(
        job_type="ping",
        params={},
        requirements={"blender": True},
    )
    assert worker_db.claim_jobs(worker_id="a", capabilities={"filesystem": True}) == []
    claimed = worker_db.claim_jobs(
        worker_id="a", capabilities={"filesystem": True, "blender": True}
    )
    assert len(claimed) == 1


def test_lease_reclaim(worker_db):
    job = worker_db.submit_job(job_type="ping", params={})
    claimed = worker_db.claim_jobs(
        worker_id="w1", capabilities={}, lease_seconds=1
    )
    assert claimed
    # Force expiry
    import sqlite3
    from api.device_workers.store import database_path

    conn = sqlite3.connect(str(database_path()))
    conn.execute(
        "UPDATE jobs SET claim_expires = ? WHERE id = ?",
        (time.time() - 10, claimed[0]["id"]),
    )
    conn.commit()
    conn.close()
    n = worker_db.reclaim_expired()
    assert n == 1
    again = worker_db.claim_jobs(worker_id="w2", capabilities={})
    assert len(again) == 1
    assert again[0]["claimed_by"] == "w2"


def test_file_copy_executor(tmp_path, monkeypatch):
    from api.device_workers import executor as ex

    src = tmp_path / "src.txt"
    src.write_text("hello-mesh", encoding="utf-8")
    dest = tmp_path / "out" / "dest.txt"
    monkeypatch.setattr(
        ex,
        "allowed_path_prefixes",
        lambda: [str(tmp_path)],
    )
    result = ex.execute_job(
        {
            "type": "file_copy",
            "params": {"source": str(src), "dest": str(dest)},
        }
    )
    assert result["ok"] is True
    assert Path(result["dest"]).read_text(encoding="utf-8") == "hello-mesh"


def test_file_copy_rejects_outside_allowlist(tmp_path, monkeypatch):
    from api.device_workers import executor as ex
    from api.device_workers.executor import JobExecError

    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ex, "allowed_path_prefixes", lambda: [str(tmp_path / "only")])
    with pytest.raises(JobExecError, match="allowlisted"):
        ex.execute_job(
            {
                "type": "file_copy",
                "params": {
                    "source": str(src),
                    "dest": str(tmp_path / "b.txt"),
                },
            }
        )


def test_auth_loopback_without_token(monkeypatch):
    # No loopback exemption: loopback without a bearer authorizes nothing.
    from api.device_workers import auth as auth_mod


    class Req:
        headers = {}
        remote_addr = "127.0.0.1"

    ok, err = auth_mod.authorize_worker_request(Req())
    assert not ok and err

    class Remote:
        headers = {}
        remote_addr = "192.0.2.50"

    ok2, err2 = auth_mod.authorize_worker_request(Remote())
    assert not ok2


def test_auth_enrolled_token(worker_db, monkeypatch):
    from api.device_workers import auth as auth_mod

    enrolled = worker_db.enroll_device(worker_id="worker-a", hostname="WORKER-A", remote_addr="192.0.2.40")
    monkeypatch.setattr("api.device_workers.store.get_store", lambda: worker_db)

    class Good:
        headers = {"Authorization": f"Bearer {enrolled['token']}"}
        remote_addr = "192.0.2.40"

    assert auth_mod.authorize_worker_request(Good())[0] is True


def test_enroll_from_lan(worker_db, monkeypatch):
    monkeypatch.setattr("api.device_workers.routes.device_workers_enabled", lambda: True)
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    monkeypatch.setattr("api.device_workers.auth.lan_access_enabled", lambda: True)

    from api.device_workers.routes import workers_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    client = app.test_client()

    r = client.post(
        "/api/workers/enroll",
        json={
            "worker_id": "worker-a",
            "hostname": "WORKER-A",
            "pairing_secret": "ab" * 32,
        },
        environ_base={"REMOTE_ADDR": "192.0.2.40"},
    )
    assert r.status_code == 202
    pending = r.get_json()
    assert pending["status"] == "pending" and "token" not in pending

    # Host owner approves; worker polls once for its token.
    from api.device_workers import enroll_approval as enroll_mod

    enroll_mod.decide(pending["request_id"], "approve")
    store = worker_db
    enrolled = store.enroll_device(
        worker_id="worker-a", hostname="WORKER-A", rotate=store.is_enrolled("worker-a")
    )
    enroll_mod.attach_token(pending["request_id"], enrolled["token"])
    data = client.post(
        f"/api/workers/enroll/{pending['request_id']}/poll",
        json={"pairing_secret": "ab" * 32},
        environ_base={"REMOTE_ADDR": "192.0.2.40"},
    ).get_json()
    assert data["status"] == "approved" and data["token"]

    # Remote register with enrolled token
    r2 = client.post(
        "/api/workers/register",
        json={"worker_id": "worker-a", "hostname": "WORKER-A", "capabilities": {"filesystem": True}},
        headers={"Authorization": f"Bearer {data['token']}"},
        environ_base={"REMOTE_ADDR": "192.0.2.40"},
    )
    assert r2.status_code == 200


def test_auth_bearer_token(worker_db, monkeypatch):
    from api.device_workers import auth as auth_mod


    token = worker_db.enroll_device(worker_id="fixture-worker")["token"]
    monkeypatch.setattr("api.device_workers.store.get_store", lambda: worker_db)

    class Bad:
        headers = {"Authorization": "Bearer nope"}
        remote_addr = "192.0.2.50"

    assert auth_mod.authorize_worker_request(Bad())[0] is False

    class Good:
        headers = {"Authorization": f"Bearer {token}"}
        remote_addr = "192.0.2.50"

    assert auth_mod.authorize_worker_request(Good())[0] is True


def test_flask_workers_routes(worker_db, monkeypatch):
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_ENABLED", "1")
    monkeypatch.setattr(
        "api.device_workers.routes.device_workers_enabled", lambda: True
    )
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    monkeypatch.setattr(
        "api.device_workers.routes.require_ui_operator",
        lambda: ({"id": 1, "username": "test-owner", "auth_provider": "local"}, None),
    )

    from api.device_workers.routes import workers_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    client = app.test_client()

    # Runtime routes need the worker's own bearer even on loopback.
    bearer = worker_db.enroll_device(worker_id="worker-a")["token"]
    auth_headers = {"Authorization": f"Bearer {bearer}"}

    r = client.post(
        "/api/workers/register",
        json={
            "worker_id": "worker-a",
            "hostname": "WORKER-A",
            "capabilities": {"filesystem": True},
        },
        headers=auth_headers,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert r.status_code == 200
    assert r.get_json()["success"] is True

    r2 = client.get("/api/workers")
    assert r2.status_code == 200
    body = r2.get_json()
    assert body["enabled"] is True
    assert any(w["worker_id"] == "worker-a" for w in body["workers"])
    assert "online_remote" in body
    assert "self_worker_id" in body

    r3 = client.post(
        "/api/workers/jobs",
        json={"type": "ping", "params": {"echo": 1}, "submitted_by": "jobs-ui-test"},
    )
    assert r3.status_code == 200
    job_body = r3.get_json()["job"]
    job_id = job_body["id"]
    assert job_body.get("submitted_by") == "jobs-ui-test"

    r4 = client.post(
        "/api/workers/jobs/claim",
        json={"worker_id": "worker-a", "capabilities": {"filesystem": True}},
        headers=auth_headers,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert r4.status_code == 200
    jobs = r4.get_json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id

    r5 = client.post(
        f"/api/workers/jobs/{job_id}/complete",
        json={"worker_id": "worker-a", "result": {"pong": True}},
        headers=auth_headers,
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert r5.status_code == 200
    assert worker_db.get_job(job_id)["status"] == "succeeded"


def test_cancel_and_plan_and_blender_validate(worker_db, monkeypatch):
    monkeypatch.setattr(
        "api.device_workers.routes.device_workers_enabled", lambda: True
    )
    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    monkeypatch.setattr(
        "api.device_workers.platform.get_store", lambda: worker_db
    )
    monkeypatch.setattr(
        "api.device_workers.platform.device_workers_enabled", lambda: True
    )
    monkeypatch.setattr(
        "api.device_workers.platform.worker_id", lambda: "kcstower"
    )
    monkeypatch.setattr(
        "api.device_workers.routes.require_ui_operator",
        lambda: ({"id": 1, "username": "test-owner", "auth_provider": "local"}, None),
    )

    from api.device_workers.routes import workers_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    client = app.test_client()

    job = worker_db.submit_job(job_type="ping", params={"echo": "x"}, submitted_by="t")
    r = client.post(f"/api/workers/jobs/{job['id']}/cancel", json={"reason": "test"})
    assert r.status_code == 200
    assert r.get_json()["job"]["status"] == "cancelled"

    worker_db.upsert_worker(
        worker_id="worker-a",
        hostname="WORKER-A",
        capabilities={"blender": True, "filesystem": True},
    )
    # Force online via fresh last_seen (upsert already now)
    r2 = client.post(
        "/api/workers/plan",
        json={"message": "use workers to render this blender scene"},
    )
    assert r2.status_code == 200
    plan = r2.get_json()
    assert plan["classification"]["mesh"] is True
    assert plan["classification"]["kind"] == "blender_render"

    from api.device_workers.executor import validate_job_submission

    ok, err = validate_job_submission(
        "blender_render",
        {
            "blend_file": r"C:\Users\x\Desktop\a.blend",
            "output_dir": r"C:\Users\x\Desktop\out",
            "frame_start": 1,
            "frame_end": 10,
        },
    )
    assert ok, err

    from api.device_workers.intent import shard_frame_ranges

    assert shard_frame_ranges(1, 5, worker_count=2) == [(1, 3), (4, 5)]


def test_blender_dry_run_and_discovery(tmp_path, monkeypatch):
    from api.device_workers import executor as ex
    from api.device_workers.capabilities import find_blender_executable

    # Discovery should not crash when Program Files missing in CI
    found = find_blender_executable()
    assert found is None or Path(found).is_file()

    blend = tmp_path / "a.blend"
    blend.write_bytes(b"BLENDER")
    out = tmp_path / "out"
    monkeypatch.setattr(ex, "allowed_path_prefixes", lambda: [str(tmp_path)])
    monkeypatch.setattr(
        ex,
        "_resolve_blender",
        lambda hint="": r"C:\fake\blender.exe",
    )
    result = ex.execute_job(
        {
            "type": "blender_render",
            "params": {
                "blend_file": str(blend),
                "output_dir": str(out),
                "frame_start": 1,
                "frame_end": 3,
                "dry_run": True,
            },
        }
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["frame_start"] == 1
    assert result["frame_end"] == 3
    assert "-a" in result["cmd"]


def test_shard_frame_ranges():
    from api.device_workers.intent import (
        auto_chunk_size,
        chunk_frame_ranges,
        shard_frame_ranges,
    )

    assert shard_frame_ranges(1, 4, worker_count=2) == [(1, 2), (3, 4)]
    assert shard_frame_ranges(1, 5, worker_count=2) == [(1, 3), (4, 5)]
    assert shard_frame_ranges(10, 10, worker_count=3) == [(10, 10)]
    assert chunk_frame_ranges(1, 10, chunk_size=4) == [(1, 4), (5, 8), (9, 10)]
    assert chunk_frame_ranges(1, 3, chunk_size=10) == [(1, 3)]
    # Steal-friendly defaults: clamp ≤8, ~8 chunks/worker target.
    assert auto_chunk_size(206, 2) == 8
    assert auto_chunk_size(8, 2, min_size=4, max_size=24) == 4
    assert auto_chunk_size(263, 2) == 8
    assert auto_chunk_size(10, 2) == 1


def test_count_frames_in_range(tmp_path):
    from api.device_workers.executor import _count_frames_in_range

    out = tmp_path / "out"
    out.mkdir()
    (out / "frame_0001.png").write_bytes(b"a")
    (out / "frame_0104.png").write_bytes(b"b")
    (out / "frame_0206.png").write_bytes(b"c")
    (out / "readme.txt").write_text("x", encoding="utf-8")
    assert _count_frames_in_range(out, 1, 103) == 1
    assert _count_frames_in_range(out, 104, 206) == 2


def test_work_steal_blender_shards_untargeted(tmp_path, monkeypatch):
    from api.device_workers import platform as plat
    from api.device_workers.store import DeviceWorkerStore

    db = DeviceWorkerStore(tmp_path / "dw.db")
    now = __import__("time").time()
    for wid in ("kcslaptop", "kcstower"):
        db.upsert_worker(
            worker_id=wid,
            hostname=wid,
            capabilities={"blender": True, "filesystem": True},
            load={"gpu_pct": 0},
        )
        # Force online: patch last_seen via SQL is already now from upsert
    monkeypatch.setattr(plat, "device_workers_enabled", lambda: True)
    monkeypatch.setattr(plat, "get_store", lambda: db)
    monkeypatch.setattr(
        plat,
        "list_workers",
        lambda online_only=False: {
            "success": True,
            "workers": [
                {
                    "worker_id": "kcslaptop",
                    "online": True,
                    "is_self": False,
                    "capabilities": {"blender": True},
                    "interactive_priority": "low",
                    "load": {},
                },
                {
                    "worker_id": "kcstower",
                    "online": True,
                    "is_self": True,
                    "capabilities": {"blender": True},
                    "interactive_priority": "low",
                    "load": {},
                },
            ],
            "self_worker_id": "kcstower",
        },
    )
    # Bypass path validation for submit
    monkeypatch.setattr(
        plat,
        "validate_job_submission",
        lambda job_type, params: (True, ""),
    )
    result = plat.submit_blender_shards(
        blend_file=r"G:\fake\a.blend",
        output_dir=r"G:\fake\out",
        frame_start=1,
        frame_end=50,
        chunk_size=10,
        distribution="work_steal",
        dry_run=True,
    )
    assert result["success"], result
    assert result["distribution"] == "work_steal"
    assert result["shard_count"] == 5
    assert result["batch_id"]
    jobs = result["jobs"]
    assert all(j.get("target_worker_id") in (None, "") for j in jobs)
    assert jobs[0]["params"]["frame_start"] == 1
    assert jobs[0]["params"]["frame_end"] == 10
    assert jobs[-1]["params"]["frame_end"] == 50
    # Either worker can claim first untargeted chunk
    claimed = db.claim_jobs(
        worker_id="kcslaptop", capabilities={"blender": True}, limit=1
    )
    assert len(claimed) == 1
    assert claimed[0]["params"]["frame_start"] == 1


def test_render_profile_ewma(tmp_path):
    from api.device_workers.profiles import maybe_record_job_result
    from api.device_workers.store import DeviceWorkerStore

    db = DeviceWorkerStore(tmp_path / "dw.db")
    db.upsert_worker(worker_id="worker-a", hostname="worker-a", capabilities={"blender": True})
    job = {
        "type": "blender_render",
        "status": "succeeded",
        "params": {"engine": "BLENDER_EEVEE_NEXT", "frame_start": 1, "frame_end": 10},
        "result": {
            "ok": True,
            "sec_per_frame": 2.0,
            "frame_start": 1,
            "frame_end": 10,
            "elapsed_seconds": 20,
        },
    }
    maybe_record_job_result(db, "worker-a", job)
    w = db.get_worker("worker-a")
    assert w["meta"]["render_profile"]["eevee"]["ewma_spf"] == 2.0
    job["result"]["sec_per_frame"] = 1.0
    maybe_record_job_result(db, "worker-a", job)
    w = db.get_worker("worker-a")
    ewma = w["meta"]["render_profile"]["eevee"]["ewma_spf"]
    assert 1.0 < ewma < 2.0
    assert w["meta"]["render_profile"]["eevee"]["samples"] == 2


def test_ssh_approval_once_and_session():
    from api.device_workers import ssh_approval as sa

    sa.clear_session_grant()
    row = sa.create_request(
        worker_id="kcstower",
        target="cuttle-lan",
        command_preview="echo hi",
    )
    assert row["status"] == "pending"
    assert sa.list_pending()
    decided = sa.decide(row["id"], "once")
    assert decided["status"] == "once"
    assert not sa.session_granted()

    row2 = sa.create_request(worker_id="kcstower", target="cuttle-lan", command_preview="hostname")
    sa.decide(row2["id"], "session")
    sa.grant_session()
    assert sa.session_granted()
    sa.clear_session_grant()
    assert not sa.session_granted()


def test_ssh_hitl_blocks_without_approval(monkeypatch):
    from api.device_workers import executor as ex
    from api.device_workers import ssh_approval as sa
    from api.device_workers.executor import JobExecError

    sa.clear_session_grant()
    monkeypatch.setenv("CUTTLE_SSH_APPROVAL_BYPASS", "0")
    monkeypatch.setattr(ex, "_execute_shell_ssh_enabled", lambda: True)
    monkeypatch.setattr(ex, "_command_allowed", lambda c: True)
    monkeypatch.setattr(ex, "_dw_settings", lambda: {
        "execute_shell_ssh_enabled": True,
        "ssh_host": "cuttle-lan",
        "ssh_identity": "",
        "ssh_approval_required": True,
        "ssh_approval_timeout_seconds": 1,
        "execute_shell_prefixes": ["echo "],
    })
    monkeypatch.setattr(
        sa,
        "request_via_coordinator",
        lambda **kw: "deny",
    )
    monkeypatch.setattr(sa, "unsafe_shell_approval_required", lambda: True)
    monkeypatch.setattr(sa, "session_granted", lambda: False)

    with pytest.raises(JobExecError, match="denied"):
        ex.execute_job({"type": "execute_shell_ssh", "params": {"command": "echo hi"}})


def test_execute_shell_unsafe_hitl_blocks_without_approval(monkeypatch):
    from api.device_workers import executor as ex
    from api.device_workers import ssh_approval as sa
    from api.device_workers.executor import JobExecError

    sa.clear_session_grant()
    monkeypatch.setenv("CUTTLE_SSH_APPROVAL_BYPASS", "0")
    monkeypatch.setattr(ex, "_execute_shell_unsafe_enabled", lambda: True)
    monkeypatch.setattr(ex, "_command_allowed", lambda c: True)
    monkeypatch.setattr(ex, "_dw_settings", lambda: {
        "execute_shell_unsafe_enabled": True,
        "ssh_approval_required": True,
        "execute_shell_prefixes": ["echo "],
    })
    monkeypatch.setattr(sa, "request_via_coordinator", lambda **kw: "deny")
    monkeypatch.setattr(sa, "unsafe_shell_approval_required", lambda: True)
    monkeypatch.setattr(sa, "session_granted", lambda: False)

    with pytest.raises(JobExecError, match="denied"):
        ex.execute_job({"type": "execute_shell_unsafe", "params": {"command": "echo hi"}})


def test_shell_recipe_allowlist_and_self_update_schedules(tmp_path, monkeypatch):
    from api.device_workers import executor as ex

    ok, err = ex.validate_job_submission("shell", {"recipe": "nope"})
    assert not ok
    ok, err = ex.validate_job_submission("shell", {"recipe": "git_status"})
    assert ok, err

    calls = []

    def fake_popen(*a, **k):
        calls.append((a, k))

        class P:
            pid = 1

        return P()

    monkeypatch.setattr(ex.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("CUTTLE_REPO_ROOT", str(tmp_path))
    (tmp_path / ".git").mkdir()
    scripts = tmp_path / ".cuttle_global" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "client-self-update.ps1").write_text("# stub\n", encoding="utf-8")
    (scripts / "client-self-update.sh").write_text("#!/bin/bash\n", encoding="utf-8")

    monkeypatch.setattr(
        ex,
        "_git_update_checkout",
        lambda repo, log_path=None, helper_path=None: {"ok": True, "stashed": False, "head": "abc1234", "log": ""},
    )

    result = ex.execute_job({"type": "cuttle_self_update", "params": {"repo": str(tmp_path)}})
    assert result.get("scheduled") is True
    assert calls, "expected detached updater spawn"

    # execute_shell_unsafe defaults off
    try:
        ex.execute_job({"type": "execute_shell_unsafe", "params": {"command": "echo hi"}})
        assert False, "expected JobExecError"
    except ex.JobExecError as e:
        assert "disabled" in str(e).lower() or "unsafe" in str(e).lower()


def test_queue_ttl_expires_before_claim(worker_db):
    job = worker_db.submit_job(
        job_type="ping",
        params={},
        ttl_seconds=30,
    )
    assert job["expires_at"] is not None
    assert job["expires_at"] <= time.time() + 35
    # Force past expiry
    import sqlite3
    from api.device_workers.store import database_path

    conn = sqlite3.connect(str(database_path()))
    conn.execute(
        "UPDATE jobs SET expires_at = ? WHERE id = ?",
        (time.time() - 5, job["id"]),
    )
    conn.commit()
    conn.close()
    n = worker_db.expire_queued()
    assert n == 1
    assert worker_db.get_job(job["id"])["status"] == "cancelled"
    assert worker_db.claim_jobs(worker_id="w", capabilities={}) == []


def test_unsafe_shell_lease_does_not_requeue(worker_db):
    job = worker_db.submit_job(
        job_type="execute_shell_unsafe",
        params={"command": "echo hi"},
        max_attempts=1,
    )
    claimed = worker_db.claim_jobs(
        worker_id="w1", capabilities={}, lease_seconds=60
    )
    assert claimed
    assert claimed[0]["attempts"] == 1
    import sqlite3
    from api.device_workers.store import database_path

    conn = sqlite3.connect(str(database_path()))
    conn.execute(
        "UPDATE jobs SET claim_expires = ? WHERE id = ?",
        (time.time() - 10, claimed[0]["id"]),
    )
    conn.commit()
    conn.close()
    worker_db.reclaim_expired()
    done = worker_db.get_job(job["id"])
    assert done["status"] == "cancelled"
    assert "does not requeue" in (done.get("error") or "")
    assert worker_db.claim_jobs(worker_id="w2", capabilities={}) == []


def test_ping_lease_requeue_until_max_attempts(worker_db):
    job = worker_db.submit_job(job_type="ping", params={}, max_attempts=2)
    claimed = worker_db.claim_jobs(worker_id="w1", capabilities={}, lease_seconds=60)
    assert claimed and claimed[0]["attempts"] == 1
    import sqlite3
    from api.device_workers.store import database_path

    def force_expire(jid):
        conn = sqlite3.connect(str(database_path()))
        conn.execute(
            "UPDATE jobs SET claim_expires = ? WHERE id = ?",
            (time.time() - 10, jid),
        )
        conn.commit()
        conn.close()

    force_expire(claimed[0]["id"])
    worker_db.reclaim_expired()
    assert worker_db.get_job(job["id"])["status"] == "queued"
    claimed2 = worker_db.claim_jobs(worker_id="w2", capabilities={}, lease_seconds=60)
    assert claimed2 and claimed2[0]["attempts"] == 2
    force_expire(claimed2[0]["id"])
    worker_db.reclaim_expired()
    done = worker_db.get_job(job["id"])
    assert done["status"] == "cancelled"
    assert "max attempts" in (done.get("error") or "")

def test_build_batch_watch_bars_overall_and_workers(tmp_path):
    from api.device_workers.platform import build_batch_watch_bars

    out = tmp_path / "frames"
    out.mkdir()
    for n in (1, 2, 3, 11, 12):
        (out / f"frame_{n:04d}.png").write_bytes(b"x")

    summary = {
        "success": True,
        "batch_id": "demo",
        "jobs": [
            {
                "status": "succeeded",
                "claimed_by": "kcstower",
                "params": {
                    "output_dir": str(out),
                    "frame_start": 1,
                    "frame_end": 10,
                    "batch_id": "demo",
                },
            },
            {
                "status": "running",
                "claimed_by": "kcslaptop",
                "params": {
                    "output_dir": str(out),
                    "frame_start": 11,
                    "frame_end": 20,
                    "batch_id": "demo",
                },
            },
            {
                "status": "queued",
                "params": {
                    "output_dir": str(out),
                    "frame_start": 21,
                    "frame_end": 30,
                    "batch_id": "demo",
                },
            },
        ],
    }
    built = build_batch_watch_bars(summary, label_prefix="Demo bake")
    assert built["success"]
    assert built["frames_total"] == 30
    assert built["frames_done"] == 5
    bars = built["bars"]
    assert bars[0]["kind"] == "primary"
    assert bars[0]["id"] == "overall"
    kinds = {b["id"]: b for b in bars[1:]}
    assert "kcstower" in kinds and kinds["kcstower"]["kind"] == "worker"
    assert "kcslaptop" in kinds and kinds["kcslaptop"]["kind"] == "worker"
    assert "unclaimed" not in kinds
    assert kinds["kcslaptop"]["detail"].startswith("2/")
    assert built.get("chunk_reallocations") == 0


def test_build_batch_watch_bars_counts_chunk_reallocations():
    from api.device_workers.platform import build_batch_watch_bars

    summary = {
        "success": True,
        "batch_id": "demo",
        "jobs": [
            {
                "status": "succeeded",
                "claimed_by": "kcstower",
                "attempts": 1,
                "params": {
                    "output_dir": r"G:\missing",
                    "frame_start": 1,
                    "frame_end": 10,
                    "batch_id": "demo",
                },
            },
            {
                "status": "running",
                "claimed_by": "kcslaptop",
                "attempts": 3,
                "params": {
                    "output_dir": r"G:\missing",
                    "frame_start": 11,
                    "frame_end": 20,
                    "batch_id": "demo",
                },
            },
            {
                "status": "queued",
                "attempts": 2,
                "params": {
                    "output_dir": r"G:\missing",
                    "frame_start": 21,
                    "frame_end": 30,
                    "batch_id": "demo",
                },
            },
        ],
    }
    built = build_batch_watch_bars(summary)
    # attempts 3 → 2 reallocs; attempts 2 → 1 realloc; total 3
    assert built["chunk_reallocations"] == 3
    assert built["chunks_reallocated"] == 2
    assert "3 reallocs" in built["bars"][0]["detail"]
    assert "3 chunk reallocs" in built["label"]


def test_fail_retry_respects_max_attempts(worker_db):
    from api.device_workers.job_policy import fail_disposition

    assert fail_disposition(
        job_type="blender_render", attempts=2, max_attempts=5, retry=True
    )[0] == "requeue"
    assert fail_disposition(
        job_type="blender_render", attempts=5, max_attempts=5, retry=True
    )[0] == "fail"
    assert fail_disposition(
        job_type="execute_shell_unsafe", attempts=1, max_attempts=1, retry=True
    )[0] == "fail"

    job = worker_db.submit_job(
        job_type="ping",
        params={"echo": "x"},
        max_attempts=2,
    )
    claimed = worker_db.claim_jobs(
        worker_id="w1",
        capabilities={},
        limit=1,
        lease_seconds=600,
    )
    assert claimed and claimed[0]["id"] == job["id"]
    # First retry → requeue
    assert worker_db.fail_job(
        job["id"], "w1", error="boom", retry=True
    )
    row = worker_db.get_job(job["id"])
    assert row["status"] == "queued"
    # Claim again (attempts=2) then retry at cap → failed
    claimed2 = worker_db.claim_jobs(
        worker_id="w1", capabilities={}, limit=1, lease_seconds=600
    )
    assert claimed2
    assert int(claimed2[0]["attempts"]) >= 2
    assert worker_db.fail_job(
        claimed2[0]["id"], "w1", error="boom2", retry=True
    )
    row2 = worker_db.get_job(job["id"])
    assert row2["status"] == "failed"


def test_heartbeat_stores_progress(worker_db):
    job = worker_db.submit_job(job_type="ping", params={"echo": "p"})
    claimed = worker_db.claim_jobs(
        worker_id="w1", capabilities={}, limit=1, lease_seconds=60
    )
    assert claimed
    ok = worker_db.heartbeat_job(
        job["id"],
        "w1",
        lease_seconds=60,
        progress={"units_done": 3, "units_total": 10, "last_unit_id": "3"},
    )
    assert ok
    row = worker_db.get_job(job["id"])
    assert row["status"] == "running"
    assert row["progress"]["units_done"] == 3
    assert row["progress"]["last_unit_id"] == "3"


def test_fail_job_params_update_on_requeue(worker_db):
    job = worker_db.submit_job(
        job_type="blender_render",
        params={
            "blend_file": r"G:\a.blend",
            "output_dir": r"G:\out",
            "frame_start": 1,
            "frame_end": 10,
        },
        requirements={"blender": True},
        max_attempts=5,
    )
    worker_db.upsert_worker(
        worker_id="gpu",
        capabilities={"blender": True},
    )
    claimed = worker_db.claim_jobs(
        worker_id="gpu",
        capabilities={"blender": True},
        limit=1,
        lease_seconds=600,
    )
    assert claimed
    assert worker_db.fail_job(
        job["id"],
        "gpu",
        error="idle timeout",
        retry=True,
        params_update={"frame_start": 7, "frame_end": 10},
        partial_result={"frames_written": 6},
    )
    row = worker_db.get_job(job["id"])
    assert row["status"] == "queued"
    assert row["params"]["frame_start"] == 7
    assert row["params"]["frame_end"] == 10
    assert row["result"]["partial"] is True
    assert row["result"]["frames_written"] == 6


def test_resolve_timeouts_generic():
    from api.device_workers.job_policy import resolve_timeouts

    idle, hard, mode = resolve_timeouts(job_type="blender_render", units=4)
    assert idle == 45 * 60
    assert hard == 24 * 3600
    assert mode == "units"

    idle2, hard2, mode2 = resolve_timeouts(job_type="shell")
    assert idle2 == 0
    assert hard2 == 4 * 3600
    assert mode2 == "alive"

    # Legacy timeout_seconds raises hard floor
    _, hard3, _ = resolve_timeouts(
        job_type="shell", params={"timeout_seconds": 7200}
    )
    assert hard3 >= 7200

    # Generic secs_per_unit_hint (no engine name)
    _, hard4, _ = resolve_timeouts(
        job_type="blender_render",
        params={"secs_per_unit_hint": 600},
        units=10,
    )
    assert hard4 >= 10 * 600


def test_long_run_idle_and_hard(tmp_path):
    import sys

    from api.device_workers.long_run import (
        HardTimeoutError,
        IdleTimeoutError,
        ProgressSnapshot,
        run_long_process,
    )

    # Idle: units never advance
    with pytest.raises(IdleTimeoutError):
        run_long_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            hard_timeout_seconds=60,
            idle_timeout_seconds=1,
            progress_mode="units",
            progress_poll=lambda: ProgressSnapshot(units_done=0, units_total=5),
            poll_interval=0.2,
        )

    # Hard: alive mode with tiny hard cap
    with pytest.raises(HardTimeoutError):
        run_long_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            hard_timeout_seconds=1,
            idle_timeout_seconds=0,
            progress_mode="alive",
            poll_interval=0.2,
        )

    # Success
    res = run_long_process(
        [sys.executable, "-c", "print('ok')"],
        hard_timeout_seconds=30,
        idle_timeout_seconds=0,
        progress_mode="alive",
        poll_interval=0.2,
    )
    assert res.returncode == 0
    assert "ok" in res.stdout


def test_gap_fill_batch_missing_frames(tmp_path, worker_db, monkeypatch):
    import api.device_workers.platform as plat

    monkeypatch.setattr(plat, "get_store", lambda: worker_db)
    out = tmp_path / "frames"
    out.mkdir()
    # Pretend frames 1 and 2 landed; 3-5 missing
    (out / "frame_0001.png").write_bytes(b"x")
    (out / "frame_0002.png").write_bytes(b"x")

    worker_db.upsert_worker(
        worker_id="gpu",
        capabilities={"blender": True},
    )
    worker_db.submit_job(
        job_type="blender_render",
        params={
            "blend_file": str(tmp_path / "a.blend"),
            "output_dir": str(out),
            "frame_start": 1,
            "frame_end": 5,
            "batch_id": "gap-demo",
            "chunk_index": 0,
        },
        requirements={"blender": True},
        submitted_by="test",
    )
    jobs = worker_db.list_jobs(limit=10)
    jid = jobs[0]["id"]
    claimed = worker_db.claim_jobs(
        worker_id="gpu",
        capabilities={"blender": True},
        limit=1,
        lease_seconds=60,
    )
    assert claimed
    worker_db.fail_job(jid, "gpu", error="timeout", retry=False)

    monkeypatch.setattr(
        "api.device_workers.executor.path_allowed", lambda *a, **k: True
    )

    dry = plat.gap_fill_batch("gap-demo", chunk_size=1, dry_run=True)
    assert dry["success"]
    assert dry["missing_count"] == 3
    assert len(dry["ranges"]) == 3

    live = plat.gap_fill_batch("gap-demo", chunk_size=1, submitted_by="test")
    assert live["success"]
    assert live["shard_count"] == 3
    queued = [j for j in worker_db.list_jobs(limit=50) if j["status"] == "queued"]
    assert len(queued) >= 3


def test_partial_retry_error_fields():
    from api.device_workers.executor import PartialRetryError

    err = PartialRetryError(
        "shrink",
        params_update={"frame_start": 5, "frame_end": 8},
        partial_result={"frames_written": 4},
    )
    assert err.params_update["frame_start"] == 5
    assert err.partial_result["frames_written"] == 4


def test_frame_grid_inventory_retry_and_missing_output(tmp_path):
    from api.device_workers.platform import build_batch_frame_grid, build_batch_watch_bars
    out = tmp_path / 'frames'
    out.mkdir()
    (out / 'frame_0001.png').write_bytes(b'x')
    original = {'status': 'succeeded', 'claimed_by': 'tower', 'attempts': 1,
                'params': {'output_dir': str(out), 'frame_start': 1, 'frame_end': 3}}
    gap = {'status': 'running', 'claimed_by': 'laptop', 'attempts': 1,
           'params': {'output_dir': str(out), 'frame_start': 2, 'frame_end': 2, 'gap_fill': True}}
    summary = {'jobs': [original, gap]}
    grid = build_batch_frame_grid(summary)
    assert [(c['state'], c['group']) for c in grid['cells']] == [
        ('completed', 'tower'), ('running', 'laptop'), ('missing', '')]
    assert grid['cells'][1]['marked']
    assert (grid['unit'], grid['marked_label']) == ('frame', 'gap-fill')
    assert grid['inventory'] == 'verified'
    incomplete = build_batch_watch_bars({'jobs': [original]})
    assert incomplete['state'] == 'failed'
    assert incomplete['percent'] == 33
    original['attempts'] = 2
    assert build_batch_frame_grid({'jobs': [original]})['cells'][0]['group'] == ''
    original['result'] = {'frame_times': [{'frame': 1, 'seconds': 2}]}
    assert build_batch_frame_grid({'jobs': [original]})['cells'][0]['group'] == 'tower'


def test_frame_grid_remote_and_bound(tmp_path):
    from api.device_workers.platform import build_batch_frame_grid, build_batch_watch_bars
    job = {'status': 'succeeded', 'claimed_by': 'tower',
           'params': {'output_dir': str(tmp_path / 'absent'), 'frame_start': 1, 'frame_end': 3000}}
    grid = build_batch_frame_grid({'jobs': [job, dict(job)]})
    assert len(grid['cells']) == 2048
    assert grid['omitted'] == 952
    assert grid['total'] == 3000
    assert grid['inventory'] == 'reported'
    assert grid['cells'][0]['state'] == 'completed'
    assert grid['cells'][0]['group'] == ''  # overlapping claims are ambiguous
    built = build_batch_watch_bars({'jobs': [job, dict(job)]})
    assert built['frames_done'] == 3000
    assert built['frames_total'] == 3000


def test_batch_inventory_exceeds_recent_job_limit(worker_db):
    from api.device_workers.platform import batch_status
    for n in range(205):
        worker_db.submit_job(job_type='blender_render', params={
            'batch_id': 'large', 'frame_start': n + 1, 'frame_end': n + 1})
    worker_db.submit_job(job_type='blender_render', params={'batch_id': 'other'})
    assert len(worker_db.list_jobs(limit=200)) == 200
    summary = batch_status('large')
    assert summary['job_count'] == 205
    assert summary['pending'] == 205


def test_batch_watch_grid_flag_and_payload(tmp_path, monkeypatch):
    from api.device_workers import platform as plat
    from api import job_watch
    import json
    summary = {'success': True, 'batch_id': 'grid', 'pending': 1, 'jobs': [
        {'status': 'queued', 'params': {'frame_start': 1, 'frame_end': 2}}]}
    monkeypatch.setattr(plat, 'batch_status', lambda _: summary)
    monkeypatch.setattr(job_watch, 'output_dir', lambda: tmp_path)
    monkeypatch.setattr(plat, '_frame_grid_enabled', lambda: False)
    plat.write_batch_watch('grid')
    assert 'grid' not in json.loads((tmp_path / 'grid-status.json').read_text(encoding="utf-8"))
    monkeypatch.setattr(plat, '_frame_grid_enabled', lambda: True)
    monkeypatch.setattr(job_watch, 'grid_enabled', lambda: True)
    plat.write_batch_watch('grid')
    payload = json.loads((tmp_path / 'grid-status.json').read_text(encoding="utf-8"))
    assert payload['bars'][0]['id'] == 'overall'
    assert len(payload['grid']['cells']) == 2



def test_watch_grid_persisted_snapshot_sanitization():
    from api.action_forms import merge_watch_snapshot_into_spec
    raw = {'cells': [{'frame': 1, 'state': 'completed', 'worker': 'tower'},
                     {'frame': 1, 'state': 'failed'}, {'frame': True, 'state': 'missing'},
                     {'frame': '', 'state': 'missing'},
                     {'frame': 2, 'state': []}], 'total': 2, 'workers': ['tower'], 'inventory': 'verified'}
    spec = merge_watch_snapshot_into_spec({'watch': {'id': 'b', 'url': '/output/b.json'}},
                                         snapshot={'state': 'done', 'grid': raw}, terminal=True)
    grid = spec['watch']['snapshot']['grid']
    assert len(grid['cells']) == 2
    assert grid['cells'][0]['group'] == 'tower'  # legacy worker key
    assert grid['cells'][1]['state'] == 'pending'
    assert grid['groups'] == ['tower'] and grid['inventory'] == 'verified'
    assert spec['watch']['terminal']
