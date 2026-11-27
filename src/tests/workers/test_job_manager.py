"""Job Manager on the mesh: telemetry v2, admission + reservations, memory scopes,
the ComfyUI adapter, and runs (plan → approve → live watch → one resume).

Design: ``docs/guides/JOB_MANAGER.md``.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys

import pytest

from api.device_workers import admission, capabilities, isolation, run_watch
from api.device_workers.adapters import comfyui


@pytest.fixture()
def worker_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_DB", str(tmp_path / "device_workers.db"))
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_ENABLED", "1")
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    (tmp_path / "home" / "output").mkdir(parents=True, exist_ok=True)
    import api.device_workers.store as store_mod

    store_mod._store = None
    yield store_mod.get_store()
    store_mod._store = None


def _load(*, ram_total=32.0, ram_available=24.0, vram_total=24576, vram_used=1000, **extra):
    return {"ram_total_gb": ram_total, "ram_available_gb": ram_available,
            "gpus": [{"index": 0, "name": "RTX 3090", "vram_total_mb": vram_total, "vram_used_mb": vram_used}],
            **extra}


def _register(store, wid="desk", **load):
    store.upsert_worker(worker_id=wid, hostname=wid, capabilities={"shell": True}, load=_load(**load))


# ── J0 telemetry ─────────────────────────────────────────────────────────────

def test_parsers_read_recorded_nvidia_smi_and_psi_output():
    gpus = capabilities.parse_nvidia_gpus(
        "0, GPU-aaa, NVIDIA GeForce RTX 3090, 1412, 24576, 7\n1, GPU-bbb, RTX 5050 Laptop, 300, 8151, [N/A]\n")
    assert gpus[0] == {"index": 0, "uuid": "GPU-aaa", "name": "NVIDIA GeForce RTX 3090",
                       "vram_used_mb": 1412, "vram_total_mb": 24576, "util_pct": 7.0}
    assert gpus[1]["util_pct"] is None
    assert capabilities.parse_nvidia_apps("GPU-aaa, 4242, 9120\n") == [
        {"gpu_uuid": "GPU-aaa", "pid": 4242, "used_mb": 9120}]
    psi = capabilities.parse_psi("some avg10=12.50 avg60=3.00 avg300=1.00 total=1\n"
                                 "full avg10=4.25 avg60=0.00 avg300=0.00 total=2\n")
    assert psi == {"some": 12.5, "full": 4.25}


def test_services_are_recognised_by_command_line():
    assert capabilities.classify_service(["python", "/mnt/galaxy/AI/ComfyUI/main.py", "--listen"]) == "comfyui"
    assert capabilities.classify_service(["python", "-m", "http.server"]) == ""


# ── J1 admission ─────────────────────────────────────────────────────────────

def test_admission_takes_the_smaller_of_budget_and_live_headroom():
    need = admission.requested({"resources": {"ram_mb": 8000, "vram_mb": 7000}})
    assert need == {"ram_mb": 8000, "vram_mb": 7000, "gpu_slots": 1}
    res, why = admission.admit(need, _load(), [], telemetry_age=5)
    assert why == "" and res == {"ram_mb": 8000, "gpu_index": 0, "vram_mb": 7000, "gpu_slots": 1}
    # Live RAM is short (a Unity Editor took it) even though the budget is free.
    res, why = admission.admit(need, _load(ram_available=6.0), [], telemetry_age=5)
    assert res is None and "GB RAM" in why
    # VRAM held by a game.
    res, why = admission.admit(need, _load(vram_used=20000), [], telemetry_age=5)
    assert res is None and "VRAM" in why
    # One GPU-heavy job per GPU.
    res, why = admission.admit(need, _load(), [{"gpu_index": 0, "vram_mb": 1, "gpu_slots": 1}], telemetry_age=5)
    assert res is None and why == "GPU busy with another job"
    res, why = admission.admit(need, _load(), [], telemetry_age=600)
    assert res is None and "telemetry" in why
    assert admission.admit({}, {}, [], telemetry_age=1e9) == ({}, "")


def test_memory_a_service_already_holds_counts_as_free_for_its_own_jobs():
    load = _load(ram_available=6.0, vram_used=18000,
                 services={"comfyui": {"rss_mb": 9000, "vram_mb": {"0": 16000}}})
    plain = admission.requested({"resources": {"ram_mb": 8000, "vram_mb": 7000}})
    inside = admission.requested({"resources": {"ram_mb": 8000, "vram_mb": 7000, "service": "comfyui"}})
    assert admission.admit(plain, load, [], telemetry_age=1)[0] is None
    res, _ = admission.admit(inside, load, [], telemetry_age=1)
    assert res and res["service"] == "comfyui"


def test_claim_reserves_and_waits_with_a_reason(worker_db):
    _register(worker_db, ram_total=24.0, ram_available=22.0)  # budget 20 GB
    ids = [worker_db.submit_job(job_type="ping", requirements={"resources": {"ram_mb": 8192}})["id"]
           for _ in range(3)]
    got = worker_db.claim_jobs(worker_id="desk", limit=5)
    assert [j["id"] for j in got] == ids[:2]
    assert got[0]["reserved"] == {"ram_mb": 8192}
    waiting = worker_db.get_job(ids[2])
    assert waiting["status"] == "queued" and "desk: needs 8.0 GB RAM" in waiting["wait_reason"]
    # An ended job releases its reservation: the third one is admitted next.
    worker_db.complete_job(ids[0], "desk", result={})
    assert [j["id"] for j in worker_db.claim_jobs(worker_id="desk", limit=5)] == [ids[2]]
    assert worker_db.get_job(ids[2])["wait_reason"] is None


def test_cancel_releases_the_reservation_and_refuses_the_heartbeat(worker_db):
    _register(worker_db)
    a = worker_db.submit_job(job_type="ping", requirements={"resources": {"vram_mb": 6000}})["id"]
    b = worker_db.submit_job(job_type="ping", requirements={"resources": {"vram_mb": 6000}})["id"]
    assert [j["id"] for j in worker_db.claim_jobs(worker_id="desk", limit=5)] == [a]  # one GPU slot
    worker_db.cancel_job(a)
    assert worker_db.heartbeat_job(a, "desk") is False  # → the worker's _cancelled flag
    assert [j["id"] for j in worker_db.claim_jobs(worker_id="desk", limit=5)] == [b]


def _claim_in_process(db_path, out):
    os.environ["CUTTLE_DEVICE_WORKERS_DB"] = db_path
    from api.device_workers.store import DeviceWorkerStore

    out.put([j["id"] for j in DeviceWorkerStore().claim_jobs(worker_id="desk", limit=5)])


def test_concurrent_claims_from_separate_processes_never_oversubscribe(worker_db):
    _register(worker_db, ram_total=24.0, ram_available=22.0)
    for _ in range(12):
        worker_db.submit_job(job_type="ping", requirements={"resources": {"ram_mb": 8192}})
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    procs = [ctx.Process(target=_claim_in_process, args=(str(worker_db.db_path), out)) for _ in range(8)]
    for p in procs:
        p.start()
    claimed = [jid for _ in procs for jid in out.get(timeout=60)]
    for p in procs:
        p.join(timeout=30)
    assert len(claimed) == len(set(claimed)) == 2  # 20 GB budget holds two 8 GB jobs


# ── J2 memory scopes ─────────────────────────────────────────────────────────

def test_scoped_argv_wraps_only_jobs_that_declare_ram(monkeypatch):
    monkeypatch.setattr(isolation, "available", lambda: True)
    argv, scope = isolation.scoped_argv(["blender", "-b"], job_id="abc", ram_mb=4096)
    assert scope == "cuttle-job-abc.scope"
    assert argv[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
    assert "MemoryMax=5120M" in argv and argv[-2:] == ["blender", "-b"]
    assert isolation.scoped_argv(["x"], job_id="abc", ram_mb=0) == (["x"], "")


def _user_systemd() -> bool:
    if not isolation.available():
        return False
    return subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True).returncode in (0, 1)


@pytest.mark.skipif(not _user_systemd() or os.environ.get("CUTTLE_TEST_MEMORY_SCOPE") != "1",
                    reason="real memory-scope test: set CUTTLE_TEST_MEMORY_SCOPE=1 on a systemd host")
def test_a_job_past_its_scope_is_oom_killed_alone():
    hog = [sys.executable, "-c", "b = bytearray(3 * 1024**3); import time; time.sleep(5)"]
    argv, scope = isolation.scoped_argv(hog, job_id=f"oomtest-{os.getpid()}", ram_mb=256)
    done = subprocess.run(argv, capture_output=True)
    assert done.returncode != 0
    assert isolation.oom_killed(scope)


# ── ComfyUI adapter ──────────────────────────────────────────────────────────

PROMPT = {"3": {"class_type": "KSampler", "inputs": {}}, "7": {"class_type": "VAEDecode", "inputs": {}},
          "9": {"class_type": "SaveImage", "inputs": {}, "_meta": {"title": "Save hero"}}}


def test_validate_refuses_non_loopback_servers_and_ui_format_graphs():
    assert comfyui.validate({"prompt": PROMPT}) == (True, "")
    assert not comfyui.validate({"prompt": PROMPT, "server": "http://10.0.0.5:8188"})[0]
    assert not comfyui.validate({"prompt": {"nodes": []}})[0]


def test_prompt_id_is_stable_per_job():
    hexid = "0123456789abcdef0123456789abcdef"
    assert comfyui.prompt_id_for(hexid) == "01234567-89ab-cdef-0123-456789abcdef"
    assert comfyui.prompt_id_for("run-1") == comfyui.prompt_id_for("run-1")


def _events(pid):
    return [
        {"type": "status", "data": {"status": {}}},
        {"type": "execution_start", "data": {"prompt_id": pid}},
        {"type": "execution_cached", "data": {"prompt_id": pid, "nodes": []}},
        {"type": "executing", "data": {"prompt_id": pid, "node": "3"}},
        {"type": "progress", "data": {"prompt_id": pid, "node": "3", "value": 18, "max": 30}},
        {"type": "progress", "data": {"prompt_id": "someone-else", "value": 1, "max": 2}},
        {"type": "executing", "data": {"prompt_id": pid, "node": "7"}},
        {"type": "executing", "data": {"prompt_id": pid, "node": "9"}},
        {"type": "executed", "data": {"prompt_id": pid, "node": "9",
                                      "output": {"images": [{"filename": "hero.png", "subfolder": "", "type": "output"}]}}},
        {"type": "execution_success", "data": {"prompt_id": pid}},
    ]


def test_tracker_turns_events_into_node_progress_and_timings():
    clock = iter(range(100, 200))
    t = comfyui.Tracker("p1", comfyui.stage_labels(PROMPT), now=lambda: float(next(clock)))
    assert t.progress()["message"] == "waiting for ComfyUI"
    events = _events("p1")
    for e in events[:5]:
        t.handle(e, {"vram_used_mb": 9000})
    p = t.progress()
    assert p["message"] == "KSampler 18/30" and p["stage"] == "KSampler" and (p["step"], p["steps"]) == (18, 30)
    assert t.handle(events[5]) is False  # another client's prompt
    for e in events[6:]:
        t.handle(e, {"vram_used_mb": 9500})
    assert t.outcome == "success" and t.progress()["units_done"] == 3
    assert [x["label"] for x in t.timings] == ["KSampler", "VAEDecode", "Save hero"]
    assert t.timings[0]["peak_vram_mb"] == 9500


def test_tracker_reports_the_failing_node():
    t = comfyui.Tracker("p1", comfyui.stage_labels(PROMPT))
    t.handle({"type": "executing", "data": {"prompt_id": "p1", "node": "7"}})
    t.handle({"type": "execution_error", "data": {"prompt_id": "p1", "node_id": "7", "node_type": "VAEDecode",
                                                  "exception_message": "CUDA out of memory"}})
    assert t.outcome == "error" and t.error == "VAEDecode: CUDA out of memory"


def test_preview_frames_decode():
    import struct

    assert comfyui.decode_preview(struct.pack(">II", 1, 1) + b"JPG") == ("jpg", b"JPG")
    meta = json.dumps({"image_type": "image/png"}).encode()
    assert comfyui.decode_preview(struct.pack(">II", 4, len(meta)) + meta + b"PNG") == ("png", b"PNG")
    assert comfyui.decode_preview(struct.pack(">II", 3, 0)) is None


class _Resp:
    def __init__(self, data, ok=True):
        self._data, self.ok, self.headers, self.text = data, ok, {"content-type": "application/json"}, ""

    def json(self):
        return self._data


class FakeComfy:
    """Recorded-session stand-in for ComfyUI's HTTP + WebSocket API."""

    def __init__(self, events=None, history=None, queue=None):
        self.events, self.history, self.queue = list(events or []), dict(history or {}), queue or {}
        self.posts = []

    def get(self, url, timeout=0):
        if url.endswith("/system_stats"):
            return _Resp({})
        if "/history/" in url:
            pid = url.rsplit("/", 1)[1]
            return _Resp({pid: self.history[pid]} if pid in self.history else {})
        return _Resp(self.queue)

    def post(self, url, json=None, timeout=0):
        self.posts.append((url.split("8188", 1)[1], json))
        return _Resp({"prompt_id": (json or {}).get("prompt_id"), "number": 1, "node_errors": {}})

    def connect(self, url, **_kw):
        fake = self

        class WS:
            def recv(self, timeout=None):
                if fake.events:
                    return json.dumps(fake.events.pop(0))
                raise TimeoutError

            def close(self):
                pass

        return WS()


