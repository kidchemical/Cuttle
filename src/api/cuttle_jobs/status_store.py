"""Shared on-disk status for remote Gitea Cuttle jobs (daemon ↔ Flask Jobs UI)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_lock = threading.Lock()

_SRC_ROOT = Path(__file__).resolve().parents[2]  # …/src
_PROJECT_ROOT = _SRC_ROOT.parent  # …/Cuttle (daemon tray queue lives here)
_STATUS_PATH = _SRC_ROOT / "output" / "cuttle_jobs_running.json"
_HISTORY_PATH = _SRC_ROOT / "output" / "cuttle_jobs_history.jsonl"
_NOTIFY_PATH = _PROJECT_ROOT / "cuttle_notify_queue.jsonl"
_UI_TOAST_PATH = _PROJECT_ROOT / "cuttle_ui_toasts.jsonl"

_HISTORY_MAX = 200


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read() -> Dict[str, Any]:
    if not _STATUS_PATH.is_file():
        return {"jobs": []}
    try:
        data = json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"jobs": []}


def _write(data: Dict[str, Any]) -> None:
    _STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STATUS_PATH)


def mark_running(job: Dict[str, Any]) -> str:
    """Record a claimed job as running. Returns status id."""
    job_id = int(job["id"])
    sid = f"cuttle-job-{job_id}"
    entry = {
        "query_id": sid,
        "pipeline_name": (
            f"Gitea @{job.get('command')}: "
            f"{job.get('repository')}#{job.get('issue_number')}"
        ),
        "start_time": _utc_iso(),
        "source": "cuttle_jobs",
        "job_id": job_id,
        "command": str(job.get("command") or ""),
        "repository": str(job.get("repository") or ""),
        "issue_number": int(job.get("issue_number") or 0),
        "issue_title": str(job.get("issue_title") or ""),
        "gitea_url": _issue_url(job),
        "triggering_user": str(job.get("triggering_user") or ""),
    }
    with _lock:
        data = _read()
        jobs = [j for j in data.get("jobs", []) if j.get("query_id") != sid]
        jobs.append(entry)
        _write({"jobs": jobs})
    return sid


def mark_done(job_id: int) -> None:
    sid = f"cuttle-job-{int(job_id)}"
    with _lock:
        data = _read()
        jobs = [j for j in data.get("jobs", []) if j.get("query_id") != sid]
        _write({"jobs": jobs})


def list_running() -> List[dict]:
    with _lock:
        return list(_read().get("jobs") or [])


def get_running(job_id: int) -> Optional[dict]:
    sid = f"cuttle-job-{int(job_id)}"
    with _lock:
        for j in _read().get("jobs") or []:
            if j.get("query_id") == sid or j.get("job_id") == int(job_id):
                return dict(j)
    return None


def append_history(entry: Dict[str, Any]) -> None:
    """Append a completed/failed job to the local ring buffer (newest last on disk)."""
    if not isinstance(entry, dict) or not entry.get("job_id"):
        return
    row = {
        "job_id": int(entry["job_id"]),
        "command": str(entry.get("command") or ""),
        "repository": str(entry.get("repository") or ""),
        "issue_number": int(entry.get("issue_number") or 0),
        "issue_title": str(entry.get("issue_title") or ""),
        "gitea_url": str(entry.get("gitea_url") or ""),
        "triggering_user": str(entry.get("triggering_user") or ""),
        "start_time": str(entry.get("start_time") or ""),
        "end_time": str(entry.get("end_time") or _utc_iso()),
        "duration_sec": entry.get("duration_sec"),
        "status": str(entry.get("status") or "completed"),
        "error": (str(entry.get("error") or "")[:1000] or None),
        "result": entry.get("result") if isinstance(entry.get("result"), dict) else None,
        "source": "cuttle_jobs",
    }
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(_HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(line)
            # Trim to last N lines if oversized
            if _HISTORY_PATH.stat().st_size > 512_000:
                lines = _HISTORY_PATH.read_text(encoding="utf-8").splitlines()
                keep = lines[-_HISTORY_MAX:]
                _HISTORY_PATH.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        except OSError:
            pass


def list_history(*, limit: int = 50) -> List[dict]:
    """Return local history newest-first."""
    limit = max(1, min(int(limit), 200))
    with _lock:
        if not _HISTORY_PATH.is_file():
            return []
        try:
            raw = _HISTORY_PATH.read_text(encoding="utf-8")
        except OSError:
            return []
    out: List[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and entry.get("job_id"):
                out.append(entry)
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out[:limit]


def _issue_url(job: Dict[str, Any]) -> str:
    repo = str(job.get("repository") or "")
    num = job.get("issue_number")
    if not repo or not num:
        return ""
    try:
        from api.gitea_client import load_gitea_config

        base = load_gitea_config().get("base_url") or "http://127.0.0.1:3000"
        return f"{base.rstrip('/')}/{repo}/issues/{int(num)}"
    except Exception:
        return f"http://127.0.0.1:3000/{repo}/issues/{int(num)}"


def issue_url_for(repository: str, issue_number: Any) -> str:
    return _issue_url({"repository": repository, "issue_number": issue_number})


def notify_tray(message: str, *, variant: str = "info") -> None:
    """Notify Electron/web UI (toast+chirp poll) and daemon pystray queue."""
    title = "Cuttle"
    if variant == "error":
        title = "Cuttle — Error"
    elif variant == "success":
        title = "Cuttle — Success"
    msg = (message or "")[:500]
    tray_entry = json.dumps({"title": title, "message": msg}, ensure_ascii=False) + "\n"
    ui_entry = (
        json.dumps(
            {"title": title, "message": msg, "variant": variant or "info", "ts": _utc_iso()},
            ensure_ascii=False,
        )
        + "\n"
    )
    try:
        with _lock:
            with open(_NOTIFY_PATH, "a", encoding="utf-8") as f:
                f.write(tray_entry)
            with open(_UI_TOAST_PATH, "a", encoding="utf-8") as f:
                f.write(ui_entry)
    except OSError:
        pass


def pull_ui_toasts() -> List[dict]:
    """Drain pending UI toasts (Flask → Electron/app_shell)."""
    with _lock:
        if not _UI_TOAST_PATH.is_file() or _UI_TOAST_PATH.stat().st_size == 0:
            return []
        try:
            raw = _UI_TOAST_PATH.read_text(encoding="utf-8")
            _UI_TOAST_PATH.write_text("", encoding="utf-8")
        except OSError:
            return []
    out: List[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and entry.get("message"):
                out.append(entry)
        except json.JSONDecodeError:
            continue
    return out
