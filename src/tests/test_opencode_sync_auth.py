"""Unit tests for OpenCode auth sync (LLM-free key handoff)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / ".cuttle_global" / "scripts" / "opencode_sync_auth.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("opencode_sync_auth", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def sync_mod(tmp_path, monkeypatch):
    mod = _load_mod()
    auth = tmp_path / "auth.json"
    cfg = tmp_path / "opencode.json"
    env_file = tmp_path / "src" / ".env"
    env_file.parent.mkdir(parents=True)
    monkeypatch.setenv("CUTTLE_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("OPENCODE_AUTH_PATH", str(auth))
    monkeypatch.setenv("OPENCODE_CONFIG_PATH", str(cfg))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_KEY", raising=False)
    return mod, auth, cfg, env_file


def test_sync_openrouter_from_env(sync_mod, monkeypatch):
    mod, auth, cfg, _env = sync_mod
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-openrouter")
    msg = mod.sync_provider("openrouter", pin_model=True)
    assert msg.startswith("Synced openrouter")
    assert "sk-or" not in msg
    data = json.loads(auth.read_text(encoding="utf-8"))
    assert data["openrouter"]["type"] == "api"
    assert data["openrouter"]["key"] == "sk-or-v1-test-openrouter"
    pinned = json.loads(cfg.read_text(encoding="utf-8"))
    assert pinned["model"] == "openrouter/z-ai/glm-5.3-flash"


def test_sync_openrouter_from_dotenv(sync_mod):
    mod, auth, _cfg, env_file = sync_mod
    env_file.write_text("OPENROUTER_API_KEY=sk-or-from-dotenv\n", encoding="utf-8")
    msg = mod.sync_provider("openrouter", pin_model=False)
    assert "Synced openrouter" in msg
    data = json.loads(auth.read_text(encoding="utf-8"))
    assert data["openrouter"]["key"] == "sk-or-from-dotenv"


def test_sync_all_pins_openrouter_when_present(sync_mod, monkeypatch):
    mod, auth, cfg, env_file = sync_mod
    env_file.write_text(
        "OPENROUTER_API_KEY=sk-or-all\nOPENAI_API_KEY=sk-openai-all\n",
        encoding="utf-8",
    )
    assert mod.main(["all"]) == 0
    pinned = json.loads(cfg.read_text(encoding="utf-8"))
    assert pinned["model"] == "openrouter/z-ai/glm-5.3-flash"


def test_sync_openai_from_env(sync_mod, monkeypatch):
    mod, auth, cfg, _env = sync_mod
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-123")
    msg = mod.sync_provider("openai", pin_model=True)
    assert msg.startswith("Synced openai")
    assert "sk-test" not in msg
    data = json.loads(auth.read_text(encoding="utf-8"))
    assert data["openai"]["type"] == "api"
    assert data["openai"]["key"] == "sk-test-openai-123"
    pinned = json.loads(cfg.read_text(encoding="utf-8"))
    assert pinned["model"] == "openai/gpt-4o-mini"


def test_sync_openai_from_dotenv(sync_mod):
    mod, auth, _cfg, env_file = sync_mod
    env_file.write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    msg = mod.sync_provider("openai", pin_model=False)
    assert "Synced openai" in msg
    assert "dotenv" in msg
    data = json.loads(auth.read_text(encoding="utf-8"))
    assert data["openai"]["key"] == "sk-from-dotenv"


def test_sync_missing_key(sync_mod):
    mod, auth, _cfg, _env = sync_mod
    msg = mod.sync_provider("openai")
    assert "Missing" in msg
    assert not auth.exists()


def test_status_never_leaks_key(sync_mod, monkeypatch):
    mod, _auth, _cfg, _env = sync_mod
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-should-not-appear")
    line = mod.status_line()
    assert "sk-secret" not in line
    assert "openai: cuttle=yes" in line


def test_main_status_exit_0(sync_mod, monkeypatch, capsys):
    mod, *_ = sync_mod
    monkeypatch.setenv("CUTTLE_PARAM_PROVIDER", "status")
    assert mod.main([]) == 0
    out = capsys.readouterr().out
    assert "OpenCode auth status" in out
    assert "sk-" not in out
