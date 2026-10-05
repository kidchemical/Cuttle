"""Agent-agnostic device-workers platform verbs (host-side).

Used by CLI / action YAML (project `.cuttle/actions`, global `.cuttle_global/actions`) —
not Cursor-specific. Any harness invokes the same contract via Context Compiler
→ cuttle-workers.md → these verbs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from api.device_workers.config import device_workers_enabled, heartbeat_stale_seconds, worker_id
from api.device_workers.executor import validate_job_submission
from api.device_workers.store import get_store


def list_workers(*, online_only: bool = False) -> Dict[str, Any]:
    if not device_workers_enabled():
        return {
            "success": True,
            "enabled": False,
            "workers": [],
            "self_worker_id": "",
            "online_remote": 0,
        }
    from api.device_workers.capabilities import (
        cuttle_git_rev as local_cuttle_git_rev,
        cuttle_version as local_cuttle_version,
        git_revs_differ,
    )

    store = get_store()
    self_id = worker_id()
    workers = store.list_workers(stale_after=float(heartbeat_stale_seconds()))
    online_remote = 0
    out: List[Dict[str, Any]] = []
    local_ver = local_cuttle_version()
    local_rev = local_cuttle_git_rev()
    for w in workers:
        is_self = str(w.get("worker_id") or "") == self_id
        w = dict(w)
        w["is_self"] = is_self
        meta = w.get("meta") if isinstance(w.get("meta"), dict) else {}
        peer_rev = str(meta.get("cuttle_git_rev") or w.get("cuttle_git_rev") or "").strip()
        if is_self:
            if not w.get("cuttle_version") and local_ver:
                w["cuttle_version"] = local_ver
            if not peer_rev and local_rev:
                peer_rev = local_rev
                meta = {**meta, "cuttle_git_rev": local_rev}
                w["meta"] = meta
        w["cuttle_git_rev"] = peer_rev
        peer_ver = str(w.get("cuttle_version") or "").strip()
        ver_mismatch = bool(
            (not is_self)
            and local_ver
            and peer_ver
            and peer_ver != local_ver
        )
        caps = w.get("capabilities") if isinstance(w.get("capabilities"), dict) else {}
        # Missing git rev on an updatable Client = pre-rev sidecar / not yet
        # pulled — treat as behind so Update shows after a Host push even when
        # electron/package.json was not bumped.
        rev_mismatch = bool(
            (not is_self)
            and local_rev
            and (
                (peer_rev and git_revs_differ(local_rev, peer_rev))
                or (
                    (not peer_rev)
                    and bool(caps.get("cuttle_self_update"))
                    and bool(w.get("online"))
                )
            )
        )
        w["version_mismatch"] = ver_mismatch
        w["git_mismatch"] = rev_mismatch
        boot_rev = str(meta.get("boot_git_rev") or w.get("boot_git_rev") or "").strip()
        w["boot_git_rev"] = boot_rev
        # Disk rev is what the worker advertises as cuttle_git_rev; boot is the
        # rev loaded into the live claim loop. Differ ⇒ stale process (CH-000478).
        stale_process = bool(
            boot_rev and peer_rev and git_revs_differ(boot_rev, peer_rev)
        )
        w["stale_process"] = stale_process
        w["needs_update"] = bool(
            (ver_mismatch or rev_mismatch or stale_process) and w.get("online")
        )
        if w.get("online") and not is_self:
            online_remote += 1
        if online_only and not w.get("online"):
            continue
        out.append(w)

    # Stable display order: host (self) always #1, then by first-seen registration
    # time (then worker_id). Do NOT sort by last_seen — that reshuffles the table
    # on every heartbeat poll.
    def _slot_key(w: Dict[str, Any]) -> tuple:
        is_self = 0 if w.get("is_self") else 1
        try:
            reg = float(w.get("registered_at") or 0)
        except (TypeError, ValueError):
            reg = 0.0
        return (is_self, reg, str(w.get("worker_id") or "").lower())

    out.sort(key=_slot_key)
    for i, w in enumerate(out, start=1):
        w["slot"] = i

    return {
        "success": True,
        "enabled": True,
        "workers": out,
        "self_worker_id": self_id,
        "host_cuttle_version": local_ver,
        "host_git_rev": local_rev,
        "online_remote": online_remote,
        "online_total": sum(1 for w in out if w.get("online")),
        "outdated_workers": [
            str(w.get("worker_id") or "")
            for w in out
            if w.get("needs_update")
        ],
    }


def remove_worker(
    worker_id_arg: str,
    *,
    revoke_enroll: bool = True,
) -> Dict[str, Any]:
    """Remove a registered device from the mesh registry (UI / agents).

    Refuses the live host worker id (it would just reappear on the next
    heartbeat). Offline smoke-test junk and Clients are fair game.
    """
    if not device_workers_enabled():
        return {"success": False, "error": "device workers disabled"}
    wid = (worker_id_arg or "").strip()
    if not wid:
        return {"success": False, "error": "worker_id required"}
    self_id = (worker_id() or "").strip()
    if self_id and wid.lower() == self_id.lower():
        return {
            "success": False,
            "error": "cannot remove the host worker",
            "worker_id": wid,
        }
    try:
        result = get_store().remove_worker(wid, revoke_enroll=revoke_enroll)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    if not result.get("found"):
        return {
            "success": False,
            "error": "worker not found",
            "worker_id": wid,
        }
    return {
        "success": True,
        "worker_id": wid,
        "removed": bool(result.get("removed")),
        "enroll_revoked": bool(result.get("enroll_revoked")),
    }


def submit_job(
    *,
    job_type: str,
    params: Optional[Dict[str, Any]] = None,
    requirements: Optional[Dict[str, Any]] = None,
    target_worker_id: Optional[str] = None,
    submitted_by: Optional[str] = None,
    priority: int = 50,
    ttl_seconds: Optional[float] = None,
    expires_at: Optional[float] = None,
    max_attempts: Optional[int] = None,
) -> Dict[str, Any]:
    if not device_workers_enabled():
        return {"success": False, "error": "device workers disabled"}
    params = params if isinstance(params, dict) else {}
    from api.device_workers.executor import _normalize_job_type

    job_type = _normalize_job_type(job_type)
    ok, err = validate_job_submission(job_type, params)
    if not ok:
        return {"success": False, "error": err}
    job = get_store().submit_job(
        job_type=job_type,
        params=params,
        requirements=requirements if isinstance(requirements, dict) else {},
        target_worker_id=target_worker_id,
        submitted_by=submitted_by or "platform",
        priority=priority,
        ttl_seconds=ttl_seconds,
        expires_at=expires_at,
        max_attempts=max_attempts,
    )
    return {"success": True, "job": job}


def get_job(job_id: str) -> Dict[str, Any]:
    job = get_store().get_job(job_id)
    if not job:
        return {"success": False, "error": "not found"}
    return {"success": True, "job": job}


def list_jobs(*, status: Optional[str] = None, limit: int = 40) -> Dict[str, Any]:
    get_store().reclaim_expired()
    jobs = get_store().list_jobs(status=status, limit=limit)
    return {"success": True, "jobs": jobs}


def cancel_job(job_id: str, *, reason: str = "cancelled") -> Dict[str, Any]:
    job = get_store().cancel_job(job_id, reason=reason)
    if not job:
        return {"success": False, "error": "not found or not cancellable"}
    return {"success": True, "job": job}


def wait_jobs(
    job_ids: List[str],
    *,
    timeout_seconds: float = 600,
    poll_seconds: float = 2,
) -> Dict[str, Any]:
    ids = [str(j).strip() for j in job_ids if str(j).strip()]
    if not ids:
        return {"success": False, "error": "job_ids required"}
    deadline = time.time() + max(1.0, float(timeout_seconds))
    poll = max(0.5, float(poll_seconds))
    store = get_store()
    terminal = {"succeeded", "failed", "cancelled"}
    results: Dict[str, Any] = {}
    while time.time() < deadline:
        store.reclaim_expired()
        pending = []
        for jid in ids:
            job = store.get_job(jid)
            if not job:
                results[jid] = {"id": jid, "status": "missing"}
                continue
            results[jid] = job
            if str(job.get("status") or "") not in terminal:
                pending.append(jid)
        if not pending:
            all_ok = all(
                str((results.get(j) or {}).get("status") or "") == "succeeded" for j in ids
            )
            return {
                "success": all_ok,
                "done": True,
                "jobs": [results[j] for j in ids],
            }
        time.sleep(poll)
    return {
        "success": False,
        "done": False,
        "error": "timeout",
        "jobs": [results.get(j) for j in ids],
    }


def submit_blender_shards(
    *,
    blend_file: str,
    output_dir: str,
    frame_start: int,
    frame_end: int,
    engine: str = "",
    blender_bin: str = "",
    submitted_by: str = "platform",
    prefer_remote: bool = True,
    max_shards: int = 8,
    dry_run: bool = False,
    distribution: str = "work_steal",
    chunk_size: int = 0,
    batch_id: str = "",
) -> Dict[str, Any]:
    """Enqueue blender_render work across online blender-capable workers.

    distribution:
      - work_steal (default): small untargeted chunks; idle workers claim next
      - pinned: equal frame counts pinned to specific workers (legacy)
    """
    import uuid

    from api.device_workers.intent import (
        auto_chunk_size,
        chunk_frame_ranges,
        pick_blender_workers,
        shard_frame_ranges,
    )

    if frame_end < frame_start:
        return {"success": False, "error": "frame_end must be >= frame_start"}
    listing = list_workers(online_only=True)
    workers = listing.get("workers") or []
    targets = pick_blender_workers(
        workers,
        prefer_remote=prefer_remote,
        self_worker_id=str(listing.get("self_worker_id") or ""),
    )
    if not targets:
        return {
            "success": False,
            "error": "no online workers with capabilities.blender",
            "workers_seen": len(workers),
            "hint": "Blender must be installed (Program Files or PATH); restart worker/daemon to refresh ads",
        }

    mode = (distribution or "work_steal").strip().lower().replace("-", "_")
    if mode in ("steal", "chunks", "chunk", "dynamic"):
        mode = "work_steal"
    if mode not in ("work_steal", "pinned"):
        return {
            "success": False,
            "error": f"unknown distribution={distribution!r} (use work_steal|pinned)",
        }

    total = int(frame_end) - int(frame_start) + 1
    bid = (batch_id or "").strip() or uuid.uuid4().hex[:12]
    reexpanded = False
    explicit_chunk = int(chunk_size or 0) > 0
    if mode == "work_steal":
        size = int(chunk_size) if int(chunk_size or 0) > 0 else auto_chunk_size(
            total, len(targets)
        )
        # Prefer many small units over silent sticky growth. Explicit chunk_size
        # never re-expands; auto may raise the cap so long ranges stay stealable.
        max_chunks = max(int(max_shards) * 32, len(targets) * 16, 64)
        ranges = chunk_frame_ranges(frame_start, frame_end, chunk_size=size)
        if len(ranges) > max_chunks and not explicit_chunk:
            # Re-chunk larger so we stay under max_chunks (last resort).
            size = max(1, (total + max_chunks - 1) // max_chunks)
            ranges = chunk_frame_ranges(frame_start, frame_end, chunk_size=size)
            reexpanded = True
        elif len(ranges) > max_chunks and explicit_chunk:
            # Keep explicit size; allow more jobs than the soft cap.
            pass
    else:
        size = 0
        ranges = shard_frame_ranges(
            frame_start,
            frame_end,
            worker_count=min(len(targets), max(1, int(max_shards))),
        )

    if not ranges:
        return {"success": False, "error": "empty frame range"}

    jobs = []
    for i, (fs, fe) in enumerate(ranges):
        params: Dict[str, Any] = {
            "blend_file": blend_file,
            "output_dir": output_dir,
            "frame_start": fs,
            "frame_end": fe,
            "batch_id": bid,
            "chunk_index": i,
            "chunk_count": len(ranges),
            "distribution": mode,
        }
        if engine:
            params["engine"] = engine
        if blender_bin:
            params["blender_bin"] = blender_bin
        if dry_run:
            params["dry_run"] = True
        # Earlier chunks slightly higher priority so the queue drains in order
        # while still allowing steal of later chunks once workers free up.
        priority = 60 + max(0, 20 - i)
        target_id = None
        if mode == "pinned":
            target_id = str(targets[i % len(targets)]["worker_id"])
        res = submit_job(
            job_type="blender_render",
            params=params,
            requirements={"blender": True},
            target_worker_id=target_id,
            submitted_by=submitted_by,
            priority=priority,
        )
        if not res.get("success"):
            return {
                "success": False,
                "error": res.get("error") or "submit failed",
                "jobs_submitted": jobs,
                "batch_id": bid,
            }
        jobs.append(res["job"])
    return {
        "success": True,
        "batch_id": bid,
        "distribution": mode,
        "chunk_size": size if mode == "work_steal" else None,
        "chunk_reexpanded": bool(reexpanded) if mode == "work_steal" else False,
        "shard_count": len(jobs),
        "targets": [t["worker_id"] for t in targets],
        "eligible_workers": [t["worker_id"] for t in targets],
        "jobs": jobs,
        "dry_run": dry_run,
    }


def batch_status(batch_id: str) -> Dict[str, Any]:
    """Summarize all mesh jobs sharing params.batch_id."""
    bid = (batch_id or "").strip()
    if not bid:
        return {"success": False, "error": "batch_id required"}
    store = get_store()
    store.reclaim_expired()
    jobs = store.list_batch_jobs(bid)
    matched = [
        j
        for j in jobs
        if isinstance(j.get("params"), dict)
        and str(j["params"].get("batch_id") or "") == bid
    ]
    matched.sort(key=lambda j: int((j.get("params") or {}).get("chunk_index") or 0))
    by_status: Dict[str, int] = {}
    by_worker: Dict[str, Dict[str, Any]] = {}
    frame_times: List[Dict[str, Any]] = []
    for j in matched:
        st = str(j.get("status") or "?")
        by_status[st] = by_status.get(st, 0) + 1
        wid = str(j.get("claimed_by") or j.get("target_worker_id") or "unclaimed")
        slot = by_worker.setdefault(
            wid, {"chunks": 0, "frames": 0, "elapsed_seconds": 0.0, "statuses": {}}
        )
        slot["chunks"] += 1
        slot["statuses"][st] = slot["statuses"].get(st, 0) + 1
        params = j.get("params") or {}
        result = j.get("result") if isinstance(j.get("result"), dict) else {}
        try:
            fs = int(params.get("frame_start") or 0)
            fe = int(params.get("frame_end") or fs)
            slot["frames"] += max(0, fe - fs + 1)
        except (TypeError, ValueError):
            pass
        if isinstance(result.get("elapsed_seconds"), (int, float)):
            slot["elapsed_seconds"] = round(
                float(slot["elapsed_seconds"]) + float(result["elapsed_seconds"]), 2
            )
        if isinstance(result.get("sec_per_frame"), (int, float)):
            slot.setdefault("sec_per_frame_samples", []).append(
                float(result["sec_per_frame"])
            )
        for ft in result.get("frame_times") or []:
            if isinstance(ft, dict):
                frame_times.append(ft)
    terminal = {"succeeded", "failed", "cancelled"}
    pending = sum(1 for j in matched if str(j.get("status") or "") not in terminal)
    return {
        "success": True,
        "batch_id": bid,
        "job_count": len(matched),
        "pending": pending,
        "done": pending == 0 and bool(matched),
        "by_status": by_status,
        "by_worker": by_worker,
        "frame_times_sample": frame_times[-40:],
        "jobs": matched,
        "workers": store.list_workers(),
    }


def gap_fill_batch(
    batch_id: str,
    *,
    chunk_size: int = 1,
    submitted_by: str = "gap-fill",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Enqueue stealable jobs for durable units still missing from a batch.

    Scans ``output_dir`` (from any job in the batch) for ``frame_*`` files across
    the union of all chunk spans, then submits work_steal chunks for gaps.
    Generic idea: durable unit inventory → only missing units re-queued.
    """
    from api.device_workers.executor import _list_frames_in_range, _missing_frames
    from api.device_workers.intent import chunk_frame_ranges

    status = batch_status(batch_id)
    if not status.get("success"):
        return status
    jobs = status.get("jobs") or []
    if not jobs:
        return {"success": False, "error": "no jobs for batch", "batch_id": batch_id}

    # Prefer a job that still has blend/output params.
    template = None
    overall_start = None
    overall_end = None
    for j in jobs:
        params = j.get("params") if isinstance(j.get("params"), dict) else {}
        if not params.get("blend_file") or not params.get("output_dir"):
            continue
        template = params
        fs, fe = _job_frame_span(j)
        if fe < fs:
            continue
        overall_start = fs if overall_start is None else min(overall_start, fs)
        overall_end = fe if overall_end is None else max(overall_end, fe)
    if template is None or overall_start is None or overall_end is None:
        return {
            "success": False,
            "error": "batch jobs missing blend_file/output_dir/frame range",
            "batch_id": batch_id,
        }

    out_dir = str(template.get("output_dir") or "").strip()
    from pathlib import Path

    out_path = Path(out_dir)
    present = _list_frames_in_range(out_path, int(overall_start), int(overall_end))
    missing = _missing_frames(int(overall_start), int(overall_end), present)
    if not missing:
        return {
            "success": True,
            "batch_id": batch_id,
            "missing_count": 0,
            "jobs_submitted": [],
            "message": "no missing frames",
        }

    # Skip ranges already queued/running/claimed to avoid duplicate work.
    active_spans = []
    for j in jobs:
        st = str(j.get("status") or "")
        if st not in ("queued", "claimed", "running"):
            continue
        fs, fe = _job_frame_span(j)
        if fe >= fs:
            active_spans.append((fs, fe))

    def _covered(frame: int) -> bool:
        for a, b in active_spans:
            if a <= frame <= b:
                return True
        return False

    still_missing = [f for f in missing if not _covered(f)]
    if not still_missing:
        return {
            "success": True,
            "batch_id": batch_id,
            "missing_count": len(missing),
            "jobs_submitted": [],
            "message": "missing frames already covered by active jobs",
        }

    size = max(1, int(chunk_size or 1))
    # Build contiguous ranges from still_missing, then chunk.
    ranges: List[tuple] = []
    run_start = still_missing[0]
    prev = still_missing[0]
    for f in still_missing[1:]:
        if int(f) == prev + 1:
            prev = int(f)
            continue
        ranges.extend(chunk_frame_ranges(run_start, prev, chunk_size=size))
        run_start = int(f)
        prev = int(f)
    ranges.extend(chunk_frame_ranges(run_start, prev, chunk_size=size))

    if dry_run:
        return {
            "success": True,
            "batch_id": batch_id,
            "dry_run": True,
            "missing_count": len(still_missing),
            "ranges": [{"frame_start": a, "frame_end": b} for a, b in ranges],
        }

    submitted = []
    for i, (fs, fe) in enumerate(ranges):
        params = {
            "blend_file": template.get("blend_file"),
            "output_dir": template.get("output_dir"),
            "frame_start": fs,
            "frame_end": fe,
            "batch_id": batch_id,
            "chunk_index": 10000 + i,
            "chunk_count": len(ranges),
            "distribution": "work_steal",
            "gap_fill": True,
        }
        if template.get("engine"):
            params["engine"] = template.get("engine")
        if template.get("blender_bin"):
            params["blender_bin"] = template.get("blender_bin")
        res = submit_job(
            job_type="blender_render",
            params=params,
            requirements={"blender": True},
            submitted_by=submitted_by,
            priority=80,
        )
        if not res.get("success"):
            return {
                "success": False,
                "error": res.get("error") or "gap-fill submit failed",
                "batch_id": batch_id,
                "jobs_submitted": submitted,
                "missing_count": len(still_missing),
            }
        submitted.append(res.get("job"))

    return {
        "success": True,
        "batch_id": batch_id,
        "missing_count": len(still_missing),
        "shard_count": len(submitted),
        "chunk_size": size,
        "jobs_submitted": submitted,
    }