@pytest.fixture()
def fake_comfy(monkeypatch):
    def install(**kw):
        fake = FakeComfy(**kw)
        monkeypatch.setattr(comfyui.requests, "get", fake.get)
        monkeypatch.setattr(comfyui.requests, "post", fake.post)
        import websockets.sync.client as wsc

        monkeypatch.setattr(wsc, "connect", fake.connect)
        monkeypatch.setattr(comfyui, "_sample", lambda: {"vram_used_mb": 8000, "ram_available_mb": 20000})
        monkeypatch.setattr(comfyui, "_preview_target", lambda job_id: (None, ""))
        return fake
    return install


JOB = "0123456789abcdef0123456789abcdef"
PID = comfyui.prompt_id_for(JOB)


def test_run_submits_follows_events_and_collects_outputs(fake_comfy):
    done = {"status": {"status_str": "success"},
            "outputs": {"9": {"images": [{"filename": "hero.png", "subfolder": "", "type": "output"}]}}}
    fake = fake_comfy(events=_events(PID))
    seen = []
    orig_get = fake.get

    def get(url, timeout=0):  # history appears once the prompt has run
        if not fake.events:
            fake.history[PID] = done
        return orig_get(url, timeout)

    comfyui.requests.get = get
    result = comfyui.run({"prompt": PROMPT, "_job_id": JOB, "_on_progress": seen.append})
    assert fake.posts[0][0] == "/prompt" and fake.posts[0][1]["prompt_id"] == PID
    assert any(p.get("message") == "KSampler 18/30" for p in seen)
    assert result["files"] == [{"node": "9", "kind": "images", "filename": "hero.png", "subfolder": "", "type": "output"}]
    assert [t["label"] for t in result["node_timings"]] == ["KSampler", "VAEDecode", "Save hero"]


