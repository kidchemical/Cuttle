"""
Shared subprocess lifecycle helpers for the agent CLI tools (Codex, Hermes,
Gemini, Muse).

Two things every agent CLI needs and used to get wrong independently:

* **Registration** — an unregistered run is invisible to Stop/cancel and to the
  restart drain check, and its chat busy lock gets reaped as a zombie after 90s,
  so the UI shows an idle chat while the CLI is still running.
* **Tree kill** — these CLIs spawn helper children (Codex starts
  `codex-code-mode-host`); killing only the parent orphans them.
* **Interruptible drain** — wall-clock ``communicate()`` / ``wait()`` that
  discards buffered stdout on timeout loses work the CLI already streamed.
  ``run_interruptible`` keeps partials and uses an activity-aware deadline.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


def attach_to_chat_run(chat_session_id: Optional[str], proc) -> None:
    """Register a spawned CLI with the chat run registry (best effort)."""
    if not chat_session_id or proc is None:
        return
    try:
        from api.chat_run_registry import attach_process

        attach_process(chat_session_id, proc)
    except Exception:
        pass


async def kill_process_tree(proc) -> None:
    """Kill an asyncio-spawned CLI and all of its children (no orphans)."""
    if proc is None:
        return
    # Prefer the registry helper so descendant snapshots / reparented helpers
    # (Codex ``codex-code-mode-host``) are swept the same way Stop does.
    try:
        from api.chat_run_registry import kill_process_tree as _sync_kill

        _sync_kill(proc)
        return
    except Exception:
        pass
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and pid:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=15)
        except Exception:
            pass
    try:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    except ProcessLookupError:
        pass
    except Exception:
        pass


@dataclass
class InterruptibleRunResult:
    """Outcome of ``run_interruptible`` — always carries whatever was buffered."""

    stdout: bytes = b""
    stderr: bytes = b""
    returncode: Optional[int] = None
    timed_out: bool = False
    cancelled: bool = False
    reason: Optional[str] = None
    elapsed_sec: float = 0.0
    meta: dict = field(default_factory=dict)


def format_interrupt_notice(
    agent_label: str,
    reason: str,
    *,
    elapsed_sec: float = 0.0,
    session_saved: bool = False,
    last_activity: str = "",
    resume_slash: str = "",
) -> str:
    """Short markdown footer for chat when a CLI turn is killed mid-stream."""
    lines = [
        f"*{agent_label} interrupted after {int(elapsed_sec)}s — {reason}.*"
    ]
    if last_activity and last_activity not in ("starting", "running"):
        lines.append(f"*Last activity: {last_activity}*")
    slash = (resume_slash or "").strip() or agent_label.split()[0].lower()
    if session_saved:
        lines.append(
            f"*Session resume was saved — the next `/{slash}` turn continues "
            "the same CLI transcript.*"
        )
    else:
        lines.append(
            "*No session id was captured before the interrupt — this turn's "
            "CLI-local transcript may not resume.*"
        )
    return "\n".join(lines)


async def run_interruptible(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float,
    cancel_event: Any = None,
    on_stdout_line: Optional[Callable[[bytes], None]] = None,
    on_stderr_chunk: Optional[Callable[[bytes], None]] = None,
    absolute_timeout: Optional[float] = None,
    line_mode: bool = True,
) -> InterruptibleRunResult:
    """Drain a CLI subprocess with an activity-aware budget.

    ``timeout`` is the *idle* budget (resets on stdout/stderr). An absolute
    runaway cap still applies (see ``ActivityDeadline``). On timeout or
    cancel, buffered stdout/stderr are returned instead of discarded.
    """
    from api.agent_harness.timeouts import ActivityDeadline

    started = time.monotonic()
    deadline = (
        ActivityDeadline(timeout)
        if absolute_timeout is None
        else ActivityDeadline(timeout, absolute_timeout=absolute_timeout)
    )
    stdout_parts: List[bytes] = []
    stderr_parts: List[bytes] = []
    timed_out_reason: Optional[str] = None
    was_cancelled = False

    async def _pump_stderr() -> None:
        stderr = getattr(proc, "stderr", None)
        if stderr is None:
            return
        try:
            while True:
                chunk = await stderr.read(4096)
                if not chunk:
                    return
                deadline.poke()
                stderr_parts.append(chunk)
                if on_stderr_chunk is not None:
                    try:
                        on_stderr_chunk(chunk)
                    except Exception:
                        pass
        except Exception:
            return

    err_task = asyncio.create_task(_pump_stderr())
    try:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            was_cancelled = True
            await kill_process_tree(proc)
        elif getattr(proc, "stdout", None) is None and getattr(proc, "stderr", None) is None:
            # Legacy test stubs / odd spawns with no pipes — wait with a hard cap.
            try:
                await asyncio.wait_for(proc.wait(), timeout=max(1.0, float(timeout)))
            except asyncio.TimeoutError:
                timed_out_reason = f"timed out after {float(timeout):.0f}s"
                await kill_process_tree(proc)
        else:
            while True:
                if cancel_event is not None and getattr(
                    cancel_event, "is_set", lambda: False
                )():
                    was_cancelled = True
                    await kill_process_tree(proc)
                    break
                expiry = deadline.check()
                if expiry:
                    timed_out_reason = expiry
                    await kill_process_tree(proc)
                    break
                poll = deadline.next_wake(poll=1.0)
                stdout = getattr(proc, "stdout", None)
                if stdout is None:
                    break
                try:
                    if line_mode:
                        chunk = await asyncio.wait_for(stdout.readline(), timeout=poll)
                    else:
                        chunk = await asyncio.wait_for(stdout.read(65536), timeout=poll)
                except asyncio.TimeoutError:
                    continue
                except ValueError:
                    # Oversized line vs stream limit — drop and keep going.
                    deadline.poke()
                    continue
                if not chunk:
                    break
                deadline.poke()
                stdout_parts.append(chunk)
                if on_stdout_line is not None:
                    try:
                        on_stdout_line(chunk)
                    except Exception:
                        pass

            if not was_cancelled and timed_out_reason is None:
                try:
                    await proc.wait()
                except ProcessLookupError:
                    pass
    finally:
        err_task.cancel()
        try:
            await err_task
        except (asyncio.CancelledError, Exception):
            pass

    elapsed = time.monotonic() - started
    reason = None
    if was_cancelled:
        reason = "cancelled (Stop or chat deleted)"
    elif timed_out_reason:
        reason = timed_out_reason

    return InterruptibleRunResult(
        stdout=b"".join(stdout_parts),
        stderr=b"".join(stderr_parts),
        returncode=getattr(proc, "returncode", None),
        timed_out=bool(timed_out_reason),
        cancelled=was_cancelled,
        reason=reason,
        elapsed_sec=elapsed,
    )
