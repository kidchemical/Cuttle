"""
Track in-flight chat-backed subprocesses (e.g. Cursor Agent) by session id.

Deleting a chat session should cancel these runs so agents do not keep working
in the background after the conversation is gone.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Set

_lock = threading.Lock()
# session_key -> {
#   cancel: threading.Event,
#   procs: list[Popen],
#   pids: set[int],  # root + descendants snapshotted for orphan sweep
#   query_ids: set[str],
# }
_runs: Dict[str, Dict[str, Any]] = {}
# session_key -> watch job ids (survive after the kick subprocess exits)
_session_jobs: Dict[str, set] = {}


def _session_keys(session_id) -> List[str]:
    from api.session_keys import chat_session_keys

    return chat_session_keys(session_id)


def _new_entry() -> Dict[str, Any]:
    return {
        "cancel": threading.Event(),
        "procs": [],
        "pids": set(),
        "query_ids": set(),
    }


def begin_run(session_id, query_id: Optional[str] = None) -> threading.Event:
    """Start (or join) a cancellable run for this chat session. Returns cancel Event."""
    keys = _session_keys(session_id)
    if not keys:
        return threading.Event()
    sticky = False
    try:
        from api.chat_delivery import is_turn_cancelled

        sticky = is_turn_cancelled(session_id)
    except Exception:
        sticky = False
    with _lock:
        entry = None
        for k in keys:
            if k in _runs:
                entry = _runs[k]
                break
        if entry is None:
            entry = _new_entry()
        entry.setdefault("pids", set())
        if query_id:
            entry["query_ids"].add(str(query_id))
        for k in keys:
            _runs[k] = entry
        if sticky:
            entry["cancel"].set()
            return entry["cancel"]

    # Fresh user turn (try_begin cleared delivery sticky cancel). A prior
    # Stop may leave cancel set and the CLI still exiting — inheriting that
    # aborts the new turn instantly or races the old process. Kill + wait
    # *outside* the lock so cancel/followup cannot deadlock (CH-000513).
    ensure_session_procs_dead(session_id, timeout=20.0)
    with _lock:
        entry = None
        for k in keys:
            if k in _runs:
                entry = _runs[k]
                break
        if entry is None:
            entry = _new_entry()
            for k in keys:
                _runs[k] = entry
        if query_id:
            entry["query_ids"].add(str(query_id))
        entry["procs"] = []
        entry["pids"] = set()
        entry["cancel"] = threading.Event()
        for k in keys:
            _runs[k] = entry
        return entry["cancel"]


def attach_job(session_id, job_id: str) -> None:
    """Bind a watch-job id to this chat so Stop can cancel the worker tree."""
    ident = str(job_id or "").strip()
    keys = _session_keys(session_id)
    if not ident or not keys:
        return
    with _lock:
        for k in keys:
            _session_jobs.setdefault(k, set()).add(ident)


def session_job_ids(session_id) -> List[str]:
    keys = _session_keys(session_id)
    with _lock:
        found: set = set()
        for k in keys:
            found |= set(_session_jobs.get(k) or ())
        return sorted(found)


def clear_session_jobs(session_id, job_id: Optional[str] = None) -> None:
    keys = _session_keys(session_id)
    ident = str(job_id or "").strip()
    with _lock:
        for k in keys:
            jobs = _session_jobs.get(k)
            if not jobs:
                continue
            if ident:
                jobs.discard(ident)
            else:
                jobs.clear()
            if not jobs:
                _session_jobs.pop(k, None)


def attach_process(session_id, proc) -> None:
    """Register a subprocess so cancel_session_runs can kill it."""
    if proc is None:
        return
    keys = _session_keys(session_id)
    if not keys:
        return
    pid = getattr(proc, "pid", None)
    try:
        pid = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid = None
    if pid is not None and (isinstance(pid, bool) or pid <= 1):
        pid = None
    with _lock:
        entry = None
        for k in keys:
            if k in _runs:
                entry = _runs[k]
                break
        if entry is None:
            entry = _new_entry()
            for k in keys:
                _runs[k] = entry
        entry.setdefault("pids", set())
        if proc not in entry["procs"]:
            entry["procs"].append(proc)
        if pid:
            try:
                entry["pids"].add(int(pid))
            except (TypeError, ValueError):
                pass
            for child in list_descendant_pids(pid):
                entry["pids"].add(int(child))


def track_pid(session_id, pid) -> None:
    """Remember an extra PID (helper/orphan) for this session's kill sweep."""
    keys = _session_keys(session_id)
    if not keys:
        return
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return
    if isinstance(pid, bool) or pid_i <= 1:
        return
    with _lock:
        entry = None
        for k in keys:
            if k in _runs:
                entry = _runs[k]
                break
        if entry is None:
            entry = _new_entry()
            for k in keys:
                _runs[k] = entry
        entry.setdefault("pids", set()).add(pid_i)


