"""Job performance history: how long a workload takes and how much it needs, per machine.

Every mesh job that ends on a worker (succeeded, failed, OOM) adds one row to
``job_metrics`` in ``device_workers.db``: wall time, queue wait, measured peak
RAM/VRAM (from the adapter or the job's memory scope), what it had reserved,
and per-stage seconds. Rows are keyed three ways:

* ``workload`` — what kind of job it is (:func:`workload_key`: template,
  else type + recipe);
* ``config`` — a digest of the parameters that change its cost (for ComfyUI:
  node classes and numeric inputs, not prompts, seeds or file names);
* ``worker_id`` — the machine.

:func:`estimate` answers from the narrowest match with enough samples
(same machine + config, then same config anywhere, then same workload), so
ETAs and reservations sharpen as runs accumulate. :func:`suggest` turns the
measured peaks into a reservation, raised after an OOM.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from typing import Any, Dict, List, Optional

SAMPLES = 20
HEADROOM = 1.15  # reservation = measured peak + 15 %
OOM_GROWTH = 1.5  # an OOM at reservation R asks for 1.5 R next time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_metrics (
    job_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    workload TEXT NOT NULL,
    config TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_s REAL,
    wait_s REAL,
    peak_ram_mb INTEGER,
    peak_vram_mb INTEGER,
    reserved_ram_mb INTEGER,
    reserved_vram_mb INTEGER,
    stages_json TEXT NOT NULL DEFAULT '[]',
    finished_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_metrics_key ON job_metrics(workload, config, worker_id, finished_at DESC);
"""

_VOLATILE = {"label", "template", "watch_id", "batch_id", "seed", "noise_seed", "chunk_index"}


def workload_key(job: Dict[str, Any]) -> str:
    params = job.get("params") or {}
    return str(params.get("template") or f"{job.get('type')}:{params.get('recipe') or ''}")


def _comfy_config(prompt: Dict[str, Any]) -> List[Any]:
    out = []
    for node_id in sorted(prompt, key=str):
        node = prompt[node_id] if isinstance(prompt[node_id], dict) else {}
        knobs = sorted((k, v) for k, v in (node.get("inputs") or {}).items()
                       if isinstance(v, (int, float, bool)) and k not in _VOLATILE)
        out.append([node.get("class_type"), knobs])
    return out


def config_key(job: Dict[str, Any]) -> str:
    """Digest of the parameters that change a job's cost (see module doc)."""
    params = job.get("params") or {}
    if str(job.get("type")) == "comfyui_prompt" and isinstance(params.get("prompt"), dict):
        basis: Any = _comfy_config(params["prompt"])
    else:
        basis = {k: v for k, v in params.items()
                 if not k.startswith("_") and k not in _VOLATILE and isinstance(v, (str, int, float, bool))}
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _connect():
    from api.device_workers.store import _connect as store_connect

    conn = store_connect()
    conn.executescript(_SCHEMA)
    return conn


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and int(value) > 0 else None
    except (TypeError, ValueError):
        return None


def record(job: Dict[str, Any], worker_id: str) -> None:
    """Add a finished job's measurements (host-side, from complete/fail)."""
    status = str(job.get("status") or "")
    if status not in ("succeeded", "failed") or not worker_id:
        return
    result = job.get("result") or {}
    error = str(job.get("error") or "")
    outcome = "oom" if error.startswith("oom") else status
    finished = float(job.get("completed_at") or time.time())
    # Wall time from the first claim: a retry that reattached to work already under
    # way reports only its own few seconds in ``elapsed_seconds``.
    duration = finished - float(job["started_at"]) if job.get("started_at") else result.get("elapsed_seconds")
    reserved = job.get("reserved") or {}
    stages = [{"label": t.get("label"), "seconds": t.get("seconds")}
              for t in (result.get("node_timings") or []) if isinstance(t, dict)][:64]
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO job_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job["id"], worker_id, str(job.get("type") or ""), workload_key(job), config_key(job), outcome,
             float(duration) if isinstance(duration, (int, float)) else None,
             float(job["started_at"]) - float(job["created_at"]) if job.get("started_at") and job.get("created_at") else None,
             _int(result.get("peak_ram_mb")), _int(result.get("peak_vram_mb")),
             _int(reserved.get("ram_mb")), _int(reserved.get("vram_mb")),
             json.dumps(stages), finished),
        )
        conn.commit()
    finally:
        conn.close()


