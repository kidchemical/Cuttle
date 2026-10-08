"""Device worker poll loop — local (in-process store) or remote coordinator HTTP."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from api.device_workers.capabilities import (
    collect_capabilities,
    process_stale,
    reload_device_workers_modules,
)
from api.device_workers.config import (
    coordinator_base_url,
    device_workers_enabled,
    interactive_priority,
    lease_seconds,
    poll_seconds,
    worker_id,
)

log = logging.getLogger("cuttle.device_workers")


def _ads() -> dict:
    return collect_capabilities(interactive_priority=interactive_priority())


def _meta_from_ads(ads: dict) -> dict:
    meta = {}
    ver = str(ads.get("cuttle_version") or "").strip()
    rev = str(ads.get("cuttle_git_rev") or "").strip()
    boot = str(ads.get("boot_git_rev") or "").strip()
    if ver:
        meta["cuttle_version"] = ver
    if rev:
        meta["cuttle_git_rev"] = rev
    if boot:
        meta["boot_git_rev"] = boot
    if ads.get("boot_at"):
        try:
            meta["boot_at"] = float(ads["boot_at"])
        except (TypeError, ValueError):
            pass
    if ads.get("stale_process"):
        meta["stale_process"] = True
    return meta


def _maybe_reload_stale_code() -> None:
    """If disk checkout moved under this process, hot-reload execution modules."""
    try:
        if not process_stale():
            return
    except Exception:
        return
    log.warning(
        "device worker code on disk is newer than boot — reloading modules"
    )
    try:
        result = reload_device_workers_modules()
        if result.get("ok"):
            log.info(
                "device worker modules reloaded boot=%s reloaded=%s",
                result.get("boot_git_rev"),
                result.get("reloaded"),
            )
        else:
            log.warning("device worker reload incomplete: %s", result)
    except Exception:
        log.exception("device worker reload failed")


def _local_register(store: Any, ads: dict) -> None:
    store.upsert_worker(
        worker_id=worker_id(),
        hostname=ads.get("hostname") or "",
        os_name=ads.get("os") or "",
        capabilities=ads.get("capabilities") or {},
        storage=ads.get("storage") or {},
        load=ads.get("load") or {},
        interactive_priority=ads.get("interactive_priority") or "low",
        ac_power=ads.get("ac_power"),
        meta=_meta_from_ads(ads),
    )


def _run_one(
    job: dict,
    *,
    complete,
    fail,
    heartbeat,
    lease_seconds: int = 600,
    auth_token: Optional[str] = None,
) -> None:
    """Execute one job while refreshing the claim lease in the background.

    Long jobs (Cycles, Unity/compile shell) exceed the default lease (10m).
    Heartbeats carry optional progress so soft idle timeouts and Jobs UI can
    see durable unit advancement.
    """
    # Bind late so hot-reload after git pull picks up new executor code.
    from api.device_workers import executor as executor_mod

    jid = job.get("id")
    stop = threading.Event()
    progress_lock = threading.Lock()
    latest_progress: dict = {}

    def _on_progress(snap: Any) -> None:
        try:
            payload = snap.as_dict() if hasattr(snap, "as_dict") else dict(snap or {})
        except Exception:
            return
        with progress_lock:
            latest_progress.clear()
            latest_progress.update(payload)
        try:
            heartbeat(jid, progress=payload)
        except Exception:
            log.debug("job %s progress heartbeat failed", jid, exc_info=True)

    def _pulse() -> None:
        # Refresh before the lease is halfway gone.
        interval = max(30.0, min(120.0, float(lease_seconds) * 0.4))
        while not stop.wait(interval):
            try:
                with progress_lock:
                    prog = dict(latest_progress) if latest_progress else None
                heartbeat(jid, progress=prog)
            except Exception:
                log.debug("job %s heartbeat failed", jid, exc_info=True)

    # Inject progress callback for executors that support it (not serialized).
    params = dict(job.get("params") or {})
    params["_on_progress"] = _on_progress
    params["_job_id"] = str(jid or "")
    job_exec = dict(job)
    job_exec["params"] = params

    try:
        heartbeat(jid)
        pulse = threading.Thread(
            target=_pulse, name=f"job-lease-{jid}", daemon=True
        )
        pulse.start()
        try:
            result = executor_mod.execute_job(job_exec, auth_token=auth_token)
        finally:
            stop.set()
            pulse.join(timeout=5)
        complete(jid, result)
        log.info("job %s succeeded type=%s", jid, job.get("type"))
    except executor_mod.PartialRetryError as e:
        stop.set()
        fail(
            jid,
            error=str(e),
            retry=True,
            params_update=e.params_update,
            partial_result=e.partial_result,
        )
        log.warning("job %s partial retry: %s", jid, e)
    except executor_mod.JobExecError as e:
        stop.set()
        fail(jid, error=str(e), retry=False)
        log.warning("job %s permanent fail: %s", jid, e)
    except Exception as e:
        stop.set()
        fail(jid, error=str(e), retry=True)
        log.warning("job %s retryable fail: %s", jid, e)


def run_local_worker_loop(*, should_continue: Callable[[], bool]) -> None:
    """Host-side worker: registers into the local SQLite store and claims jobs."""
    for _ in range(10):
        if not should_continue():
            return
        time.sleep(0.5)

    if not device_workers_enabled():
        print("[DAEMON] Device workers disabled")
        log.info("device workers disabled")
        return

    from api.device_workers.store import get_store

    store = get_store()
    wid = worker_id()
    local_token = store.ensure_local_worker_token(wid)
    poll = poll_seconds()
    lease = lease_seconds()
    print(f"[DAEMON] Device worker local loop worker_id={wid} poll={poll}s")
    log.info("local device worker started id=%s", wid)

    while should_continue():
        try:
            _maybe_reload_stale_code()
            ads = _ads()
            _local_register(store, ads)
            caps = dict(ads.get("capabilities") or {})
            # Merge storage for requirement checks
            storage = ads.get("storage") or {}
            if isinstance(storage, dict):
                caps = {**caps, "storage": storage}
            jobs = store.claim_jobs(
                worker_id=wid,
                capabilities=caps,
                limit=1,
                lease_seconds=lease,
            )
            for job in jobs:
                _run_one(
                    job,
                    complete=lambda jid, result: store.complete_job(jid, wid, result=result),
                    fail=lambda jid, error, retry, params_update=None, partial_result=None: store.fail_job(
                        jid,
                        wid,
                        error=error,
                        retry=retry,
                        params_update=params_update,
                        partial_result=partial_result,
                    ),
                    heartbeat=lambda jid, progress=None: store.heartbeat_job(
                        jid, wid, lease_seconds=lease, progress=progress
                    ),
                    lease_seconds=lease,
                    auth_token=local_token,
                )
        except Exception as e:
            log.warning("local worker tick error: %s", e)
            print(f"[DAEMON] Device worker error: {e}")

        for _ in range(poll):
            if not should_continue():
                return
            time.sleep(1)


def run_remote_worker_loop(
    *,
    should_continue: Callable[[], bool],
    base_url: Optional[str] = None,
) -> None:
    """Sidecar worker: HTTP register/claim against a coordinator Flask."""
    from api.device_workers.client import DeviceWorkerClient

    url = (base_url or coordinator_base_url()).rstrip("/")
    if not url:
        raise RuntimeError("CUTTLE_DEVICE_WORKERS_COORDINATOR_URL / coordinator_url required")

    for _ in range(5):
        if not should_continue():
            return
        time.sleep(0.5)

    client = DeviceWorkerClient(base_url=url)
    remote_token = client.token
    poll = poll_seconds()
    print(f"[WORKER] Device worker → {url} id={client.worker_id} poll={poll}s")
    log.info("remote device worker started url=%s id=%s", url, client.worker_id)

    while should_continue():
        try:
            _maybe_reload_stale_code()
            ads = _ads()
            payload = {
                "worker_id": client.worker_id,
                "hostname": ads.get("hostname"),
                "os": ads.get("os"),
                "capabilities": ads.get("capabilities"),
                "storage": ads.get("storage"),
                "load": ads.get("load"),
                "interactive_priority": ads.get("interactive_priority"),
                "ac_power": ads.get("ac_power"),
                "meta": _meta_from_ads(ads),
            }
            client.register(payload)
            caps = dict(ads.get("capabilities") or {})
            storage = ads.get("storage") or {}
            if isinstance(storage, dict):
                caps = {**caps, "storage": storage}
            jobs = client.claim(limit=1, capabilities=caps)
            lease = lease_seconds()
            for job in jobs:
                _run_one(
                    job,
                    complete=lambda jid, result: client.complete(jid, result),
                    fail=lambda jid, error, retry, params_update=None, partial_result=None: client.fail(
                        jid,
                        error=error,
                        retry=retry,
                        params_update=params_update,
                        partial_result=partial_result,
                    ),
                    heartbeat=lambda jid, progress=None: client.job_heartbeat(
                        jid, progress=progress
                    ),
                    lease_seconds=lease,
                    auth_token=remote_token,
                )
        except Exception as e:
            log.warning("remote worker tick error: %s", e)
            print(f"[WORKER] tick error: {e}")

        for _ in range(poll):
            if not should_continue():
                return
            time.sleep(1)
