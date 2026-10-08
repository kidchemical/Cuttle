"""HTTP client for a remote Cuttle device-worker coordinator."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from api.device_workers.config import coordinator_base_url, worker_id, worker_token
from api.tls_cert import urlopen as tls_urlopen


class DeviceWorkerClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        worker_id_value: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or coordinator_base_url()).rstrip("/")
        self.token = token if token is not None else worker_token()
        self.worker_id = worker_id_value or worker_id()

    def _request_urllib(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "cuttle-device-worker/0.2",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            # Remote HTTPS requires the desktop app's key pin (api.tls_cert).
            with tls_urlopen(req, timeout=timeout) as resp:
                raw = resp.read() or b"{}"
                parsed = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                return parsed if isinstance(parsed, dict) else {"data": parsed}
        except urllib.error.HTTPError as e:
            err_body: Any = {}
            try:
                err_body = json.loads(e.read().decode("utf-8", errors="replace") or "{}")
            except Exception:
                err_body = {"raw": str(e.reason)}
            err = err_body.get("error") if isinstance(err_body, dict) else err_body
            raise RuntimeError(
                f"workers API {method} {path} → HTTP {e.code}: {err}"
            ) from e

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("coordinator URL not configured")
        return self._request_urllib(method, path, json_body=json_body, timeout=timeout)

    def register(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(payload)
        body.setdefault("worker_id", self.worker_id)
        return self._request("POST", "/api/workers/register", json_body=body)

    def heartbeat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(payload)
        body.setdefault("worker_id", self.worker_id)
        return self._request("POST", "/api/workers/heartbeat", json_body=body)

    def claim(self, *, limit: int = 1, capabilities: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        data = self._request(
            "POST",
            "/api/workers/jobs/claim",
            json_body={
                "worker_id": self.worker_id,
                "limit": limit,
                "capabilities": capabilities or {},
            },
        )
        jobs = data.get("jobs") or []
        return list(jobs) if isinstance(jobs, list) else []

    def job_heartbeat(
        self, job_id: str, *, progress: Optional[Dict[str, Any]] = None
    ) -> None:
        body: Dict[str, Any] = {"worker_id": self.worker_id}
        if isinstance(progress, dict) and progress:
            body["progress"] = progress
        self._request(
            "POST",
            f"/api/workers/jobs/{job_id}/heartbeat",
            json_body=body,
        )

    def complete(self, job_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        self._request(
            "POST",
            f"/api/workers/jobs/{job_id}/complete",
            json_body={"worker_id": self.worker_id, "result": result or {}},
        )

    def fail(
        self,
        job_id: str,
        *,
        error: str,
        retry: bool = False,
        params_update: Optional[Dict[str, Any]] = None,
        partial_result: Optional[Dict[str, Any]] = None,
    ) -> None:
        body: Dict[str, Any] = {
            "worker_id": self.worker_id,
            "error": (error or "")[:1000],
            "retry": bool(retry),
        }
        if isinstance(params_update, dict) and params_update:
            body["params_update"] = params_update
        if isinstance(partial_result, dict) and partial_result:
            body["partial_result"] = partial_result
        self._request(
            "POST",
            f"/api/workers/jobs/{job_id}/fail",
            json_body=body,
        )
