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
    # started from its own folder (start-comfyui.sh): only the working directory says so
    assert capabilities.classify_service([".venv/bin/python", "main.py", "--port", "8188"],
                                         "/mnt/galaxy/AI/ComfyUI") == "comfyui"
    assert capabilities.classify_service(["python", "main.py"], "/home/me/other-app") == ""


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


def test_open_editors_keep_burst_room_and_refusals_name_what_uses_memory():
    load = _load(ram_total=32.0, ram_available=14.0,
                 interactive=[{"name": "Unity Editor", "rss_mb": 6144}],
                 heavy=[{"name": "Unity Editor", "rss_mb": 6144, "vram_mb": 900, "kind": "interactive"},
                        {"name": "blender", "rss_mb": 3500, "vram_mb": 2100, "kind": "headless"},
                        {"name": "python3", "rss_mb": 1500, "vram_mb": 0, "kind": "external"}])
    # 14 GB free − 1 GB margin − 3 GB kept for the open Unity Editor = 10 GB.
    assert admission.admit({"ram_mb": 10 * 1024}, load, [], telemetry_age=1)[0] is not None
    res, why = admission.admit({"ram_mb": 11 * 1024}, load, [], telemetry_age=1)
    assert res is None and why.startswith("needs 11.0 GB RAM, 10.0 GB free")
    assert "keeping room for Unity Editor" in why
    assert "Unity Editor 6.0 GB" in why and "blender 3.4 GB (headless)" in why
    res, why = admission.admit({"vram_mb": 21 * 1024}, load, [], telemetry_age=1)
    assert res is None and "blender 2.1 GB (headless)" in why


def test_heavy_processes_are_tagged_by_owner(monkeypatch):
    import psutil

    class P:
        def __init__(self, pid, name, cmd, rss):
            self.info = {"pid": pid, "name": name, "cmdline": cmd,
                         "memory_info": type("M", (), {"rss": rss * 1024**2})()}

        def cwd(self):
            return "/ai/ComfyUI"

    procs = [P(1, "Unity Main Thre", ["/opt/Unity/Hub/Editor/6000.6.3f1/Editor/Unity", "-projectPath", "/g"], 6000), P(2, "blender", ["blender", "-b", "x.blend"], 3000),
             P(3, "python3", ["python3", "main.py"], 9000), P(4, "python3", ["python3", "hog.py"], 200),
             P(5, "bash", ["bash"], 10)]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: procs)
    monkeypatch.setattr(capabilities, "_in_job_scope", lambda pid: False)
    gpus = [{"index": 0, "uuid": "GPU-a"}]
    apps = [{"gpu_uuid": "GPU-a", "pid": 4, "used_mb": 8000}, {"gpu_uuid": "GPU-a", "pid": 3, "used_mb": 5000}]
    interactive, services, heavy = capabilities._processes(apps, gpus)
    assert interactive == [{"name": "Unity Editor", "rss_mb": 6000}]
    assert services["comfyui"]["vram_mb"] == {"0": 5000}
    kinds = {h["pid"]: h["kind"] for h in heavy}
    assert kinds == {1: "interactive", 2: "headless", 3: "service:comfyui", 4: "external"}  # bash is not heavy


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


def _gpu_pair(store):
    a = store.submit_job(job_type="ping", requirements={"resources": {"vram_mb": 6000}})["id"]
    b = store.submit_job(job_type="ping", requirements={"resources": {"vram_mb": 6000}})["id"]
    assert [j["id"] for j in store.claim_jobs(worker_id="desk", limit=5)] == [a]  # one GPU slot
    return a, b


