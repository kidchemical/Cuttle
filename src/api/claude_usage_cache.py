"""Last successful Claude plan windows, shared by usage reports and gizmos."""
import hashlib
import json
import threading
import time
import uuid

from core.runtime_paths import runtime_cache_path

_lock = threading.RLock()


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
        if cached and 0 <= now - cached["updated_at"] < 60:
            return cached
        result = fetch()
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
