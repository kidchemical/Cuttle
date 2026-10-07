"""Pollable job status for Cuttle action-form progress bars.

Writes ``<home>/output/<id>-status.json``, served as ``/output/<id>-status.json``.
CLI: ``python -m api.job_watch write --id my-job --state running --percent 40 --label "…"``
"""

from __future__ import annotations

import argparse
import json
import re
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
            pct = max(0, min(100, int(item.get("percent") or 0)))
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


def write_status(
    job_id: str,
    *,
    state: str,
    percent: int = 0,
    label: str = "",
    bars: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
    grid: Optional[Dict[str, Any]] = None,
) -> Path:
    sid = sanitize_job_id(job_id)
    st = (state or "running").strip().lower()
    if st not in _STATES:
        raise ValueError(f"state must be one of {_STATES}, got {state!r}")
    pct = max(0, min(100, int(percent)))
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
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
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
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
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
        percent=int(data.get("percent") or 0),
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
    }


def read_status(job_id: str) -> Optional[Dict[str, Any]]:
    path = status_path(job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


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
        return {"state": "running", "percent": 0, "label": "Starting…"}
    state = str(status.get("state") or "").strip().lower()
    if state != "running":
        return {"state": "running", "percent": 0, "label": "Starting…"}
    snap: Dict[str, Any] = {
        "state": "running",
        "percent": max(0, min(100, int(status.get("percent") or 0))),
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


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m api.job_watch")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="Write status JSON for the watch bar")
    w.add_argument("--id", default="job")
    w.add_argument("--state", required=True, choices=_STATES)
    w.add_argument("--percent", type=int, default=0)
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

    args = p.parse_args(argv)
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
