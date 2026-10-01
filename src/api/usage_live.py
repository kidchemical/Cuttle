"""Shared, demand-driven usage snapshots. No background account polling."""
import json
import threading
import time

from flask import Blueprint, jsonify, request
from api.http_authz import owner_required

AGENTS = frozenset({"cursor", "codex", "muse", "hermes", "opencode"})
TTL = 60
_cache = {}
_locks = {}
_guard = threading.Lock()


def usage_snapshot(agent, days=30):
    if agent not in AGENTS:
        raise ValueError("Unsupported usage agent")
    days = max(1, min(int(days), 365)) if agent in {"muse", "hermes", "opencode"} else 30
    key = (agent, days)
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    # Concurrent panes/devices wait for the same refresh, including failed refreshes.
    with lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < TTL:
            return dict(cached[1])
        from api import agent_usage
        try:
            if agent == "cursor":
                from api.cursor_agent_commands import _run_cursor_usage
                markdown = _run_cursor_usage()
            else:
                reporter = getattr(agent_usage, "run_" + agent + "_usage")
                markdown = reporter() if agent == "codex" else reporter(days=days)
            result = {"agent": agent, "days": days, "markdown": markdown,
                      "updated_at": time.time()}
        except Exception:
            # Keep the last report on transient failure, without leaking credentials.
            result = dict(cached[1]) if cached else {
                "agent": agent, "days": days, "markdown": "Usage temporarily unavailable.",
                "updated_at": None}
            result["error"] = "Refresh failed; retrying in one minute."
        _cache[key] = (time.monotonic(), result)
        return dict(result)


def live_usage_reply(agent, args=""):
    from api.agent_usage import _days_from_args
    snapshot = usage_snapshot(agent, _days_from_args(args))
    # JSON escapes '<' so embedded meter tags cannot terminate the live wrapper.
    body = json.dumps(snapshot, ensure_ascii=False).replace("<", "\\u003c")
    return "<cuttle_usage_live>" + body + "</cuttle_usage_live>"


usage_live_bp = Blueprint("usage_live", __name__)


@usage_live_bp.get("/api/usage-live")
@owner_required
def get_usage_live():
    try:
        result = usage_snapshot(request.args.get("agent", ""), request.args.get("days", "30"))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid usage agent or day range"}), 400
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response
