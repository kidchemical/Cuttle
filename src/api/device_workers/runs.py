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

Durations and reservations come from ``api.device_workers.metrics``: a job
whose history measured a higher peak than it declared reserves the measured
peak (+15 %) instead, and the form says so.

When nothing can place a job, Cuttle never queues silently: the approval form
says why for every worker (named processes, offline machines), and a run that
makes no progress for ``ask_after_s`` (default 10 min) posts one decision form
(:func:`ask_if_blocked`): move the waiting jobs to a worker that fits now,
keep waiting, or cancel. ``workers.run-decide`` applies the pick (:func:`decide`).

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

from api.device_workers import metrics, run_watch
from api.device_workers.admission import VRAM_MARGIN_MB, admit, requested
from api.device_workers.config import device_workers_enabled, heartbeat_stale_seconds
from api.device_workers.executor import validate_job_submission
from api.device_workers.store import _requirements_met, get_store

MAX_JOBS = 200
ASK_AFTER_SEC = 600
WAIT_MORE_SEC = 1800


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


def _with_history(jobs: List[Dict[str, Any]], workers: List[Dict[str, Any]]) -> List[str]:
    """Raise each job's reservation to what its history measured; notes for the form."""
    vram_cap = max((int(g.get("vram_total_mb") or 0) for w in workers for g in (w.get("load") or {}).get("gpus") or []),
                   default=0) - VRAM_MARGIN_MB
    notes = []
    for job in jobs:
        declared = dict((job["requirements"] or {}).get("resources") or {})
        est = metrics.estimate(job)
        if not est["samples"]:
            continue
        wanted = metrics.suggest(declared, est)
        if vram_cap > 0 and int(wanted.get("vram_mb") or 0) > vram_cap:
            wanted["vram_mb"] = max(int(declared.get("vram_mb") or 0), vram_cap)
        raised = [k for k in ("ram_mb", "vram_mb") if int(wanted.get(k) or 0) > int(declared.get(k) or 0)]
        if not raised:
            continue
        job["requirements"] = {**job["requirements"], "resources": wanted}
        what = ", ".join(f"{k[:-3].upper()} {int(declared.get(k) or 0) / 1024:.1f} → {wanted[k] / 1024:.1f} GB"
                         for k in raised)
        notes.append(f"{job['params'].get('label') or job['type']}: reservation from {est['samples']} past run(s) "
                     f"({est['basis']}): {what}")
    return notes


