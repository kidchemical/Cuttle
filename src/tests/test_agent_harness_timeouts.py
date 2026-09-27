"""Activity-aware turn budgets (idle timeout + absolute runaway cap).

Long productive harness turns must not be killed by the kernel's wall clock;
only turns that go silent (or run away chatty forever) get aborted.
"""

from __future__ import annotations

import asyncio

import pytest

from api.agent_harness.timeouts import ActivityDeadline


# ── ActivityDeadline unit tests (deterministic fake clock) ───────────────────

class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_deadline_healthy_then_idle_expiry():
    clock = _FakeClock()
    d = ActivityDeadline(60.0, monotonic=clock)
    assert d.check() is None
    clock.advance(59.0)
    assert d.check() is None
    clock.advance(2.0)  # idle past budget
    reason = d.check()
    assert reason and "idle for 60s" in reason and "activity timeout" in reason


def test_deadline_poke_resets_idle_window():
    clock = _FakeClock()
    d = ActivityDeadline(60.0, monotonic=clock)
    clock.advance(50.0)
    d.poke()  # activity observed
    clock.advance(50.0)
    assert d.check() is None  # 50s since last poke, not 100s
    clock.advance(11.0)
    assert d.check() and "idle" in d.check()


def test_deadline_absolute_cap_fires_despite_activity():
    clock = _FakeClock()
    d = ActivityDeadline(60.0, absolute_timeout=120.0, monotonic=clock)
    for _ in range(5):
        clock.advance(30.0)
        d.poke()  # constantly active, but past the absolute cap
    reason = d.check()
    assert reason and "absolute turn cap of 120s" in reason


def test_deadline_default_absolute_is_four_times_idle():
    clock = _FakeClock()
    d = ActivityDeadline(1800.0, monotonic=clock)
    assert d.absolute_timeout == pytest.approx(7200.0)


def test_deadline_next_wake_never_overshoots():
    # absolute edge is the binding constraint (idle recently poked)
    clock = _FakeClock()
    d = ActivityDeadline(100.0, absolute_timeout=110.0, monotonic=clock)
    clock.advance(105.0)  # elapsed 105 → 5s left on the absolute cap
    d.poke()
    assert d.next_wake(poll=8.0) == pytest.approx(5.0)
    assert d.next_wake(poll=1.0) == pytest.approx(1.0)  # poll clamps

    # idle edge is the binding constraint
    clock2 = _FakeClock()
    d2 = ActivityDeadline(10.0, absolute_timeout=100.0, monotonic=clock2)
    clock2.advance(9.5)  # 0.5s of idle budget left
    assert d2.next_wake(poll=1.0) == pytest.approx(0.5)


# ── OpenCode adapter integration (fake subprocess) ───────────────────────────

def _patch_spawn(monkeypatch, oc, proc):
    async def _spawn(*args, **kwargs):
        return proc

    async def _no_kill(p):
        return None

    monkeypatch.setattr(oc, "opencode_executable", lambda: "opencode.exe")
    monkeypatch.setattr(oc.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(oc, "attach_to_chat_run", lambda *a, **k: None)
    monkeypatch.setattr(oc, "kill_process_tree", _no_kill)


class _Writer:
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, data):
        self.written += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class _Reader:
    def __init__(self, lines, delay=0.0, hang_after=None):
        self._lines = list(lines)
        self._delay = delay
        self._hang_after = hang_after
        self._served = 0

    async def readline(self):
        if self._hang_after is not None and self._served >= self._hang_after:
            await asyncio.sleep(3600)
        if self._delay:
            await asyncio.sleep(self._delay)
        if not self._lines:
            return b""
        self._served += 1
        return self._lines.pop(0)


class _Proc:
    def __init__(self, out_lines, err_lines=None, returncode=0, out_delay=0.0):
        self.stdin = _Writer()
        self.stdout = _Reader(out_lines, delay=out_delay)
        self.stderr = _Reader(err_lines or [])
        self.returncode = returncode
        self.killed = False

    async def wait(self):
        return self.returncode


