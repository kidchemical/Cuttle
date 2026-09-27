#!/usr/bin/env python3
"""POST /api/flask/restart (daemon-owned). Used by flask.restart action forms.

Params come from CUTTLE_PARAM_* (action forms) or CUTTLE_RESTART_* (shell).
Does not kill Flask from this process.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request


API = "https://127.0.0.1:8080/api/flask/restart"
VALID = ("graceful", "when-idle", "force", "status")


def _first_env(*names: str) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def format_restart_result(payload: str) -> str:
    if not payload:
        return ""
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    if not isinstance(obj, dict):
        return payload
    work = obj.get("active_work") if isinstance(obj.get("active_work"), dict) else {}
    if work.get("is_idle"):
        busy = "no active work"
    else:
        count = work.get("active_count")
        busy = f"{count} active task(s)" if count is not None else ""
    if obj.get("error"):
        return str(obj["error"])
    state = str(obj.get("state") or "")
    if not state:
        status = obj.get("status")
        if isinstance(status, dict):
            state = str(status.get("state") or "")
    mapping = {
        "waiting_for_idle": f"Waiting for {busy} to finish…" if busy else "Waiting for idle…",
        "acknowledged": "Restarting Flask…",
        "preparing": "Restarting Flask…",
        "rejected": f"Postponed — {busy} still running" if busy else "Postponed — still running",
        "healthy": f"Last restart healthy ({busy})" if busy else "Last restart healthy",
    }
    if state in mapping:
        return mapping[state]
    if state:
        return f"{state} ({busy})" if busy else state
    if obj.get("response"):
        return " ".join(str(obj["response"]).split())
    return payload


def main() -> int:
    mode = _first_env("CUTTLE_PARAM_MODE", "CUTTLE_RESTART_MODE") or "graceful"
    mode = mode.strip().lower()
    if mode not in VALID:
        print("Unknown restart mode '" + mode + "'. Use: " + ", ".join(VALID))
        return 1
    session_id = _first_env(
        "CUTTLE_PARAM_SESSION_ID", "CUTTLE_SESSION_ID", "CUTTLE_RESTART_SESSION_ID"
    )
    notify_raw = _first_env("CUTTLE_PARAM_CHAT_NOTIFY", "CUTTLE_RESTART_CHAT_NOTIFY")
    chat_notify = _truthy(notify_raw) if notify_raw else False
    body: dict = {
        "mode": mode,
        "source": "flask.restart_action",
        "chat_notify": chat_notify,
    }
    if session_id:
        body["session_id"] = session_id
    if mode == "force":
        body["confirm"] = True
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            print(format_restart_result(raw))
            return 0
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = format_restart_result(e.read().decode("utf-8", errors="replace"))
        except Exception:
            detail = ""
        if not detail:
            detail = str(e)
        print(f"Restart ({mode}) failed: {detail}")
        return 1
    except Exception as e:
        print(f"Restart ({mode}) failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