def session_tracked_pids(session_id) -> List[int]:
    """PIDs registered for this chat (roots + snapshotted descendants)."""
    keys = _session_keys(session_id)
    with _lock:
        found: Set[int] = set()
        for k in keys:
            entry = _runs.get(k)
            if not entry:
                continue
            for proc in list(entry.get("procs") or []):
                pid = getattr(proc, "pid", None)
                if pid:
                    try:
                        found.add(int(pid))
                    except (TypeError, ValueError):
                        pass
            for pid in list(entry.get("pids") or ()):
                try:
                    found.add(int(pid))
                except (TypeError, ValueError):
                    pass
        return sorted(found)


def _proc_returncode(proc):
    """Exit code for both `subprocess.Popen` and `asyncio.subprocess.Process`.

    Agent CLIs are spawned either way; asyncio processes have no `poll()`, and
    treating that as "not running" made every asyncio-backed run invisible to
    cancel and to the restart drain check.
    """
    poll = getattr(proc, "poll", None)
    if callable(poll):
        return poll()
    return getattr(proc, "returncode", None)


def _proc_alive(proc) -> bool:
    if proc is None:
        return False
    try:
        if _proc_returncode(proc) is not None:
            return False
    except Exception:
        return False
    if callable(getattr(proc, "poll", None)):
        return True
    # asyncio only sets returncode when its own loop reaps the child; once that
    # loop is gone (adapter raised mid-read) it reads None forever.
    pid = getattr(proc, "pid", None)
    return pid is None or pid_alive(pid)


def pid_alive(pid) -> bool:
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_i <= 0:
        return False
    try:
        import psutil

        if not psutil.pid_exists(pid_i):
            return False
        p = psutil.Process(pid_i)
        if not p.is_running():
            return False
        try:
            # An unreaped child has exited; it is not work in flight.
            return p.status() != psutil.STATUS_ZOMBIE
        except psutil.ZombieProcess:
            return False
    except Exception:
        return False


def list_descendant_pids(root_pid) -> List[int]:
    """Descendants of ``root_pid``. Empty for init/self/ancestors (never PID 1's tree)."""
    try:
        from api.process_kill_safety import coerce_pid, is_forbidden_kill_target, may_kill_pid

        root = coerce_pid(root_pid)
        if root is None or is_forbidden_kill_target(root) or not may_kill_pid(root):
            return []
    except Exception:
        try:
            root = int(root_pid)
        except (TypeError, ValueError):
            return []
        if root <= 1:
            return []
    try:
        import psutil

        proc = psutil.Process(root)
        out: List[int] = []
        for child in proc.children(recursive=True):
            try:
                cid = int(child.pid)
            except (TypeError, ValueError):
                continue
            if cid <= 1:
                continue
            out.append(cid)
        return out
    except Exception:
        return []


def _wait_for_exit(proc, timeout: float) -> None:
    """Block until the process is gone, without awaiting an asyncio coroutine.

    `asyncio.subprocess.Process.wait()` only completes on its own event loop,
    which is not this thread, so poll the return code instead.
    """
    if callable(getattr(proc, "poll", None)):
        try:
            proc.wait(timeout=timeout)
            return
        except Exception:
            return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _proc_alive(proc):
            return
        time.sleep(0.1)


