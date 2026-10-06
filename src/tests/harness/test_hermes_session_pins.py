"""Hermes per-chat model/effort pins + palette wiring (minimal)."""

from pathlib import Path

import pytest
import yaml

from scripts.utilities import hermes_cli_session_store as store
from scripts.utilities.hermes_cli_tool import (
    HERMES_REASONING_EFFORTS,
    hermes_model_label,
    hermes_model_supports_reasoning_extra_body,
    hermes_reasoning_effort_override,
    load_hermes_config_reasoning_effort,
    resolve_hermes_default_model,
    resolve_hermes_runtime,
)


def test_hermes_model_effort_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "hermes_map.json")
    assert store.load_hermes_model("s1") is None
    assert store.save_hermes_model("s1", "z-ai/glm-5.3-flash") == "z-ai/glm-5.3-flash"
    assert store.load_hermes_model("s1") == "z-ai/glm-5.3-flash"
    assert store.save_hermes_model("s1", None) is None
    assert store.load_hermes_model("s1") is None

    assert store.load_hermes_effort("s1") is None
    assert store.save_hermes_effort("s1", "high") == "high"
    assert store.load_hermes_effort("s1") == "high"
    assert store.save_hermes_effort("s1", None) is None
    assert store.load_hermes_effort("s1") is None


def test_hermes_effort_slash_set_list_reject(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "hermes_map.json")
    from api.agent_harness.agents.hermes import adapter as hm

    store.save_hermes_model("s1", "deepseek/deepseek-chat")
    result = hm.Adapter().handle_meta("/effort high", chat_session_id="s1", model=None)
    assert result is not None and result.success
    assert result.meta.get("agent_effort") == "high"
    assert store.load_hermes_effort("s1") == "high"

    listed = hm.Adapter().handle_meta("/effort", chat_session_id="s1", model=None)
    assert listed is not None and "high" in (listed.output or "")

    assert hm.Adapter().handle_meta("effort the login flow", chat_session_id="s1", model=None) is None

    unknown = hm.Adapter().handle_meta("/effort banana", chat_session_id="s1", model=None)
    assert unknown is not None and "Unknown effort" in (unknown.output or "")
    assert store.load_hermes_effort("s1") == "high"

    reset = hm.Adapter().handle_meta("/effort reset", chat_session_id="s1", model=None)
    assert reset is not None and reset.success
    assert store.load_hermes_effort("s1") is None


def test_hermes_model_slash_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "hermes_map.json")
    from api.agent_harness.agents.hermes import adapter as hm

    setter = hm.Adapter().handle_meta(
        "/model z-ai/glm-5.3-flash", chat_session_id="s1", model=None
    )
    assert setter is not None and setter.success
    assert setter.meta.get("agent_model") == "z-ai/glm-5.3-flash"
    assert store.load_hermes_model("s1") == "z-ai/glm-5.3-flash"