def _job_frame_span(job: Dict[str, Any]) -> tuple[int, int]:
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    try:
        fs = int(params.get("frame_start") or 0)
        fe = int(params.get("frame_end") or fs)
    except (TypeError, ValueError):
        return 0, -1
    if fe < fs:
        return 0, -1
    return fs, fe


def build_batch_watch_bars(
    summary: Dict[str, Any],
    *,
    label_prefix: str = "Mesh bake",
) -> Dict[str, Any]:
    """Build job_watch fields (percent/label/bars/state) from a batch_status summary.

    Always returns overall ``kind:primary`` plus one ``kind:worker`` bar per claimed
    worker (skips ``unclaimed``). Frame counts prefer on-disk ``frame_*`` files when
    ``output_dir`` is readable; otherwise uses succeeded chunk spans.
    """
    from pathlib import Path

    from api.device_workers.executor import _count_frames_in_range

    jobs = [j for j in (summary.get("jobs") or []) if isinstance(j, dict)]
    if not jobs:
        return {
            "success": False,
            "error": "no jobs in batch",
            "state": "running",
            "percent": 0,
            "label": f"{label_prefix} — waiting for shards",
            "bars": [
                {
                    "id": "overall",
                    "label": "Overall",
                    "percent": 0,
                    "kind": "primary",
                    "detail": "0/0",
                }
            ],
        }

    out_dir = ""
    frame_lo: Optional[int] = None
    frame_hi: Optional[int] = None
    for j in jobs:
        params = j.get("params") if isinstance(j.get("params"), dict) else {}
        if not out_dir:
            out_dir = str(params.get("output_dir") or "").strip()
        fs, fe = _job_frame_span(j)
        if fe < fs:
            continue
        frame_lo = fs if frame_lo is None else min(frame_lo, fs)
        frame_hi = fe if frame_hi is None else max(frame_hi, fe)

    total = 0
    if frame_lo is not None and frame_hi is not None and frame_hi >= frame_lo:
        total = frame_hi - frame_lo + 1

    out_path = Path(out_dir) if out_dir else None
    disk_ok = bool(out_path and out_path.is_dir())

    def frames_in_span(fs: int, fe: int) -> int:
        if fe < fs:
            return 0
        if disk_ok and out_path is not None:
            return int(_count_frames_in_range(out_path, fs, fe))
        return 0

    done_overall = (
        frames_in_span(frame_lo, frame_hi)
        if frame_lo is not None and frame_hi is not None
        else 0
    )
    if not disk_ok:
        # Union spans: a retry/gap-fill must never count the same frame twice.
        spans = sorted(_job_frame_span(j) for j in jobs if j.get("status") == "succeeded")
        done_overall = 0
        end = None
        for fs, fe in spans:
            if fe < fs:
                continue
            start = fs if end is None else max(fs, end + 1)
            done_overall += max(0, fe - start + 1)
            end = fe if end is None else max(end, fe)

    pct = min(100, round(100 * done_overall / total)) if total else 0
    n_ok = sum(1 for j in jobs if j.get("status") == "succeeded")
    n_fail = sum(1 for j in jobs if j.get("status") == "failed")
    n_run = sum(1 for j in jobs if j.get("status") in ("claimed", "running"))
    # attempts increments on each claim; first claim is not a reallocation.
    reallocations = 0
    chunks_reallocated = 0
    for j in jobs:
        try:
            attempts = max(0, int(j.get("attempts") or 0))
        except (TypeError, ValueError):
            attempts = 0
        extra = max(0, attempts - 1)
        if extra:
            reallocations += extra
            chunks_reallocated += 1
    realloc_note = ""
    if reallocations:
        realloc_note = (
            f", {reallocations} chunk realloc"
            f"{'s' if reallocations != 1 else ''}"
            f" ({chunks_reallocated} chunk"
            f"{'s' if chunks_reallocated != 1 else ''})"
        )
    label = (
        f"{label_prefix} — {done_overall}/{total or '?'} frames "
        f"({n_ok}/{len(jobs)} shards done, {n_run} active, {n_fail} failed"
        f"{realloc_note})"
    )

    overall_detail = f"{done_overall}/{total}" if total else f"{done_overall}"
    if reallocations:
        overall_detail = (
            f"{overall_detail} · {reallocations} realloc"
            f"{'s' if reallocations != 1 else ''}"
        )

    bars: List[Dict[str, Any]] = [
        {
            "id": "overall",
            "label": "Overall",
            "percent": pct,
            "kind": "primary",
            "detail": overall_detail,
        }
    ]

    # Group jobs by claimed worker (skip queue).
    by_wid: Dict[str, List[Dict[str, Any]]] = {}
    for j in jobs:
        wid = str(j.get("claimed_by") or j.get("target_worker_id") or "").strip()
        if not wid or wid == "unclaimed":
            continue
        by_wid.setdefault(wid, []).append(j)

    worker_gpus = {str(w.get("worker_id")): str((w.get("capabilities") or {}).get("blender_gpu") or "")
                   for w in summary.get("workers", [])}
    for wid in sorted(by_wid.keys()):
        wjobs = by_wid[wid]
        assigned = 0
        written = 0
        ok_chunks = 0
        for j in wjobs:
            fs, fe = _job_frame_span(j)
            if fe < fs:
                continue
            span = fe - fs + 1
            assigned += span
            st = str(j.get("status") or "")
            if st == "succeeded":
                ok_chunks += 1
                written += span if not disk_ok else frames_in_span(fs, fe)
            elif st in ("claimed", "running", "failed"):
                written += frames_in_span(fs, fe) if disk_ok else 0
            else:
                written += frames_in_span(fs, fe) if disk_ok else 0
        written = min(written, assigned) if assigned else written
        wpct = min(100, round(100 * written / assigned)) if assigned else 0
        bars.append(
            {
                "id": wid[:64],
                "label": (wid + (" · " + worker_gpus[wid] if worker_gpus.get(wid) else ""))[:120],
                "percent": wpct,
                "kind": "worker",
                "detail": f"{written}/{assigned} · {ok_chunks}/{len(wjobs)} chunks",
            }
        )

    state = "running"
    if n_fail and (n_ok + n_fail) >= len(jobs):
        state = "failed"
        label = label + " — shard failures"
    elif len(jobs) > 0 and n_ok >= len(jobs) and n_fail == 0:
        state = "done"
        pct = 100 if total and done_overall >= total else pct
        bars[0]["percent"] = pct
        label = f"{label_prefix} done — {done_overall}/{total or '?'} frames"
        if reallocations:
            label += (
                f" ({reallocations} chunk realloc"
                f"{'s' if reallocations != 1 else ''})"
            )

    if state == "done" and total and done_overall < total:
        state = "failed"
        label = f"{label_prefix} incomplete — {done_overall}/{total} frames"
    if all(j.get("status") in ("succeeded", "failed", "cancelled") for j in jobs) and state == "running":
        state = "failed"
        label += " — cancelled shards"

    return {
        "success": True,
        "batch_id": str(summary.get("batch_id") or ""),
        "state": state,
        "percent": pct,
        "label": label,
        "bars": bars,
        "frames_done": done_overall,
        "frames_total": total,
        "output_dir": out_dir,
        "chunk_reallocations": reallocations,
        "chunks_reallocated": chunks_reallocated,
    }


