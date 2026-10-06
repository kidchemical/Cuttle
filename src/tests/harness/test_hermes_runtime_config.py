"""Hermes config.yaml → local vs cloud backend resolution."""

from pathlib import Path

import yaml

from scripts.utilities.hermes_cli_tool import (
    hermes_uses_local_backend,
    load_hermes_model_config,
    resolve_hermes_runtime,
)


def _write_cfg(tmp_path: Path, model: dict) -> Path:
    home = tmp_path / "hermes"
    home.mkdir()
    path = home / "config.yaml"
    path.write_text(yaml.safe_dump({"model": model}), encoding="utf-8")
    return home


def test_openrouter_glm_is_not_local(tmp_path, monkeypatch):
    home = _write_cfg(
        tmp_path,
        {
            "default": "z-ai/glm-5.3-flash",
            "provider": "openrouter",
        },
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert hermes_uses_local_backend() is False
    assert load_hermes_model_config()["provider"] == "openrouter"
    rt = resolve_hermes_runtime()
    assert rt["model"] == "z-ai/glm-5.3-flash"
    assert rt["provider"] == "openrouter"


def test_custom_local_is_local(tmp_path, monkeypatch):
    home = _write_cfg(
        tmp_path,
        {
            "default": "qwen3-coder",
            "provider": "custom",
            "base_url": "http://127.0.0.1:8081/v1",
        },
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert hermes_uses_local_backend() is True
    rt = resolve_hermes_runtime()
    assert rt["model"] == "qwen3-coder"
    assert rt["provider"] == "custom"


def test_explicit_override_beats_config(tmp_path, monkeypatch):
    home = _write_cfg(
        tmp_path,
        {"default": "z-ai/glm-5.3-flash", "provider": "openrouter"},
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    rt = resolve_hermes_runtime("qwen3-coder", "custom")
    assert rt == {"model": "qwen3-coder", "provider": "custom"}
