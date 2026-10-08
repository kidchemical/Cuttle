"""Client-daemon enroll: always pair-capable, even with a saved bearer."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent.parent


def _load_daemon():
    path = SRC_DIR / "scripts" / "cuttle_client_daemon.py"
    spec = importlib.util.spec_from_file_location("cuttle_client_daemon_enroll", str(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enroll_sends_pairing_secret_with_saved_bearer(monkeypatch):
    """A stale saved bearer must still carry a fresh secret so 202 can re-pair."""
    mod = _load_daemon()
    seen = {}

    def fake_http_json(method, base, path, *, token="", body=None, timeout=20):
        seen["token"] = token
        seen["body"] = dict(body or {})
        return {"success": True, "worker_id": "w1"}

    monkeypatch.setattr(mod, "http_json", fake_http_json)
    out = mod.enroll({"workerId": "w1", "workerToken": "stale-saved"}, ["https://h:8080"])
    assert out["success"] is True
    assert seen["token"] == "stale-saved"
    secret = seen["body"].get("pairing_secret") or ""
    assert len(secret) >= 32


def test_enroll_stale_bearer_repairs_via_pairing(monkeypatch):
    """202 pending + owner approve -> poll returns the fresh token."""
    mod = _load_daemon()
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def fake_http_json(method, base, path, *, token="", body=None, timeout=20):
        calls.append((method, path, dict(body or {})))
        if path == "/api/workers/enroll":
            return {"success": False, "status": "pending", "request_id": "r1", "code": "123456"}
        return {"success": True, "status": "approved", "worker_id": "w1", "token": "fresh"}

    monkeypatch.setattr(mod, "http_json", fake_http_json)
    out = mod.enroll({"workerId": "w1", "workerToken": "stale-saved"}, ["https://h:8080"])
    assert out["token"] == "fresh"
    poll_bodies = [b for m, p, b in calls if p.endswith("/poll")]
    assert poll_bodies, calls
    assert poll_bodies[0].get("pairing_secret")
    enroll_bodies = [b for m, p, b in calls if p == "/api/workers/enroll"]
    assert enroll_bodies[0].get("pairing_secret") == poll_bodies[0].get("pairing_secret")
