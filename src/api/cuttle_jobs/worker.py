"""Daemon poll loop for remote Cuttle Jobs (Gitea @cuttle)."""

from __future__ import annotations

import logging
import time
from typing import Any

from api.cuttle_jobs.client import CuttleJobsClient, jobs_enabled, load_jobs_config
from api.cuttle_jobs.executor import process_claimed_job

log = logging.getLogger("cuttle.jobs.worker")


def run_cuttle_jobs_loop(*, should_continue: Any) -> None:
    """
    Poll the Ubuntu Cuttle Jobs API while the daemon is running.

    ``should_continue`` is a zero-arg callable (e.g. ``lambda: daemon_running``).
    """
    # Let Flask / tray settle
    for _ in range(20):
        if not should_continue():
            return
        time.sleep(1)

    if not jobs_enabled():
        log.info(
            "Cuttle Jobs worker disabled "
            "(set CUTTLE_JOBS_ENABLED=1 plus BASE_URL and WORKER_TOKEN in src/.env)"
        )
        print(
            "[DAEMON] Cuttle Jobs worker disabled "
            "(CUTTLE_JOBS_ENABLED not set)"
        )
        return

    cfg = load_jobs_config()
    try:
        poll = max(5, min(int(cfg.get("poll_seconds") or 15), 120))
    except ValueError:
        poll = 15

    client = CuttleJobsClient()
    print(
        f"[DAEMON] Cuttle Jobs worker polling {cfg['base_url']} "
        f"(poll={poll}s, worker_id={client.worker_id})"
    )
    log.info("worker polling base=%s poll=%s", cfg["base_url"], poll)

    offline = False
    fail_streak = 0
    sleep_s = poll

    while should_continue():
        try:
            jobs = client.claim(limit=1)
            if offline:
                print(f"[DAEMON] Cuttle Jobs reachable again ({cfg['base_url']})")
                log.info("jobs reachable again base=%s", cfg["base_url"])
            offline = False
            fail_streak = 0
            sleep_s = poll
            if jobs:
                job = jobs[0]
                log.info("job claimed id=%s cmd=%s", job.get("id"), job.get("command"))
                print(
                    f"[DAEMON] Cuttle job claimed id={job.get('id')} "
                    f"cmd={job.get('command')} issue=#{job.get('issue_number')}"
                )
                process_claimed_job(job, client=client)
        except Exception as e:
            # Transient network (LAN/host down at boot) — keep polling with backoff.
            # Log every failure; only print to the daemon console on state change
            # so WinError 10065 does not spam every 15s.
            fail_streak += 1
            log.warning("jobs poll error: %s", e)
            if not offline:
                offline = True
                print(
                    f"[DAEMON] Cuttle Jobs unreachable ({cfg['base_url']}): {e}"
                )
                print(
                    "[DAEMON] Retrying with backoff; further poll errors "
                    "suppressed until the host is reachable again"
                )
            sleep_s = min(poll * (2 ** min(fail_streak - 1, 4)), 300)

        for _ in range(int(sleep_s)):
            if not should_continue():
                return
            time.sleep(1)
