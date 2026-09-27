"""One-shot dogfood: claim blender_render shards as a temporary worker (no daemon restart).

Usage (repo root):
  .venv\\Scripts\\python.exe src\\scripts\\dogfood_blender_mesh.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from api.device_workers.capabilities import collect_capabilities, find_blender_executable
from api.device_workers.executor import execute_job
from api.device_workers.platform import list_jobs, submit_blender_shards, wait_jobs
from api.device_workers.store import get_store


def main() -> int:
    blender = find_blender_executable()
    if not blender:
        print("FAIL: no blender found")
        return 1

    desktop = Path.home() / "Desktop" / "CuttleWorkerBlend"
    blend = desktop / "mesh_smoke.blend"
    out = desktop / "out_mesh"
    out.mkdir(parents=True, exist_ok=True)
    if not blend.is_file():
        print(f"FAIL: missing {blend} — create it first")
        return 1

    wid = "dogfood-blender"
    ads = collect_capabilities()
    store = get_store()
    store.upsert_worker(
        worker_id=wid,
        hostname=ads.get("hostname") or "dogfood",
        os_name=ads.get("os") or "",
        capabilities={**(ads.get("capabilities") or {}), "blender": True},
        load=ads.get("load") or {},
        interactive_priority="low",
        ac_power=True,
        meta={"cuttle_version": ads.get("cuttle_version") or "", "dogfood": True},
    )

    # Prefer only this temporary worker for the shard (patch listing via target).
    # submit_blender_shards picks any blender worker — ensure we are online with blender.
    # Force prefer_remote=False and make us the only blender-capable online by
    # submitting two jobs targeted at wid directly if shard picks others.
    result = submit_blender_shards(
        blend_file=str(blend),
        output_dir=str(out),
        frame_start=1,
        frame_end=2,
        engine="BLENDER_WORKBENCH",
        blender_bin=blender,
        submitted_by="dogfood",
        prefer_remote=False,
        max_shards=2,
        dry_run=False,
    )
    print("shard submit:", result.get("success"), "count=", result.get("shard_count"), result.get("error"))
    if not result.get("success"):
        # Fallback: two targeted jobs
        from api.device_workers.platform import submit_job

        jobs = []
        for fs, fe in ((1, 1), (2, 2)):
            r = submit_job(
                job_type="blender_render",
                params={
                    "blend_file": str(blend),
                    "output_dir": str(out),
                    "frame_start": fs,
                    "frame_end": fe,
                    "engine": "BLENDER_WORKBENCH",
                    "blender_bin": blender,
                    "timeout_seconds": 180,
                },
                requirements={"blender": True},
                target_worker_id=wid,
                submitted_by="dogfood",
                priority=90,
            )
            jobs.append(r.get("job"))
        result = {"success": True, "jobs": jobs, "shard_count": len(jobs), "targets": [wid]}

    job_ids = [str(j["id"]) for j in (result.get("jobs") or []) if j and j.get("id")]
    print("job_ids", job_ids)

    deadline = time.time() + 240
    while time.time() < deadline and job_ids:
        store.upsert_worker(
            worker_id=wid,
            hostname=ads.get("hostname") or "dogfood",
            os_name=ads.get("os") or "",
            capabilities={**(ads.get("capabilities") or {}), "blender": True},
            load=ads.get("load") or {},
            meta={"cuttle_version": ads.get("cuttle_version") or "", "dogfood": True},
        )
        claimed = store.claim_jobs(
            worker_id=wid,
            capabilities={"blender": True, "filesystem": True},
            limit=2,
            lease_seconds=120,
        )
        for job in claimed:
            jid = job["id"]
            print(f"claimed {jid} frames {job.get('params')}")
            try:
                store.heartbeat_job(jid, wid, lease_seconds=120)
                res = execute_job(job)
                store.complete_job(jid, wid, result=res)
                print(f"  ok {res}")
            except Exception as e:
                store.fail_job(jid, wid, error=str(e), retry=False)
                print(f"  FAIL {e}")
        # Check remaining
        pending = []
        for jid in job_ids:
            j = store.get_job(jid)
            if j and j.get("status") not in ("succeeded", "failed", "cancelled"):
                pending.append(jid)
        job_ids = pending
        if not job_ids:
            break
        time.sleep(0.5)

    listing = list_jobs(limit=10)
    print("recent jobs:")
    for j in listing.get("jobs") or []:
        if j.get("submitted_by") == "dogfood" or j.get("type") == "blender_render":
            print(
                " ",
                j.get("id", "")[:8],
                j.get("status"),
                j.get("params", {}).get("frame_start"),
                j.get("params", {}).get("frame_end"),
                j.get("error") or (j.get("result") or {}).get("elapsed_seconds"),
            )
    frames = list(out.glob("frame_*"))
    print(f"output files ({len(frames)}):", [p.name for p in frames[:10]])
    return 0 if frames else 1


if __name__ == "__main__":
    raise SystemExit(main())
