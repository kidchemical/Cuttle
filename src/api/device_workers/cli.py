"""CLI entry: python -m api.device_workers.cli <verb> …

Reads CUTTLE_ACTION_PARAMS_JSON / CUTTLE_PARAM_* when invoked from .cuttle/actions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional


def _params_from_env() -> Dict[str, Any]:
    raw = (os.environ.get("CUTTLE_ACTION_PARAMS_JSON") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    out: Dict[str, Any] = {}
    prefix = "CUTTLE_PARAM_"
    for key, val in os.environ.items():
        if key.startswith(prefix) and val is not None:
            out[key[len(prefix) :].lower()] = val
    return out


def _parse_json_maybe(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def main(argv: Optional[List[str]] = None) -> int:
    # Ensure `api` imports when launched from repo root via actions.
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if here not in sys.path:
        sys.path.insert(0, here)

    from api.device_workers import platform as plat
    from api.device_workers.intent import plan_from_message

    env_params = _params_from_env()
    parser = argparse.ArgumentParser(prog="api.device_workers.cli", description="Cuttle workers platform verbs")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List device workers")
    p_list.add_argument("--online-only", action="store_true")

    p_submit = sub.add_parser("submit", help="Submit a mesh job")
    p_submit.add_argument("--type", dest="job_type", default="")
    p_submit.add_argument("--params-json", default="")
    p_submit.add_argument("--target", default="")
    p_submit.add_argument("--submitted-by", default="")
    p_submit.add_argument("--priority", type=int, default=50)
    p_submit.add_argument("--requirements-json", default="")
    p_submit.add_argument("--ttl-seconds", type=float, default=0)
    p_submit.add_argument("--expires-at", type=float, default=0)
    p_submit.add_argument("--max-attempts", type=int, default=0)

    p_status = sub.add_parser("status", help="Job status or recent jobs")
    p_status.add_argument("--job-id", default="")
    p_status.add_argument("--limit", type=int, default=40)

    p_cancel = sub.add_parser("cancel", help="Cancel a job")
    p_cancel.add_argument("--job-id", default="")
    p_cancel.add_argument("--reason", default="cancelled")

    p_wait = sub.add_parser("wait", help="Wait for job id(s)")
    p_wait.add_argument("--job-ids", default="")
    p_wait.add_argument("--timeout", type=float, default=600)
    p_wait.add_argument("--poll", type=float, default=2)

    p_plan = sub.add_parser("plan", help="Classify a message for mesh intent (no enqueue)")
    p_plan.add_argument("--message", default="")

    p_blend = sub.add_parser("blender-shard", help="Shard blender_render across workers")
    p_blend.add_argument("--blend-file", default="")
    p_blend.add_argument("--output-dir", default="")
    p_blend.add_argument("--frame-start", type=int, default=0)
    p_blend.add_argument("--frame-end", type=int, default=0)
    p_blend.add_argument("--engine", default="")
    p_blend.add_argument("--blender-bin", default="")
    p_blend.add_argument("--submitted-by", default="platform")
    p_blend.add_argument("--max-shards", type=int, default=8)
    p_blend.add_argument("--dry-run", action="store_true")
    p_blend.add_argument("--prefer-local", action="store_true", help="Prefer host over remote for shards")
    p_blend.add_argument(
        "--distribution",
        default="work_steal",
        help="work_steal (default, chunk queue) or pinned (equal ranges)",
    )
    p_blend.add_argument(
        "--chunk-size",
        type=int,
        default=0,
        help="Frames per stealable chunk (0=auto)",
    )
    p_blend.add_argument("--batch-id", default="", help="Optional batch id for grouping")

    p_batch = sub.add_parser("batch-status", help="Summarize a blender shard batch")
    p_batch.add_argument("--batch-id", default="")

    p_gap = sub.add_parser(
        "gap-fill",
        help="Enqueue stealable jobs for missing durable frames in a batch",
    )
    p_gap.add_argument("--batch-id", default="")
    p_gap.add_argument("--chunk-size", type=int, default=1)
    p_gap.add_argument("--submitted-by", default="gap-fill")
    p_gap.add_argument("--dry-run", action="store_true")

    p_bwatch = sub.add_parser(
        "batch-watch",
        help="Write job_watch status with overall + per-worker bars for a batch",
    )
    p_bwatch.add_argument("--batch-id", default="")
    p_bwatch.add_argument(
        "--id",
        dest="watch_id",
        default="",
        help="job_watch id (default: batch-id)",
    )
    p_bwatch.add_argument("--label-prefix", default="Mesh bake")
    p_bwatch.add_argument(
        "--loop",
        action="store_true",
        help="Poll until batch done/failed (or --max-hours)",
    )
    p_bwatch.add_argument("--interval", type=float, default=60)
    p_bwatch.add_argument("--max-hours", type=float, default=12)

    p_su = sub.add_parser(
        "self-update",
        help="Ask a Client worker to git pull + restart Electron/client-daemon",
    )
    p_su.add_argument("--target", default="")
    p_su.add_argument("--repo", default="")
    p_su.add_argument("--no-electron", action="store_true")
    p_su.add_argument("--no-daemon", action="store_true")
    p_su.add_argument("--submitted-by", default="platform")

    p_probe = sub.add_parser("probe", help="Ping a worker and measure mesh RTT")
    p_probe.add_argument("--target", default="")
    p_probe.add_argument("--timeout", type=float, default=20)

    args = parser.parse_args(argv)

    # Env params fill missing CLI flags (action forms).
    def env(key: str, fallback: Any = "") -> Any:
        if key in env_params and env_params[key] not in (None, ""):
            return env_params[key]
        return fallback

    if args.cmd == "list":
        result = plat.list_workers(online_only=bool(args.online_only or env("online_only")))
    elif args.cmd == "submit":
        job_type = str(args.job_type or env("type") or env("job_type") or "").strip()
        params_raw = args.params_json or env("params_json") or env("params") or "{}"
        params = _parse_json_maybe(str(params_raw))
        if not isinstance(params, dict):
            params = {}
        # Flatten common file_copy / blender / shell fields from env
        for k in (
            "source",
            "dest",
            "blend_file",
            "output_dir",
            "frame_start",
            "frame_end",
            "engine",
            "blender_bin",
            "echo",
            "recipe",
            "repo",
            "restart_electron",
            "restart_daemon",
        ):
            if k in env_params and k not in params:
                params[k] = env_params[k]
        if not job_type and params.get("recipe"):
            job_type = "shell"
        if not job_type and (
            "restart_electron" in params or env("target") and env("recipe") == ""
        ):
            pass
        req_raw = args.requirements_json or env("requirements_json") or env("requirements") or "{}"
        reqs = _parse_json_maybe(str(req_raw))
        if not isinstance(reqs, dict):
            reqs = {}
        if job_type == "blender_render" and "blender" not in reqs:
            reqs["blender"] = True
        if job_type == "cuttle_self_update" and "cuttle_self_update" not in reqs:
            reqs["cuttle_self_update"] = True
        target = str(args.target or env("target") or env("target_worker_id") or "") or None
        ttl_raw = args.ttl_seconds or env("ttl_seconds") or 0
        exp_raw = args.expires_at or env("expires_at") or 0
        max_raw = args.max_attempts or env("max_attempts") or 0
        try:
            ttl_v = float(ttl_raw) if float(ttl_raw or 0) > 0 else None
        except (TypeError, ValueError):
            ttl_v = None
        try:
            exp_v = float(exp_raw) if float(exp_raw or 0) > 0 else None
        except (TypeError, ValueError):
            exp_v = None
        try:
            max_v = int(max_raw) if int(max_raw or 0) > 0 else None
        except (TypeError, ValueError):
            max_v = None
        result = plat.submit_job(
            job_type=job_type,
            params=params,
            requirements=reqs,
            target_worker_id=target,
            submitted_by=str(args.submitted_by or env("submitted_by") or "platform"),
            priority=int(args.priority or env("priority") or 50),
            ttl_seconds=ttl_v,
            expires_at=exp_v,
            max_attempts=max_v,
        )
    elif args.cmd == "status":
        jid = str(args.job_id or env("job_id") or "").strip()
        if jid:
            result = plat.get_job(jid)
        else:
            result = plat.list_jobs(limit=int(args.limit or env("limit") or 40))
    elif args.cmd == "cancel":
        jid = str(args.job_id or env("job_id") or "").strip()
        result = plat.cancel_job(jid, reason=str(args.reason or env("reason") or "cancelled"))
    elif args.cmd == "wait":
        raw_ids = str(args.job_ids or env("job_ids") or env("job_id") or "")
        ids = [p.strip() for p in raw_ids.replace(";", ",").split(",") if p.strip()]
        result = plat.wait_jobs(
            ids,
            timeout_seconds=float(args.timeout or env("timeout") or 600),
            poll_seconds=float(args.poll or env("poll") or 2),
        )
    elif args.cmd == "plan":
        msg = str(args.message or env("message") or env("text") or env("content") or "")
        listing = plat.list_workers()
        result = plan_from_message(
            msg,
            workers=listing.get("workers") or [],
            self_worker_id=str(listing.get("self_worker_id") or ""),
        )
    elif args.cmd == "blender-shard":
        try:
            fs = int(args.frame_start or env("frame_start") or 1)
            fe = int(args.frame_end or env("frame_end") or fs)
        except (TypeError, ValueError):
            print(json.dumps({"success": False, "error": "bad frame range"}), flush=True)
            return 1
        result = plat.submit_blender_shards(
            blend_file=str(args.blend_file or env("blend_file") or "").strip(),
            output_dir=str(args.output_dir or env("output_dir") or "").strip(),
            frame_start=fs,
            frame_end=fe,
            engine=str(args.engine or env("engine") or "").strip(),
            blender_bin=str(args.blender_bin or env("blender_bin") or "").strip(),
            submitted_by=str(args.submitted_by or env("submitted_by") or "platform"),
            max_shards=int(args.max_shards or env("max_shards") or 8),
            dry_run=bool(args.dry_run or env("dry_run")),
            prefer_remote=not bool(args.prefer_local or env("prefer_local")),
            distribution=str(
                args.distribution or env("distribution") or "work_steal"
            ).strip()
            or "work_steal",
            chunk_size=int(args.chunk_size or env("chunk_size") or 0),
            batch_id=str(args.batch_id or env("batch_id") or "").strip(),
        )
    elif args.cmd == "batch-status":
        bid = str(args.batch_id or env("batch_id") or "").strip()
        if not bid:
            print(json.dumps({"success": False, "error": "batch_id required"}), flush=True)
            return 1
        result = plat.batch_status(bid)
    elif args.cmd == "gap-fill":
        bid = str(args.batch_id or env("batch_id") or "").strip()
        if not bid:
            print(json.dumps({"success": False, "error": "batch_id required"}), flush=True)
            return 1
        result = plat.gap_fill_batch(
            bid,
            chunk_size=int(args.chunk_size or env("chunk_size") or 1),
            submitted_by=str(args.submitted_by or env("submitted_by") or "gap-fill"),
            dry_run=bool(args.dry_run or env("dry_run")),
        )
    elif args.cmd == "batch-watch":
        bid = str(args.batch_id or env("batch_id") or "").strip()
        if not bid:
            print(json.dumps({"success": False, "error": "batch_id required"}), flush=True)
            return 1
        watch_id = str(args.watch_id or env("watch_id") or env("id") or bid).strip()
        label_prefix = str(
            args.label_prefix or env("label_prefix") or "Mesh bake"
        ).strip() or "Mesh bake"
        do_loop = bool(args.loop or env("loop"))
        try:
            interval = float(args.interval or env("interval") or 60)
        except (TypeError, ValueError):
            interval = 60.0
        try:
            max_hours = float(args.max_hours or env("max_hours") or 12)
        except (TypeError, ValueError):
            max_hours = 12.0
        if not do_loop:
            result = plat.write_batch_watch(
                bid, watch_id=watch_id, label_prefix=label_prefix
            )
        else:
            import time

            deadline = time.time() + max(60.0, max_hours * 3600.0)
            result = plat.write_batch_watch(
                bid, watch_id=watch_id, label_prefix=label_prefix
            )
            while (
                result.get("success")
                and str(result.get("state") or "") == "running"
                and time.time() < deadline
            ):
                time.sleep(max(5.0, interval))
                result = plat.write_batch_watch(
                    bid, watch_id=watch_id, label_prefix=label_prefix
                )
            if (
                result.get("success")
                and str(result.get("state") or "") == "running"
                and time.time() >= deadline
            ):
                from api.job_watch import write_status

                write_status(
                    watch_id,
                    state="failed",
                    percent=int(result.get("percent") or 0),
                    label=(str(result.get("label") or "") + " — watch timed out")[:200],
                    bars=result.get("bars"),
                )
                result = {**result, "state": "failed", "error": "watch_timeout"}
    elif args.cmd == "self-update":
        target = str(args.target or env("target") or env("target_worker_id") or "").strip()
        if not target:
            print(json.dumps({"success": False, "error": "target worker_id required"}), flush=True)
            return 1
        result = plat.submit_self_update(
            target_worker_id=target,
            repo=str(args.repo or env("repo") or "").strip(),
            restart_electron=not bool(args.no_electron or env("no_electron")),
            restart_daemon=not bool(args.no_daemon or env("no_daemon")),
            submitted_by=str(args.submitted_by or env("submitted_by") or "platform"),
        )
    elif args.cmd == "probe":
        target = str(args.target or env("target") or env("target_worker_id") or env("worker_id") or "").strip()
        if not target:
            print(json.dumps({"success": False, "error": "target worker_id required"}), flush=True)
            return 1
        result = plat.probe_worker(
            target,
            timeout_seconds=float(args.timeout or env("timeout") or env("timeout_seconds") or 20),
        )
    else:
        parser.error(f"unknown command {args.cmd}")
        return 2

    print(plat.dumps(result), flush=True)
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
