"""Pollable job status for Cuttle action-form progress bars.

Writes ``<home>/output/<id>-status.json``, served as ``/output/<id>-status.json``.
CLI: ``python -m api.job_watch write --id my-job --state running --percent 40 --label "…"``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_STATES = ("running", "done", "failed")


def output_dir() -> Path:
    from core.runtime_paths import output_dir as home_output_dir

    return home_output_dir()


def status_path(job_id: str) -> Path:
    return output_dir() / f"{sanitize_job_id(job_id)}-status.json"


def status_url(job_id: str) -> str:
    return f"/output/{sanitize_job_id(job_id)}-status.json"


def sanitize_job_id(job_id: str) -> str:
    raw = (job_id or "job").strip() or "job"
    if not _ID_RE.match(raw):
        raise ValueError(f"invalid job id {job_id!r} (use kebab-case, e.g. ep-release)")
    return raw


def _measured_percent(value: Any) -> Optional[int]:
    """0-100 when progress was measured, None when it was not (missing / blank)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return max(0, min(100, int(value)))


def sanitize_bars(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """Optional breakdown bars for watch cards (overall + per-worker, etc.).

    Each bar: ``{id, label, percent, kind?}`` where ``kind`` is ``primary``
    (overall) or ``worker`` / ``secondary`` (breakdown). ``percent`` alone on
    the status object remains the primary overall value for older clients.
    """
    if not isinstance(raw, list) or not raw:
        return None
    out: List[Dict[str, Any]] = []
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        try:
            # No percent = progress not measured: the bar is indeterminate, not 0%.
            pct = _measured_percent(item.get("percent"))
        except (TypeError, ValueError):
            continue
        bid = str(item.get("id") or item.get("label") or f"bar{len(out)}").strip()
        if not bid:
            continue
        kind = str(item.get("kind") or "").strip().lower() or (
            "primary" if len(out) == 0 else "secondary"
        )
        if kind not in ("primary", "worker", "secondary"):
            kind = "secondary"
        bar: Dict[str, Any] = {
            "id": bid[:64],
            "label": str(item.get("label") or bid)[:120],
            "percent": pct,
            "kind": kind,
        }
        detail = str(item.get("detail") or "").strip()
        if detail:
            bar["detail"] = detail[:160]
        out.append(bar)
    return out or None


GRID_STATES = ("pending", "running", "completed", "failed", "missing", "cancelled", "skipped")
GRID_MAX_CELLS = 2048
# Older producers (and persisted snapshots) used render vocabulary.
_GRID_STATE_ALIASES = {"rendering": "running", "active": "running", "done": "completed"}


def grid_enabled() -> bool:
    """Experimental gate shared by every grid producer (``progress_grid``)."""
    from api.experimental import is_enabled
    return is_enabled("progress_grid")


def _grid_key(raw: Any) -> Any:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:64]
    return None


def sanitize_grid(raw: Any) -> Optional[Dict[str, Any]]:
    """Bound the optional progress grid for status files and persisted cards.

    One cell per unit of work (frame, file, test, shard, …). ``unit`` names
    the noun; ``group`` (who did it) drives a stable colour; ``marked`` draws
    an outline whose meaning is ``marked_label``. ``inventory`` is a claim
    about how completion was established and is omitted when unknown.
    Legacy keys (``frame``/``worker``/``gap_fill``/``workers``) are accepted.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("cells"), list):
        return None
    cells = []
    seen = set()
    for cell in raw["cells"][:GRID_MAX_CELLS]:
        if not isinstance(cell, dict):
            continue
        key = _grid_key(cell.get("key", cell.get("frame")))
        if key is None or key in seen:
            continue
        seen.add(key)
        state = str(cell.get("state") or "")
        state = _GRID_STATE_ALIASES.get(state, state)
        out = {"key": key, "state": state if state in GRID_STATES else "pending",
               "group": str(cell.get("group") or cell.get("worker") or "")[:120],
               "marked": bool(cell.get("marked", cell.get("gap_fill")))}
        note = str(cell.get("note") or "").strip()
        if note:
            out["note"] = note[:160]
        cells.append(out)
    try:
        total = max(len(cells), min(1000000000, int(raw.get("total") or len(cells))))
    except (TypeError, ValueError, OverflowError):
        total = len(cells)
    groups = raw.get("groups", raw.get("workers"))
    legacy = "unit" not in raw and any(isinstance(c, dict) and "frame" in c for c in raw["cells"][:1])
    grid: Dict[str, Any] = {
        "unit": (str(raw.get("unit") or "").strip() or ("frame" if legacy else "item"))[:24],
        "title": str(raw.get("title") or "").strip()[:60],
        "cells": cells, "total": total, "omitted": total - len(cells),
        "groups": [str(g)[:120] for g in (groups if isinstance(groups, list) else [])[:32]],
        "marked_label": (str(raw.get("marked_label") or "").strip() or ("gap-fill" if legacy else ""))[:40],
    }
    if raw.get("inventory") in ("verified", "reported"):
        grid["inventory"] = raw["inventory"]
    return grid



_WRITE_LOCK = threading.Lock()


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` to ``path`` atomically.

    The temp name is unique per writer: a background wait's own thread and a
    Stop/cancel can update the same status at once, and a shared
    ``<name>.json.tmp`` let one writer rename the other's file away.
    """
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    )
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        # Same-process writers take turns; on Windows a replace racing another
        # replace (or a reader holding the file) fails with PermissionError,
        # so retry briefly for writers in other processes.
        with _WRITE_LOCK:
            for attempt in range(20):
                try:
                    tmp.replace(path)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.005 * (attempt + 1))
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

