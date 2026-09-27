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