def test_hermes_reasoning_effort_override_restores(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text(
        yaml.safe_dump({"agent": {"reasoning_effort": "low"}, "model": {"default": "x"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert load_hermes_config_reasoning_effort() == "low"
    with hermes_reasoning_effort_override("high"):
        assert load_hermes_config_reasoning_effort() == "high"
    assert load_hermes_config_reasoning_effort() == "low"


def test_hermes_z_ai_does_not_support_reasoning_extra_body():
    assert hermes_model_supports_reasoning_extra_body("z-ai/glm-5.3-flash") is False
    assert hermes_model_supports_reasoning_extra_body("deepseek/deepseek-chat") is True
    assert "high" in HERMES_REASONING_EFFORTS
    assert hermes_model_label("z-ai/glm-5.3-flash") == "GLM 5.3 Flash"


def test_hermes_runtime_infers_openrouter_for_slash_models(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "qwen3-coder", "provider": "custom"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    rt = resolve_hermes_runtime("z-ai/glm-5.3-flash", None)
    assert rt["model"] == "z-ai/glm-5.3-flash"
    assert rt["provider"] == "openrouter"
    assert resolve_hermes_default_model() == "qwen3-coder"


def test_hermes_palette_wired_into_chat_page():
    js = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"
    text = js.read_text(encoding="utf-8")
    assert "function buildHermesModelPaletteItems" in text
    assert "function buildHermesEffortPaletteItems" in text
    assert ".concat(hermesModels)" in text
    assert "cmd.category === 'hermes-model'" in text
    assert "persistHermesModelSelection" in text
    assert "'/api/hermes/model'" in text
    assert "hasActiveHermesAgentChip" in text
    assert "seedHermesSupplementFromSessionData" in text
    assert "function agentModelEffortBadgeLabel" in text
    assert "function slashChipShouldNotShorten" in text
    assert "function enrichSlashCommandWithAgentPin" in text


def test_opencode_effort_palette_wired_into_chat_page():
    js = Path(__file__).resolve().parents[2] / "web" / "js" / "chat/chat_page.js"
    text = js.read_text(encoding="utf-8")
    assert "function buildOpenCodeEffortPaletteItems" in text
    assert "function buildOpenCodeModelPaletteItems" in text
    assert ".concat(opencodeEfforts)" in text
    assert "cmd.category === 'opencode-effort'" in text
    assert "persistOpenCodeEffortSelection" in text
    assert "'/api/opencode/effort'" in text
    assert "hasActiveOpenCodeAgentChip" in text
    assert "enrichSlashCommandWithOpenCodeModel" in text
    assert "seedOpenCodeSupplementFromSessionData" in text


def test_hermes_resume_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "hermes_map.json")
    cwd = str(tmp_path)
    sid = "20260917_044611_6d8cec"
    assert store.load_hermes_resume_id(cwd, 42) is None
    store.save_hermes_resume_id(cwd, 42, sid)
    assert store.load_hermes_resume_id(cwd, 42) == sid
    assert store.load_hermes_resume_id(cwd, 99) is None
    store.clear_hermes_resume_id(cwd, 42)
    assert store.load_hermes_resume_id(cwd, 42) is None


@pytest.mark.asyncio
async def test_hermes_turn2_passes_resume_on_argv(tmp_path, monkeypatch):
    """Parity with Cursor: turn 2 must launch with ``--resume <id>`` (not oneshot ``-z``)."""
    import asyncio

    from scripts.utilities import hermes_cli_tool as tool

    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "hermes_map.json")
    monkeypatch.setattr(tool, "hermes_executable", lambda: str(tmp_path / "hermes.exe"))
    monkeypatch.setattr(tool, "ensure_hermes_streaming_enabled", lambda: False)

    captured: list[list[str]] = []
    hermes_sid = "20260917_044611_6d8cec"

    class _ByteReader:
        def __init__(self, data: bytes):
            self._data = data
            self._done = False

        async def readline(self):
            if self._done:
                return b""
            self._done = True
            if not self._data:
                return b""
            return self._data if self._data.endswith(b"\n") else self._data + b"\n"

        async def read(self, _size=-1):
            if self._done:
                return b""
            self._done = True
            return self._data

    class _Proc:
        returncode = 0

        def __init__(self):
            self.stdout = _ByteReader(b"ok\n")
            self.stderr = _ByteReader(f"session_id: {hermes_sid}\n".encode())

        async def wait(self):
            return 0

        def kill(self):
            self.returncode = -9

    async def fake_exec(*cmd, **kwargs):
        captured.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    hm = tool.HermesCliTool(model="deepseek/deepseek-chat", provider="openrouter")
    r1 = await hm.execute_prompt("remember banana", cwd=str(tmp_path), timeout=5.0)
    assert r1.get("success")
    assert r1.get("hermes_session_id") == hermes_sid
    assert "--resume" not in captured[0]
    assert "chat" in captured[0] and "-Q" in captured[0]
    assert "-z" not in captured[0]

    # Adapter/kernel would save; simulate that here then pass resume on turn 2.
    store.save_hermes_resume_id(str(tmp_path), 7, hermes_sid)
    resume = store.load_hermes_resume_id(str(tmp_path), 7)
    r2 = await hm.execute_prompt(
        "what word?", cwd=str(tmp_path), timeout=5.0, resume=resume
    )
    assert r2.get("success")
    assert "--resume" in captured[1]
    assert captured[1][captured[1].index("--resume") + 1] == hermes_sid
