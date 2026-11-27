"""Plan → approve → submit a mesh run (a batch of jobs owned by one chat).

``plan`` validates a run spec, checks every online worker against each job's
capabilities and declared resources (the same admission the claim uses), adds
history-based duration estimates, stores the spec under ``<home>/mesh_runs/``
and returns a ready approval form. Each option dispatches the allowlisted
``workers.run`` action with the run id, target and the spec's SHA-256, so the
user approves this exact spec: ``submit`` refuses one that changed after
approval. ``submit`` queues the jobs and registers the run's watch
(``api.job_watch`` + ``run_watch``), which shows progress live and resumes the
chat once when the run ends.

Spec::

    {"title": "Hunyuan drafts", "resume_message": "…",
     "jobs": [{"type": "comfyui_prompt", "label": "nacre", "template": "hunyuan-shape",
               "params": {...}, "requirements": {"resources": {"ram_mb": 9000, "vram_mb": 7000,
                                                               "service": "comfyui"}}}]}
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from api.device_workers import run_watch
from api.device_workers.admission import admit, requested
from api.device_workers.config import device_workers_enabled, heartbeat_stale_seconds
from api.device_workers.executor import validate_job_submission
from api.device_workers.store import _requirements_met, get_store

MAX_JOBS = 200


def _spec_path(run_id: str):
    from core.runtime_paths import runtime_state_path

    return runtime_state_path("mesh_runs", f"{run_id}.json")


def _job_specs(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    jobs = spec.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("spec.jobs must be a nonempty list")
    if len(jobs) > MAX_JOBS:
        raise ValueError(f"at most {MAX_JOBS} jobs per run")
    out = []
    for i, job in enumerate(jobs):
        if not isinstance(job, dict) or not job.get("type"):
            raise ValueError(f"jobs[{i}] needs a type")
        params = dict(job.get("params") or {})
        for key in ("label", "template"):
            if job.get(key):
                params[key] = str(job[key])
        ok, err = validate_job_submission(str(job["type"]), params)
        if not ok:
            raise ValueError(f"jobs[{i}] ({params.get('label') or job['type']}): {err}")
        out.append({"type": str(job["type"]), "params": params,
                    "requirements": dict(job.get("requirements") or {}),
                    "priority": int(job.get("priority") or 50)})
    return out


def _worker_fit(worker: Dict[str, Any], jobs: List[Dict[str, Any]], held: List[Dict[str, Any]]) -> Dict[str, Any]:
    caps = {**(worker.get("capabilities") or {}), "storage": worker.get("storage") or {}}
    age = time.time() - float(worker.get("last_seen") or 0)
    fits, reasons = 0, []
    for job in jobs:
        if not _requirements_met(job["requirements"], caps):
            reasons.append("missing capability")
            continue
        reservation, reason = admit(requested(job["requirements"]), worker.get("load") or {}, held,
                                    telemetry_age=age)
        if reservation is None:
            reasons.append(reason)
        else:
            fits += 1
    return {"worker_id": worker["worker_id"], "hostname": worker.get("hostname") or "",
            "fits_now": fits, "reasons": sorted(set(reasons))[:3]}


def _fmt_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return "unknown (no history yet)"
    sec = int(sec)
    return f"{sec // 3600}h {sec % 3600 // 60}m" if sec >= 3600 else f"{sec // 60}m {sec % 60}s"


def plan(spec: Dict[str, Any], *, chat_session_id: Any = None) -> Dict[str, Any]:
    """Validate + estimate a run and return its approval form (nothing is queued)."""
    from api.cuttle_ui_capabilities import numeric_chat_session_id

    if not device_workers_enabled():
        return {"success": False, "error": "device workers disabled"}
    try:
        jobs = _job_specs(spec)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    sid = numeric_chat_session_id(chat_session_id or os.environ.get("CUTTLE_CHAT_SESSION_ID"))
    if sid is None:
        return {"success": False, "error": "no chat to resume: pass --chat CH-…"}
    store = get_store()
    workers = [w for w in store.list_workers(stale_after=float(heartbeat_stale_seconds())) if w.get("online")]
    active = store.list_jobs(status="running", limit=200) + store.list_jobs(status="claimed", limit=200)
    fit = [_worker_fit(w, jobs, [j["reserved"] for j in active
                                 if j.get("claimed_by") == w["worker_id"] and j.get("reserved")])
           for w in workers]

    history = run_watch.median_durations(store.list_jobs(status="succeeded", limit=300))
    per_job = [history.get(run_watch.history_key(j)) for j in jobs]
    total = None if any(d is None for d in per_job) else sum(per_job)  # type: ignore[arg-type]

    title = str(spec.get("title") or "Mesh run")[:80]
    run_id = run_watch.new_run_id(title)
    stored = {"title": title, "chat_session_id": int(sid), "jobs": jobs,
              "resume_message": str(spec.get("resume_message") or "")[:18000]}
    digest = run_watch.spec_digest(stored)
    _spec_path(run_id).write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

    needs = [requested(j["requirements"]) for j in jobs]
    peak_ram = max((n.get("ram_mb", 0) for n in needs), default=0)
    peak_vram = max((n.get("vram_mb", 0) for n in needs), default=0)
    lines = [f"{len(jobs)} job(s); estimated {_fmt_seconds(total)} run one at a time."]
    if peak_ram or peak_vram:
        lines.append(f"Largest job reserves {peak_ram / 1024:.1f} GB RAM, {peak_vram / 1024:.1f} GB VRAM.")
    for f in fit:
        why = f" — {'; '.join(f['reasons'])}" if f["reasons"] else ""
        lines.append(f"{f['worker_id']}: {f['fits_now']}/{len(jobs)} fit now{why}")
    params = {"run_id": run_id, "spec_sha256": digest}
    options = [{"id": "auto", "label": "Run — any worker as resources free up",
                "action": "workers.run", "params": {**params, "target": ""}}]
    options += [{"id": f"on-{f['worker_id']}", "label": f"Run on {f['worker_id']} only",
                 "action": "workers.run", "params": {**params, "target": f["worker_id"]}}
                for f in fit if f["fits_now"] or f["reasons"] != ["missing capability"]]
    options.append({"id": "not-now", "label": "Don't run it"})
    form = {"title": f"Approve mesh run — {title}", "mode": "choice", "resume": True,
            "description": "\n".join(lines), "options": options}
    return {"success": True, "run_id": run_id, "spec_sha256": digest, "workers": fit,
            "estimate_seconds": total, "per_job_seconds": per_job, "form": form,
            "card": "<cuttle_action_form>\n" + json.dumps(form, ensure_ascii=False, indent=2)
                    + "\n</cuttle_action_form>"}


def submit(run_id: str, *, spec_sha256: str, target: str = "") -> Dict[str, Any]:
    """Queue an approved run and register its watch; refuses a changed spec."""
    from api.job_watch import register_unit_watch

    path = _spec_path(run_id) if run_id and "/" not in run_id and "\\" not in run_id else None
    if path is None or not path.is_file():
        return {"success": False, "error": f"unknown run {run_id!r} (plan it first)"}
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored.get("submitted"):
        return {"success": False, "error": "run already submitted", "jobs": stored.get("job_ids")}
    if run_watch.spec_digest(stored) != (spec_sha256 or "").strip():
        return {"success": False, "error": "run spec changed after approval; plan it again"}
    store = get_store()
    ids = []
    for job in stored["jobs"]:
        created = store.submit_job(
            job_type=job["type"], params={**job["params"], "watch_id": run_id, "batch_id": run_id},
            requirements=job["requirements"], target_worker_id=(target or "").strip() or None,
            submitted_by=f"run:{run_id}", priority=job["priority"])
        ids.append(created["id"])
    resume = stored.get("resume_message") or (
        f"Mesh run {run_id} ({stored['title']}) has finished. Read its watch status for per-job "
        "results, errors and output files, then continue. Do not resubmit jobs that succeeded.")
    watch = register_unit_watch(mesh_jobs=ids, chat_session_id=stored["chat_session_id"], job_id=run_id,
                                title=f"Mesh run — {stored['title']}", resume_message=resume)
    stored.update({"submitted": time.time(), "job_ids": ids, "target": target})
    path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    return {"success": True, "run_id": run_id, "jobs": ids, "target": target or "any",
            "status_url": watch["url"], "card": watch["card"]}
