"""Mesh runs: a batch of jobs a chat submitted, shown live and resumed once.

A run is the jobs submitted together by :func:`submit_run` (they share
``params.watch_id``) plus one ``api.job_watch`` unit-watch record with
``mesh_jobs``. Nothing here polls on an agent's behalf:

* the host (daemon + Flask chat-ops pump, every 15 s, exactly-once) calls
  :func:`check` through ``job_watch.check_unit_watch``; a finished run gets its
  terminal status and **one** resume turn through the chat-ops outbox;
* every job heartbeat that lands on the host calls :func:`touch`, so the card
  follows node-level progress within a couple of seconds.

:func:`summarize` is pure: jobs (+ worker telemetry + expected durations from
``api.device_workers.metrics``) → the watch status (``percent``, ``label``,
``bars``, ``grid``, activity ``steps``) plus ``eta_s``, ``elapsed_s``,
``errors``, ``previews`` and per-worker ``resources``.

A run is finished only when every job has ended **and** no worker still holds
a reservation for one (a cancel the worker has not confirmed yet): until then
the card says it is stopping. A run that makes no progress for its
``ask_after_s`` gets one decision form (``runs.ask_if_blocked``) instead of
waiting silently.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

log = logging.getLogger("cuttle.device_workers")

TERMINAL = ("succeeded", "failed", "cancelled")
TOUCH_MIN_SEC = 2.0
_touched: Dict[str, float] = {}
_touch_lock = threading.Lock()


def _fraction(job: Dict[str, Any], expected: Optional[float], now: float) -> float:
    status = job.get("status")
    if status in TERMINAL:
        return 1.0
    if status not in ("claimed", "running"):
        return 0.0
    prog = job.get("progress") or {}
    total = prog.get("units_total") or 0
    if total:
        partial = (prog.get("step") or 0) / prog["steps"] if prog.get("steps") else 0.0
        return min(0.99, (float(prog.get("units_done") or 0) + partial) / float(total))
    if expected and job.get("started_at"):
        return min(0.95, (now - float(job["started_at"])) / expected)
    return 0.0


def _remaining(job: Dict[str, Any], frac: float, expected: Optional[float], now: float) -> Optional[float]:
    status = job.get("status")
    if status in TERMINAL:
        return 0.0
    if status in ("claimed", "running") and job.get("started_at"):
        elapsed = now - float(job["started_at"])
        if expected:
            # Measured history of this workload beats node counts: one sampler node is
            # most of a graph's time but one unit of its progress.
            return max(0.0, expected - elapsed) if elapsed < expected else expected * 0.05
        return elapsed * (1 - frac) / frac if frac >= 0.05 else None
    return expected


def _job_label(job: Dict[str, Any], index: int) -> str:
    params = job.get("params") or {}
    return str(params.get("label") or params.get("template") or f"{job.get('type')} {index + 1}")[:80]


def _detail(job: Dict[str, Any]) -> str:
    status = job.get("status")
    prog = job.get("progress") or {}
    if status in TERMINAL and job.get("held_by"):
        return f"stopping — waiting for {job['held_by']} to confirm the workload ended"
    if status in ("claimed", "running"):
        where = job.get("claimed_by") or ""
        return f"{where}: {prog.get('message') or 'running'}"[:240]
    if status == "queued":
        return _waiting(job)
    if status in ("failed", "cancelled"):
        return str(job.get("error") or status)[:240]
    return "done"


def _waiting(job: Dict[str, Any]) -> str:
    if job.get("held_by"):
        return f"{job['held_by']} stopped responding — kept for it, its work may still be running"
    return f"waiting — {job['wait_reason']}" if job.get("wait_reason") else "queued"


_STEP_STATE = {"queued": "pending", "claimed": "running", "running": "running",
               "succeeded": "done", "failed": "failed", "cancelled": "cancelled"}
_CELL_STATE = {"queued": "pending", "claimed": "running", "running": "running",
               "succeeded": "completed", "failed": "failed", "cancelled": "cancelled"}


def _resources(workers: List[Dict[str, Any]], jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Live telemetry of the workers this run is using or waiting on, for the card."""
    involved: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        wid = job.get("claimed_by") or job.get("held_by") or job.get("target_worker_id")
        if wid:
            involved.setdefault(str(wid), []).append(job)
    out = []
    for w in workers:
        wid = str(w.get("worker_id") or "")
        if wid not in involved:
            continue
        load = w.get("load") or {}
        gpus = load.get("gpus") or []
        reserved = [j.get("reserved") or {} for j in involved[wid] if j.get("held_by") == wid]
        out.append({
            "worker": wid,
            "ram_used_gb": load.get("ram_used_gb"),
            "ram_available_gb": load.get("ram_available_gb"),
            "ram_total_gb": load.get("ram_total_gb"),
            "swap_used_gb": load.get("swap_used_gb"),
            "vram_used_mb": sum(int(g.get("vram_used_mb") or 0) for g in gpus) if gpus else None,
            "vram_total_mb": sum(int(g.get("vram_total_mb") or 0) for g in gpus) if gpus else None,
            "gpu_pct": load.get("gpu_pct"),
            "psi_memory_some": ((load.get("psi") or {}).get("memory") or {}).get("some"),
            "reserved_ram_mb": sum(int(r.get("ram_mb") or 0) for r in reserved),
            "reserved_vram_mb": sum(int(r.get("vram_mb") or 0) for r in reserved),
            "apps": [{"name": a.get("name"), "rss_mb": a.get("rss_mb"), "vram_mb": a.get("vram_mb"),
                      "kind": a.get("kind")} for a in (load.get("heavy") or [])][:6],
        })
    return out


