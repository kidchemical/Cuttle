"""Generic long-running process helper for mesh jobs.

Uses soft (idle) + hard (absolute) timeouts instead of a single wall-clock
``subprocess.run(timeout=)``. Progress can come from:

- durable unit inventory (e.g. frames on disk)
- stdout/stderr activity
- process-alive pulses (``progress_mode="alive"``)

No engine-specific profiles — callers pass timeouts / a progress poller.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union


@dataclass
class ProgressSnapshot:
    units_done: int = 0
    units_total: Optional[int] = None
    last_unit_id: Optional[str] = None
    message: str = ""
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "units_done": int(self.units_done),
            "units_total": (
                int(self.units_total) if self.units_total is not None else None
            ),
            "last_unit_id": self.last_unit_id,
            "message": (self.message or "")[:500],
            "updated_at": float(self.updated_at),
        }


class IdleTimeoutError(TimeoutError):
    """No progress for idle_timeout_seconds."""

    def __init__(self, message: str, *, elapsed: float, idle: float):
        super().__init__(message)
        self.elapsed = elapsed
        self.idle = idle


class HardTimeoutError(TimeoutError):
    """Absolute hard_timeout_seconds exceeded."""

    def __init__(self, message: str, *, elapsed: float, hard: float):
        super().__init__(message)
        self.elapsed = elapsed
        self.hard = hard


@dataclass
class LongRunResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    progress: ProgressSnapshot
    timed_out: Optional[str] = None  # "idle" | "hard" | None


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            # Kill the whole tree; CREATE_NEW_PROCESS_GROUP helps isolation.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=15)
    except Exception:
        pass


def run_long_process(
    cmd: Union[str, Sequence[str]],
    *,
    hard_timeout_seconds: float,
    idle_timeout_seconds: float = 0,
    progress_mode: str = "output",
    progress_poll: Optional[Callable[[], Optional[ProgressSnapshot]]] = None,
    on_progress: Optional[Callable[[ProgressSnapshot], None]] = None,
    poll_interval: float = 2.0,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    shell: bool = False,
    creationflags: int = 0,
) -> LongRunResult:
    """Run a subprocess with soft idle + hard absolute timeouts.

    ``idle_timeout_seconds <= 0`` disables the soft deadline.
    ``progress_mode``:
      - ``output``: any stdout/stderr bytes reset the idle clock
      - ``alive``: process still running resets the idle clock (hard timeout only
        is meaningful unless idle is also set as a safety net — typically idle=0)
      - ``units``: only ``progress_poll`` advances count as progress (no output reset)
    """
    hard = max(1.0, float(hard_timeout_seconds))
    idle = max(0.0, float(idle_timeout_seconds or 0))
    mode = (progress_mode or "output").strip().lower()
    if mode not in ("output", "alive", "units"):
        mode = "output"
    interval = max(0.5, float(poll_interval))

    started = time.time()
    last_progress_at = started
    snapshot = ProgressSnapshot(updated_at=started)
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []
    stdout_lock = threading.Lock()

    popen_kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": cwd,
        "env": env,
        "shell": shell,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if creationflags:
        popen_kwargs["creationflags"] = creationflags
    if os.name == "nt" and not shell:
        # New process group so taskkill /T can tear down children (Blender).
        popen_kwargs["creationflags"] = int(
            popen_kwargs.get("creationflags") or 0
        ) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    elif os.name != "nt":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)

    def _reader(stream, bucket: List[str], *, counts_as_output: bool) -> None:
        nonlocal last_progress_at
        try:
            while True:
                line = stream.readline()
                if line == "" and proc.poll() is not None:
                    break
                if not line:
                    time.sleep(0.05)
                    continue
                with stdout_lock:
                    bucket.append(line)
                    if len(bucket) > 200:
                        del bucket[:-100]
                if counts_as_output and mode == "output":
                    last_progress_at = time.time()
        except Exception:
            pass

    t_out = threading.Thread(
        target=_reader,
        args=(proc.stdout, stdout_chunks),
        kwargs={"counts_as_output": True},
        daemon=True,
    )
    t_err = threading.Thread(
        target=_reader,
        args=(proc.stderr, stderr_chunks),
        kwargs={"counts_as_output": True},
        daemon=True,
    )
    t_out.start()
    t_err.start()

    timed_out: Optional[str] = None
    try:
        while True:
            rc = proc.poll()
            now = time.time()

            if progress_poll is not None:
                try:
                    polled = progress_poll()
                except Exception:
                    polled = None
                if polled is not None:
                    advanced = (
                        int(polled.units_done) > int(snapshot.units_done)
                        or (
                            polled.last_unit_id
                            and polled.last_unit_id != snapshot.last_unit_id
                        )
                    )
                    snapshot = ProgressSnapshot(
                        units_done=int(polled.units_done),
                        units_total=polled.units_total,
                        last_unit_id=polled.last_unit_id,
                        message=polled.message or snapshot.message,
                        updated_at=now,
                    )
                    if advanced:
                        last_progress_at = now
                        if on_progress is not None:
                            try:
                                on_progress(snapshot)
                            except Exception:
                                pass

            if mode == "alive" and rc is None:
                last_progress_at = now

            if rc is not None:
                break

            if now - started >= hard:
                timed_out = "hard"
                _kill_process_tree(proc)
                raise HardTimeoutError(
                    f"hard timeout after {int(hard)}s",
                    elapsed=now - started,
                    hard=hard,
                )

            if idle > 0 and (now - last_progress_at) >= idle:
                timed_out = "idle"
                _kill_process_tree(proc)
                raise IdleTimeoutError(
                    f"idle timeout after {int(idle)}s without progress",
                    elapsed=now - started,
                    idle=idle,
                )

            time.sleep(interval)
    finally:
        if proc.poll() is None:
            _kill_process_tree(proc)
        try:
            t_out.join(timeout=2)
            t_err.join(timeout=2)
        except Exception:
            pass
        try:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass

    with stdout_lock:
        out = "".join(stdout_chunks)
        err = "".join(stderr_chunks)

    return LongRunResult(
        returncode=int(proc.returncode if proc.returncode is not None else -1),
        stdout=out,
        stderr=err,
        elapsed_seconds=round(time.time() - started, 2),
        progress=snapshot,
        timed_out=timed_out,
    )