def test_a_retry_reattaches_instead_of_running_the_graph_twice(fake_comfy):
    fake = fake_comfy(history={PID: {"status": {"status_str": "success"}, "outputs": {}}})
    result = comfyui.run({"prompt": PROMPT, "_job_id": JOB})
    assert result["ok"] and not [p for p in fake.posts if p[0] == "/prompt"]


def test_cancel_cancels_the_comfyui_prompt(fake_comfy):
    from api.device_workers.executor import JobExecError

    fake = fake_comfy(events=[{"type": "execution_start", "data": {"prompt_id": PID}}])
    with pytest.raises(JobExecError, match="cancelled"):
        comfyui.run({"prompt": PROMPT, "_job_id": JOB, "_cancelled": lambda: True})
    assert ("/api/jobs/" + PID + "/cancel", None) in fake.posts


def test_execution_error_fails_the_job_with_the_node(fake_comfy):
    from api.device_workers.executor import JobExecError

    fake_comfy(events=[{"type": "executing", "data": {"prompt_id": PID, "node": "7"}},
                       {"type": "execution_error", "data": {"prompt_id": PID, "node_type": "VAEDecode",
                                                            "exception_message": "CUDA out of memory"}}])
    with pytest.raises(JobExecError, match="VAEDecode: CUDA out of memory"):
        comfyui.run({"prompt": PROMPT, "_job_id": JOB})


