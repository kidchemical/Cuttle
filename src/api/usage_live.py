"""Shared, demand-driven usage snapshots. No background account polling."""
import json
import threading
import time
import uuid

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
        lock = _locks.setdefault(key, threading.RLock())
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


@usage_live_bp.post("/api/usage/codex/reset")
@owner_required
def redeem_codex_reset():
    """Redeem only the explicitly selected credit; retries keep the same key."""
    if not request.is_json:
        return jsonify({"error": "JSON body required"}), 415
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid request"}), 400
    credit_id = body.get("credit_id")
    account_id = body.get("account_id")
    try:
        key = str(uuid.UUID(body.get("idempotency_key", "")))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"error": "Invalid redemption key"}), 400
    if (not isinstance(credit_id, str) or not credit_id.strip() or len(credit_id) > 512
            or body.get("confirmed") is not True
            or (account_id is not None and not isinstance(account_id, str))):
        return jsonify({"error": "Select and confirm a reset credit"}), 400
    from scripts.utilities.codex_account import consume_codex_reset
    with _guard:
        lock = _locks.setdefault(("codex", 30), threading.RLock())
    with lock:
        try:
            result = consume_codex_reset(credit_id, key, account_id)
        except ValueError:
            return jsonify({"error": "Codex account changed. Request a new usage report."}), 409
        except Exception:
            return jsonify({"error": "Redemption could not be verified. Retry this button to check the same attempt."}), 502
        outcome = result.get("outcome")
        messages = {
            "reset": "Reset redeemed.",
            "alreadyRedeemed": "This reset was already redeemed.",
            "nothingToReset": "No usage window is eligible for a reset.",
            "noCredit": "This reset is no longer available.",
        }
        if outcome not in messages:
            return jsonify({"error": "Unexpected redemption response. Retry the same attempt."}), 502
        _cache.pop(("codex", 30), None)
        snapshot = usage_snapshot("codex")
    response = jsonify({"outcome": outcome, "message": messages[outcome], "snapshot": snapshot})
    response.headers["Cache-Control"] = "no-store"
    return response