def write_batch_watch(
    batch_id: str,
    *,
    watch_id: str = "",
    label_prefix: str = "Mesh bake",
    auto_gap_fill: bool = True,
) -> Dict[str, Any]:
    """Write ``/output/<watch_id>-status.json`` with overall + per-worker bars.

    When ``auto_gap_fill`` and the batch looks failed/incomplete with durable
    missing frames, enqueue stealable gap-fill jobs so the farm self-heals.
    """
    from api.job_watch import write_status

    summary = batch_status(batch_id)
    if not summary.get("success"):
        return summary
    built = build_batch_watch_bars(summary, label_prefix=label_prefix)
    if not built.get("success") and not built.get("bars"):
        return built

    gap = None
    state = str(built.get("state") or "running")
    pending = int(summary.get("pending") or 0)
    # Heal when no active work remains but frames are still missing.
    if auto_gap_fill and pending == 0 and state in ("failed", "done"):
        try:
            pct = int(built.get("percent") or 0)
        except (TypeError, ValueError):
            pct = 0
        if pct < 100 or state == "failed":
            gap = gap_fill_batch(batch_id, chunk_size=1, submitted_by="batch-watch")
            if gap.get("success") and int(gap.get("shard_count") or 0) > 0:
                summary = batch_status(batch_id)
                built = build_batch_watch_bars(summary, label_prefix=label_prefix)
                state = str(built.get("state") or "running")

    wid = (watch_id or batch_id or "mesh-batch").strip()
    from api.device_workers.render_results import reconcile
    try:
        render_result = reconcile(batch_id)
    except Exception as exc:
        render_result = {"success": False, "state": "output_unavailable", "error": str(exc)}
    path = write_status(
        wid,
        state=state,
        percent=int(built.get("percent") or 0),
        label=str(built.get("label") or ""),
        bars=built.get("bars"),
        extra={
            "batch_id": str(summary.get("batch_id") or batch_id),
            "gap_fill": gap,
            "render_result": render_result,
        },
        grid=build_batch_frame_grid(summary) if _frame_grid_enabled() else None,
    )
    return {
        **built,
        "watch_id": wid,
        "status_path": str(path),
        "job_count": summary.get("job_count"),
        "pending": summary.get("pending"),
        "by_worker": summary.get("by_worker"),
        "gap_fill": gap,
        "render_result": render_result,
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)


