"""Durable supervised-task persistence (survives Flask restart)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime_paths import runtime_state_path

from api.agent_router.supervised.types import SupervisedTask

_lock = threading.RLock()


def _repo_root() -> Path:
    # .../src/api/agent_router/supervised/store.py -> Cuttle
    return Path(__file__).resolve().parents[4]


def store_dir() -> Path:
    d = runtime_state_path("supervised_tasks", project_root=_repo_root(),
                           legacy="workspace/supervised_tasks")
    return d


def _task_path(task_id: str) -> Path:
    safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
    return store_dir() / f"{safe}.json"


def _index_path() -> Path:
    return store_dir() / "_index.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_index() -> Dict[str, Any]:
    path = _index_path()
    if not path.is_file():
        return {"by_session": {}, "active": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"by_session": {}, "active": {}}
    except (OSError, json.JSONDecodeError):
        return {"by_session": {}, "active": {}}


def _write_index(data: Dict[str, Any]) -> None:
    path = _index_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def save_task(task: SupervisedTask) -> SupervisedTask:
    with _lock:
        task.updated_at = _now()
        if not task.created_at:
            task.created_at = task.updated_at
        path = _task_path(task.task_id)
        tmp = path.with_suffix(".tmp")
        payload = task.to_dict()
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        idx = _load_index()
        by_session = dict(idx.get("by_session") or {})
        sid = str(task.parent_session_id or "")
        lst = list(by_session.get(sid) or [])
        if task.task_id not in lst:
            lst.append(task.task_id)
        by_session[sid] = lst
        active = dict(idx.get("active") or {})
        terminal = {
            "approved",
            "escalated",
            "failed",
            "cancelled",
            "budget_exhausted",
        }
        if task.phase in terminal:
            active.pop(sid, None)
        else:
            active[sid] = task.task_id
        idx["by_session"] = by_session
        idx["active"] = active
        _write_index(idx)
        return task


def load_task(task_id: str) -> Optional[SupervisedTask]:
    with _lock:
        path = _task_path(task_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return SupervisedTask.from_dict(raw)


def get_active_task(session_id: Any) -> Optional[SupervisedTask]:
    if session_id is None:
        return None
    with _lock:
        idx = _load_index()
        tid = (idx.get("active") or {}).get(str(session_id))
        if not tid:
            return None
        return load_task(str(tid))


def list_session_tasks(session_id: Any) -> List[SupervisedTask]:
    if session_id is None:
        return []
    with _lock:
        idx = _load_index()
        ids = list((idx.get("by_session") or {}).get(str(session_id)) or [])
        out: List[SupervisedTask] = []
        for tid in ids:
            t = load_task(str(tid))
            if t:
                out.append(t)
        return out


def append_event(task: SupervisedTask, event: str, **fields: Any) -> SupervisedTask:
    entry = {"event": event, "at": _now(), **fields}
    task.events.append(entry)
    return save_task(task)


def persist_raw_worker_output(task_id: str, run_id: str, raw_output: str) -> Dict[str, Any]:
    """
    Write full raw worker stdout to a durable file independent of structured parse.

    Returns metadata with path and length measurements. Never truncates the file.
    """
    safe_tid = "".join(c for c in task_id if c.isalnum() or c in "-_")
    safe_rid = "".join(c for c in run_id if c.isalnum() or c in "-_")
    art_dir = store_dir() / safe_tid / "runs"
    art_dir.mkdir(parents=True, exist_ok=True)
    path = art_dir / f"{safe_rid}.raw.txt"
    text = raw_output or ""
    data = text.encode("utf-8", errors="replace")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return {
        "path": str(path),
        "chars": len(text),
        "bytes": len(data),
        "truncated": False,
        "component": "supervised.store.persist_raw_worker_output",
    }


def record_control_event(
    task: SupervisedTask,
    *,
    control_id: str,
    kind: str,
    result: Dict[str, Any],
    user_message_id: Any = None,
    assistant_message_id: Any = None,
) -> SupervisedTask:
    """Persist an idempotency/control-event record on the task.

    Stores a bounded response snapshot so transport retries can reconcile to the
    original exchange without appending a second chat pair.
    """
    events = list(task.control_events or [])
    # Replace prior row for the same control_id (retry after partial persist).
    events = [
        ev
        for ev in events
        if not (isinstance(ev, dict) and ev.get("control_id") == control_id)
    ]
    snap = dict(result) if isinstance(result, dict) else {}
    resp = str(snap.get("response") or "")
    if len(resp) > 12000:
        snap["response"] = resp[:12000] + "…"
    events.append(
        {
            "control_id": control_id,
            "kind": kind,
            "at": _now(),
            "result_type": snap.get("type"),
            "followup_id": snap.get("followup_id"),
            "response": snap.get("response") or "",
            "result": {
                k: snap.get(k)
                for k in (
                    "type",
                    "task_id",
                    "followup_id",
                    "followup_state",
                    "pending_count",
                    "queue_position",
                    "worker_state",
                    "idempotent",
                    "control_request_id",
                    "delivery_handled",
                    "skip_history_persist",
                )
                if k in snap
            },
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
            "history_persisted": bool(
                user_message_id is not None or assistant_message_id is not None
            ),
        }
    )
    task.control_events = events[-50:]
    return save_task(task)


def find_control_event(task: SupervisedTask, control_id: str) -> Optional[Dict[str, Any]]:
    if not control_id:
        return None
    for ev in reversed(list(task.control_events or [])):
        if isinstance(ev, dict) and ev.get("control_id") == control_id:
            return ev
    return None


def control_event_response_body(prior: Dict[str, Any], *, control_id: str) -> Dict[str, Any]:
    """Rebuild an idempotent control response from a stored control event."""
    result = dict(prior.get("result") or {}) if isinstance(prior.get("result"), dict) else {}
    body = {
        "success": True,
        "response": str(prior.get("response") or result.get("response") or (
            f"Control `{control_id}` already applied (idempotent)."
        )),
        "type": result.get("type") or prior.get("result_type") or "supervised_control",
        "idempotent": True,
        "reconcile_only": True,
        "control_request_id": control_id,
        "skip_history_persist": True,
        "user_message_id": prior.get("user_message_id"),
        "assistant_message_id": prior.get("assistant_message_id"),
    }
    for k in (
        "task_id",
        "followup_id",
        "followup_state",
        "pending_count",
        "queue_position",
        "worker_state",
        "delivery_handled",
    ):
        if k in result and result[k] is not None:
            body[k] = result[k]
    return body

