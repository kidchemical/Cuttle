"""OpenRouter Management Key: accessor, credits fetch, and Settings probe."""

from __future__ import annotations

import io
import json
import urllib.error


def _fake_urlopen(payload, status=200):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(json.dumps(payload).encode("utf-8"))


def test_management_key_env_then_env_file(tmp_path, monkeypatch):
    from api import openrouter_management as om

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv(om.ENV_NAME, raising=False)
    assert om.management_key() == ""
    assert om.has_management_key() is False

    (tmp_path / "home" / ".env").write_text(
        "# management\nexport OPENROUTER_MANAGEMENT_KEY=sk-or-mgmt-from-file\n",
        encoding="utf-8",
    )
    assert om.management_key() == "sk-or-mgmt-from-file"

    # Process env wins over the file.
    monkeypatch.setenv(om.ENV_NAME, "sk-or-mgmt-from-process")
    assert om.management_key() == "sk-or-mgmt-from-process"


def test_fetch_credits_normalizes_balance(monkeypatch):
    from api import openrouter_management as om

    monkeypatch.setenv(om.ENV_NAME, "sk-or-mgmt")
    monkeypatch.setattr(
        om.urllib.request,
        "urlopen",
        lambda *a, **k: _fake_urlopen(
            {"data": {"total_credits": 100.5, "total_usage": 25.75}}
        ),
    )
    result = om.fetch_credits()
    assert result["success"] is True
    assert result["total_credits"] == 100.5
    assert result["total_usage"] == 25.75
    assert result["balance"] == 74.75


def test_fetch_credits_without_key(tmp_path, monkeypatch):
    from api import openrouter_management as om

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(om.ENV_NAME, raising=False)
    result = om.fetch_credits()
    assert result["success"] is False
    assert "No OpenRouter management key" in result["error"]


def test_fetch_credits_http_errors(monkeypatch):
    from api import openrouter_management as om

    monkeypatch.setenv(om.ENV_NAME, "sk-or-mgmt")
    for code, needle in ((401, "401"), (403, "Management key")):
        def _raise(*a, _code=code, **k):
            raise urllib.error.HTTPError(om.CREDITS_URL, _code, "err", {}, None)

        monkeypatch.setattr(om.urllib.request, "urlopen", _raise)
        result = om.fetch_credits()
        assert result["success"] is False
        assert needle in result["error"]


def test_probe_reports_balance(monkeypatch):
    from api import openrouter_management as om

    monkeypatch.setenv(om.ENV_NAME, "sk-or-mgmt")
    monkeypatch.setattr(
        om.urllib.request,
        "urlopen",
        lambda *a, **k: _fake_urlopen(
            {"data": {"total_credits": 50, "total_usage": 12.5}}
        ),
    )
    result = om.probe()
    assert result["success"] is True
    assert "$37.50 credit left" in result["message"]
    assert "$12.50 of $50.00 used" in result["message"]