def submit_self_update(
    *,
    target_worker_id: str,
    repo: str = "",
    restart_electron: bool = True,
    restart_daemon: bool = True,
    submitted_by: str = "platform",
    host: str = "",
) -> Dict[str, Any]:
    """Enqueue cuttle_self_update on a Client worker (stash+pull, then relaunch).

    Embeds the Host's current updater scripts so Clients still on an old
    checkout run the latest updater even before git pull succeeds.
    """
    params: Dict[str, Any] = {
        "restart_electron": bool(restart_electron),
        "restart_daemon": bool(restart_daemon),
    }
    if repo:
        params["repo"] = repo
    if (host or "").strip():
        params["host"] = host.strip()
    # Ship the latest updater bytes with the job (chicken-egg with dirty Clients).
    scripts_dir = Path(__file__).resolve().parents[3] / ".cuttle_global" / "scripts"
    try:
        ps1 = scripts_dir / "client-self-update.ps1"
        if ps1.is_file():
            text = ps1.read_text(encoding="utf-8")
            if text.strip():
                params["script_text"] = text
                params["script_name"] = "client-self-update.ps1"
    except OSError:
        pass
    try:
        sh = scripts_dir / "client-self-update.sh"
        if sh.is_file():
            text = sh.read_text(encoding="utf-8")
            if text.strip():
                params["script_text_posix"] = text
                params["script_name_posix"] = "client-self-update.sh"
    except OSError:
        pass
    return submit_job(
        job_type="cuttle_self_update",
        params=params,
        requirements={"cuttle_self_update": True},
        target_worker_id=target_worker_id,
        submitted_by=submitted_by,
        priority=80,
    )