def test_a_cancel_holds_the_reservation_until_the_worker_confirms(worker_db):
    _register(worker_db)
    a, b = _gpu_pair(worker_db)
    worker_db.cancel_job(a)
    assert worker_db.heartbeat_job(a, "desk") is False  # → the worker's _cancelled flag
    # Cancelled, but the workload may still be running: the GPU slot stays taken.
    assert worker_db.get_job(a)["held_by"] == "desk"
    assert worker_db.claim_jobs(worker_id="desk", limit=5) == []
    assert [j["id"] for j in worker_db.held_after_end("desk")] == [a]
    # The worker stopped it and reports (its fail on a cancelled job): released.
    assert worker_db.fail_job(a, "desk", error="cancelled") is False
    assert worker_db.get_job(a)["held_by"] is None and worker_db.get_job(a)["status"] == "cancelled"
    assert [j["id"] for j in worker_db.claim_jobs(worker_id="desk", limit=5)] == [b]


def test_an_unconfirmed_stop_keeps_the_reservation(worker_db):
    _register(worker_db)
    a, b = _gpu_pair(worker_db)
    assert worker_db.fail_job(a, "desk", error="ComfyUI did not confirm the cancel", keep_hold=True)
    assert worker_db.get_job(a)["status"] == "failed" and worker_db.get_job(a)["held_by"] == "desk"
    assert worker_db.claim_jobs(worker_id="desk", limit=5) == []
    assert worker_db.release_hold(a, "lap") is False  # only the holder can release
    assert worker_db.release_hold(a, "desk") is True
    assert [j["id"] for j in worker_db.claim_jobs(worker_id="desk", limit=5)] == [b]


def test_a_lost_lease_requeues_pinned_to_the_worker_that_may_still_run_it(worker_db):
    _register(worker_db)
    _register(worker_db, wid="lap")
    a, _b = _gpu_pair(worker_db)
    with worker_db_conn(worker_db) as conn:
        conn.execute("UPDATE jobs SET claim_expires = 1 WHERE id = ?", (a,))
    worker_db.reclaim_expired()
    job = worker_db.get_job(a)
    assert job["status"] == "queued" and job["held_by"] == "desk"
    assert a not in [j["id"] for j in worker_db.claim_jobs(worker_id="lap", limit=5)]  # never twice
    # The holder re-claims its own job (the adapter reattaches) without counting itself twice.
    assert a in [j["id"] for j in worker_db.claim_jobs(worker_id="desk", limit=5)]


def test_a_holder_gone_for_half_an_hour_releases_its_jobs(worker_db):
    from api.device_workers import store as store_mod

    _register(worker_db)
    _register(worker_db, wid="lap")
    a, _b = _gpu_pair(worker_db)
    worker_db.cancel_job(a)
    with worker_db_conn(worker_db) as conn:
        conn.execute("UPDATE workers SET last_seen = ? WHERE worker_id = 'desk'",
                     (__import__("time").time() - store_mod.HOLD_OFFLINE_RELEASE_SEC - 5,))
    worker_db.reclaim_expired()
    assert worker_db.get_job(a)["held_by"] is None


def test_a_restarted_worker_gets_its_jobs_back_at_once(worker_db):
    caps = {"shell": True}
    worker_db.upsert_worker(worker_id="desk", capabilities=caps, load=_load(), meta={"worker_run": "one"})
    a, _b = _gpu_pair(worker_db)
    worker_db.heartbeat_job(a, "desk")
    worker_db.upsert_worker(worker_id="desk", capabilities=caps, load=_load(), meta={"worker_run": "one"})
    assert worker_db.get_job(a)["status"] == "running"  # same process: nothing happens
    worker_db.upsert_worker(worker_id="desk", capabilities=caps, load=_load(), meta={"worker_run": "two"})
    job = worker_db.get_job(a)
    assert job["status"] == "queued" and job["held_by"] == "desk" and "worker restarted" in job["error"]
    assert [j["id"] for j in worker_db.claim_jobs(worker_id="desk", limit=5)] == [a]
    assert worker_db.get_job(a)["attempts"] == 2