def _gb(mb: Any) -> str:
    return f"{float(mb) / 1024:.1f}"


def summarize(
    jobs: List[Dict[str, Any]],
    *,
    now: float,
    started_ts: float,
    expected: Optional[Dict[str, float]] = None,
    workers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The watch status for a run's jobs (see module doc); ``expected`` maps job id → seconds."""
    expected_by_id = expected or {}
    n = len(jobs)
    counts = {s: sum(1 for j in jobs if j.get("status") == s) for s in ("queued", "succeeded", "failed", "cancelled")}
    active = [j for j in jobs if j.get("status") in ("claimed", "running")]
    stopping = [j for j in jobs if j.get("status") in TERMINAL and j.get("held_by")]
    finished = all(j.get("status") in TERMINAL for j in jobs) and not stopping

    fractions, remaining = [], []
    for job in jobs:
        expected = expected_by_id.get(str(job.get("id")))
        frac = _fraction(job, expected, now)
        fractions.append(frac)
        remaining.append(_remaining(job, frac, expected, now))
    percent = int(round(100 * sum(fractions) / n)) if n else 0

    eta = None
    if not finished and all(r is not None for r in remaining):
        lanes = max(1, len({j.get("claimed_by") for j in active}))
        eta = int(sum(remaining) / lanes)  # type: ignore[arg-type]

    parts = [f"{counts['succeeded']}/{n} done"]
    if counts["failed"] or counts["cancelled"]:
        parts.append(f"{counts['failed'] + counts['cancelled']} failed")
    if active:
        focus = active[0]
        parts.append(f"{_job_label(focus, jobs.index(focus))}: {(focus.get('progress') or {}).get('message') or 'running'}")
    elif counts["queued"] and not finished:
        parts.append(_waiting(next(j for j in jobs if j.get("status") == "queued")))
    elif stopping:
        parts.append(f"stopping — waiting for {stopping[0]['held_by']} to confirm the workload ended")

    bars = [{"id": "overall", "label": "Overall", "percent": percent, "kind": "primary"}]
    by_worker: Dict[str, List[float]] = {}
    for job, frac in zip(jobs, fractions):
        if job.get("claimed_by"):
            by_worker.setdefault(str(job["claimed_by"]), []).append(frac)
    for wid, fr in sorted(by_worker.items()):
        bars.append({"id": f"w-{wid}", "label": wid, "percent": int(round(100 * sum(fr) / len(fr))), "kind": "worker"})

    errors = [{"job": j.get("id"), "label": _job_label(j, i), "error": str(j.get("error") or "")[:300],
               "oom": str(j.get("error") or "").startswith("oom")}
              for i, j in enumerate(jobs) if j.get("status") in ("failed", "cancelled")]
    previews = [{"job": j.get("id"), "url": (j.get("progress") or {}).get("preview_url")}
                for j in jobs if (j.get("progress") or {}).get("preview_url")]

    status: Dict[str, Any] = {
        "state": ("done" if counts["succeeded"] == n else "failed") if finished else "running",
        "percent": percent,
        "label": " · ".join(parts)[:200],
        "bars": bars,
        "elapsed_s": int(now - started_ts),
        "eta_s": eta,
        "errors": errors,
        "previews": previews[-4:],
        "resources": _resources(workers or [], [j for j in jobs if j.get("status") not in TERMINAL or j.get("held_by")]),
        "counts": {**counts, "running": len(active), "stopping": len(stopping), "total": n},
    }
    if n > 1:
        status["grid"] = {"unit": "item", "total": n,
                          "groups": sorted(by_worker),
                          "cells": [{"key": i + 1, "state": _CELL_STATE.get(str(j.get("status")), "pending"),
                                     "group": str(j.get("claimed_by") or ""),
                                     "note": f"{_job_label(j, i)} — {_detail(j)}"[:160]}
                                    for i, j in enumerate(jobs)]}
    status["activity"] = {
        "schema_version": 1,
        "summary": status["label"],
        "steps": [{"id": str(j.get("id"))[:64], "label": _job_label(j, i),
                   "state": _STEP_STATE.get(str(j.get("status")), "pending"), "detail": _detail(j)}
                  for i, j in enumerate(jobs[:32])],
    }
    return status


def _jobs_for(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    from api.device_workers.store import get_store

    store = get_store()
    return [store.get_job(jid) or {"id": jid, "status": "cancelled", "error": "job record missing"}
            for jid in record.get("mesh_jobs") or []]


def _status_for(record: Dict[str, Any], now: float, jobs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    from api.device_workers import metrics
    from api.device_workers.store import get_store

    store = get_store()
    jobs = _jobs_for(record) if jobs is None else jobs
    expected = {}
    for job in jobs:
        if job.get("status") not in TERMINAL:
            est = metrics.estimate(job, job.get("claimed_by") or job.get("target_worker_id"))
            if est["seconds"] is not None:
                expected[str(job["id"])] = est["seconds"]
    return summarize(jobs, now=now, started_ts=float(record.get("created_ts") or now),
                     expected=expected, workers=store.list_workers())


def write(record: Dict[str, Any], status: Dict[str, Any]) -> None:
    """Publish a running status (terminal ones go through the watch's resolve step)."""
    from api.job_watch import report_activity, write_status

    activity = status.pop("activity", None)
    write_status(record["id"], state="running", percent=status.pop("percent"), label=status.pop("label"),
                 bars=status.pop("bars"), grid=status.pop("grid", None),
                 extra={**status, "state": "running", **watch_fields(record)})
    if activity:
        report_activity(record["id"], activity)


def watch_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    """Status keys every write of a run's card keeps (Stop and the card read them)."""
    started = datetime.fromtimestamp(float(record.get("created_ts") or time.time()))
    return {"started_at": started.isoformat(timespec="seconds"), "started_ts": record.get("created_ts"),
            "run_id": record["id"], "unit_watch": True,
            "mesh_jobs": list(record.get("mesh_jobs") or []),
            "chat_session_id": str(record.get("chat_session_id") or ""), "pid": 0}


def check(record: Dict[str, Any], *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """None while the run is going (its status refreshed); else the watch outcome."""
    from api.device_workers import runs
    from api.device_workers.store import get_store

    now = time.time() if now is None else now
    get_store().reclaim_expired()  # lost leases and long-gone holders, even with no worker polling
    jobs = _jobs_for(record)
    status = _status_for(record, now, jobs)
    if status["state"] == "running":
        decision = runs.ask_if_blocked(record, jobs, now=now)
        if decision:
            status["decision"] = decision
        write(record, status)
        return None
    counts = status["counts"]
    summary = f"{counts['succeeded']} succeeded, {counts['failed']} failed, {counts['cancelled']} cancelled"
    stopped = sum(1 for j in jobs if j.get("status") == "cancelled" and j.get("stopped"))
    if stopped:
        summary += f" ({stopped} stopped on purpose from the card, Jobs or the CLI: do not resubmit them)"
    activity = status.pop("activity", None)
    if activity:
        from api.job_watch import report_activity

        report_activity(record["id"], activity)
    return {"state": status["state"], "exit": None, "result": "", "summary": summary,
            "label": status["label"], "status_extra": status,
            # Every job stopped on purpose is a Stop however it was done: no resume.
            **({"stopped_by_user": True} if jobs and stopped == len(jobs) else {})}


def touch(job_id: str) -> None:
    """Refresh the card of the run a job belongs to, right after its heartbeat lands.

    Host-side only (the heartbeat/complete/fail routes and the local worker
    loop); rate-limited per run. Never raises: a card refresh must not fail a
    heartbeat. The 15 s watch poll stays the backstop.
    """
    try:
        from api.device_workers.store import get_store
        from api.job_watch import unit_watch_records

        job = get_store().get_job(job_id)
        watch_id = str(((job or {}).get("params") or {}).get("watch_id") or "")
        if not watch_id:
            return
        now = time.time()
        with _touch_lock:
            if now - _touched.get(watch_id, 0.0) < TOUCH_MIN_SEC:
                return
            _touched[watch_id] = now
        for record in unit_watch_records():
            if record.get("id") == watch_id and record.get("mesh_jobs"):
                status = _status_for(record, now)
                if status["state"] == "running":
                    write(record, status)
                return
    except Exception:
        log.debug("run card refresh failed for job %s", job_id, exc_info=True)


def cancel(record_or_status: Dict[str, Any], *, reason: str = "cancelled from the watch card") -> int:
    """Cancel a run's unfinished jobs; returns how many were cancelled."""
    from api.device_workers.store import get_store

    store = get_store()
    return sum(1 for jid in record_or_status.get("mesh_jobs") or [] if store.cancel_job(jid, reason=reason))


def spec_digest(spec: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def new_run_id(title: str) -> str:
    stem = "".join(c if c.isalnum() else "-" for c in (title or "run").lower()).strip("-")[:40] or "run"
    return f"{stem}-{uuid.uuid4().hex[:6]}"