def probe_worker(worker_id: str, *, timeout_seconds: float = 20) -> Dict[str, Any]:
    """Submit a targeted ping and measure time until success (mesh latency proxy)."""
    wid = (worker_id or "").strip()
    if not wid:
        return {"success": False, "error": "worker_id required"}
    if not device_workers_enabled():
        return {"success": False, "error": "device workers disabled"}
    t0 = time.time()
    submitted = submit_job(
        job_type="ping",
        params={"echo": "probe", "t0": t0},
        target_worker_id=wid,
        submitted_by="probe",
        priority=100,
    )
    if not submitted.get("success"):
        return submitted
    job = submitted["job"]
    jid = str(job.get("id") or "")
    deadline = t0 + max(3.0, float(timeout_seconds))
    store = get_store()
    while time.time() < deadline:
        store.reclaim_expired()
        cur = store.get_job(jid)
        if not cur:
            break
        st = str(cur.get("status") or "")
        if st == "succeeded":
            rtt_ms = round((time.time() - t0) * 1000.0, 1)
            store.patch_worker_meta(
                wid,
                {
                    "last_rtt_ms": rtt_ms,
                    "last_rtt_at": time.time(),
                    "last_rtt_job_id": jid,
                },
            )
            return {
                "success": True,
                "worker_id": wid,
                "rtt_ms": rtt_ms,
                "job_id": jid,
                "job": cur,
            }
        if st in ("failed", "cancelled"):
            return {
                "success": False,
                "error": cur.get("error") or st,
                "worker_id": wid,
                "job_id": jid,
                "job": cur,
            }
        time.sleep(0.35)
    return {
        "success": False,
        "error": "probe timeout (worker may be slow to claim — check poll interval)",
        "worker_id": wid,
        "job_id": jid,
    }