def _rows(workload: str, config: Optional[str], worker_id: Optional[str]) -> List[Dict[str, Any]]:
    sql, args = "SELECT * FROM job_metrics WHERE workload = ?", [workload]
    if config:
        sql, args = sql + " AND config = ?", args + [config]
    if worker_id:
        sql, args = sql + " AND worker_id = ?", args + [worker_id]
    conn = _connect()
    try:
        rows = conn.execute(sql + " ORDER BY finished_at DESC LIMIT ?", (*args, SAMPLES * 2)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def estimate(job: Dict[str, Any], worker_id: Optional[str] = None,
             gpu_mb: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Expected duration and peaks for ``job`` (on ``worker_id`` when given).

    ``{"seconds", "peak_ram_mb", "peak_vram_mb", "samples", "basis", "oom_reserved_ram_mb",
    "vram_basis"}``; ``seconds`` is None with no history.

    With ``gpu_mb`` (each worker's largest GPU, MB) the VRAM peak comes from
    :func:`_vram_peak` instead: services such as ComfyUI keep more resident on
    a bigger card, so a peak only carries to a GPU at least as large."""
    workload, config = workload_key(job), config_key(job)
    tiers = ([("this machine, same settings", config, worker_id)] if worker_id else []) + [
        ("same settings, any machine", config, None), ("same workload", None, None)]
    out: Dict[str, Any] = {"seconds": None, "peak_ram_mb": None, "peak_vram_mb": None,
                           "oom_reserved_ram_mb": None, "samples": 0, "basis": "no history"}
    for basis, cfg, wid in tiers:
        rows = _rows(workload, cfg, wid)
        done = [r for r in rows if r["outcome"] == "succeeded" and r["duration_s"]][:SAMPLES]
        if not done:
            continue
        peaks = [r for r in rows if r["outcome"] in ("succeeded", "oom")][:SAMPLES]
        ooms = [r["reserved_ram_mb"] for r in rows if r["outcome"] == "oom" and r["reserved_ram_mb"]]
        out = {
            "seconds": statistics.median(r["duration_s"] for r in done),
            "peak_ram_mb": max((r["peak_ram_mb"] or 0 for r in peaks), default=0) or None,
            "peak_vram_mb": max((r["peak_vram_mb"] or 0 for r in peaks), default=0) or None,
            "oom_reserved_ram_mb": max(ooms) if ooms else None,
            "samples": len(done),
            "basis": basis,
        }
        break
    if gpu_mb is not None and worker_id:
        out["peak_vram_mb"], out["vram_basis"] = _vram_peak(workload, config, worker_id, gpu_mb)
    return out


def _vram_peak(workload: str, config: str, worker_id: str,
               gpu_mb: Dict[str, int]) -> "tuple[Optional[int], str]":
    """Measured VRAM peak for ``worker_id``: its own runs first, then runs on a
    GPU no larger than its own. ``(None, why)`` when neither exists."""
    mine = int(gpu_mb.get(worker_id) or 0)
    def measured(rows):
        return [r for r in rows if r["outcome"] in ("succeeded", "oom") and r["peak_vram_mb"]]

    for basis, cfg in (("same settings", config), ("same workload", None)):
        here = measured(_rows(workload, cfg, worker_id))[:SAMPLES]
        if here:
            return max(r["peak_vram_mb"] for r in here), f"this machine, {basis}"
        smaller = [r for r in measured(_rows(workload, cfg, None))
                   if mine and 0 < int(gpu_mb.get(r["worker_id"]) or 0) <= mine][:SAMPLES]
        if smaller:
            return max(r["peak_vram_mb"] for r in smaller), f"{basis}, GPU no larger"
    return None, "no history from a GPU this size or smaller"


def suggest(declared: Dict[str, Any], est: Dict[str, Any]) -> Dict[str, Any]:
    """Reservation to use given what the job declared and what history measured.

    Never lowers a declaration (the author may know about a spike history has
    not seen); raises it to the measured peak + headroom, and past any
    reservation that was OOM-killed."""
    out = dict(declared)
    ram = [int(declared.get("ram_mb") or 0)]
    if est.get("peak_ram_mb"):
        ram.append(math.ceil(est["peak_ram_mb"] * HEADROOM))
    if est.get("oom_reserved_ram_mb"):
        ram.append(math.ceil(est["oom_reserved_ram_mb"] * OOM_GROWTH))
    if max(ram):
        out["ram_mb"] = max(ram)
    if est.get("peak_vram_mb"):
        out["vram_mb"] = max(int(declared.get("vram_mb") or 0), math.ceil(est["peak_vram_mb"] * HEADROOM))
    return out