def write_status(
    job_id: str,
    *,
    state: str,
    percent: Optional[int] = None,
    label: str = "",
    bars: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
    grid: Optional[Dict[str, Any]] = None,
) -> Path:
    sid = sanitize_job_id(job_id)
    st = (state or "running").strip().lower()
    if st not in _STATES:
        raise ValueError(f"state must be one of {_STATES}, got {state!r}")
    # None = progress not measured (the UI shows an indeterminate bar, no "0%").
    pct = _measured_percent(percent)
    payload: Dict[str, Any] = {
        "state": st,
        "percent": pct,
        "label": label or "",
        "id": sid,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    clean_bars = sanitize_bars(bars)
    if clean_bars:
        payload["bars"] = clean_bars
    if extra:
        # Allow extra["bars"] too; explicit bars= wins.
        payload.update(extra)
        if clean_bars:
            payload["bars"] = clean_bars
        elif "bars" in payload:
            sanitized = sanitize_bars(payload.get("bars"))
            if sanitized:
                payload["bars"] = sanitized
            else:
                payload.pop("bars", None)
    if grid is not None:
        payload["grid"] = grid
    if "grid" in payload:
        grid = sanitize_grid(payload["grid"]) if grid_enabled() else None
        if grid:
            payload["grid"] = grid
        else:
            payload.pop("grid", None)
    path = status_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, payload)
    return path


def pids_from_status(data: Optional[Dict[str, Any]]) -> List[int]:
    """PIDs recorded in a status JSON (``pid``, ``worker_pid``, ``pids``; ``unity_pid`` is legacy)."""
    if not isinstance(data, dict):
        return []
    out: List[int] = []
    seen = set()

    def _add(raw: Any) -> None:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            return
        if pid <= 0 or pid in seen:
            return
        seen.add(pid)
        out.append(pid)

    for key in ("pid", "worker_pid", "unity_pid"):
        _add(data.get(key))
    extra = data.get("pids")
    if isinstance(extra, list):
        for item in extra:
            _add(item)
    return out


def pid_alive(pid: int) -> bool:
    """Return True while ``pid`` names a running process (Windows and POSIX)."""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        import os

        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # alive, owned by another user
        except OSError:
            return False
        return True
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        still_active = 259
        handle = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok and code.value == still_active)
    except Exception:
        return False


def reconcile_status(
    data: Optional[Dict[str, Any]],
    *,
    persist: bool = False,
    job_id: str = "",
) -> Optional[Dict[str, Any]]:
    """If a watch card says running but every recorded PID is gone, mark failed."""
    if not isinstance(data, dict):
        return data
    state = str(data.get("state") or "").strip().lower()
    if state != "running":
        return data
    pids = pids_from_status(data)
    if not pids:
        return data
    if any(pid_alive(pid) for pid in pids):
        return data
    label = str(data.get("label") or "").strip()
    if "stopped unexpectedly" not in label.lower():
        label = (
            f"{label} — process stopped unexpectedly"
            if label
            else "Process stopped unexpectedly"
        )
    out = dict(data)
    out["state"] = "failed"
    out["label"] = label
    out["stale_reconciled"] = True
    out["time"] = datetime.now().isoformat()
    out["pid"] = 0
    out["unity_pid"] = 0
    if persist and job_id:
        path = status_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, out)
    return out


def kill_status_pids(pids: List[int]) -> int:
    """Kill each PID's process tree. Returns how many kills were attempted."""
    if not pids:
        return 0
    try:
        from api.chat_run_registry import kill_pid_tree
    except Exception:
        return 0
    n = 0
    for pid in pids:
        try:
            from api.process_kill_safety import may_kill_pid

            if not may_kill_pid(pid):
                continue
        except Exception:
            try:
                if int(pid) <= 1:
                    continue
            except (TypeError, ValueError):
                continue
        if kill_pid_tree(pid):
            n += 1
    return n


def cancel_job(job_id: str) -> Dict[str, Any]:
    """Stop a running watch job: kill recorded PIDs and mark status failed."""
    sid = sanitize_job_id(job_id)
    data = read_status(sid)
    if data is None:
        return {"ok": False, "id": sid, "error": "no status", "killed": 0, "pids": []}
    data = data or {}
    state = str(data.get("state") or "").strip().lower()
    pids = pids_from_status(data)
    killed = 0
    if state == "running" or pids:
        killed = kill_status_pids(pids)
    disarmed = ""
    if data.get("unit_watch") and data.get("unit") and state == "running":
        # A scheduled unit has no process yet: Stop must disarm its timer, or it
        # would start the job after the user stopped it.
        timer = timer_state(str(data["unit"]))
        if timer.get("waiting"):
            _systemctl(["stop", timer["unit"]])
            disarmed = timer["unit"]
    if state in ("done", "failed") and not pids:
        return {
            "ok": True,
            "id": sid,
            "already": state,
            "killed": 0,
            "pids": [],
        }
    label = str(data.get("label") or "").strip()
    if "cancel" not in label.lower():
        label = "Cancelled" + (f" — {label}" if label else "")
    path = write_status(
        sid,
        state="failed",
        percent=data.get("percent"),  # keep what was measured; unknown stays unknown
        label=label or "Cancelled",
        extra={"cancelled": True, "pid": 0},
    )
    return {
        "ok": True,
        "id": sid,
        "killed": killed,
        "pids": pids,
        "path": str(path),
        "state": "failed",
        **({"disarmed_timer": disarmed} if disarmed else {}),
    }


