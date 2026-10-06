"""Offline Codex thread-ownership tests: one writer per thread id.

A Codex thread accepts a single app-server writer; a second server
resuming the same thread fails with a thread-store conflict (or hangs).
The turn runner, the token probe, and compaction coordinate through
``api.agent_harness.codex_thread_ownership``. These tests reproduce the
overlap in both directions with fake servers only — no live Codex prompts,
no network, no database.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from api.agent_harness import codex_thread_ownership as own
from scripts.utilities import codex_app_server as cas

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="fake server scripts use a shebang"
)

_TID = "th-ownership-1"


def _poll(fn, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.05)
    return False


def _fake_bin(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


# Reactive fake app-server. HOLD variant answers initialize / thread /
# turn start, then blocks on stdin until killed (an open turn). QUICK
# variant answers the usage-probe handshake and exits.
_FAKE_TURN_SERVER = r'''
import json, os, sys
def out(msg):
    msg["jsonrpc"] = "2.0"
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
tid = os.environ.get("S2_TID", "th-1")
mode = os.environ.get("S2_MODE", "hold")
log = os.environ.get("S2_LOG")
hold_sleep = float(os.environ.get("S2_SLEEP", "4"))
def note(s):
    if log:
        with open(log, "a") as fh:
            fh.write(s + "\n")
for line in sys.stdin:
    try:
        m = json.loads(line)
    except Exception:
        continue
    method, mid = m.get("method"), m.get("id")
    note(str(method))
    if method == "initialize" and mid is not None:
        out({"id": mid, "result": {}})
    elif method in ("thread/resume", "thread/start") and mid is not None:
        out({"id": mid, "result": {"thread": {"id": tid}}})
        if mode == "slow-usage":
            import time as _t
            _t.sleep(hold_sleep)
        if mode in ("usage", "slow-usage"):
            out({"method": "thread/tokenUsage/updated",
                 "params": {"threadId": tid,
                            "tokenUsage": {"last": {"inputTokens": 100,
                                                    "totalTokens": 120},
                                           "modelContextWindow": 200000}}})
    elif method == "turn/start" and mid is not None:
        out({"id": mid, "result": {"turn": {"id": "t-9"}}})
        out({"method": "turn/started", "params": {"turn": {"id": "t-9"}}})
        if mode == "hold":
            sys.stdin.read()
            break
    elif method == "thread/unsubscribe" and mid is not None:
        out({"id": mid, "result": {"status": "unsubscribed"}})
        break
'''

_EXIT_BIN = "import sys\nsys.exit(0)\n"


def _noop_store(monkeypatch):
    monkeypatch.setattr(
        "scripts.utilities.codex_cli_session_store.save_codex_resume_id",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "scripts.utilities.codex_cli_session_store.save_codex_context_snapshot",
        lambda *a, **k: None,
    )


def _run_turn_in_thread(mod, results, **kw):
    def _go():
        try:
            results.append(asyncio.run(mod.run_codex_turn_app_server(**kw)))
        except BaseException as exc:  # never lose the worker outcome
            results.append({"worker_error": repr(exc)})

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t


def test_primitive_mutual_exclusion():
    assert own.owner_of("nope") is None
    assert own.try_acquire("", "x") is None
    t1 = own.try_acquire(_TID, "turn")
    assert t1 is not None
    assert own.owner_of(_TID) == "turn"
    assert own.try_acquire(_TID, "probe") is None  # held: no second writer
    assert own.release(_TID, t1 + 999) is False  # wrong token: stays held
    assert own.owner_of(_TID) == "turn"
    assert own.release(_TID, t1) is True
    assert own.owner_of(_TID) is None
    assert own.release(_TID, t1) is False


def test_release_needs_exact_token():
    token = own.try_acquire(_TID, "holder")
    assert token is not None
    try:
        assert own.release(_TID, None) is False
        assert own.owner_of(_TID) == "holder"
    finally:
        assert own.release(_TID, token) is True
    assert own.owner_of(_TID) is None


def test_agent_context_skips_fetch_when_thread_owned(monkeypatch):
    """The incident regression: no steer registration, turn owns thread."""
    import api.agent_context as ac
    import scripts.utilities.codex_cli_session_store as store

    calls = []
    monkeypatch.setattr(store, "load_codex_context_snapshot", lambda sid: None)
    monkeypatch.setattr(
        store, "save_codex_context_snapshot", lambda sid, snap: None
    )
    monkeypatch.setattr(
        cas,
        "fetch_codex_thread_token_usage",
        lambda *a, **k: calls.append((a, k)) or {"success": False},
    )
    token = own.try_acquire("th-owned-ctx", "codex-turn")
    try:
        ac._codex_apply_live_or_snapshot(
            chat_session_id=5151, cwd="/tmp", resume_id="th-owned-ctx",
            tokens=0, token_source="none", limit=0, limit_source="none",
            live=True,
        )
        assert calls == []
    finally:
        assert own.release("th-owned-ctx", token) is True
    ac._codex_apply_live_or_snapshot(
        chat_session_id=5151, cwd="/tmp", resume_id="th-owned-ctx",
        tokens=0, token_source="none", limit=0, limit_source="none",
        live=True,
    )
    assert len(calls) == 1  # idle observation still probes


_real_popen = cas.subprocess.Popen


def _bomb_popen(monkeypatch):
    """Fail-closed spawn guard: exe resolution is fine, Popen must not run."""

    def _bomb(*a, **k):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(cas.subprocess, "Popen", _bomb)


def test_fetch_skips_spawn_when_busy_then_runs_when_free(tmp_path, monkeypatch):
    monkeypatch.setattr(cas, "codex_executable", lambda: "/fake/codex")
    _bomb_popen(monkeypatch)
    token = own.try_acquire("th-busy-1", "codex-turn")
    try:
        res = cas.fetch_codex_thread_token_usage("th-busy-1", cwd=str(tmp_path))
        assert res["success"] is False and res["status"] == "busy"
        assert "busy" in (res.get("error") or "")
        res = cas.compact_codex_thread("th-busy-1", cwd=str(tmp_path))
        assert res["success"] is False and res["status"] == "busy"
    finally:
        assert own.release("th-busy-1", token) is True


def test_turn_holds_ownership_probe_skips_then_succeeds(tmp_path, monkeypatch):
    import scripts.utilities.codex_app_server_turn as mod

    tid = "th-hold-9"
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_MODE", "hold")
    monkeypatch.setattr(mod, "codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _FAKE_TURN_SERVER))
    _noop_store(monkeypatch)
    ev = threading.Event()
    results = []
    worker = _run_turn_in_thread(
        mod, results, prompt="go", cwd=str(tmp_path), resume=tid,
        model=None, reasoning_effort=None, chat_session_id="db_session_9001",
        timeout=120, cancel_event=ev,
    )
    try:
        assert _poll(lambda: own.owner_of(tid) == "codex-turn", timeout=15.0), (
            "turn must own its resume thread from startup"
        )
        # Probe direction: a live turn means skip, and no second server.
        monkeypatch.setattr(cas, "codex_executable", lambda: "/fake/codex")
        _bomb_popen(monkeypatch)
        res = cas.fetch_codex_thread_token_usage(tid, cwd=str(tmp_path), timeout=5.0)
        assert res["success"] is False and res["status"] == "busy"
    finally:
        ev.set()  # cancel: turn must unwind and release
        worker.join(30)
    assert results and results[0].get("cancelled") is True
    assert own.owner_of(tid) is None
    # Idle observation works again after release (quick fake usage server).
    monkeypatch.setattr(cas.subprocess, "Popen", _real_popen)
    monkeypatch.setenv("S2_MODE", "usage")
    monkeypatch.setattr(cas, "codex_executable",
                        lambda: _fake_bin(tmp_path, "codex2", _FAKE_TURN_SERVER))
    res = cas.fetch_codex_thread_token_usage(tid, cwd=str(tmp_path), timeout=10.0)
    assert res["success"] is True
    assert res["token_usage"]["context_tokens"] == 120
    # The exact reaper frees the lease once the server process is gone.
    assert _poll(lambda: own.owner_of(tid) is None, timeout=10.0)


def test_probe_first_turn_fails_closed_without_touching_thread(tmp_path, monkeypatch):
    import scripts.utilities.codex_app_server_turn as mod

    tid = "th-first-1"
    monkeypatch.setattr(mod, "_OWNERSHIP_WAIT_SEC", 0.5)
    log_path = tmp_path / "methods.log"
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_MODE", "hold")
    monkeypatch.setenv("S2_LOG", str(log_path))
    monkeypatch.setattr(mod, "codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _FAKE_TURN_SERVER))
    _noop_store(monkeypatch)
    token = own.try_acquire(tid, "probe")  # in-flight probe owns it first
    try:
        results = []
        worker = _run_turn_in_thread(
            mod, results, prompt="go", cwd=str(tmp_path), resume=tid,
            model=None, reasoning_effort=None, chat_session_id="db_session_9002",
            timeout=60,
        )
        worker.join(30)
        assert results, "turn worker produced no result"
        res = results[0]
        assert res.get("success") is False and "busy" in (res.get("error") or "")
        assert "fallback" not in res  # honest error, not an exec retry
        assert own.owner_of(tid) == "probe"  # never stolen
        if log_path.exists():
            assert "initialize" not in log_path.read_text(encoding="utf-8")  # never touched
    finally:
        assert own.release(tid, token) is True
    assert own.owner_of(tid) is None


def test_cancel_during_ownership_wait_reaps_proc(tmp_path, monkeypatch):
    import scripts.utilities.codex_app_server_turn as mod

    tid = "th-cancelwait-1"
    monkeypatch.setattr(mod, "_OWNERSHIP_WAIT_SEC", 30.0)
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_MODE", "hold")
    monkeypatch.setattr(mod, "codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _FAKE_TURN_SERVER))
    _noop_store(monkeypatch)
    token = own.try_acquire(tid, "probe")
    try:
        ev = threading.Event()
        ev.set()  # already cancelled before the wait starts
        started = time.monotonic()
        results = []
        worker = _run_turn_in_thread(
            mod, results, prompt="go", cwd=str(tmp_path), resume=tid,
            model=None, reasoning_effort=None, chat_session_id="db_session_9003",
            timeout=60, cancel_event=ev,
        )
        worker.join(30)
        assert time.monotonic() - started < 25.0  # did not sit out the wait
        assert results and results[0].get("cancelled") is True
        assert own.owner_of(tid) == "probe"
    finally:
        assert own.release(tid, token) is True


def test_setup_failure_releases_ownership(tmp_path, monkeypatch):
    import scripts.utilities.codex_app_server_turn as mod

    tid = "th-fail-1"
    monkeypatch.setattr(mod, "codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _EXIT_BIN))
    _noop_store(monkeypatch)
    res = asyncio.run(
        mod.run_codex_turn_app_server(
            "go", cwd=str(tmp_path), resume=tid, model=None,
            reasoning_effort=None, chat_session_id="db_session_9004",
            timeout=30,
        )
    )
    assert res["success"] is False
    assert own.owner_of(tid) is None  # acquired pre-init, released in finally


def test_probe_first_waiting_turn_acquires_after_release(tmp_path, monkeypatch):
    """Real handoff: a running probe holds, a waiting turn wins on release."""
    import scripts.utilities.codex_app_server_turn as mod

    tid = "th-handoff-1"
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_MODE", "slow-usage")
    monkeypatch.setenv("S2_SLEEP", "4")
    monkeypatch.setattr(cas, "codex_executable",
                        lambda: _fake_bin(tmp_path, "codex-p", _FAKE_TURN_SERVER))
    probe_results = []

    def _probe():
        try:
            probe_results.append(
                cas.fetch_codex_thread_token_usage(
                    tid, cwd=str(tmp_path), timeout=20.0))
        except BaseException as exc:
            probe_results.append({"worker_error": repr(exc)})

    pt = threading.Thread(target=_probe, daemon=True)
    pt.start()
    try:
        assert _poll(lambda: own.owner_of(tid) == "probe", timeout=10.0), (
            "running probe must hold its thread"
        )
        # A turn starting now must wait — not fail, not spawn, not steal.
        turn_log = tmp_path / "turn-methods.log"
        monkeypatch.setenv("S2_MODE", "hold")
        monkeypatch.setenv("S2_LOG", str(turn_log))
        monkeypatch.setattr(mod, "codex_executable",
                            lambda: _fake_bin(tmp_path, "codex-t", _FAKE_TURN_SERVER))
        _noop_store(monkeypatch)
        monkeypatch.setattr(mod, "_OWNERSHIP_WAIT_SEC", 25.0)
        ev = threading.Event()
        results = []
        wt = _run_turn_in_thread(
            mod, results, prompt="go", cwd=str(tmp_path), resume=tid,
            model=None, reasoning_effort=None, chat_session_id="db_session_9005",
            timeout=120, cancel_event=ev,
        )
        try:
            time.sleep(1.0)  # turn reaches its wait while the probe holds
            assert own.owner_of(tid) == "probe"
            assert results == []
            pt.join(30)  # probe finishes its session and releases
            assert probe_results and probe_results[0].get("success") is True
            assert _poll(lambda: own.owner_of(tid) == "codex-turn",
                         timeout=15.0), "waiting turn must win on release"
            # The lease is held before the server spawns, so the log may
            # not exist yet: poll for the actual initialize handshake.
            assert _poll(
                lambda: turn_log.exists()
                and "initialize" in turn_log.read_text(encoding="utf-8"),
                timeout=15.0,
            ), "turn proceeded to initialize its own server"
        finally:
            ev.set()
            wt.join(30)
        assert results and results[0].get("cancelled") is True
    finally:
        pt.join(30)
    assert own.owner_of(tid) is None


class _EofStdin:
    def write(self, data):
        return len(data)

    def flush(self):
        return None

    def close(self):
        return None


class _EofStdout:
    def __iter__(self):
        return self

    def __next__(self):
        raise StopIteration


class _DelayedProc:
    """Fake server whose exit lags its kill: wait() blocks until kill().

    ``poll()`` mirrors Popen: None while alive, the returncode once the
    exit is confirmed — so the confirmed-exit gate can be exercised.
    """

    def __init__(self):
        self.stdin = _EofStdin()
        self.stdout = _EofStdout()
        self.stderr = None
        self._killed = threading.Event()
        self._rc = None
        self.events = []

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        self.events.append("wait")
        if self._killed.wait(timeout if timeout is not None else 30):
            self._rc = 0
            return 0
        raise subprocess.TimeoutExpired("codex", timeout)

    def kill(self):
        self.events.append("kill")
        self._killed.set()


class _UnkillableProc:
    """Fake server immune to kill: wait() never confirms an exit.

    ``poll()`` stays None and ``kill()`` is a noop, modelling a wedged
    vendor child. The first bounded wait consumes its timeout honestly;
    the unbounded final wait gives up quickly so the test can observe
    the retain decision without waiting out a hung reaper.
    """

    def __init__(self):
        self.stdin = _EofStdin()
        self.stdout = _EofStdout()
        self.stderr = None
        self.events = []

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.events.append("wait")
        time.sleep(min(float(timeout), 5.0) if timeout is not None else 0.5)
        raise subprocess.TimeoutExpired("codex", timeout)

    def kill(self):
        self.events.append("kill")


def test_delayed_kill_reaps_before_release(tmp_path, monkeypatch):
    """Kill-then-reap ordering: no release while the server may own the thread."""
    holder = {}

    def _factory(*a, **k):
        proc = _DelayedProc()
        holder["proc"] = proc
        return proc

    monkeypatch.setattr(cas.subprocess, "Popen", _factory)
    monkeypatch.setattr(cas, "codex_executable", lambda: "/fake/codex")
    tid = "th-delayed-1"
    results = []

    def _fetch():
        try:
            results.append(
                cas.fetch_codex_thread_token_usage(
                    tid, cwd=str(tmp_path), timeout=10.0))
        except BaseException as exc:
            results.append({"worker_error": repr(exc)})

    ft = threading.Thread(target=_fetch, daemon=True)
    ft.start()
    try:
        assert _poll(lambda: own.owner_of(tid) == "probe", timeout=10.0)
        ft.join(30)
        assert results and results[0].get("success") is False
        # Probe failure still reports an error AND cleans up its lease.
        assert results[0].get("error")
        # stdin closed + stdout EOF ended the session; the exact reaper
        # then ordered wait -> kill -> final wait before releasing.
        assert _poll(lambda: holder["proc"].events == ["wait", "kill", "wait"],
                     timeout=15.0), holder["proc"].events
    finally:
        ft.join(30)
    assert _poll(lambda: own.owner_of(tid) is None, timeout=10.0)


def test_unkillable_server_retains_lease(tmp_path, monkeypatch):
    """Unconfirmed exit keeps the lease: no release, no competing spawn."""
    spawns = []

    def _factory(*a, **k):
        proc = _UnkillableProc()
        spawns.append(proc)
        return proc

    monkeypatch.setattr(cas.subprocess, "Popen", _factory)
    monkeypatch.setattr(cas, "codex_executable", lambda: "/fake/codex")
    tid = "th-unkillable-1"
    real_try = own.try_acquire
    held = {}

    def _spy(tid_arg, owner):
        tok = real_try(tid_arg, owner)
        if tid_arg == tid and tok is not None:
            held["token"] = tok
        return tok

    monkeypatch.setattr(own, "try_acquire", _spy)
    try:
        res = cas.fetch_codex_thread_token_usage(
            tid, cwd=str(tmp_path), timeout=10.0)
        assert res.get("success") is False  # EOF ended the session
        assert res.get("error")
        # The reaper ordered wait -> kill -> final wait, but the exit was
        # never confirmed — the lease must stay held, not drop.
        assert _poll(lambda: getattr(spawns[0], "events", []) == ["wait", "kill", "wait"],
                     timeout=15.0), getattr(spawns[0], "events", None)
        assert _poll(lambda: own.owner_of(tid) == "probe", timeout=10.0)
        time.sleep(1.0)
        assert own.owner_of(tid) == "probe"
        # A second probe skips instead of racing: zero competing spawns.
        res2 = cas.fetch_codex_thread_token_usage(
            tid, cwd=str(tmp_path), timeout=5.0)
        assert res2["success"] is False and res2.get("status") == "busy"
        assert len(spawns) == 1
    finally:
        # In production a restart recovers this exceptional held lease;
        # the test process is the owner here, so release with the token.
        if held.get("token") is not None:
            assert own.release(tid, held["token"]) is True
    assert own.owner_of(tid) is None


def test_probe_spawn_error_releases_lease(tmp_path, monkeypatch):
    """Spawn failure releases the just-taken lease: nothing runs, nothing held."""

    def _boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(cas.subprocess, "Popen", _boom)
    monkeypatch.setattr(cas, "codex_executable", lambda: "/fake/codex")
    tid = "th-spawnfail-1"
    res = cas.fetch_codex_thread_token_usage(tid, cwd=str(tmp_path), timeout=5.0)
    assert res.get("success") is False
    assert "spawn" in (res.get("error") or "").lower()
    assert own.owner_of(tid) is None


_FAKE_EXEC_SERVER = r'''
import json, os, sys, time
def out(m):
    sys.stdout.write(json.dumps(m) + "\n"); sys.stdout.flush()
tid = os.environ.get("S2_TID", "th-exec-1")
mode = os.environ.get("S2_EXEC_MODE", "ok")
log = os.environ.get("S2_LOG")
def note(s):
    if log:
        with open(log, "a") as fh:
            fh.write(s + "\n")
note("spawned")
args = sys.argv[1:]
outpath = args[args.index("-o") + 1] if "-o" in args else None
# Learn the id up front (as the real CLI does), then work: ownership must
# be observable for the whole run, not just at completion.
out({"type": "thread.started", "thread_id": tid})
if mode == "slow":
    time.sleep(float(os.environ.get("S2_SLEEP", "6")))
out({"type": "item.completed",
     "item": {"type": "agent_message", "text": "exec done"}})
if outpath:
    with open(outpath, "w") as fh:
        fh.write("exec done")
'''


def _run_exec_in_thread(tool, results, **kw):
    def _go():
        try:
            results.append(asyncio.run(tool.execute_prompt(**kw)))
        except BaseException as exc:
            results.append({"worker_error": repr(exc)})

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t


def test_exec_resume_acquires_before_spawn_and_releases(tmp_path, monkeypatch):
    from scripts.utilities.codex_cli_tool import CodexCliTool

    tid = "th-exec-1"
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_EXEC_MODE", "slow")
    monkeypatch.setenv("S2_SLEEP", "3")
    monkeypatch.setattr("scripts.utilities.codex_cli_tool.codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _FAKE_EXEC_SERVER))
    tool = CodexCliTool()
    results = []
    wt = _run_exec_in_thread(tool, results, prompt="go", cwd=str(tmp_path),
                             resume=tid, timeout=60)
    try:
        assert _poll(lambda: own.owner_of(tid) == "codex-exec", timeout=15.0), (
            "exec resume must own its thread from startup"
        )
        # A probe during the exec run skips instead of racing it.
        monkeypatch.setattr(cas, "codex_executable", lambda: "/fake/codex")
        _bomb_popen(monkeypatch)
        res = cas.fetch_codex_thread_token_usage(tid, cwd=str(tmp_path), timeout=5.0)
        assert res["success"] is False and res["status"] == "busy"
    finally:
        wt.join(30)
    assert results and results[0].get("success") is True
    assert results[0].get("output") == "exec done"
    assert own.owner_of(tid) is None


def test_exec_resume_busy_has_no_fallback(tmp_path, monkeypatch):
    from scripts.utilities import codex_cli_tool as cli

    tid = "th-exec-busy-1"
    monkeypatch.setattr(cli, "_OWNERSHIP_WAIT_SEC", 0.3)
    monkeypatch.setattr("scripts.utilities.codex_cli_tool.codex_executable",
                        lambda: "/fake/codex")

    def _bomb(*a, **k):
        raise AssertionError("busy exec spawned")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _bomb)
    token = own.try_acquire(tid, "probe")
    try:
        res = asyncio.run(
            cli.CodexCliTool().execute_prompt(
                "go", cwd=str(tmp_path), resume=tid, timeout=30))
        assert res.get("success") is False
        assert "busy" in (res.get("error") or "")
        assert "fallback" not in res  # exec IS the fallback: stay terminal
        assert own.owner_of(tid) == "probe"
    finally:
        assert own.release(tid, token) is True


def test_exec_fresh_acquires_learned_tid(tmp_path, monkeypatch):
    from scripts.utilities.codex_cli_tool import CodexCliTool

    tid = "th-exec-fresh-1"
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_EXEC_MODE", "slow")
    monkeypatch.setenv("S2_SLEEP", "2")
    monkeypatch.setattr("scripts.utilities.codex_cli_tool.codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _FAKE_EXEC_SERVER))
    tool = CodexCliTool()
    results = []
    wt = _run_exec_in_thread(tool, results, prompt="go", cwd=str(tmp_path),
                             timeout=60)
    try:
        assert _poll(lambda: own.owner_of(tid) == "codex-exec", timeout=15.0), (
            "fresh exec must claim its learned thread id"
        )
    finally:
        wt.join(30)
    assert results and results[0].get("success") is True
    assert own.owner_of(tid) is None


def test_exec_cancel_releases_lease(tmp_path, monkeypatch):
    from scripts.utilities.codex_cli_tool import CodexCliTool

    tid = "th-exec-cancel-1"
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_EXEC_MODE", "slow")
    monkeypatch.setenv("S2_SLEEP", "30")
    monkeypatch.setattr("scripts.utilities.codex_cli_tool.codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _FAKE_EXEC_SERVER))
    tool = CodexCliTool()
    ev = threading.Event()
    results = []
    wt = _run_exec_in_thread(tool, results, prompt="go", cwd=str(tmp_path),
                             resume=tid, timeout=120, cancel_event=ev)
    try:
        assert _poll(lambda: own.owner_of(tid) == "codex-exec", timeout=15.0)
        ev.set()
        wt.join(30)
        assert results and results[0].get("cancelled") is True
    finally:
        ev.set()
        wt.join(30)
    assert own.owner_of(tid) is None


def test_reap_confirmed_gate():
    """reap_confirmed is True ONLY on confirmed exit; it never raises."""

    class _Gone:
        returncode = 0

        def __init__(self):
            self.waited = False

        async def wait(self):
            self.waited = True
            return 0

    class _Hung:
        returncode = None

        async def wait(self):
            raise asyncio.TimeoutError("timed out")

    class _SlowExit:
        """wait() outlasts the bound with returncode unset: unconfirmed."""

        def __init__(self):
            self.returncode = None

        async def wait(self):
            await asyncio.sleep(5)
            return 0

    class _LyingWait:
        """wait() returns but no returncode is ever set: unconfirmed."""
        returncode = None

        async def wait(self):
            return 0

    class _SlowConfirm:
        """Normal reap: wait() returns and the returncode lands."""

        def __init__(self):
            self.returncode = None

        async def wait(self):
            await asyncio.sleep(0.01)
            self.returncode = 0
            return 0

    class _Exploding:
        @property
        def returncode(self):
            raise RuntimeError("gone")

        async def wait(self):
            raise AssertionError("must not wait")

    assert asyncio.run(own.reap_confirmed(None, timeout=0.2)) is True
    gone = _Gone()
    assert asyncio.run(own.reap_confirmed(gone, timeout=0.2)) is True
    assert gone.waited is False  # already-exited fast path never waits
    assert asyncio.run(own.reap_confirmed(_Hung(), timeout=0.2)) is False
    started = time.monotonic()
    assert asyncio.run(own.reap_confirmed(_SlowExit(), timeout=0.2)) is False
    assert time.monotonic() - started < 5.0  # bounded, not wedged
    assert asyncio.run(own.reap_confirmed(_LyingWait(), timeout=0.2)) is False
    assert asyncio.run(own.reap_confirmed(_SlowConfirm(), timeout=2.0)) is True
    assert asyncio.run(own.reap_confirmed(_Exploding(), timeout=0.2)) is False


def _spy_await_acquire(monkeypatch, tid, held):
    real_await = own.await_acquire

    async def _spy(tid_arg, owner, **kw):
        tok = await real_await(tid_arg, owner, **kw)
        if tid_arg == tid and tok is not None:
            held["token"] = tok
        return tok

    monkeypatch.setattr(own, "await_acquire", _spy)


def _never_reaps(monkeypatch):
    async def _never(proc, **kw):
        return False

    monkeypatch.setattr(own, "reap_confirmed", _never)


def test_turn_unconfirmed_exit_retains_lease(tmp_path, monkeypatch):
    """Cancelled turn with unconfirmed server exit keeps its lease."""
    import scripts.utilities.codex_app_server_turn as mod

    tid = "th-turn-retain-1"
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_MODE", "hold")
    monkeypatch.setattr(mod, "codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _FAKE_TURN_SERVER))
    _noop_store(monkeypatch)
    _never_reaps(monkeypatch)
    held = {}
    _spy_await_acquire(monkeypatch, tid, held)
    ev = threading.Event()
    results = []
    wt = _run_turn_in_thread(
        mod, results, prompt="go", cwd=str(tmp_path), resume=tid,
        model=None, reasoning_effort=None, chat_session_id="db_session_9010",
        timeout=60, cancel_event=ev,
    )
    try:
        assert _poll(lambda: own.owner_of(tid) == "codex-turn", timeout=15.0)
        ev.set()
        wt.join(30)
        assert results and results[0].get("cancelled") is True
        # Exit unconfirmed: the lease stays held — no second writer allowed.
        time.sleep(0.5)
        assert own.owner_of(tid) == "codex-turn"
    finally:
        ev.set()
        wt.join(30)
        # Production recovery for this exceptional held lease is a
        # restart; the test process is the owner here, so release it.
        if held.get("token") is not None:
            assert own.release(tid, held["token"]) is True
    assert own.owner_of(tid) is None


def test_exec_unconfirmed_exit_retains_lease(tmp_path, monkeypatch):
    """Successful exec with unconfirmed child exit keeps its lease."""
    from scripts.utilities.codex_cli_tool import CodexCliTool

    tid = "th-exec-retain-1"
    monkeypatch.setenv("S2_TID", tid)
    monkeypatch.setenv("S2_EXEC_MODE", "ok")
    monkeypatch.setattr("scripts.utilities.codex_cli_tool.codex_executable",
                        lambda: _fake_bin(tmp_path, "codex", _FAKE_EXEC_SERVER))
    _never_reaps(monkeypatch)
    held = {}
    _spy_await_acquire(monkeypatch, tid, held)
    try:
        res = asyncio.run(
            CodexCliTool().execute_prompt(
                "go", cwd=str(tmp_path), resume=tid, timeout=60))
        assert res.get("success") is True  # the exec itself succeeded
        assert res.get("output") == "exec done"
        # ...but with the child exit unconfirmed its lease is retained.
        assert own.owner_of(tid) == "codex-exec"
    finally:
        if held.get("token") is not None:
            assert own.release(tid, held["token"]) is True
    assert own.owner_of(tid) is None
