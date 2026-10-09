"""Agent verbs for the unified Jobs surface (no model execution)."""
import argparse
import json
import sys

from api.jobs import ledger
from api.jobs.service import JobNotCancellableError, JobNotFoundError, JobNotRerunnableError, cancel_job, job_stats, rerun_mesh_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="verb", required=True)

    p_list = sub.add_parser("list", help="Recent unified jobs")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--kind", choices=["run", "mesh"])
    p_list.add_argument("--status")
    p_list.add_argument("--active-only", action="store_true")

    sub.add_parser("inflight", help="Current in-flight jobs")
    sub.add_parser("stats", help="Jobs telemetry").add_argument("--hours", type=int, default=24)

    p_cancel = sub.add_parser("cancel", help="Cancel a job: cancel <run|mesh> <id>")
    p_cancel.add_argument("kind", choices=["run", "mesh"])
    p_cancel.add_argument("job_id")

    p_rerun = sub.add_parser("rerun", help="Re-submit a terminal mesh job")
    p_rerun.add_argument("job_id")

    p_view = sub.add_parser("view", help="View one job: view <run|mesh> <id>")
    p_view.add_argument("kind", choices=["run", "mesh"])
    p_view.add_argument("job_id")

    args = parser.parse_args()
    try:
        if args.verb == "list":
            out = {"jobs": ledger.unified_jobs(
                limit=args.limit, kind=args.kind, status=args.status, active_only=args.active_only)}
        elif args.verb == "inflight":
            out = {"executing_jobs": ledger.inflight()}
        elif args.verb == "stats":
            out = job_stats(hours=args.hours)
        elif args.verb == "cancel":
            out = cancel_job(args.kind, args.job_id)
        elif args.verb == "rerun":
            out = rerun_mesh_job(args.job_id)
        else:
            out = {"job": ledger.get_job(args.kind, args.job_id)}
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0
    except (JobNotFoundError, JobNotCancellableError, JobNotRerunnableError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())