def worker_db_conn(store):
    from contextlib import contextmanager

    from api.device_workers.store import _connect

    @contextmanager
    def cm():
        conn = _connect(store.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    return cm()


def test_worker_releases_only_what_it_could_stop(monkeypatch):
    from api.device_workers import executor, worker_loop

    stopped = {"a": True, "b": False}
    monkeypatch.setattr(executor, "stop_workload", lambda job: stopped[job["id"]])
    released = []
    worker_loop._release_ended([{"id": "a"}, {"id": "b"}], released.append)
    assert released == ["a"]


def test_a_workload_still_running_fails_the_job_but_keeps_its_hold(monkeypatch):
    from api.device_workers import executor, worker_loop

    def boom(job, auth_token=None):
        raise executor.WorkloadStillRunning("cancel not confirmed")

    monkeypatch.setattr(executor, "execute_job", boom)
    calls = []
    worker_loop._run_one({"id": "j", "params": {}}, complete=lambda *a: None,
                         fail=lambda jid, **kw: calls.append(kw), heartbeat=lambda jid, progress=None: True)
    assert calls == [{"error": "cancel not confirmed", "retry": False, "keep_hold": True}]


def test_stop_scope_stops_a_scope_left_running(monkeypatch):
    seen = []
    state = {"active": "active"}

    def fake(*args):
        seen.append(args)
        if args[0] == "stop":
            state["active"] = "inactive"
        return state["active"] if args[0] == "is-active" else ""

    monkeypatch.setattr(isolation, "available", lambda: True)
    monkeypatch.setattr(isolation, "_systemctl", fake)
    assert isolation.stop_scope("job1") is True
    assert ("stop", "cuttle-job-job1.scope") in seen
    state["active"] = "active"
    monkeypatch.setattr(isolation, "_systemctl", lambda *a: "active")  # will not stop
    assert isolation.stop_scope("job1") is False


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
    s = run_watch.summarize(jobs, now=now, started_ts=now - 300, expected={"b": 120.0, "c": 120.0})
    assert s["state"] == "running" and s["percent"] == round(100 * (1 + 0.375 + 0 + 1) / 4)
    assert "1/4 done" in s["label"] and "1 failed" in s["label"] and "KSampler 15/30" in s["label"]
    assert s["eta_s"] == (120 - 30) + 120  # history of the workload, not node counts
    no_history = run_watch.summarize(jobs[1:2], now=now, started_ts=now - 30)
    assert no_history["eta_s"] == int(30 * (1 - 0.375) / 0.375)  # observed rate when there is none
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


def test_an_unacknowledged_comfyui_cancel_keeps_the_hold(fake_comfy, monkeypatch):
    from api.device_workers.executor import WorkloadStillRunning

    monkeypatch.setattr(comfyui, "CANCEL_CONFIRM_SEC", 0)
    fake_comfy(events=[{"type": "execution_start", "data": {"prompt_id": PID}}],
               queue={"queue_running": [[1, PID]], "queue_pending": []})  # ComfyUI keeps running it
    with pytest.raises(WorkloadStillRunning):
        comfyui.run({"prompt": PROMPT, "_job_id": JOB, "_cancelled": lambda: True})
    assert comfyui.stop({"_job_id": JOB}) is False


def test_stop_confirms_once_comfyui_no_longer_lists_the_prompt(fake_comfy):
    fake = fake_comfy(queue={"queue_running": [], "queue_pending": []})
    assert comfyui.stop({"_job_id": JOB}) is True
    assert fake.posts == [("/api/jobs/" + PID + "/cancel", None)]


def test_losing_comfyui_mid_prompt_retries_with_the_hold(fake_comfy):
    from websockets.exceptions import ConnectionClosed

    from api.device_workers.executor import WorkloadStillRunning

    fake = fake_comfy(queue={"queue_running": [[1, PID]], "queue_pending": []})

    def drop(url, **_kw):
        class WS:
            def recv(self, timeout=None):
                raise ConnectionClosed(None, None)

            def close(self):
                pass
        return WS()

    import websockets.sync.client as wsc

    wsc.connect = drop
    with pytest.raises(WorkloadStillRunning) as err:
        comfyui.run({"prompt": PROMPT, "_job_id": JOB})
    assert err.value.retry is True and not [p for p in fake.posts if p[0] == "/prompt"]  # already queued


# ── Performance history ──────────────────────────────────────────────────────

def _finished(store, wid, *, seconds, peak_ram=0, template="hunyuan", steps=30, outcome="succeeded", reserved=0):
    from api.device_workers import metrics

    job = {"id": __import__("uuid").uuid4().hex, "type": "comfyui_prompt", "status": outcome if outcome != "oom" else "failed",
           "params": {"template": template, "prompt": {"3": {"class_type": "KSampler", "inputs": {"steps": steps, "seed": 1}}}},
           "result": {"elapsed_seconds": seconds, "peak_ram_mb": peak_ram},
           "error": "oom: past its scope" if outcome == "oom" else None,
           "reserved": {"ram_mb": reserved} if reserved else {}, "created_at": 1.0, "started_at": 2.0,
           "completed_at": 2.0 + seconds}
    metrics.record(job, wid)
    return job


def test_config_ignores_seeds_and_text_but_not_steps():
    from api.device_workers import metrics

    def job(seed, steps, text):
        return {"type": "comfyui_prompt", "params": {"prompt": {
            "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": text}}}}}
    assert metrics.config_key(job(1, 30, "a gun")) == metrics.config_key(job(99, 30, "a crate"))
    assert metrics.config_key(job(1, 30, "a gun")) != metrics.config_key(job(1, 50, "a gun"))