def wait_pid_dead(pid, timeout: float = 5.0) -> bool:
    deadline = time.time() + max(0.0, float(timeout))
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


def _prune_dead_procs(entry: Dict[str, Any]) -> None:
    """Drop finished subprocesses so end_run can release the session slot."""
    # Snapshot children while a wrapper is still alive — Codex's .cmd often
    # exits after spawning the long-lived CLI, and late children were missed
    # if we only captured PIDs at attach time.
    pids = entry.setdefault("pids", set())
    for proc in list(entry.get("procs") or []):
        pid = getattr(proc, "pid", None)
        try:
            pid_i = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_i = None
        if pid_i is None or pid_i <= 1 or not _proc_alive(proc):
            continue
        pids.add(pid_i)
        for child in list_descendant_pids(pid_i):
            try:
                pids.add(int(child))
            except (TypeError, ValueError):
                pass
    live = []
    for proc in list(entry.get("procs") or []):
        if _proc_alive(proc):
            live.append(proc)
    entry["procs"] = live
    alive_pids: Set[int] = set()
    for pid in list(entry.get("pids") or ()):
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            continue
        if pid_alive(pid_i):
            alive_pids.add(pid_i)
    entry["pids"] = alive_pids


def end_run(session_id, query_id: Optional[str] = None, proc=None) -> None:
    """Drop a finished run (or just one process) for this session."""
    keys = _session_keys(session_id)
    if not keys:
        return
    with _lock:
        entry = None
        for k in keys:
            if k in _runs:
                entry = _runs[k]
                break
        if not entry:
            return
        if proc is not None and proc in entry["procs"]:
            try:
                entry["procs"].remove(proc)
            except ValueError:
                pass
        if query_id:
            entry["query_ids"].discard(str(query_id))
        # Always drop exited procs. Callers often invoke end_run(session, query_id=...)
        # without the Popen handle — leaving dead procs used to pin the registry
        # entry forever and make cancel/history think the chat was still running.
        _prune_dead_procs(entry)
        if not entry["procs"] and not entry.get("pids"):
            for k in keys:
                _runs.pop(k, None)
    try:
        from api.flask_restart import maybe_fire_when_idle

        maybe_fire_when_idle()
    except Exception:
        pass


def live_query_ids(session_id) -> List[str]:
    """Query ids tracked for this chat's in-flight run (for active-work dedupe)."""
    keys = _session_keys(session_id)
    if not keys:
        return []
    with _lock:
        for k in keys:
            entry = _runs.get(k)
            if entry:
                return [str(q) for q in (entry.get("query_ids") or set())]
    return []


def has_live_process(session_id) -> bool:
    """True if this chat still has a tracked subprocess/PID that has not exited."""
    keys = _session_keys(session_id)
    if not keys:
        return False
    with _lock:
        entry = None
        for k in keys:
            if k in _runs:
                entry = _runs[k]
                break
        if not entry:
            return False
        _prune_dead_procs(entry)
        if not entry["procs"] and not entry.get("pids"):
            # No live procs — drop the empty entry so cancel/history stay accurate.
            for k in keys:
                _runs.pop(k, None)
            return False
        return True


def active_run_session_ids() -> List[str]:
    """Bare session ids that still have a live tracked subprocess."""
    from api.session_keys import bare_chat_session_id

    out: List[str] = []
    seen = set()
    with _lock:
        for k, entry in list(_runs.items()):
            if not entry:
                continue
            _prune_dead_procs(entry)
            if not entry["procs"] and not entry.get("pids"):
                # Sweep empty aliases for this entry
                for kk, vv in list(_runs.items()):
                    if vv is entry:
                        _runs.pop(kk, None)
                continue
            # Registry stores every alias (478 / db_session_478 / CH-000478).
            # Collapse to one canonical id or restart drain double-counts.
            bare = bare_chat_session_id(k)
            if not bare or bare in seen:
                continue
            seen.add(bare)
            out.append(bare)
    return out