def _offline(store) -> List[str]:
    now = time.time()
    return [f"{w['worker_id']}: offline (last seen {_fmt_seconds(now - float(w.get('last_seen') or now))} ago)"
            for w in store.list_workers(stale_after=float(heartbeat_stale_seconds())) if not w.get("online")]


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
    history_notes = _with_history(jobs, workers)
    fit = [_worker_fit(w, jobs, _held(store, w["worker_id"])) for w in workers]

    estimates = [metrics.estimate(j) for j in jobs]
    per_job = [e["seconds"] for e in estimates]
    total = None if any(d is None for d in per_job) else sum(per_job)  # type: ignore[arg-type]
    bases = sorted({e["basis"] for e in estimates if e["samples"]})

    title = str(spec.get("title") or "Mesh run")[:80]
    run_id = run_watch.new_run_id(title)
    ask_after = max(60, int(spec.get("ask_after_s") or ASK_AFTER_SEC))
    stored = {"title": title, "chat_session_id": int(sid), "jobs": jobs, "ask_after_s": ask_after,
              "resume_message": str(spec.get("resume_message") or "")[:18000]}
    digest = run_watch.spec_digest(stored)
    _save(run_id, stored)

    needs = [requested(j["requirements"]) for j in jobs]
    peak_ram = max((n.get("ram_mb", 0) for n in needs), default=0)
    peak_vram = max((n.get("vram_mb", 0) for n in needs), default=0)
    fits_anywhere = any(f["fits_now"] for f in fit)
    lines = [f"{len(jobs)} job(s); estimated {_fmt_seconds(total)} run one at a time"
             + (f" (history: {', '.join(bases)})." if total is not None else ".")]
    if not fits_anywhere:
        lines.insert(0, f"Nothing can start right now. If it still cannot after {ask_after // 60} min, "
                        "Cuttle asks you again with the alternatives; it never waits silently.")
    if peak_ram or peak_vram:
        lines.append(f"Largest job reserves {peak_ram / 1024:.1f} GB RAM, {peak_vram / 1024:.1f} GB VRAM.")
    lines += history_notes
    for f in fit:
        why = f" — {'; '.join(f['reasons'])}" if f["reasons"] else ""
        lines.append(f"{f['worker_id']}: {f['fits_now']}/{len(jobs)} fit now{why}")
    lines += _offline(store)
    params = {"run_id": run_id, "spec_sha256": digest}
    options = [{"id": "auto", "label": ("Run — any worker as resources free up" if fits_anywhere
                                         else f"Queue it — ask me again if it hasn't started in {ask_after // 60} min"),
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


def _held(store, worker_id: str) -> List[Dict[str, Any]]:
    return [j["reserved"] for j in store.list_jobs(limit=500)
            if j.get("held_by") == worker_id and j.get("reserved")]


def _load(run_id: str) -> Optional[Dict[str, Any]]:
    if not run_id or "/" in run_id or "\\" in run_id:
        return None
    path = _spec_path(run_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _save(run_id: str, stored: Dict[str, Any]) -> None:
    _spec_path(run_id).write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")


def _decision_form(run_id: str, stored: Dict[str, Any], waiting: List[Dict[str, Any]], store) -> Dict[str, Any]:
    workers = [w for w in store.list_workers(stale_after=float(heartbeat_stale_seconds())) if w.get("online")]
    lines = [f"{len(waiting)} job(s) of “{stored['title']}” have not been able to start:"]
    lines += [f"- {j['params'].get('label') or j['type']}: {j.get('wait_reason') or 'no worker took it (busy, or none can run it)'}"
              for j in waiting[:6]]
    options = []
    for w in workers:
        f = _worker_fit(w, waiting, _held(store, w["worker_id"]))
        lines.append(f"{w['worker_id']}: {f['fits_now']}/{len(waiting)} fit now"
                     + (f" — {'; '.join(f['reasons'])}" if f["reasons"] else ""))
        if f["fits_now"] == len(waiting) and any(j.get("target_worker_id") != w["worker_id"] for j in waiting):
            options.append({"id": f"move-{w['worker_id']}", "label": f"Move them to {w['worker_id']} (fits now)",
                            "action": "workers.run-decide", "params": {"run_id": run_id, "choice": f"move:{w['worker_id']}"}})
    lines += _offline(store)
    options += [
        {"id": "wait", "label": f"Keep waiting — ask again in {WAIT_MORE_SEC // 60} min",
         "action": "workers.run-decide", "params": {"run_id": run_id, "choice": "wait"}},
        {"id": "cancel", "label": "Cancel the waiting jobs",
         "action": "workers.run-decide", "params": {"run_id": run_id, "choice": "cancel"}},
    ]
    return {"title": f"Mesh run can't start — {stored['title']}", "mode": "choice", "resume": False,
            "description": "\n".join(lines), "options": options}


def ask_if_blocked(record: Dict[str, Any], jobs: List[Dict[str, Any]], *, now: float) -> Optional[Dict[str, Any]]:
    """Post one decision form when the run has made no progress for ``ask_after_s``.

    Called by ``run_watch.check`` on every poll of a running run; returns the open
    decision (for the card) or None. A pick (``decide``) re-arms it."""
    from api.chat_ops import store as chat_ops_store

    stored = _load(str(record.get("id") or ""))
    if not stored or not stored.get("submitted"):
        return None
    if any(j.get("status") in ("claimed", "running") for j in jobs):
        return None
    waiting = [j for j in jobs if j.get("status") == "queued"]
    if not waiting:
        return None
    decision = stored.get("decision") or {}
    if decision.get("asked_at") and not decision.get("answered_at"):
        return {"asked_at": decision["asked_at"], "form": True}
    progress_at = max([float(stored["submitted"]), float(decision.get("answered_at") or 0)]
                      + [float(j["completed_at"]) for j in jobs if j.get("completed_at")])
    due = float(decision.get("wait_until") or progress_at + float(stored.get("ask_after_s") or ASK_AFTER_SEC))
    if now < due:
        return None
    form = _decision_form(record["id"], stored, waiting, get_store())
    n = int(decision.get("n") or 0) + 1
    text = (f"Mesh run `{record['id']}` ({stored['title']}) can't start. Cuttle prepared the decision form "
            "below: post it exactly as given and end the turn. Do not change, resubmit or cancel jobs yourself; "
            "the user's pick is applied by Cuttle.\n\n<cuttle_action_form>\n"
            + json.dumps(form, ensure_ascii=False, indent=2) + "\n</cuttle_action_form>")
    chat_ops_store.create("notice", int(stored["chat_session_id"]), int(stored["chat_session_id"]),
                          {"text": text, "label": "Mesh run needs a decision", "job_id": f"{record['id']}#decide{n}"})
    stored["decision"] = {"asked_at": now, "n": n}
    _save(record["id"], stored)
    return {"asked_at": now, "form": True}


def decide(run_id: str, *, choice: str) -> Dict[str, Any]:
    """Apply the pick from a decision form: ``move:<worker>``, ``wait`` or ``cancel``."""
    from api.job_watch import cancel_job as stop_watch

    stored = _load(run_id)
    if not stored or not stored.get("submitted"):
        return {"success": False, "error": f"unknown or unsubmitted run {run_id!r}"}
    store = get_store()
    now = time.time()
    decision = {**(stored.get("decision") or {}), "answered_at": now, "choice": choice}
    if choice == "cancel":
        stop_watch(run_id)
        out: Dict[str, Any] = {"success": True, "cancelled": True}
    elif choice == "wait":
        decision["wait_until"] = now + WAIT_MORE_SEC
        out = {"success": True, "ask_again_in_s": WAIT_MORE_SEC}
    elif choice.startswith("move:"):
        target = choice.split(":", 1)[1].strip()
        moved = store.retarget_queued(stored.get("job_ids") or [], target)
        decision.pop("wait_until", None)
        out = {"success": True, "moved": moved, "target": target}
    else:
        return {"success": False, "error": f"unknown choice {choice!r}"}
    stored["decision"] = decision
    _save(run_id, stored)
    return {**out, "run_id": run_id}


def submit(run_id: str, *, spec_sha256: str, target: str = "") -> Dict[str, Any]:
    """Queue an approved run and register its watch; refuses a changed spec."""
    from api.job_watch import register_unit_watch

    stored = _load(run_id)
    if stored is None:
        return {"success": False, "error": f"unknown run {run_id!r} (plan it first)"}
    if stored.get("submitted"):
        return {"success": False, "error": "run already submitted", "jobs": stored.get("job_ids")}
    if run_watch.spec_digest(stored) != (spec_sha256 or "").strip():
        return {"success": False, "error": "run spec changed after approval; plan it again"}
    store = get_store()
    if store.submissions_paused():
        return {"success": False, "error": "mesh job submissions are paused for a host upgrade; approve again once it is done"}
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
    _save(run_id, stored)
    return {"success": True, "run_id": run_id, "jobs": ids, "target": target or "any",
            "status_url": watch["url"], "card": watch["card"]}
