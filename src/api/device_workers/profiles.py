"""Worker performance profiles for mesh scheduling heuristics.

Records EWMA sec/frame from completed blender_render jobs into worker meta
(`render_profile`). Used for observability now; future weighted shard plans
can read the same blob.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


_EWMA_ALPHA = 0.35


def _engine_key(params: Optional[Dict[str, Any]], result: Optional[Dict[str, Any]]) -> str:
    for src in (result or {}, params or {}):
        eng = str(src.get("engine") or "").strip().lower()
        if eng:
            # Normalize Blender RNA ids to short labels
            if "eevee" in eng:
                return "eevee"
            if "cycles" in eng:
                return "cycles"
            if "workbench" in eng:
                return "workbench"
            return eng[:32]
    return "default"


def sec_per_frame_from_result(result: Dict[str, Any]) -> Optional[float]:
    """Best available sec/frame from a blender_render result payload."""
    if not isinstance(result, dict):
        return None
    spf = result.get("sec_per_frame")
    if isinstance(spf, (int, float)) and float(spf) > 0:
        return float(spf)
    times = result.get("frame_times")
    if isinstance(times, list) and times:
        secs = [
            float(t.get("seconds"))
            for t in times
            if isinstance(t, dict) and isinstance(t.get("seconds"), (int, float))
        ]
        if secs:
            return sum(secs) / len(secs)
    elapsed = result.get("elapsed_seconds")
    fs = result.get("frame_start")
    fe = result.get("frame_end")
    try:
        n = int(fe) - int(fs) + 1
        if isinstance(elapsed, (int, float)) and n > 0 and float(elapsed) > 0:
            return float(elapsed) / n
    except (TypeError, ValueError):
        pass
    return None


def maybe_record_job_result(store: Any, worker_id: str, job: Dict[str, Any]) -> None:
    """If job is a successful blender_render, merge EWMA into worker meta."""
    if not isinstance(job, dict):
        return
    if str(job.get("type") or "") != "blender_render":
        return
    if str(job.get("status") or "") != "succeeded":
        return
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if result.get("dry_run"):
        return
    spf = sec_per_frame_from_result(result)
    if spf is None or spf <= 0:
        return
    wid = (worker_id or "").strip()
    if not wid:
        return
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    key = _engine_key(params, result)
    worker = store.get_worker(wid)
    meta = dict(worker.get("meta") or {}) if worker else {}
    profile = meta.get("render_profile")
    if not isinstance(profile, dict):
        profile = {}
    bucket = profile.get(key)
    if not isinstance(bucket, dict):
        bucket = {}
    prev = bucket.get("ewma_spf")
    samples = int(bucket.get("samples") or 0)
    if isinstance(prev, (int, float)) and samples > 0:
        ewma = (_EWMA_ALPHA * spf) + ((1.0 - _EWMA_ALPHA) * float(prev))
    else:
        ewma = spf
    frames = 0
    try:
        fs = int(result.get("frame_start") if result.get("frame_start") is not None else params.get("frame_start") or 0)
        fe = int(result.get("frame_end") if result.get("frame_end") is not None else params.get("frame_end") or fs)
        frames = max(0, fe - fs + 1)
    except (TypeError, ValueError):
        frames = int(result.get("frames_written") or 0) or 0
    profile[key] = {
        "ewma_spf": round(float(ewma), 4),
        "last_spf": round(float(spf), 4),
        "samples": samples + 1,
        "frames_total": int(bucket.get("frames_total") or 0) + frames,
        "updated_at": time.time(),
    }
    store.patch_worker_meta(wid, {"render_profile": profile})


def ewma_spf_for_worker(worker: Dict[str, Any], *, engine: str = "") -> Optional[float]:
    """Read EWMA sec/frame from a worker listing row (for future weighted plans)."""
    meta = worker.get("meta") if isinstance(worker.get("meta"), dict) else {}
    profile = meta.get("render_profile") if isinstance(meta.get("render_profile"), dict) else {}
    key = _engine_key({"engine": engine}, {})
    for candidate in (key, "default", "eevee", "cycles"):
        bucket = profile.get(candidate)
        if isinstance(bucket, dict) and isinstance(bucket.get("ewma_spf"), (int, float)):
            val = float(bucket["ewma_spf"])
            if val > 0:
                return val
    return None