def is_session_busy(session_id) -> bool:
    return has_live_process(session_id)


def kill_pid_tree(pid) -> bool:
    """Kill ``pid`` and its children (``taskkill /T`` on Windows).

    Refuses PID 1, the current process, its parent/ancestors, and any PID that
    is not a descendant of this process (stale job-status PIDs after a reboot).
    If signaling the root is denied (EPERM), descendants are **not** swept —
    listing children of PID 1 is the whole machine.
    """
    try:
        from api.process_kill_safety import coerce_pid, is_forbidden_kill_target, may_kill_pid
    except Exception:
        coerce_pid = None  # type: ignore[assignment]

    if coerce_pid is not None:
        pid_i = coerce_pid(pid)
        if pid_i is None or is_forbidden_kill_target(pid_i) or not may_kill_pid(pid_i):
            return False
    else:
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            return False
        if pid_i <= 1 or pid_i == os.getpid():
            return False
    try:
        from api.cuttle_managed_process_guard import list_managed_python_pids

        if pid_i in list_managed_python_pids():
            return False
    except Exception:
        pass
    descendants = list_descendant_pids(pid_i)
    killed = False
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid_i)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            killed = True
        else:
            os.kill(pid_i, 15)
            killed = True
    except PermissionError:
        return False
    except ProcessLookupError:
        killed = False
    except Exception:
        return False
    for child in descendants:
        try:
            child_i = int(child)
        except (TypeError, ValueError):
            continue
        if coerce_pid is not None:
            if is_forbidden_kill_target(child_i) or not may_kill_pid(child_i):
                continue
        elif child_i <= 1 or child_i == os.getpid():
            continue
        if pid_alive(child_i):
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(child_i)],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                else:
                    os.kill(child_i, 9)
            except PermissionError:
                continue
            except Exception:
                pass
        wait_pid_dead(child_i, timeout=3.0)
    wait_pid_dead(pid_i, timeout=5.0)
    return killed


def kill_process_tree(proc) -> bool:
    """Kill a spawned CLI *and its children*.

    Agent CLIs spawn helpers (Codex starts `codex-code-mode-host`); killing only
    the parent leaves those running as orphans and holds the session writer lock
    (CH-000513). Snapshot the tree first, then sweep any survivors.
    """
    if proc is None:
        return False
    pid = getattr(proc, "pid", None)
    try:
        pid = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid = None
    if pid is not None and pid <= 0:
        pid = None
    alive_before = _proc_alive(proc) or (bool(pid) and pid_alive(pid))
    if not alive_before and not pid:
        return False
    killed = bool(pid) and kill_pid_tree(pid)
    if not killed:
        try:
            proc.terminate()
            killed = True
        except Exception:
            try:
                proc.kill()
                killed = True
            except Exception:
                pass
    _wait_for_exit(proc, 5)
    if _proc_alive(proc):
        try:
            proc.kill()
        except Exception:
            pass
        _wait_for_exit(proc, 5)
    if pid:
        wait_pid_dead(pid, timeout=5.0)
        for child in list_descendant_pids(pid):
            if pid_alive(child):
                kill_pid_tree(child)
    return killed


def _kill_proc(proc) -> bool:
    return kill_process_tree(proc)


