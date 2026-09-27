"""Codex starred-effort behavior for new chats (Codex-specific paths).

Shared first-turn ``agent_pins`` / dirty-attach coverage lives in
``test_first_turn_agent_pins.py`` (Muse/Hermes/OpenCode/Codex matrix).
This file keeps Codex-only surfaces: ``/api/codex/effort``, the Codex
adapter starred fallback, and ``ensureCodexEffortForSend`` (no Muse/Hermes
equivalent).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from api.agent_harness import agent_defaults as defaults


class _FakeSettings:
    def __init__(self):
        self.store = {}

    def get_setting(self, key, default=None):
        return self.store.get(key, default)

    def set_setting(self, key, value):
        self.store[key] = value
        return True


def _install_low_star(monkeypatch):
    settings = _FakeSettings()
    monkeypatch.setattr(defaults, "_settings", lambda: settings)
    defaults.set_starred_effort("codex", "low")
    return settings


def test_new_chat_codex_effort_endpoint_returns_starred_default(monkeypatch):
    from api import web_chat_api
    from api.agent_harness.agents.codex import model_catalog

    _install_low_star(monkeypatch)
    monkeypatch.setattr(
        model_catalog,
        "codex_efforts_for_model",
        lambda _model=None: ["low", "medium", "xhigh"],
    )

    response = web_chat_api.app.test_client().get("/api/codex/effort")

    assert response.status_code == 200
    body = response.get_json()
    assert body["preferredEffort"] == "low"
    assert body["preferredSource"] == "starred"
    assert next(level for level in body["levels"] if level["id"] == "low")["current"]


def test_codex_effort_info_reports_starred_default_for_unpinned_chat(
    tmp_path, monkeypatch
):
    from scripts.utilities import codex_cli_session_store as store
    from api.agent_harness.agents.codex import model_catalog
    from api.agent_harness.agents.codex.adapter import Adapter

    _install_low_star(monkeypatch)
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "codex_map.json")
    monkeypatch.setattr(
        model_catalog,
        "codex_efforts_for_model",
        lambda _model=None: ["low", "medium", "xhigh"],
    )

    result = Adapter().handle_meta("effort", chat_session_id="new-chat", model=None)

    assert result is not None and result.success
    assert "Starred default for new chats: `low`." in result.output
    assert "No pin — Codex uses its config default." not in result.output
    assert result.meta["agent_effort"] == "low"
    assert result.meta["effort_source"] == "starred"


def test_codex_execution_passes_starred_effort_to_cli(tmp_path, monkeypatch):
    from scripts.utilities import codex_cli_session_store as store
    from scripts.utilities import codex_cli_tool
    from api.agent_harness.agents.codex import model_catalog
    from api.agent_harness.agents.codex.adapter import Adapter

    _install_low_star(monkeypatch)
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "codex_map.json")
    monkeypatch.setattr(
        model_catalog,
        "codex_efforts_for_model",
        lambda _model=None: ["low", "medium", "xhigh"],
    )
    seen = {}

    class FakeCodexCliTool:
        def __init__(self, *, model=None, reasoning_effort=None):
            seen["model"] = model
            seen["reasoning_effort"] = reasoning_effort

        async def execute_prompt(self, *_args, **_kwargs):
            return {"success": True, "output": "ok", "usage": {}}

    monkeypatch.setattr(codex_cli_tool, "CodexCliTool", FakeCodexCliTool)
    result = asyncio.run(
        Adapter().execute(
            "hello",
            cwd=str(tmp_path),
            resume=None,
            model=None,
            chat_session_id="new-chat",
        )
    )

    assert result.success
    assert seen["reasoning_effort"] == "low"
    assert result.meta["agent_effort"] == "low"
    assert result.meta["effort_source"] == "starred"


def test_codex_execution_override_beats_session_and_starred(tmp_path, monkeypatch):
    """Frozen send identity (reasoning_effort) must win over a stale session pin."""
    from scripts.utilities import codex_cli_session_store as store
    from scripts.utilities import codex_cli_tool
    from api.agent_harness.agents.codex import adapter as cadapter
    from api.agent_harness.agents.codex.adapter import Adapter

    _install_low_star(monkeypatch)
    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "codex_map.json")
    store.save_codex_effort("s1", "low")
    monkeypatch.setattr(
        cadapter,
        "codex_efforts_for_model",
        lambda _model=None: ["low", "medium", "xhigh", "max"],
    )
    seen = {}

    class FakeCodexCliTool:
        def __init__(self, *, model=None, reasoning_effort=None):
            seen["model"] = model
            seen["reasoning_effort"] = reasoning_effort

        async def execute_prompt(self, *_args, **_kwargs):
            return {"success": True, "output": "ok", "usage": {}}

    monkeypatch.setattr(codex_cli_tool, "CodexCliTool", FakeCodexCliTool)
    result = asyncio.run(
        Adapter().execute(
            "hello",
            cwd=str(tmp_path),
            resume=None,
            model=None,
            chat_session_id="s1",
            reasoning_effort="max",
        )
    )

    assert result.success
    assert seen["reasoning_effort"] == "max"
    assert result.meta["agent_effort"] == "max"
    assert result.meta["effort_source"] == "override"


def test_ensure_codex_effort_for_send_respects_dirty_pick():
    """Codex-only pre-send fetch must not clobber a dirty palette pick."""
    chat_js = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
    source = chat_js.read_text(encoding="utf-8")

    helper_start = source.index("async function ensureCodexEffortForSend(message)")
    helper_end = source.index("function persistCodexEffortSelection", helper_start)
    helper = source[helper_start:helper_end]
    assert "codexEffortDirty" in helper
    assert helper.index("codexEffortDirty") < helper.index("await fetch")

    welcome_start = source.index("async function sendWelcomeMessage()")
    welcome_end = source.index("function handleWelcomeInputKeyDown", welcome_start)
    welcome = source[welcome_start:welcome_end]
    assert welcome.index("await ensureCodexEffortForSend(message)") < welcome.index(
        "addMessageToUI(displayMessage, 'user'"
    )

    send_start = source.index("async function sendMessage(")
    send_end = source.index("function userAvatarInnerHtmlForChat", send_start)
    send = source[send_start:send_end]
    assert send.index("await ensureCodexEffortForSend(message)") < send.index(
        "addMessageToUI(displayMessage, 'user'"
    )