# ── Runs: summary, plan → approve → watch → resume ──────────────────────────

def _job(status, **kw):
    return {"id": kw.pop("id", status), "type": "comfyui_prompt", "status": status,
            "params": {"template": "hunyuan", "label": kw.pop("label", status)}, **kw}


def test_summary_reports_percent_eta_and_errors():
    now = 1000.0
    jobs = [_job("succeeded", id="a"),
            _job("running", id="b", claimed_by="desk", started_at=now - 30,
                 progress={"units_done": 1, "units_total": 4, "step": 15, "steps": 30, "message": "KSampler 15/30"}),
            _job("queued", id="c", wait_reason="desk: needs 7.0 GB VRAM, 3.1 GB free"),
            _job("failed", id="d", error="oom: recipe went past its scope")]
    s = run_watch.summarize(jobs, now=now, started_ts=now - 300, history={"hunyuan": 120.0})
    assert s["state"] == "running" and s["percent"] == round(100 * (1 + 0.375 + 0 + 1) / 4)
    assert "1/4 done" in s["label"] and "1 failed" in s["label"] and "KSampler 15/30" in s["label"]
    assert s["eta_s"] == int(30 * (1 - 0.375) / 0.375 + 120)
    assert s["errors"][0]["oom"] is True
    assert [c["state"] for c in s["grid"]["cells"]] == ["completed", "running", "pending", "failed"]
    assert s["activity"]["steps"][2]["detail"].startswith("waiting — desk: needs 7.0 GB VRAM")
    final = run_watch.summarize([_job("succeeded"), _job("failed", id="x")], now=now, started_ts=now)
    assert final["state"] == "failed" and final["eta_s"] is None