def ensure_session_procs_dead(session_id, timeout: float = 15.0) -> bool:
    """Kill every tracked proc/PID for this chat and wait until they are gone.

    Used by Stop and by the followup ``begin_run`` path so a resumed CLI session
    never races an orphaned writer (CH-000513).
    """
    keys = _session_keys(session_id)
    if not keys:
        return True
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        with _lock:
            entry = None
            for k in keys:
                if k in _runs:
                    entry = _runs[k]
                    break
            if entry is None:
                return True
            procs = list(entry.get("procs") or [])
            pids: Set[int] = set()
            for p in entry.get("pids") or set():
                try:
                    pids.add(int(p))
                except (TypeError, ValueError):
                    pass
            for proc in procs:
                pid = getattr(proc, "pid", None)
                if pid:
                    try:
                        pids.add(int(pid))
                    except (TypeError, ValueError):
                        pass
                    for child in list_descendant_pids(pid):
                        pids.add(int(child))
            entry["pids"] = set(pids)
            real_procs = []
            for proc in procs:
                raw = getattr(proc, "pid", None)
                try:
                    n = int(raw) if raw is not None else None
                except (TypeError, ValueError):
                    n = None
                if n is not None and n > 0:
                    real_procs.append(proc)
            procs = real_procs
            entry["procs"] = list(procs)
            if not pids and not procs:
                return True

        for proc in procs:
            if _proc_alive(proc):
                _kill_proc(proc)
        for pid in list(pids):
            if pid_alive(pid):
                kill_pid_tree(pid)

        with _lock:
            entry = None
            for k in keys:
                if k in _runs:
                    entry = _runs[k]
                    break
            if entry is not None:
                _prune_dead_procs(entry)
                if not entry.get("procs") and not entry.get("pids"):
                    return True
        time.sleep(0.1)

    with _lock:
        entry = None
        for k in keys:
            if k in _runs:
                entry = _runs[k]
                break
        if entry is None:
            return True
        _prune_dead_procs(entry)
        return not entry.get("procs") and not entry.get("pids")


def _release_delivery(session_id) -> None:
    """Unblock sending and drop any reply held for a run we just cancelled."""
    try:
        from api import chat_delivery

        chat_delivery.cancel_current_turn(session_id)
    except Exception:
        try:
            from api import chat_delivery

            chat_delivery.end(session_id)
            chat_delivery.clear_result(session_id)
        except Exception:
            pass


def is_run_cancelled(session_id) -> bool:
    keys = _session_keys(session_id)
    with _lock:
        for k in keys:
            entry = _runs.get(k)
            if entry and entry["cancel"].is_set():
                return True
    try:
        from api.chat_delivery import is_turn_cancelled

        return is_turn_cancelled(session_id)
    except Exception:
        return False


def cancel_session_runs(session_id) -> Dict[str, Any]:
    """
    Cancel all tracked runs for a chat session: signal cancel, kill procs,
    unregister query executions, clear live chat status.
    """
    keys = _session_keys(session_id)
    result: Dict[str, Any] = {
        "cancelled": False,
        "killed_procs": 0,
        "query_ids": [],
        "orphans_cleared": True,
    }
    if not keys:
        return result

    # Invalidate the delivery turn even when no CLI has been spawned yet.
    # Otherwise Stop only drops the SSE stream and the late reply still persists.
    _release_delivery(session_id)

    with _lock:
        entry = None
        for k in keys:
            if k in _runs:
                entry = _runs[k]
                break
        if entry is None:
            entry = _new_entry()
            for k in keys:
                _runs[k] = entry
        entry.setdefault("pids", set())
        entry["cancel"].set()
        result["cancelled"] = True
        result["query_ids"] = list(entry.get("query_ids") or [])
        procs = list(entry.get("procs") or [])
        for proc in procs:
            pid = getattr(proc, "pid", None)
            if pid:
                try:
                    entry["pids"].add(int(pid))
                except (TypeError, ValueError):
                    pass
                for child in list_descendant_pids(pid):
                    entry["pids"].add(int(child))

    for proc in procs:
        if _kill_proc(proc):
            result["killed_procs"] += 1

    # Sweep + wait so followup resume cannot race an orphaned writer.
    result["orphans_cleared"] = ensure_session_procs_dead(session_id, timeout=20.0)

    for qid in result["query_ids"]:
        try:
            from api.active_executions import unregister_execution

            unregister_execution(qid)
        except Exception:
            pass

    try:
        from api.chat_live_status import clear_live_status

        clear_live_status(session_id)
    except Exception:
        pass

    return result
