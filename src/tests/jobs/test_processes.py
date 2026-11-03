"""Agent-owned process attribution: run markers, detached roots, guarded End."""

import os
import random
import subprocess
import sys
import time

import psutil
import pytest

from api.jobs import processes as P


def rec(pid, ppid, chat, t=100.0, **kw):
    return {"pid": pid, "ppid": ppid, "chat_session_id": chat, "create_time": t, "name": f"p{pid}", **kw}


def test_same_chat_nests_and_host_launched_root_is_attached():
    forest = P.build_forest([
        rec(10, 5, "1821", 100), rec(11, 10, "1821", 101), rec(12, 11, "1821", 102),
    ], host_pids={5})
    e = forest["1821"]
    assert e["process_count"] == 3 and e["detached_count"] == 0
    [root] = e["roots"]
    assert root["pid"] == 10 and not root["detached"]
    assert root["children"][0]["pid"] == 11 and root["children"][0]["children"][0]["pid"] == 12


def test_reparented_root_is_detached_but_keeps_its_owner():
    # nohup'd build: parent is systemd --user (not a Cuttle host) — still CH-1821's.
    forest = P.build_forest([rec(10, 5, "1821", 100), rec(20, 2563, "1821", 105), rec(21, 20, "1821", 106)],
                            host_pids={5})
    roots = {n["pid"]: n for n in forest["1821"]["roots"]}
    assert set(roots) == {10, 20}
    assert roots[20]["detached"] and not roots[10]["detached"]
    assert forest["1821"]["detached_count"] == 1
    assert roots[20]["children"][0]["pid"] == 21


def test_sub_agent_chat_records_the_chat_that_started_it():
    # Parent chat's CLI ran `python -m api.subagents spawn`, which launched the child's CLI.
    forest = P.build_forest([rec(10, 5, "1", 100), rec(11, 10, "1", 101), rec(30, 11, "2", 102)],
                            host_pids={5})
    child_root = forest["2"]["roots"][0]
    assert child_root["parent_chat"] == "1" and not child_root["detached"]
    assert forest["2"]["spawned_by"] == {"1": 11}
    assert forest["1"]["process_count"] == 2  # each chat lists only its own processes


def test_reused_parent_pid_is_not_an_ancestor():
    # pid 10 was reused by a process younger than the child: the child is detached.
    forest = P.build_forest([rec(10, 5, "1", 500), rec(11, 10, "1", 100)], host_pids={5})
    assert {n["pid"] for n in forest["1"]["roots"]} == {10, 11}
    assert next(n for n in forest["1"]["roots"] if n["pid"] == 11)["detached"]


def test_chat_ids_normalize_and_junk_is_ignored():
    forest = P.build_forest([rec(10, 1, "db_session_7"), rec(11, 1, "CH-000007"), rec(1, 0, "7"),
                             {"pid": "x"}, rec(12, 1, "")])
    assert set(forest) == {"7"} and forest["7"]["process_count"] == 2


def test_managed_cmdline_only_matches_the_real_host_scripts():
    assert P._managed_cmdline("/x/.venv/bin/python3 /x/src/api/web_chat_api.py")
    assert P._managed_cmdline("python.exe src\\scripts\\cuttle_daemon.py")
    assert not P._managed_cmdline("vim src/api/web_chat_api.py")
    assert not P._managed_cmdline("grep -rn cuttle_daemon.py .")


@pytest.fixture
def marked_child():
    """A real child process carrying a Cuttle run marker, with a grandchild."""
    chat = str(random.randint(10**8, 10**9))  # unique per test: no cross-run strays
    env = {**os.environ, "CUTTLE_CHAT_SESSION_ID": chat, "CUTTLE_AGENT_RUN_ID": "run1", "CUTTLE_AGENT_ID": "claude"}
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    proc = subprocess.Popen([sys.executable, "-c", code], env=env)
    proc.chat = chat
    deadline = time.time() + 10
    while time.time() < deadline and not psutil.Process(proc.pid).children():
        time.sleep(0.05)
    yield proc
    if proc.poll() is None:
        for p in reversed([psutil.Process(proc.pid)] + psutil.Process(proc.pid).children(recursive=True)):
            try:
                p.kill()
            except psutil.Error:
                pass
    proc.wait(timeout=5)


def test_scan_attributes_marked_processes(marked_child):
    found = [r for r in P.scan(max_age=0) if r["chat_session_id"] == marked_child.chat]
    assert {r["pid"] for r in found} >= {marked_child.pid}
    root = next(r for r in found if r["pid"] == marked_child.pid)
    assert root["run_id"] == "run1" and root["agent_id"] == "claude"
    assert len(found) == 2  # the grandchild inherited the marker


def test_shadow_instance_neither_sees_nor_ends_host_processes(marked_child, monkeypatch, tmp_path):
    """A shadow dev child is sandboxed: reading other processes' environments
    trips its file guard, and the live install's work is not its to end."""
    monkeypatch.setenv("CUTTLE_SHADOW_DATA", str(tmp_path))
    assert P.scan(max_age=0) == []
    ctime = psutil.Process(marked_child.pid).create_time()
    with pytest.raises(PermissionError, match="shadow"):
        P.end_process(marked_child.pid, ctime)
    assert marked_child.poll() is None
    monkeypatch.delenv("CUTTLE_SHADOW_DATA")
    assert any(r["pid"] == marked_child.pid for r in P.scan(max_age=0))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="liveness probe shim reads /proc")
def test_end_process_requires_identity_then_ends_the_tree(marked_child, monkeypatch):
    # psutil.wait_procs probes reaped grandchildren with kill(pid, 0); the
    # suite's kill guard (conftest) refuses non-descendants, even for probes.
    guarded = os.kill

    def probe_or_guard(pid, sig):
        if sig == 0:
            if not os.path.exists(f"/proc/{int(pid)}"):
                raise ProcessLookupError(pid)
            return None
        return guarded(pid, sig)

    monkeypatch.setattr(os, "kill", probe_or_guard)
    with pytest.raises(LookupError):
        P.end_process(marked_child.pid, None)
    with pytest.raises(LookupError):
        P.end_process(marked_child.pid, psutil.Process(marked_child.pid).create_time() + 5)
    grandchild = psutil.Process(marked_child.pid).children()[0].pid
    out = P.end_process(marked_child.pid, psutil.Process(marked_child.pid).create_time(), grace=3)
    assert out["chat_session_id"] == marked_child.chat and out["ended"] == 2
    marked_child.wait(timeout=5)
    try:
        assert psutil.Process(grandchild).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        pass


def test_end_process_refuses_unmarked_and_protected_processes():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            env={k: v for k, v in os.environ.items() if k != "CUTTLE_CHAT_SESSION_ID"})
    try:
        with pytest.raises(PermissionError):
            P.end_process(proc.pid, psutil.Process(proc.pid).create_time())
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5)
    me = psutil.Process(os.getpid())
    with pytest.raises(PermissionError):
        P.end_process(me.pid, me.create_time())
    with pytest.raises(LookupError):
        P.end_process(1 if os.name != "nt" else 0, 0)