def test_estimates_prefer_this_machine_and_these_settings(worker_db):
    from api.device_workers import metrics

    for s in (100, 120, 110):
        _finished(worker_db, "desk", seconds=s, peak_ram=7000)
    _finished(worker_db, "lap", seconds=400, peak_ram=7400)
    probe = {"type": "comfyui_prompt", "params": {"template": "hunyuan", "prompt": {
        "3": {"class_type": "KSampler", "inputs": {"steps": 30, "seed": 5}}}}}
    desk = metrics.estimate(probe, "desk")
    assert desk["seconds"] == 110 and desk["basis"] == "this machine, same settings" and desk["samples"] == 3
    assert metrics.estimate(probe, "lap")["seconds"] == 400
    anywhere = metrics.estimate(probe)
    assert anywhere["basis"] == "same settings, any machine" and anywhere["peak_ram_mb"] == 7400
    other = {**probe, "params": {**probe["params"], "prompt": {"3": {"class_type": "KSampler", "inputs": {"steps": 60}}}}}
    assert metrics.estimate(other)["basis"] == "same workload"
    assert metrics.estimate({"type": "shell", "params": {"recipe": "never-ran"}})["seconds"] is None


def test_suggestions_never_lower_and_grow_past_an_oom(worker_db):
    from api.device_workers import metrics

    assert metrics.suggest({"ram_mb": 9000}, {"peak_ram_mb": 7000})["ram_mb"] == 9000
    assert metrics.suggest({"ram_mb": 4000}, {"peak_ram_mb": 7000})["ram_mb"] == 8050
    assert metrics.suggest({"ram_mb": 4000}, {"peak_ram_mb": 3000, "oom_reserved_ram_mb": 4000})["ram_mb"] == 6000


def test_store_records_metrics_when_a_job_ends(worker_db):
    from api.device_workers import metrics

    _register(worker_db)
    jid = worker_db.submit_job(job_type="ping", params={"template": "t"},
                               requirements={"resources": {"ram_mb": 1024}})["id"]
    worker_db.claim_jobs(worker_id="desk")
    worker_db.complete_job(jid, "desk", result={"elapsed_seconds": 2.0, "peak_ram_mb": 900})  # a reattach
    job = worker_db.get_job(jid)
    est = metrics.estimate(job, "desk")
    assert est["seconds"] == pytest.approx(job["completed_at"] - job["started_at"]) and est["peak_ram_mb"] == 900


