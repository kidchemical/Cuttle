"""Offline persistent usage fallback and shared presentation contracts."""
import json

from api import agent_usage, claude_usage_cache
from api.gizmos import usage


def test_expiry_restart_and_account_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    clock = [1000]
    monkeypatch.setattr(claude_usage_cache.time, "time", lambda: clock[0])
    credentials = {"accessToken": "secret-access", "refreshToken": "secret-refresh"}
    good = {"success": True, "plan_type": "max", "windows": [
        {"label": "5-hour", "used_percent": 100, "reset_at": 2000},
        {"label": "Weekly", "used_percent": 100, "reset_at": 3000}]}
    path = tmp_path / "credentials.json"
    first = claude_usage_cache.snapshot(path, credentials, lambda: good)
    clock[0] += claude_usage_cache.FRESH_S + 1
    fail = lambda: {"success": False, "error": "token expired"}
    # No in-memory snapshot: subsequent calls recover directly from disk.
    for _ in range(2):
        stale = claude_usage_cache.snapshot(path, credentials, fail)
        assert stale["stale"] and stale["windows"] == first["windows"]
        assert stale["updated_at"] == 1000
    normalized = usage.finalize(usage.normalize_claude(stale, clock[0]), clock[0])
    assert normalized["unblock_at"] == 3000
    monkeypatch.setitem(usage._PROVIDERS, "claude", usage.UsageProvider(
        "claude", "Claude Code", lambda: usage.normalize_claude(stale, clock[0])))
    usage.clear()
    gizmo = usage.snapshot("claude")
    assert gizmo["stale"] and gizmo["updated_at"] == 1000
    assert gizmo["unblock_at"] == 3000
    usage.clear()
    report = agent_usage.format_claude_usage_markdown({"success": True, "plan": stale})
    assert "Cached usage" in report and "token expired" in report
    other = claude_usage_cache.snapshot(path, {**credentials, "refreshToken": "other"}, fail)
    assert not other["success"] and "windows" not in other
    stored = next((tmp_path / "cache" / "claude_usage").glob("*.json")).read_text()
    assert "secret-access" not in stored and "secret-refresh" not in stored
    clock[0] = 4000
    fresh = claude_usage_cache.snapshot(path, credentials, lambda: {**good, "windows": []})
    assert not fresh["stale"] and fresh["windows"] == []


def test_expired_credentials_use_persistent_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    path = tmp_path / "claude" / ".credentials.json"
    path.parent.mkdir()
    oauth = {"accessToken": "access", "refreshToken": "refresh", "expiresAt": 1}
    path.write_text(json.dumps({"claudeAiOauth": oauth}))
    claude_usage_cache.snapshot(path, oauth, lambda: {"success": True, "windows": [
        {"label": "5-hour", "used_percent": 100, "reset_at": 1}]})
    cache = next((tmp_path / "cache" / "claude_usage").glob("*.json"))
    saved = json.loads(cache.read_text())
    saved["updated_at"] = 1
    cache.write_text(json.dumps(saved))
    result = agent_usage.fetch_claude_plan_limits()
    assert result["stale"] and "expired" in result["error"]
    assert "Scheduled reset passed" in agent_usage.format_claude_usage_markdown(
        {"success": True, "plan": result})


def test_rate_limit_is_quiet_while_the_reading_is_recent_and_backs_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    claude_usage_cache._backoff_until.clear()
    clock = [10_000]
    monkeypatch.setattr(claude_usage_cache.time, "time", lambda: clock[0])
    creds = {"refreshToken": "r-429"}
    path = tmp_path / "credentials.json"
    calls = []
    good = {"success": True, "windows": [{"label": "Weekly", "used_percent": 86, "reset_at": 99_999}]}
    limited = {"success": False, "retry_after": 300, "error": "rate limited"}

    def fetch(result):
        def _f():
            calls.append(result)
            return result
        return _f

    claude_usage_cache.snapshot(path, creds, fetch(good))
    clock[0] += claude_usage_cache.FRESH_S - 1
    claude_usage_cache.snapshot(path, creds, fetch(good))
    assert len(calls) == 1  # still fresh: no request
    clock[0] += 2
    quiet = claude_usage_cache.snapshot(path, creds, fetch(limited))
    assert quiet["rate_limited"] and not quiet["stale"] and not quiet.get("error")
    clock[0] += 60
    claude_usage_cache.snapshot(path, creds, fetch(good))
    assert len(calls) == 2  # inside the Retry-After window: not asked again
    clock[0] = 10_000 + claude_usage_cache.QUIET_RATE_LIMIT_S + 1
    claude_usage_cache._backoff_until.clear()
    loud = claude_usage_cache.snapshot(path, creds, fetch(limited))
    assert loud["stale"] and "rate-limiting" in loud["error"]
