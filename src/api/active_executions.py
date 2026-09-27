"""
Tracks pipeline executions that are actively running (not merely listening).
Used by the Jobs panel to show only in-progress executions.
"""

import threading
import time
from typing import Dict, List, Optional
from datetime import datetime

# {query_id: {pipeline_name, start_time, started_at, thread}}
_active: Dict[str, dict] = {}
_lock = threading.Lock()

# Backstop for entries whose owning thread cannot be identified. Longer than any
# agent CLI timeout (Codex caps at 7200s) so a slow-but-real run is never
# mistaken for a leak.
_STALE_TTL = 4 * 3600.0


def register_execution(query_id: str, pipeline_name: str) -> None:
    """Register that a pipeline execution has started."""
    if not query_id or not pipeline_name:
        return
    with _lock:
        _active[query_id] = {
            'pipeline_name': pipeline_name,
            'start_time': datetime.now().isoformat(),
            'started_at': time.time(),
            # Workers unregister in a `finally`, but a thread that dies without
            # unwinding used to pin its job forever, which permanently rejects
            # `/restart graceful`. The owning thread is the liveness signal.
            'thread': threading.current_thread(),
        }


def unregister_execution(query_id: str) -> None:
    """Unregister when a pipeline execution has finished."""
    if not query_id:
        return
    with _lock:
        _active.pop(query_id, None)


def _is_stale(info: dict, now: float) -> bool:
    thread = info.get('thread')
    if thread is not None and not thread.is_alive():
        return True
    started_at = info.get('started_at')
    return bool(started_at) and (now - started_at) > _STALE_TTL


def reconcile_stale(now: Optional[float] = None) -> List[str]:
    """Drop jobs whose worker thread is gone. Returns the query ids cleared."""
    now = time.time() if now is None else now
    cleared: List[str] = []
    with _lock:
        for qid, info in list(_active.items()):
            if _is_stale(info, now):
                _active.pop(qid, None)
                cleared.append(qid)
    return cleared


def is_query_executing(query_id: str) -> bool:
    """True if this query_id is currently registered as an in-flight pipeline run."""
    if not query_id:
        return False
    reconcile_stale()
    with _lock:
        return query_id in _active


def get_executing_jobs() -> List[dict]:
    """Return list of currently executing jobs for the Jobs panel."""
    reconcile_stale()
    with _lock:
        out = [
            {
                'pipeline_name': info['pipeline_name'],
                'query_id': qid,
                'start_time': info.get('start_time'),
                'source': 'pipeline',
            }
            for qid, info in _active.items()
        ]
    # Remote Gitea @cuttle jobs run in the daemon process — merge file registry.
    try:
        from api.cuttle_jobs.status_store import list_running

        for j in list_running():
            out.append({
                'pipeline_name': j.get('pipeline_name') or f"Gitea job {j.get('job_id')}",
                'query_id': j.get('query_id') or '',
                'start_time': j.get('start_time'),
                'source': 'cuttle_jobs',
                'command': j.get('command'),
                'repository': j.get('repository'),
                'issue_number': j.get('issue_number'),
                'issue_title': j.get('issue_title') or '',
                'gitea_url': j.get('gitea_url') or '',
                'job_id': j.get('job_id'),
                'triggering_user': j.get('triggering_user') or '',
            })
    except Exception:
        pass
    return out