def test_plan_raises_a_reservation_to_the_measured_peak(worker_db):
    from api.device_workers import runs

    _register(worker_db)
    for _ in range(2):
        _finished(worker_db, "desk", seconds=60, peak_ram=8192, template="drafts")
    spec = {"title": "Drafts", "jobs": [{"type": "comfyui_prompt", "template": "drafts", "label": "nacre",
                                        "params": {"prompt": {"3": {"class_type": "KSampler", "inputs": {"steps": 30}}}},
                                        "requirements": {"resources": {"ram_mb": 4096, "service": "comfyui"}}}]}
    planned = runs.plan(spec, chat_session_id="CH-002819")
    stored = runs._load(planned["run_id"])
    assert stored["jobs"][0]["requirements"]["resources"]["ram_mb"] == 9421
    assert "nacre: reservation from 2 past run(s)" in planned["form"]["description"]
    assert planned["estimate_seconds"] == 60


# ── Nothing fits: explicit, never silent ────────────────────────────────────

BIG = {"title": "Big", "jobs": [{"type": "ping", "label": "big", "requirements": {"resources": {"ram_mb": 60000}}}]}


def test_plan_says_nothing_fits_and_names_offline_workers(worker_db):
    from api.device_workers import runs

    _register(worker_db)
    worker_db.upsert_worker(worker_id="lap", hostname="lap", capabilities={"shell": True}, load=_load())
    with worker_db_conn(worker_db) as conn:
        conn.execute("UPDATE workers SET last_seen = 1 WHERE worker_id = 'lap'")
    planned = runs.plan(BIG, chat_session_id="CH-002819")
    desc = planned["form"]["description"]
    assert desc.startswith("Nothing can start right now. If it still cannot after 10 min")
    assert "desk: 0/1 fit now — needs 58.6 GB RAM" in desc and "lap: offline" in desc
    assert planned["form"]["options"][0]["label"].startswith("Queue it — ask me again")


def _submitted(store, spec, **extra):
    from api.device_workers import runs

    p = runs.plan({**spec, **extra}, chat_session_id="CH-002819")["form"]["options"][0]["params"]
    return p["run_id"], runs.submit(p["run_id"], spec_sha256=p["spec_sha256"])


def test_a_stuck_run_asks_once_with_alternatives(worker_db):
    import time as _time

    from api import job_watch as jw
    from api.chat_ops import store as ops
    from api.device_workers import runs

    _register(worker_db, ram_total=32.0, ram_available=10.0)
    worker_db.upsert_worker(worker_id="lap", hostname="lap", capabilities={"shell": True},
                            load=_load(ram_total=64.0, ram_available=60.0))
    spec = {"title": "Mid", "jobs": [{"type": "ping", "label": "mid", "requirements": {"resources": {"ram_mb": 20000}}}]}
    run_id, sub = _submitted(worker_db, spec, ask_after_s=60)
    worker_db.retarget_queued(sub["jobs"], "desk")  # pinned where it does not fit
    worker_db.claim_jobs(worker_id="desk")
    assert jw.poll_unit_watches() == [] and not [o for o in ops.listing(2819) if o["kind"] == "notice"]

    later = _time.time() + 120
    for _ in range(2):  # asks once, however many polls
        jw.poll_unit_watches(now=later)
    notices = [o for o in ops.listing(2819) if o["kind"] == "notice"]
    assert len(notices) == 1 and notices[0]["payload"]["job_id"] == f"{run_id}#decide1"
    text = notices[0]["payload"]["text"]
    assert "post it exactly as given" in text and "mid: desk: needs 19.5 GB RAM" in text
    form = json.loads(text.split("<cuttle_action_form>\n", 1)[1].split("\n</cuttle_action_form>")[0])
    assert [o["id"] for o in form["options"]] == ["move-lap", "wait", "cancel"]
    assert jw.read_status(run_id)["decision"]["form"] is True

    assert runs.decide(run_id, choice="move:lap")["moved"] == 1
    assert [j["id"] for j in worker_db.claim_jobs(worker_id="lap")] == sub["jobs"]


