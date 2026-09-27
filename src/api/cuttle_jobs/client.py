"""HTTP client for the Cuttle Jobs queue API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


def _read_env_file() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if not env_path.is_file():
            return out
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_jobs_config() -> Dict[str, str]:
    file_env = _read_env_file()

    def _get(key: str, default: str = "") -> str:
        return (os.getenv(key) or file_env.get(key) or default).strip()

    return {
        "base_url": (_get("CUTTLE_JOBS_BASE_URL") or "").rstrip("/"),
        "token": _get("CUTTLE_JOBS_WORKER_TOKEN"),
        "worker_id": _get("CUTTLE_JOBS_WORKER_ID") or "cuttle-pc",
        "poll_seconds": _get("CUTTLE_JOBS_POLL_SECONDS") or "15",
        "enabled": _get("CUTTLE_JOBS_ENABLED") or "0",
        "agent": _get("CUTTLE_JOBS_AGENT") or "cursor",
        "timeout": _get("CUTTLE_JOBS_AGENT_TIMEOUT") or "3600",
    }


def jobs_enabled() -> bool:
    cfg = load_jobs_config()
    if cfg["enabled"].lower() in ("1", "true", "yes", "on"):
        return bool(cfg["base_url"] and cfg["token"])
    return False


class CuttleJobsClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        cfg = load_jobs_config()
        self.base_url = (base_url or cfg["base_url"]).rstrip("/")
        self.token = token or cfg["token"]
        self.worker_id = worker_id or cfg["worker_id"]

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        if requests is None:
            raise RuntimeError("requests package not installed")
        if not self.base_url or not self.token:
            raise RuntimeError("CUTTLE_JOBS_BASE_URL / CUTTLE_JOBS_WORKER_TOKEN not configured")
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "cuttle-jobs-worker/0.1",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        r = requests.request(
            method,
            url,
            headers=headers,
            json=json_body,
            timeout=timeout,
        )
        try:
            data = r.json() if r.content else {}
        except Exception:
            data = {"raw": (r.text or "")[:400]}
        if not r.ok:
            err = data.get("error") if isinstance(data, dict) else r.text
            raise RuntimeError(f"jobs API {method} {path} → HTTP {r.status_code}: {err}")
        return data if isinstance(data, dict) else {"data": data}

    def claim(self, *, limit: int = 1) -> List[Dict[str, Any]]:
        data = self._request(
            "POST",
            "/v1/internal/cuttle/jobs/claim",
            json_body={"limit": limit, "worker_id": self.worker_id},
        )
        jobs = data.get("jobs") or []
        return list(jobs) if isinstance(jobs, list) else []

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        repository: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        from urllib.parse import urlencode

        params: Dict[str, Any] = {
            "limit": max(1, min(int(limit), 200)),
            "offset": max(0, int(offset)),
        }
        if status:
            params["status"] = status
        if repository:
            params["repository"] = repository
        qs = urlencode(params)
        data = self._request("GET", f"/v1/internal/cuttle/jobs?{qs}")
        jobs = data.get("jobs") or []
        return list(jobs) if isinstance(jobs, list) else []

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        data = self._request("GET", f"/v1/internal/cuttle/jobs/{int(job_id)}")
        job = data.get("job")
        return job if isinstance(job, dict) else None

    def heartbeat(self, job_id: int) -> None:
        self._request(
            "POST",
            f"/v1/internal/cuttle/jobs/{int(job_id)}/heartbeat",
            json_body={"worker_id": self.worker_id},
        )

    def complete(self, job_id: int, result: Optional[Dict[str, Any]] = None) -> None:
        self._request(
            "POST",
            f"/v1/internal/cuttle/jobs/{int(job_id)}/complete",
            json_body={"worker_id": self.worker_id, "result": result or {}},
        )

    def fail(
        self,
        job_id: int,
        *,
        error: str,
        retry: bool = True,
    ) -> None:
        self._request(
            "POST",
            f"/v1/internal/cuttle/jobs/{int(job_id)}/fail",
            json_body={
                "worker_id": self.worker_id,
                "error": (error or "")[:1000],
                "retry": bool(retry),
            },
        )
