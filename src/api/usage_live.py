"""Shared, demand-driven usage snapshots. No background account polling."""
import json
import re
import threading
import time
import uuid

from flask import Blueprint, jsonify, request
from api.http_authz import owner_required

AGENTS = frozenset({"cursor", "codex", "muse", "hermes", "opencode", "claude"})
TTL = 60
_cache = {}
_locks = {}
_guard = threading.Lock()

WRAPPER_RE = re.compile(
    r"<cuttle_usage_live>([\s\S]*?)</cuttle_usage_live>", re.IGNORECASE)


def _wrapper_body(snapshot):
    return json.dumps(snapshot, ensure_ascii=False).replace("<", "\\u003c")


def replace_usage_wrapper(content, snapshot):
    """Swap the first live-usage wrapper for a fresh snapshot.

    Returns ``(new_content, replaced)``; surrounding text is untouched.
    """
    replacement = ("<cuttle_usage_live>" + _wrapper_body(snapshot)
                   + "</cuttle_usage_live>")
    new_content, count = WRAPPER_RE.subn(
        lambda _match: replacement, content, count=1)
    return new_content, count > 0


def usage_snapshot(agent, days=30):
    if agent not in AGENTS:
        raise ValueError("Unsupported usage agent")
    days = max(1, min(int(days), 365)) if agent in {"muse", "hermes", "opencode", "claude"} else 30
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
    return "<cuttle_usage_live>" + _wrapper_body(snapshot) + "</cuttle_usage_live>"


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


@usage_live_bp.post("/api/usage-live/snapshot")
def persist_usage_snapshot():
    """Refresh the stored wrapper of one live-usage message.

    The client sends only the row identity; the server recomputes the
    snapshot itself, so callers can never rewrite arbitrary history.
    Only the wrapper JSON is replaced — surrounding text is untouched.
    """
    from api.auth_db import get_auth_db
    from api.http_authz import require_chat_session_access
    if not request.is_json:
        return jsonify({"error": "JSON body required"}), 415
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid request"}), 400
    try:
        message_id = int(body.get("message_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid message"}), 400
    if isinstance(body.get("message_id"), bool) or message_id <= 0:
        return jsonify({"error": "Invalid message"}), 400
    agent = body.get("agent")
    if agent not in AGENTS:
        return jsonify({"error": "Invalid usage agent"}), 400
    db = get_auth_db()
    try:
        row = db.get_message_by_id(message_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Message not found"}), 404
    if not row or row.get("role") != "assistant":
        return jsonify({"error": "Message not found"}), 404
    _user, _nid, err = require_chat_session_access(
        "db_session_%s" % row.get("chat_session_id"))
    if err:
        return err
    match = WRAPPER_RE.search(str(row.get("content") or ""))
    if not match:
        return jsonify({"error": "Message is not a live usage report"}), 422
    try:
        stored = json.loads(match.group(1))
    except (TypeError, ValueError):
        return jsonify({"error": "Message is not a live usage report"}), 422
    if not isinstance(stored, dict) or stored.get("agent") != agent:
        return jsonify({"error": "Message is not a live usage report"}), 422
    try:
        snapshot = usage_snapshot(agent, stored.get("days", 30))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid usage agent or day range"}), 400
    if snapshot.get("error"):
        # Transient failure keeps serving the last good report; never stamp
        # the failure note into history.
        response = jsonify({"ok": True, "persisted": False,
                            "message_id": int(row["id"]), "agent": agent})
        response.headers["Cache-Control"] = "no-store"
        return response
    if snapshot.get("markdown") == stored.get("markdown"):
        # Same report text (only the fetch timestamp moved): no rewrite.
        persisted = False
    else:
        new_content, _replaced = replace_usage_wrapper(
            str(row.get("content")), snapshot)
        persisted = new_content != str(row.get("content"))
        if persisted:
            db.update_message_content(int(row["id"]), new_content)
    response = jsonify({"ok": True, "persisted": persisted,
                        "message_id": int(row["id"]), "agent": agent,
                        "days": snapshot.get("days"),
                        "updated_at": snapshot.get("updated_at")})
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