def test_decisions_wait_and_cancel(worker_db):
    import time as _time

    from api import job_watch as jw
    from api.chat_ops import store as ops
    from api.device_workers import runs

    _register(worker_db)
    run_id, sub = _submitted(worker_db, BIG, ask_after_s=60)
    jw.poll_unit_watches(now=_time.time() + 120)
    assert runs.decide(run_id, choice="wait")["ask_again_in_s"] == runs.WAIT_MORE_SEC
    jw.poll_unit_watches(now=_time.time() + 300)  # still inside the 30 min
    assert len([o for o in ops.listing(2819) if o["kind"] == "notice"]) == 1
    jw.poll_unit_watches(now=_time.time() + runs.WAIT_MORE_SEC + 60)
    assert len([o for o in ops.listing(2819) if o["kind"] == "notice"]) == 2
    assert runs.decide(run_id, choice="cancel")["cancelled"]
    assert worker_db.get_job(sub["jobs"][0])["status"] == "cancelled"
    resolved = jw.poll_unit_watches()
    assert resolved and resolved[0]["resume"] == "cancelled"  # the user chose: no resume turn
    assert not runs.decide(run_id, choice="bogus")["success"]


def test_a_run_is_not_finished_while_a_worker_still_holds_a_job(worker_db):
    from api import job_watch as jw
    from api.chat_ops import store as ops

    _register(worker_db)
    spec = {"title": "One", "jobs": [{"type": "ping", "label": "one", "requirements": {"resources": {"ram_mb": 1024}}}]}
    run_id, sub = _submitted(worker_db, spec)
    jid = worker_db.claim_jobs(worker_id="desk")[0]["id"]
    worker_db.cancel_job(jid, reason="cancelled from the Jobs page")
    assert jw.poll_unit_watches() == []
    status = jw.read_status(run_id)
    assert status["state"] == "running" and "stopping — waiting for desk" in status["label"]
    assert status["counts"]["stopping"] == 1
    worker_db.release_hold(jid, "desk")
    done = jw.poll_unit_watches()
    assert done and done[0]["state"] == "failed"
    assert len([o for o in ops.listing(2819) if o["kind"] == "notice"]) == 1


def test_a_worker_back_after_its_lease_ran_out_picks_its_job_back_up(worker_db):
    _register(worker_db)
    _register(worker_db, wid="lap")
    a, _b = _gpu_pair(worker_db)
    with worker_db_conn(worker_db) as conn:
        conn.execute("UPDATE jobs SET claim_expires = 1 WHERE id = ?", (a,))
    worker_db.reclaim_expired()
    assert worker_db.heartbeat_job(a, "lap") is False  # not lap's
    assert worker_db.heartbeat_job(a, "desk", progress={"units_done": 3, "units_total": 9}) is True
    job = worker_db.get_job(a)
    assert job["status"] == "running" and job["claimed_by"] == "desk" and job["attempts"] == 1
    # and its report of the end lands too
    with worker_db_conn(worker_db) as conn:
        conn.execute("UPDATE jobs SET claim_expires = 1 WHERE id = ?", (a,))
    worker_db.reclaim_expired()
    assert worker_db.complete_job(a, "desk", result={"ok": True}) is True
    assert worker_db.get_job(a)["status"] == "succeeded" and worker_db.get_job(a)["held_by"] is None


def test_the_pulse_refreshes_telemetry_while_a_job_runs(monkeypatch):
    import threading

    from api.device_workers import executor, worker_loop

    refreshed = threading.Event()

    def slow(job, auth_token=None):
        assert refreshed.wait(10)
        return {"ok": True}

    monkeypatch.setattr(executor, "execute_job", slow)
    done = []
    worker_loop._run_one({"id": "j", "params": {}}, complete=lambda jid, result: done.append(result),
                         fail=lambda *a, **k: None, heartbeat=lambda jid, progress=None: True,
                         lease_seconds=12, telemetry=refreshed.set)
    assert done == [{"ok": True}]


