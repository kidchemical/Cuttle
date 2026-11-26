"""Shared interruptible CLI drain — preserve partials on timeout/cancel."""

from __future__ import annotations

import asyncio
import threading

import pytest

from scripts.utilities.agent_process import (
    format_interrupt_notice,
    run_interruptible,
)


class _HangReader:
    def __init__(self, lines):
        self._lines = list(lines)
        self._i = 0

    async def readline(self):
        if self._i < len(self._lines):
            line = self._lines[self._i]
            self._i += 1
            return line
        await asyncio.sleep(3600)
        return b""

    async def read(self, _size=-1):
        if self._i < len(self._lines):
            # chunk mode: dump remaining then hang
            data = b"".join(self._lines[self._i :])
            self._i = len(self._lines)
            if data:
                return data
        await asyncio.sleep(3600)
        return b""


class _FakeProc:
    def __init__(self, lines, *, stderr_lines=None):
        self.returncode = None
        self.stdout = _HangReader(lines)
        self.stderr = _HangReader(stderr_lines or [])
        self.pid = 4242

    async def wait(self):
        await asyncio.sleep(3600)
        return -1

    def kill(self):
        self.returncode = -9


@pytest.mark.asyncio
async def test_run_interruptible_preserves_stdout_on_idle_timeout(monkeypatch):
    real_deadline = __import__(
        "api.agent_harness.timeouts", fromlist=["ActivityDeadline"]
    ).ActivityDeadline

    def _capped(idle, **kw):
        kw.pop("absolute_timeout", None)
        return real_deadline(idle, absolute_timeout=0.8, **kw)

    monkeypatch.setattr("api.agent_harness.timeouts.ActivityDeadline", _capped)

    async def fake_kill(proc):
        proc.kill()

    monkeypatch.setattr(
        "scripts.utilities.agent_process.kill_process_tree", fake_kill
    )

    proc = _FakeProc([b"partial-one\n", b"partial-two\n"])
    result = await run_interruptible(proc, timeout=0.25, line_mode=True)
    assert result.timed_out is True
    assert b"partial-one" in result.stdout
    assert b"partial-two" in result.stdout
    assert result.cancelled is False


@pytest.mark.asyncio
async def test_run_interruptible_cancel_preserves_stdout(monkeypatch):
    async def fake_kill(proc):
        proc.kill()

    monkeypatch.setattr(
        "scripts.utilities.agent_process.kill_process_tree", fake_kill
    )

    cancel = threading.Event()

    async def _arm():
        await asyncio.sleep(0.12)
        cancel.set()

    proc = _FakeProc([b"halfway\n"])
    arm = asyncio.create_task(_arm())
    try:
        result = await run_interruptible(
            proc, timeout=30.0, cancel_event=cancel, line_mode=True
        )
    finally:
        arm.cancel()

    assert result.cancelled is True
    assert b"halfway" in result.stdout


def test_format_interrupt_notice_mentions_resume():
    text = format_interrupt_notice(
        "Codex",
        "idle for 60s",
        elapsed_sec=61,
        session_saved=True,
        resume_slash="codex",
    )
    assert "interrupted after 61s" in text
    assert "/codex" in text
    assert "resume" in text.lower()


@pytest.mark.asyncio
async def test_normal_exit_drains_late_stderr_session_trailer():
    """Hermes-style session ids arrive after stdout EOF and process exit."""
    exited = asyncio.Event()
    seen = []

    class Stdout:
        async def readline(self):
            return b""

    class Stderr:
        delivered = False

        async def read(self, size):
            if self.delivered:
                return b""
            await exited.wait()
            await asyncio.sleep(0)  # trailer follows the process wait callback
            self.delivered = True
            return b"session_id: saved-at-exit\n"

    class Process:
        stdout = Stdout()
        stderr = Stderr()
        returncode = None

        async def wait(self):
            self.returncode = 0
            exited.set()
            return 0

    result = await run_interruptible(
        Process(), timeout=3, on_stderr_chunk=seen.append,
    )
    assert result.returncode == 0
    assert result.stderr == b"session_id: saved-at-exit\n"
    assert seen == [result.stderr]
    assert not result.timed_out and not result.cancelled


@pytest.mark.asyncio
async def test_exited_cli_does_not_wait_for_pipe_held_by_grandchild(monkeypatch):
    """A server the agent started (e.g. ComfyUI) must not hold the turn open.

    CH-002779: Codex finished, but a detached grandchild kept the CLI's stdout
    pipe open, so the turn stayed running for hours until Stop killed it.
    """
    import scripts.utilities.agent_process as ap

    monkeypatch.setattr(ap, "EXITED_PIPE_QUIET_SEC", 0.2)
    seen = []
    proc = _FakeProc([b'{"method":"turn/completed"}\n'])
    proc.returncode = 0  # the CLI is gone; only its stdout pipe survives

    async def wait():
        return 0

    proc.wait = wait
    result = await asyncio.wait_for(
        run_interruptible(proc, timeout=60, on_stdout_line=seen.append), timeout=10
    )
    assert seen == [b'{"method":"turn/completed"}\n']
    assert result.returncode == 0
    assert not result.timed_out and not result.cancelled


@pytest.mark.asyncio
async def test_exited_cli_drain_is_capped_when_grandchild_keeps_logging(monkeypatch):
    import scripts.utilities.agent_process as ap

    monkeypatch.setattr(ap, "EXITED_PIPE_QUIET_SEC", 0.2)
    monkeypatch.setattr(ap, "EXITED_PIPE_MAX_SEC", 0.5)

    class Chatty:
        async def readline(self):
            await asyncio.sleep(0.05)
            return b"server log line\n"

    proc = _FakeProc([])
    proc.stdout = Chatty()
    proc.returncode = 0

    async def wait():
        return 0

    proc.wait = wait
    result = await asyncio.wait_for(run_interruptible(proc, timeout=60), timeout=10)
    assert result.returncode == 0
    assert result.stdout.startswith(b"server log line\n")
    assert not result.timed_out and not result.cancelled


@pytest.mark.asyncio
async def test_real_grandchild_holding_stdout_does_not_hang_the_drain():
    import sys
    import time

    # The grandchild outlives the CLI on the inherited pipe, then ends by itself.
    script = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(12)'],"
        " stdout=sys.stdout, stderr=sys.stderr)\n"
        "print('started', flush=True)\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    started = time.monotonic()
    lines = []
    result = await asyncio.wait_for(
        run_interruptible(proc, timeout=60, on_stdout_line=lines.append), timeout=30
    )
    assert result.returncode == 0
    assert time.monotonic() - started < 9
    assert [line.strip() for line in lines] == [b"started"]