def read_status(job_id: str) -> Optional[Dict[str, Any]]:
    path = status_path(job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    activity = read_activity(job_id)
    if activity:
        data["activity"] = activity
    return data


def _activity_path(job_id: str) -> Path:
    from core.runtime_paths import runtime_state_path

    return runtime_state_path("job_activity", sanitize_job_id(job_id) + ".json")


def read_activity(job_id: str) -> Optional[Dict[str, Any]]:
    from api.job_activity import normalize

    try:
        return normalize(json.loads(_activity_path(job_id).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def report_activity(job_id: str, activity: Dict[str, Any]) -> Path:
    """Replace the runner's snapshot without racing the host's terminal status.

    One producer owns this snapshot. The host never guesses steps from logs or
    reads arbitrary paths. Output is an explicit, bounded, non-secret excerpt.
    """
    from api.job_activity import normalize

    clean = normalize({**activity, "updated_ts": time.time()})
    if clean is None:
        raise ValueError("unsupported activity schema_version (expected 1)")
    path = _activity_path(job_id)
    _atomic_write_json(path, clean)
    return path


def read_status_reconciled(job_id: str, *, persist: bool = True) -> Optional[Dict[str, Any]]:
    data = read_status(job_id)
    if data is None:
        return None
    return reconcile_status(data, persist=persist, job_id=job_id)


def stamp_watch_run(
    watch: Dict[str, Any],
    status: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Bind a watch form to one status-file generation.

    Project commands often reuse a job id (``ep-release``). Stamping
    ``started_at`` / ``action`` on the card stops a later run from painting
    its progress onto an earlier form.

    Terminal status from a previous run is ignored so a new form does not
    inherit done/failed and flash 100% before the next kick writes running.
    """
    out = dict(watch or {})
    if not isinstance(status, dict):
        return out
    state = str(status.get("state") or "").strip().lower()
    if state != "running":
        return out
    started = str(status.get("started_at") or "").strip()
    if started:
        out["started_at"] = started
    action = str(status.get("action") or "").strip()
    if action:
        out["action"] = action
    run_id = str(status.get("run_id") or "").strip()
    if run_id:
        out["run_id"] = run_id
    return out


def watch_snapshot_from_status(status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Seed a new watch card from the live status file (never a stale terminal run)."""
    if not isinstance(status, dict):
        return {"state": "running", "percent": None, "label": "Starting…"}
    state = str(status.get("state") or "").strip().lower()
    if state != "running":
        return {"state": "running", "percent": None, "label": "Starting…"}
    snap: Dict[str, Any] = {
        "state": "running",
        "percent": _measured_percent(status.get("percent")),
        "label": str(status.get("label") or "Starting…"),
    }
    for key in ("started_at", "run_id", "action", "version", "elapsed", "elapsed_sec"):
        val = status.get(key)
        if val not in (None, ""):
            snap[key] = val
    bars = sanitize_bars(status.get("bars"))
    if bars:
        snap["bars"] = bars
    return snap


def watch_form_spec(job_id: str, *, title: str = "Job") -> Dict[str, Any]:
    sid = sanitize_job_id(job_id)
    return {
        "title": title,
        "mode": "choice",
        "lock": "form",
        "silent": True,
        "watch": {
            "id": sid,
            "url": status_url(sid),
            "interval_ms": 4000,
            "done_states": ["done"],
            "fail_states": ["failed"],
            "resume_message": (
                f"Job {sid} finished. Read {status_url(sid)} (or "
                f"{status_path(sid)}) and summarize. Do not restart it."
            ),
        },
        "options": [
            {"id": "continue", "label": "Continue once it's finished", "action": "__watch_resume__"},
            {"id": "later", "label": "I'll reply once it's done", "action": "__watch_park__"},
            {"id": "stop", "label": "Stop job", "action": "__watch_cancel__"},
        ],
    }


# ── Detached unit / pid watches (daemon-polled, outlive Flask) ──────────────
#
# A watch card's ``resume_message`` only resumes a chat while a browser polls
# the status file, and a harness's own background watch (Claude Code's
# ``run_in_background``) ends with its CLI. Work a chat detached on purpose —
# ``systemd-run --user --unit=<name> …``, so a Flask restart cannot kill it —
# therefore had nothing to wake the chat when the job ended. A unit watch is
# that missing half: a record on disk, polled on a timer, which queues the
# resume turn through the ``api.chat_ops`` outbox when the unit or pid exits.
# Agent entry point: ``python -m api.job_watch watch-unit``.
#
# Two processes poll: Flask's chat-ops pump (``api.chat_ops.worker``; Flask
# restarts often, so new code reaches it quickly) and the daemon (which keeps
# polling while Flask is down). Both are safe together: a pass runs under an
# exclusive ``flock`` on the watch dir, and a record is *claimed* (renamed to
# ``<id>.json.claimed``) before anything fires, so only one process can resume
# the chat even where the lock is unavailable.

UNIT_WATCH_MAX_SEC = 72 * 3600.0
# Consecutive failed systemctl queries before a watch is resolved as unknown;
# one blip (dbus restart, load spike) must not fake an exit.
UNIT_WATCH_UNKNOWN_LIMIT = 3
_RUNNING_UNIT_STATES = ("active", "activating", "reloading", "deactivating", "refreshing")


# A claim left by a poller that died mid-fire is recovered after this long
# (and only once its process is gone).
UNIT_WATCH_CLAIM_STALE_SEC = 120.0
_CLAIM_SUFFIX = ".claimed"


def unit_watch_dir() -> Path:
    from core.runtime_paths import runtime_state_path

    return runtime_state_path("unit_watch")


def _systemctl(args: List[str]) -> tuple:
    """``systemctl --user <args>`` → ``(returncode, stdout)``; ``(None, "")`` when unavailable."""
    import shutil
    import subprocess

    exe = shutil.which("systemctl")
    if not exe:
        return None, ""
    try:
        out = subprocess.run([exe, "--user", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return out.returncode, (out.stdout or "").strip()


_FINISHED_SUB_STATES = frozenset({"exited", "dead", "failed"})


def unit_state(unit: str) -> Dict[str, Any]:
    """One systemd user unit as Cuttle reads it.

    ``queried`` is False when systemctl could not answer at all (not installed,
    no user bus) — never treated as "the job finished". A transient unit started
    without ``--remain-after-exit`` is garbage-collected on exit, so ``exit`` can
    be unknown (``None``); the resume turn says so instead of inventing a status.
    """
    code, text = _systemctl(
        ["show", str(unit or ""), "--property=ActiveState,SubState,Result,ExecMainStatus,MainPID"]
    )
    if code is None:
        return {"queried": False, "running": False}
    props: Dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        props[key.strip()] = value.strip()
    active = props.get("ActiveState") or ""
    if not active:
        return {"queried": False, "running": False}

    def _int(raw: Any) -> Optional[int]:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None

    pid = _int(props.get("MainPID")) or 0
    out = {
        "queried": True,
        "active_state": active,
        "sub_state": props.get("SubState") or "",
        "result": props.get("Result") or "",
        "exit": _int(props.get("ExecMainStatus")),
        "pid": pid if pid > 0 else 0,
        # A unit started with --remain-after-exit stays ActiveState=active after its process ends, with
        # SubState=exited: that job is finished. (Every watched build used it, so no watch ever fired.)
        "running": active in _RUNNING_UNIT_STATES and (props.get("SubState") or "") not in _FINISHED_SUB_STATES,
        "scheduled": False,
        "starts_at": None,
    }
    if not out["running"] and active == "inactive":
        timer = timer_state(unit)
        if timer.get("waiting"):
            # ``systemd-run --on-calendar=…``: the service has not started yet; its
            # timer will start it. Scheduled work is pending, not finished.
            out.update(running=True, scheduled=True, starts_at=timer.get("next_ts"),
                       sub_state="scheduled", timer=timer["unit"])
    return out


def _timer_unit(unit: str) -> str:
    stem = str(unit or "")
    stem = stem[:-len(".service")] if stem.endswith(".service") else stem
    return f"{stem}.timer" if stem and "." not in stem.rsplit("@", 1)[-1] else ""


def timer_state(unit: str) -> Dict[str, Any]:
    """The systemd timer that starts ``unit`` later (``systemd-run --on-calendar``).

    ``waiting`` is True while it is armed and has not fired; ``next_ts`` is when it
    fires (epoch). systemd owns the schedule; Cuttle only reads it."""
    timer = _timer_unit(unit)
    if not timer:
        return {"unit": "", "waiting": False}
    code, text = _systemctl(["show", timer, "--timestamp=unix",
                             "--property=LoadState,ActiveState,SubState,NextElapseUSecRealtime"])
    if code is None:
        return {"unit": timer, "waiting": False}
    props = dict(line.partition("=")[::2] for line in text.splitlines())
    raw = str(props.get("NextElapseUSecRealtime") or "").strip().lstrip("@")
    try:
        next_ts = float(raw) if raw else None
    except ValueError:
        next_ts = None
    waiting = (props.get("LoadState") == "loaded" and props.get("ActiveState") == "active"
               and props.get("SubState") == "waiting")
    return {"unit": timer, "waiting": waiting, "next_ts": next_ts}


def start_scheduled_now(job_id: str) -> Dict[str, Any]:
    """Start a timer-scheduled unit watch's service now and disarm its timer.

    Only for a pending unit watch whose unit is still waiting on its timer
    (``systemd-run --on-calendar``); anything else raises ``LookupError`` /
    ``ValueError``. The watch keeps following the same unit, so the chat is
    resumed when it exits exactly as it would have been."""
    sid = sanitize_job_id(job_id)
    rec = next((r for r in unit_watch_records() if r.get("id") == sid), None)
    if rec is None or not rec.get("unit"):
        raise LookupError(f"no pending unit watch {sid}")
    unit = str(rec["unit"])
    state = unit_state(unit)
    if not state.get("scheduled"):
        raise ValueError(f"unit {unit} is not waiting on a timer (it is {state.get('sub_state') or 'unknown'})")
    code, _ = _systemctl(["start", "--no-block", unit])
    if code != 0:
        raise ValueError(f"systemctl --user start {unit} failed")
    _systemctl(["stop", state.get("timer") or _timer_unit(unit)])
    data = read_status(sid) or {}
    write_status(sid, state="running", percent=None, label=str(data.get("label") or rec.get("label") or ""),
                 extra={"started_early_ts": time.time(), "scheduled_for": state.get("starts_at")})
    return {"ok": True, "id": sid, "unit": unit, "was_scheduled_for": state.get("starts_at")}


def _unit_job_id(unit: str, pid: int) -> str:
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", str(unit or f"pid-{pid}")).strip("-")[:32] or "unit"
    return f"unit-{stem}-{uuid.uuid4().hex[:6]}"


def _write_unit_record(record: Dict[str, Any]) -> Path:
    path = unit_watch_dir() / f"{record['id']}.json"
    _atomic_write_json(path, record)
    return path


def unit_watch_records(chat_session_id: Any = None) -> List[Dict[str, Any]]:
    """Every pending unit watch on disk (one chat's when ``chat_session_id`` is given).

    Claimed records (being fired right now) are not pending and are skipped."""
    want = None
    if chat_session_id not in (None, ""):
        try:
            want = int(str(chat_session_id).strip().removeprefix("db_session_").removeprefix("CH-"))
        except ValueError:
            return []
    out = []
    for path in sorted(unit_watch_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id"):
            if want is not None and data.get("chat_session_id") != want:
                continue
            data["record_path"] = str(path)
            out.append(data)
    return out


def _archive_unit(record: Dict[str, Any], outcome: Dict[str, Any], operation: Optional[str] = None,
                  *, delivery_error: bool = False) -> None:
    """Small durable receipt; steps/output remain with the activity owner."""
    folder = unit_watch_dir() / "finished" / str(int(record["chat_session_id"]))
    folder.mkdir(parents=True, exist_ok=True)
    receipt = {k: v for k, v in record.items() if k not in ("record_path", "claimed_by", "claimed_ts")}
    receipt.update(outcome=outcome, finished_ts=time.time(), operation=operation,
                   delivery_error=delivery_error)
    _atomic_write_json(folder / (record["id"] + ".json"), receipt)
    for path in folder.glob("*.json"):
        try:
            if time.time() - path.stat().st_mtime > 7 * 86400:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def recent_unit_results(chat_session_id: Any, *, limit: int = 3) -> List[Dict[str, Any]]:
    """Recent terminal jobs for a chat, including the outbox's current delivery state."""
    from api.chat_ops import store

    results = []
    for path in (unit_watch_dir() / "finished" / str(int(chat_session_id))).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("outcome"), dict) or not data.get("id"):
                continue
            if str(data.get("chat_session_id")) != str(chat_session_id):
                continue
            if time.time() - float(data.get("finished_ts") or 0) > 7 * 86400:
                continue
            try:
                op = store.job_notice(int(chat_session_id), data["id"], watch_token=data.get("watch_token"))
            except Exception:
                op = None  # still show the terminal job if its delivery store is unavailable
            state = (op or {}).get("state")
            handoff = {"pending": "queued", "dispatching": "sending", "verifying": "verifying",
                       "delivered": "delivered", "done": "delivered", "failed": "failed",
                       "cancelled": "cancelled"}.get(state, "retrying" if data.get("delivery_error") else "unknown")
            if state == "failed" and ((op or {}).get("result") or {}).get("query_id"):
                handoff = "turn_failed"
            data["handoff"] = handoff
            query = str(((op or {}).get("result") or {}).get("query_id") or "")
            data["followup_query_id"] = query if re.fullmatch(r"[\w-]{1,80}", query) else ""
            results.append(data)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(results, key=lambda x: x["finished_ts"], reverse=True)[:limit]


def _claim_record(record: Dict[str, Any]) -> Optional[Path]:
    """Atomically take ``record`` for firing; None when another poller has it.

    ``rename`` succeeds for exactly one caller, so whichever process wins is the
    only one that writes the terminal status and queues the resume turn."""
    src = Path(record["record_path"])
    dst = src.with_name(src.name + _CLAIM_SUFFIX)
    try:
        os.rename(src, dst)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:  # who holds it, for stale-claim recovery
        data = json.loads(dst.read_text(encoding="utf-8"))
        data.update(claimed_by=os.getpid(), claimed_ts=time.time())
        _atomic_write_json(dst, data)
    except (OSError, ValueError):
        pass
    return dst


def _release_claim(claimed: Path) -> None:
    """Put a claimed record back so the next pass retries it."""
    try:
        data = json.loads(claimed.read_text(encoding="utf-8"))
        data.pop("claimed_by", None)
        data.pop("claimed_ts", None)
        _atomic_write_json(claimed, data)
    except (OSError, ValueError):
        pass
    try:
        os.rename(claimed, claimed.with_name(claimed.name[: -len(_CLAIM_SUFFIX)]))
    except OSError:
        pass


def _recover_stale_claims(now: float) -> None:
    """A poller that died between claim and resume leaves ``*.claimed``.

    Once its process is gone: if its resume turn was already queued (the
    outbox has a notice for this job), the claim is just removed; otherwise the
    record goes back to pending. Never re-fires a resume that was queued."""
    for claimed in unit_watch_dir().glob("*.json" + _CLAIM_SUFFIX):
        try:
            data = json.loads(claimed.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        holder = int(data.get("claimed_by") or 0)
        since = float(data.get("claimed_ts") or 0) or claimed.stat().st_mtime
        if now - since < UNIT_WATCH_CLAIM_STALE_SEC:
            continue
        if holder and holder != os.getpid() and pid_alive(holder):
            continue
        try:
            from api.chat_ops import store as chat_ops_store

            queued = chat_ops_store.notice_for_job(int(data.get("chat_session_id") or 0), str(data.get("id") or ""),
                                                  watch_token=data.get("watch_token"))
        except Exception:
            continue  # cannot tell: leave it rather than risk a second resume
        if queued:
            status = read_status(data["id"]) or {}
            _archive_unit(data, {"state": status.get("state") if status.get("state") in ("done", "failed") else "unknown",
                                 "exit": status.get("exit")})
            claimed.unlink(missing_ok=True)
        else:
            _release_claim(claimed)


class _PollLock:
    """Exclusive, non-blocking ``flock`` over one poll pass (POSIX).

    ``acquired`` is False when another process (or thread) is polling right now;
    that pass is skipped. Where ``fcntl`` is unavailable the lock is a no-op and
    the per-record claim alone keeps firing exactly-once."""

    def __init__(self) -> None:
        self._fh = None
        self.acquired = False

    def __enter__(self) -> "_PollLock":
        try:
            import fcntl
        except ImportError:
            self.acquired = True
            return self
        try:
            self._fh = open(unit_watch_dir() / ".poll.lock", "a+")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fh is not None:
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self._fh.close()
            self._fh = None


def unit_watch_card(job_id: str, *, title: str, resume_message: str) -> str:
    """The watch card for a unit watch, as ``<cuttle_action_form>`` markup.

    The card shows progress and can stop the job, but it carries **no**
    ``resume_message`` and no Continue option: the host resumes the chat here
    (``poll_unit_watches``), so a client-side resume would start the same turn
    twice. The message the chat is resumed with lives in the record; the note
    fields are a courtesy for anyone reading the raw markup (Flask's watch
    normalizer keeps only the keys it knows).
    """
    spec = watch_form_spec(job_id, title=title)
    spec["watch"].pop("resume_message", None)
    spec["watch"]["resumed_by"] = "host"
    spec["watch"]["resume_note"] = resume_message[:400]
    spec["options"] = [{"id": "stop", "label": "Stop job", "action": "__watch_cancel__"}]
    return "<cuttle_action_form>\n" + json.dumps(spec, ensure_ascii=False, indent=2) + "\n</cuttle_action_form>"


def default_resume_message(job_id: str, *, unit: str = "", pid: int = 0) -> str:
    what = f"systemd unit `{unit}`" if unit else f"process {pid}"
    return (
        f"The detached job you started ({what}) has exited. Read "
        f"{status_url(job_id)} for its end state, check its own log, and continue "
        "the task from that result. Do not restart it blindly."
    )


def register_unit_watch(
    *,
    unit: str = "",
    pid: int = 0,
    resume_message: str = "",
    chat_session_id: Any = None,
    job_id: str = "",
    label: str = "",
    title: str = "",
    allow_inactive: bool = False,
    activity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record "resume this chat when ``unit`` (or ``pid``) exits" and return its card.

    The record outlives Flask and the agent's turn; ``poll_unit_watches`` (run by
    Flask's chat-ops pump and by the daemon) resolves it. Raises ``ValueError`` on anything that would make a
    watch resolve instantly or wake the wrong chat.
    """
    from api.cuttle_ui_capabilities import numeric_chat_session_id
    from api.job_activity import normalize

    if activity is not None and normalize(activity) is None:
        raise ValueError("unsupported activity schema_version (expected 1)")

    unit = str(unit or "").strip()
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        raise ValueError("pid must be a number") from None
    if not unit and pid <= 0:
        raise ValueError("pass --unit <systemd user unit> or --pid <n>")
    if unit and not re.fullmatch(r"[A-Za-z0-9@:_.-]{1,128}", unit):
        raise ValueError(f"invalid unit name {unit!r}")
    raw_session = chat_session_id if chat_session_id not in (None, "") else os.environ.get("CUTTLE_CHAT_SESSION_ID")
    sid = numeric_chat_session_id(raw_session)
    if sid is None:
        raise ValueError(
            "no chat to resume: pass --chat CH-… (or run where CUTTLE_CHAT_SESSION_ID is set)"
        )
    state = unit_state(unit) if unit else {}
    if unit and not state.get("queried"):
        raise ValueError(
            "systemctl --user cannot answer for this unit (not installed, no user bus, or unknown "
            "unit). Start the job with systemd-run --user --unit=<name>, or watch its pid: --pid <n>"
        )
    if unit and not state.get("running") and not allow_inactive:
        raise ValueError(
            f"unit {unit} is not running (ActiveState={state.get('active_state') or 'unknown'}); "
            "start it first, then register the watch (--allow-inactive to override)"
        )
    if pid and not pid_alive(pid) and not allow_inactive:
        raise ValueError(f"pid {pid} is not running (--allow-inactive to override)")
    job_id = sanitize_job_id(job_id) if job_id else _unit_job_id(unit, pid)
    resume_message = str(resume_message or "").strip() or default_resume_message(job_id, unit=unit, pid=pid)
    if len(resume_message) > 18000:
        raise ValueError("resume message must be under 18000 characters")
    label = str(label or "").strip() or (f"Running: {unit}" if unit else f"Running: pid {pid}")
    title = str(title or "").strip() or (f"Detached job — {unit}" if unit else f"Detached job — pid {pid}")
    watch_pid = pid or int(state.get("pid") or 0)
    now = time.time()
    record = {
        "schema_version": 1,
        "id": job_id,
        "chat_session_id": int(sid),
        "unit": unit,
        "pid": pid,
        "label": label,
        "title": title,
        "resume_message": resume_message,
        "created_ts": now,
        "unknown_polls": 0,
        "origin_query_id": os.environ.get("CUTTLE_AGENT_RUN_ID", ""),
        "watch_token": uuid.uuid4().hex,
    }
    write_status(
        job_id,
        state="running",
        percent=None,  # a unit only reports running/exited: no measured progress
        label=label,
        extra={
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "started_ts": now,
            "run_id": job_id,
            "unit": unit,
            "pid": watch_pid,
            "chat_session_id": str(int(sid)),
            "unit_watch": True,
        },
    )
    # An explicit id may be reused for a new run. Do not attach its old activity,
    # receipt or already-delivered wake-up to this execution.
    _activity_path(job_id).unlink(missing_ok=True)
    (unit_watch_dir() / "finished" / str(int(sid)) / (job_id + ".json")).unlink(missing_ok=True)
    path = _write_unit_record(record)
    if activity is not None:
        report_activity(job_id, activity)
    return {
        "ok": True,
        "id": job_id,
        "unit": unit,
        "pid": watch_pid,
        "chat_session_id": int(sid),
        "record": str(path),
        "status_path": str(status_path(job_id)),
        "url": status_url(job_id),
        "resume_message": resume_message,
        "card": unit_watch_card(job_id, title=title, resume_message=resume_message),
    }


def unit_resume_text(record: Dict[str, Any], outcome: Dict[str, Any]) -> str:
    """The turn the chat is resumed with: what ended, how, and the agent's own message."""
    unit, pid = record.get("unit") or "", record.get("pid") or 0
    what = f"systemd unit `{unit}`" if unit else f"process {pid}"
    exit_code = outcome.get("exit")
    if outcome.get("state") == "unknown":
        head = (f"Cuttle can no longer tell whether {what} is running "
                f"({outcome.get('detail') or 'status unavailable'}).")
    elif outcome.get("state") == "gave_up":
        head = (f"Cuttle stopped watching {what} after "
                f"{int(UNIT_WATCH_MAX_SEC // 3600)} h; it may still be running.")
    elif exit_code is None:
        head = f"{what} has exited (exit status not available — it was not started with --remain-after-exit)."
    else:
        head = f"{what} has exited with status {exit_code}" + (
            f" ({outcome.get('result')})." if outcome.get("result") else ".")
    lines = [head.capitalize() if head[:1].islower() else head, ""]
    lines.append(f"Watch status: {status_url(record['id'])} (`{status_path(record['id'])}`)")
    lines += ["", str(record.get("resume_message") or "").strip()]
    return "\n".join(lines).strip()[:19000]


def _post_unit_resume(record: Dict[str, Any], outcome: Dict[str, Any]) -> Optional[str]:
    """Queue the resume turn on the chat through the chat-ops outbox."""
    from api.chat_ops import store as chat_ops_store

    sid = int(record["chat_session_id"])
    label = "Detached job finished" if outcome.get("state") in ("done", "failed") else "Detached job watch"
    op = chat_ops_store.job_notice(sid, record["id"], watch_token=record.get("watch_token")) or chat_ops_store.create("notice", sid, sid, {
        "text": unit_resume_text(record, outcome), "label": label, "job_id": record["id"],
        "watch_token": record.get("watch_token"),
    })
    return (op or {}).get("id")


def _resolve_unit_watch(record: Dict[str, Any], outcome: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Claim, then terminal status file + queued resume turn + record removal, once.

    None when another poller claimed the record first (it fires, not us)."""
    claimed = _claim_record(record)
    if claimed is None:
        return None
    state = outcome.get("state")
    card_state = "done" if state == "done" else "failed"
    label = outcome.get("label") or ""
    try:
        previous = read_status(record["id"]) or {}
        write_status(record["id"], state=card_state, percent=100 if card_state == "done" else None,
                     label=label, extra={"started_at": previous.get("started_at"),
                                         "started_ts": record.get("created_ts"),
                                         "chat_session_id": str(record["chat_session_id"]),
                                         "unit": record.get("unit") or "", "pid": 0,
                                         "unit_watch": True, "exit": outcome.get("exit"),
                                         "result": outcome.get("result") or ""})
    except (OSError, ValueError) as exc:
        print(f"[unit_watch] status write failed for {record['id']}: {exc}", flush=True)
    op_id = None
    try:
        op_id = _post_unit_resume(record, outcome)
        if not op_id:
            raise ValueError("resume queue returned no receipt")
        _archive_unit(record, outcome, op_id)
    except Exception as exc:  # the record goes back, so the next pass retries
        print(f"[unit_watch] resume queue failed for {record['id']}: {exc}", flush=True)
        try:
            _archive_unit(record, outcome, delivery_error=True)
        except OSError:
            pass
        _release_claim(claimed)
        return {"id": record["id"], "resolved": False, "error": str(exc)[:200], **outcome}
    try:
        claimed.unlink(missing_ok=True)
    except OSError:
        pass
    return {"id": record["id"], "resolved": True, "operation": op_id,
            "chat_session_id": record["chat_session_id"], **outcome}


def _rewrite_pending(record: Dict[str, Any]) -> None:
    """Update a pending record in place; never re-creates one that was claimed."""
    if Path(record.get("record_path") or "").is_file():
        _write_unit_record({k: v for k, v in record.items() if k != "record_path"})


def check_unit_watch(record: Dict[str, Any], *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """One pass over one record: its resolution, or None while the job still runs."""
    now = time.time() if now is None else now
    unit, pid = record.get("unit") or "", int(record.get("pid") or 0)
    if unit:
        state = unit_state(unit)
        if not state.get("queried"):
            misses = int(record.get("unknown_polls") or 0) + 1
            record["unknown_polls"] = misses
            _rewrite_pending(record)
            if misses < UNIT_WATCH_UNKNOWN_LIMIT:
                return None
            return _resolve_unit_watch(record, {
                "state": "unknown", "exit": None, "result": "",
                "detail": "systemctl --user stopped answering",
                "label": f"{unit}: status unavailable"})
        if record.get("unknown_polls"):
            record["unknown_polls"] = 0
            _rewrite_pending(record)
        if state.get("running"):
            return None
        exit_code, result = state.get("exit"), state.get("result") or ""
        ok = (exit_code in (0, None)) and result in ("success", "")
        return _resolve_unit_watch(record, {
            "state": "done" if ok else "failed", "exit": exit_code, "result": result,
            "label": f"{unit} exited" + (f" (status {exit_code})" if exit_code is not None else "")})
    if pid and pid_alive(pid):
        return None
    if pid:
        return _resolve_unit_watch(record, {
            "state": "done", "exit": None, "result": "",
            "label": f"pid {pid} exited"})
    return _resolve_unit_watch(record, {
        "state": "unknown", "exit": None, "result": "", "detail": "no unit or pid to watch",
        "label": "watch had nothing to follow"})


def poll_unit_watches(*, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Resolve every finished unit watch. Safe to call from any process, at once.

    Flask's chat-ops pump and the daemon both call it on a timer, so a detached
    job's chat is resumed with no browser open and whichever of the two is
    running. A pass that finds another pass in progress returns ``[]``."""
    now = time.time() if now is None else now
    resolved = []
    with _PollLock() as lock:
        if not lock.acquired:
            return []
        try:
            _recover_stale_claims(time.time())
        except Exception as exc:
            print(f"[unit_watch] stale-claim recovery failed: {exc}", flush=True)
        for record in unit_watch_records():
            try:
                if now - float(record.get("created_ts") or now) > UNIT_WATCH_MAX_SEC:
                    out = _resolve_unit_watch(record, {
                        "state": "gave_up", "exit": None, "result": "",
                        "label": "stopped watching (72 h)"})
                else:
                    out = check_unit_watch(record, now=now)
            except Exception as exc:  # one bad record must not stop the others
                print(f"[unit_watch] check failed for {record.get('id')}: {exc}", flush=True)
                continue
            if out:
                resolved.append(out)
    return resolved


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m api.job_watch")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="Write status JSON for the watch bar")
    w.add_argument("--id", default="job")
    w.add_argument("--state", required=True, choices=_STATES)
    w.add_argument("--percent", type=int, default=None,
                   help="Measured progress 0-100; omit when unknown (the bar animates instead of showing 0%%)")
    w.add_argument("--label", default="")
    w.add_argument(
        "--grid-json",
        default="",
        help='Optional progress grid, e.g. {"unit":"test","cells":[{"key":"test_a","state":"completed"}]}',
    )
    w.add_argument(
        "--bars-json",
        default="",
        help='Optional JSON array of bars, e.g. [{"id":"overall","label":"Overall","percent":50,"kind":"primary"}]',
    )

    u = sub.add_parser("url", help="Print the poll URL")
    u.add_argument("--id", default="job")

    f = sub.add_parser("form", help="Print watch-form JSON")
    f.add_argument("--id", default="job")
    f.add_argument("--title", default="Job")

    r = sub.add_parser("read", help="Print current status JSON")
    r.add_argument("--id", default="job")

    c = sub.add_parser("cancel", help="Kill a running job by status PID and mark failed")
    c.add_argument("--id", default="job")

    wu = sub.add_parser(
        "watch-unit",
        help="Resume this chat when a detached systemd user unit (or pid) exits",
        description="Register a durable watch on a detached job and print its watch card. "
                    "Cuttle (Flask's chat-ops pump and the daemon) polls `systemctl --user` (or "
                    "the pid) and queues the resume turn on the chat exactly once when it exits — it survives Flask restarts and needs "
                    "no open browser. Start the job first, e.g. "
                    "systemd-run --user --unit=my-build --remain-after-exit <cmd>.",
    )
    wu.add_argument("--unit", default="", help="systemd --user unit name, e.g. ep-build-linux")
    wu.add_argument("--pid", type=int, default=0, help="Watch this pid instead of a unit")
    wu.add_argument("--resume", default="", help="Message the chat is resumed with when the job exits")
    wu.add_argument("--chat", default="", help="Chat to resume (CH-…; default CUTTLE_CHAT_SESSION_ID)")
    wu.add_argument("--id", default="", help="Watch/job id (default: derived from the unit)")
    wu.add_argument("--label", default="", help="Card label while it runs")
    wu.add_argument("--title", default="", help="Card title")
    wu.add_argument("--allow-inactive", action="store_true",
                    help="Register even though the unit/pid is not running yet")
    wu.add_argument("--json", action="store_true", help="Print the record as JSON instead of the card")
    wu.add_argument("--activity-json", default="", help="Version 1 activity snapshot (readable steps)")

    report = sub.add_parser("report", help="Publish readable steps, recent output and a heartbeat for an existing job")
    report.add_argument("--id", required=True)
    report.add_argument("--activity-json", required=True, help="Version 1 activity snapshot; replaces previous snapshot")

    sub.add_parser("poll-units", help="Resolve finished unit watches now (Flask and the daemon do this on a timer)")
    sub.add_parser("units", help="List pending unit watches")
    sn = sub.add_parser("start-now", help="Start a timer-scheduled unit watch's job now (systemd-run --on-calendar)")
    sn.add_argument("--id", required=True)

    args = p.parse_args(argv)
    if args.cmd == "report":
        try:
            activity = json.loads(args.activity_json)
            if not isinstance(activity, dict):
                raise ValueError("activity must be an object")
            if read_status(args.id) is None:
                raise ValueError("register or write the job status first")
            print(report_activity(args.id, activity))
            return 0
        except (ValueError, TypeError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.cmd == "write":
        bars = None
        raw_bars = getattr(args, "bars_json", "") or ""
        if str(raw_bars).strip():
            try:
                parsed = json.loads(raw_bars)
            except json.JSONDecodeError as e:
                print(f"invalid --bars-json: {e}", file=sys.stderr)
                return 2
            bars = parsed if isinstance(parsed, list) else None
        grid = None
        if str(args.grid_json or "").strip():
            try:
                grid = json.loads(args.grid_json)
            except json.JSONDecodeError as e:
                print(f"invalid --grid-json: {e}", file=sys.stderr)
                return 2
            if not isinstance(grid, dict):
                print("--grid-json must be a JSON object", file=sys.stderr)
                return 2
        path = write_status(
            args.id,
            state=args.state,
            percent=args.percent,
            label=args.label,
            bars=bars,
            grid=grid,
        )
        print(path)
        return 0
    if args.cmd == "url":
        print(status_url(args.id))
        return 0
    if args.cmd == "form":
        print(json.dumps(watch_form_spec(args.id, title=args.title), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "read":
        data = read_status(args.id)
        if data is None:
            print("{}", file=sys.stderr)
            return 1
        print(json.dumps(data, ensure_ascii=False))
        return 0
    if args.cmd == "cancel":
        info = cancel_job(args.id)
        print(json.dumps(info, ensure_ascii=False))
        return 0 if info.get("ok") else 1
    if args.cmd == "watch-unit":
        try:
            activity = json.loads(args.activity_json) if args.activity_json else None
            info = register_unit_watch(
                unit=args.unit, pid=args.pid, resume_message=args.resume,
                chat_session_id=args.chat, job_id=args.id, label=args.label,
                title=args.title, allow_inactive=args.allow_inactive, activity=activity,
            )
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                  if args.json else f"error: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(info, ensure_ascii=False))
        else:
            print(info["card"])
            print(f"\n[watch-unit] {info['id']} → resumes CH-{info['chat_session_id']:06d} "
                  f"when {info['unit'] or ('pid ' + str(info['pid']))} exits; record {info['record']}",
                  file=sys.stderr)
        return 0
    if args.cmd == "poll-units":
        print(json.dumps({"resolved": poll_unit_watches()}, ensure_ascii=False, default=str))
        return 0
    if args.cmd == "units":
        print(json.dumps({"watches": unit_watch_records()}, ensure_ascii=False, default=str))
        return 0
    if args.cmd == "start-now":
        try:
            print(json.dumps(start_scheduled_now(args.id)))
            return 0
        except (LookupError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
