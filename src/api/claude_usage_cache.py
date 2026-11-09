"""Last successful Claude plan windows, shared by usage reports and gizmos."""
import hashlib
import json
import threading
import time
import uuid

from core.runtime_paths import runtime_cache_path

_lock = threading.RLock()
# Login key -> epoch before which a rate-limited endpoint is not asked again.
_backoff_until = {}
_MAX_BACKOFF_S = 3600
# Anthropic rate-limits this endpoint at roughly per-minute polling; plan
# windows move slowly, so a reading stays fresh for five minutes.
FRESH_S = 300
# A rate-limited refresh is quiet while the last good reading is this recent.
QUIET_RATE_LIMIT_S = 15 * 60
_RATE_LIMITED = "Claude usage API is rate-limiting requests (HTTP 429); showing the last reading and retrying shortly."


def _rate_limited(cached, now):
    if cached and now - cached["updated_at"] < QUIET_RATE_LIMIT_S:
        return {**cached, "stale": False, "rate_limited": True}
    return {**cached, "stale": True, "error": _RATE_LIMITED} if cached else {"success": False, "error": _RATE_LIMITED}


def snapshot(credentials_path, oauth, fetch):
    # A refresh-token fingerprint isolates logins without storing credentials.
    # Access-token fallback is conservative: renewal invalidates the old cache.
    identity = (oauth or {}).get("refreshToken") or (oauth or {}).get("accessToken")
    with _lock:
        path = None
        cached = None
        if identity:
            key = hashlib.sha256((str(credentials_path.resolve()) + "\0" + identity).encode()).hexdigest()
            try:
                path = runtime_cache_path("claude_usage/" + key + ".json")
                cached = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(cached, dict) or not cached.get("success") or not isinstance(cached.get("updated_at"), (int, float)):
                    cached = None
            except (OSError, ValueError):
                cached = None
        now = time.time()
        if cached and 0 <= now - cached["updated_at"] < FRESH_S:
            return cached
        backoff_key = path or identity or ""
        wait_until = _backoff_until.get(backoff_key, 0)
        if now < wait_until:
            return _rate_limited(cached, now)
        result = fetch()
        if result.get("retry_after"):
            _backoff_until[backoff_key] = now + min(float(result["retry_after"]), _MAX_BACKOFF_S)
            return _rate_limited(cached, now)
        if not result.get("success"):
            if cached:
                return {**cached, "stale": True, "error": result.get("error")}
            return result
        result = {**result, "updated_at": now, "stale": False}
        if path:
            temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temp.write_text(json.dumps(result), encoding="utf-8")
                temp.replace(path)
            except OSError:
                pass  # Usage remains available when the replaceable cache is unwritable.
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
        return result