def test_reattaching_mid_prompt_shows_the_running_sampler():
    t = comfyui.Tracker(PID, {"11": "Sampling"})
    assert t.handle({"type": "progress", "data": {"prompt_id": PID, "node": "11", "value": 12, "max": 60}})
    assert t.progress()["message"] == "Sampling 12/60" and t.progress()["stage"] == "Sampling"


def test_a_hung_comfyui_is_not_taken_for_a_stopped_one(fake_comfy, monkeypatch):
    from api.device_workers.executor import WorkloadStillRunning

    monkeypatch.setattr(comfyui, "CANCEL_CONFIRM_SEC", 0)
    fake = fake_comfy(events=[{"type": "execution_start", "data": {"prompt_id": PID}}])

    answered = fake.get

    def hung(url, timeout=0):  # up at start, then stops answering mid-prompt
        if url.endswith("/system_stats") or not hung.started:
            hung.started = hung.started or "/queue" in url
            return answered(url, timeout)
        raise comfyui.requests.ReadTimeout("read timed out")
    hung.started = False

    comfyui.requests.get = hung
    with pytest.raises(WorkloadStillRunning):  # not a plain failure that would release the hold
        comfyui.run({"prompt": PROMPT, "_job_id": JOB})
    assert comfyui.stop({"_job_id": JOB}) is False

    def refused(url, timeout=0):
        raise comfyui.requests.ConnectionError("refused")

    comfyui.requests.get = refused
    comfyui.requests.post = refused
    assert comfyui.stop({"_job_id": JOB}) is True  # nothing listens: the prompt is gone


def test_remote_workers_get_release_work_and_keep_holds_over_http(worker_db, monkeypatch):
    from flask import Flask

    from api.device_workers.client import DeviceWorkerClient
    from api.device_workers.routes import workers_bp

    monkeypatch.setattr("api.device_workers.routes.get_store", lambda: worker_db)
    app = Flask(__name__)
    app.register_blueprint(workers_bp)
    http = app.test_client()
    headers = {"Authorization": f"Bearer {worker_db.enroll_device(worker_id='lap')['token']}"}

    def call(path, body):
        r = http.post(path, json={"worker_id": "lap", **body}, headers=headers,
                      environ_base={"REMOTE_ADDR": "127.0.0.1"})
        return r.status_code, r.get_json()

    worker_db.upsert_worker(worker_id="lap", capabilities={"shell": True}, load=_load())
    a = worker_db.submit_job(job_type="ping", requirements={"resources": {"vram_mb": 6000}})["id"]
    code, body = call("/api/workers/jobs/claim", {"capabilities": {"shell": True}})
    assert code == 200 and [j["id"] for j in body["jobs"]] == [a] and body["release"] == []
    # unconfirmed stop over HTTP: failed, still held
    assert call(f"/api/workers/jobs/{a}/fail", {"error": "cancel not confirmed", "keep_hold": True})[0] == 200
    assert worker_db.get_job(a)["held_by"] == "lap"
    code, body = call("/api/workers/jobs/claim", {"capabilities": {"shell": True}})
    assert [r["id"] for r in body["release"]] == [a] and body["jobs"] == []
    assert call(f"/api/workers/jobs/{a}/release", {})[0] == 200
    assert worker_db.get_job(a)["held_by"] is None

    # the client keeps the release list from its last claim
    client = DeviceWorkerClient.__new__(DeviceWorkerClient)
    client.worker_id = "lap"
    client._request = lambda method, path, json_body=None, timeout=None: {"jobs": [], "release": [{"id": "x"}]}
    assert client.claim() == [] and client.release == [{"id": "x"}]