def _frame_grid_enabled() -> bool:
    from api.job_watch import grid_enabled
    return grid_enabled()


def build_batch_frame_grid(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Frame-range producer for the generic watch ``grid`` (see action-forms.md).

    Bounded presentation snapshot; durable inventory wins over shard status.

    Missing remote files cannot be verified: succeeded spans are explicitly
    labelled reported rather than pretending the coordinator inspected them.
    Completion ownership requires a single first-attempt claim or frame timing
    evidence. Reclaimed chunks can contain output from an earlier worker.
    """
    from api.device_workers.executor import _list_frames_in_range
    from api.job_watch import GRID_MAX_CELLS

    jobs = [j for j in summary.get("jobs", []) if isinstance(j, dict)]
    spans = [(j, *_job_frame_span(j)) for j in jobs]
    spans = [(j, a, b) for j, a, b in spans if b >= a]
    if not spans:
        return {"unit": "frame", "cells": [], "total": 0, "groups": []}
    lo, hi = min(a for _, a, _ in spans), max(b for _, _, b in spans)
    out = next((str((j.get("params") or {}).get("output_dir") or "") for j, _, _ in spans
                if (j.get("params") or {}).get("output_dir")), "")
    disk_ok = bool(out and Path(out).is_dir())
    present = set(_list_frames_in_range(Path(out), lo, hi)) if disk_ok else set()
    cells = []
    for frame in range(lo, min(hi + 1, lo + GRID_MAX_CELLS)):
        covering = [j for j, a, b in spans if a <= frame <= b]
        active = [j for j in covering if j.get("status") in ("claimed", "running")]
        queued = [j for j in covering if j.get("status") == "queued"]
        succeeded = [j for j in covering if j.get("status") == "succeeded"]
        complete = frame in present if disk_ok else bool(succeeded)
        owners = set()
        if complete:
            for j in covering:
                wid = str(j.get("claimed_by") or "")
                timings = (j.get("result") or {}).get("frame_times") or []
                evidence = any(isinstance(t, dict) and t.get("frame") == frame for t in timings)
                if wid and (evidence or (len(covering) == 1 and int(j.get("attempts") or 0) <= 1)):
                    owners.add(wid)
            state = "completed"
            wid = next(iter(owners)) if len(owners) == 1 else ""
        else:
            state = ("running" if active else "pending" if queued else
                     "failed" if any(j.get("status") == "failed" for j in covering) else
                     "cancelled" if any(j.get("status") == "cancelled" for j in covering) else "missing")
            wid = str(active[0].get("claimed_by") or "") if len(active) == 1 else ""
        cells.append({"key": frame, "state": state, "group": wid,
                      "marked": any((j.get("params") or {}).get("gap_fill") for j in covering)})
    workers = sorted({str(j.get("claimed_by")) for j in jobs if j.get("claimed_by")})
    return {"unit": "frame", "title": "Frame map", "marked_label": "gap-fill",
            "cells": cells, "total": hi - lo + 1, "omitted": max(0, hi - lo + 1 - len(cells)),
            "inventory": "verified" if disk_ok else "reported", "groups": workers}