SPEC = {"title": "Ping pair", "resume_message": "Both pings ended; report them.",
        "jobs": [{"type": "ping", "label": "one", "requirements": {"resources": {"ram_mb": 2048}}},
                 {"type": "ping", "label": "two"}]}


def test_plan_approve_watch_and_resume_once(worker_db):
    from api import job_watch as jw
    from api.chat_ops import store as ops
    from api.device_workers import runs

    _register(worker_db)
    planned = runs.plan(SPEC, chat_session_id="CH-002819")
    assert planned["success"] and planned["workers"][0]["fits_now"] == 2
    ids = [o["id"] for o in planned["form"]["options"]]
    assert ids == ["auto", "on-desk", "not-now"]
    params = planned["form"]["options"][0]["params"]
    assert params["spec_sha256"] == planned["spec_sha256"]

    assert not runs.submit(params["run_id"], spec_sha256="0" * 64)["success"]  # tampered / stale approval
    sub = runs.submit(params["run_id"], spec_sha256=params["spec_sha256"])
    assert sub["success"] and len(sub["jobs"]) == 2
    assert not runs.submit(params["run_id"], spec_sha256=params["spec_sha256"])["success"]  # once

    assert jw.poll_unit_watches() == []
    running = jw.read_status(params["run_id"])
    assert running["state"] == "running" and running["mesh_jobs"] == sub["jobs"]
    assert running["activity"]["steps"][0]["label"] == "one"

    for job in worker_db.claim_jobs(worker_id="desk", limit=5):
        worker_db.complete_job(job["id"], "desk", result={"ok": True})
    done = jw.poll_unit_watches()
    assert done and done[0]["state"] == "done"
    final = jw.read_status(params["run_id"])
    assert final["state"] == "done" and final["percent"] == 100 and final["bars"][0]["percent"] == 100
    notices = [o for o in ops.listing(2819) if o["kind"] == "notice"]
    assert len(notices) == 1
    assert "2 succeeded, 0 failed, 0 cancelled" in notices[0]["payload"]["text"]
    assert "Both pings ended; report them." in notices[0]["payload"]["text"]
    assert jw.poll_unit_watches() == []


def test_stop_on_the_card_cancels_the_run_without_resuming(worker_db):
    from api import job_watch as jw
    from api.chat_ops import store as ops
    from api.device_workers import runs

    _register(worker_db)
    planned = runs.plan(SPEC, chat_session_id="CH-002819")
    p = planned["form"]["options"][0]["params"]
    sub = runs.submit(p["run_id"], spec_sha256=p["spec_sha256"])
    out = jw.cancel_job(p["run_id"])
    assert out["ok"] and out["killed"] == 2
    assert {worker_db.get_job(j)["status"] for j in sub["jobs"]} == {"cancelled"}
    resolved = jw.poll_unit_watches()
    assert resolved and resolved[0]["resume"] == "cancelled"
    assert not [o for o in ops.listing(2819) if o["kind"] == "notice"]


def test_heartbeats_refresh_the_card_between_polls(worker_db):
    from api import job_watch as jw
    from api.device_workers import runs

    _register(worker_db)
    p = runs.plan(SPEC, chat_session_id="CH-002819")["form"]["options"][0]["params"]
    runs.submit(p["run_id"], spec_sha256=p["spec_sha256"])
    job = worker_db.claim_jobs(worker_id="desk", limit=1)[0]
    worker_db.heartbeat_job(job["id"], "desk", progress={"units_done": 1, "units_total": 2, "message": "half"})
    run_watch._touched.clear()
    run_watch.touch(job["id"])
    assert "half" in jw.read_status(p["run_id"])["label"]


def test_websockets_sync_client_is_available():
    # comfyui_prompt needs it on every worker, packaged ones included (requirements.txt + lock).
    import websockets.sync.client  # noqa: F401