def test_opencode_silent_run_hits_idle_timeout(monkeypatch, tmp_path):
    from api.agent_harness.agents.opencode import adapter as oc

    # stdout hangs (never emits, never EOF) → no activity → idle expiry
    proc = _Proc([])
    proc.stdout = _Reader([], hang_after=0)
    _patch_spawn(monkeypatch, oc, proc)

    result = asyncio.run(
        oc.Adapter().execute("long task", cwd=str(tmp_path), resume=None, model=None, timeout=0.3)
    )
    assert result.success is False
    assert "idle" in result.error and "activity timeout" in result.error


def test_opencode_active_run_survives_past_idle_budget(monkeypatch, tmp_path):
    from api.agent_harness.agents.opencode import adapter as oc

    # 8 lines arriving every 0.08s (~0.64s total) with a 0.25s idle budget —
    # every line pokes the deadline, so the run must complete successfully.
    lines = [f'{{"text":"step {i}"}}\n'.encode() for i in range(8)]
    proc = _Proc(lines, out_delay=0.08)
    _patch_spawn(monkeypatch, oc, proc)

    result = asyncio.run(
        oc.Adapter().execute("long task", cwd=str(tmp_path), resume=None, model=None, timeout=0.25)
    )
    assert result.success is True
    assert "step 7" in result.output


def test_opencode_chatty_runaway_hits_absolute_cap(monkeypatch, tmp_path):
    from api.agent_harness.agents.opencode import adapter as oc

    # Lines keep flowing (never idle) but the absolute cap stops the run.
    # The adapter derives its own default cap (4x idle), so force a tiny one
    # via the injectable constructor to keep the test fast.
    real_deadline = oc.ActivityDeadline

    def _capped(idle, **kw):
        return real_deadline(idle, absolute_timeout=0.5, **kw)

    monkeypatch.setattr(oc, "ActivityDeadline", _capped)

    lines = [b'{"text":"noise"}\n'] * 1000
    proc = _Proc(lines, out_delay=0.05)
    _patch_spawn(monkeypatch, oc, proc)

    result = asyncio.run(
        oc.Adapter().execute(
            "runaway",
            cwd=str(tmp_path),
            resume=None,
            model=None,
            timeout=0.2,
        )
    )
    assert result.success is False
    assert "absolute" in result.error


def test_opencode_timeout_preserves_partial_output(monkeypatch, tmp_path):
    from api.agent_harness.agents.opencode import adapter as oc

    # Two quick lines, then silence → idle expiry, partial output preserved.
    proc = _Proc([b'{"text":"partial one"}\n', b'{"text":"partial two"}\n'], out_delay=0.01)
    proc.stdout = _Reader([b'{"text":"partial one"}\n', b'{"text":"partial two"}\n'], hang_after=2)
    _patch_spawn(monkeypatch, oc, proc)

    result = asyncio.run(
        oc.Adapter().execute("task", cwd=str(tmp_path), resume=None, model=None, timeout=0.3)
    )
    assert result.success is False
    assert "idle" in result.error
    assert "partial one" in result.output and "partial two" in result.output


def test_opencode_stderr_activity_counts_as_progress(monkeypatch, tmp_path):
    from api.agent_harness.agents.opencode import adapter as oc

    # stdout silent, but stderr keeps logging → the turn must stay alive.
    class _ErrReader:
        def __init__(self, count, delay):
            self._count = count
            self._delay = delay
            self._served = 0

        async def readline(self):
            if self._served >= self._count:
                await asyncio.sleep(3600)
            await asyncio.sleep(self._delay)
            self._served += 1
            return b"[log] working\n"

    proc = _Proc([])
    proc.stderr = _ErrReader(count=8, delay=0.08)
    _patch_spawn(monkeypatch, oc, proc)

    result = asyncio.run(
        oc.Adapter().execute("task", cwd=str(tmp_path), resume=None, model=None, timeout=0.25)
    )
    assert result.success is True
