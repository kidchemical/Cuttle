"""Tests for on-demand llama.cpp Yes/No launch gate (Local + Auto)."""

from unittest.mock import patch

import api.web_chat_api as api


def setup_function():
    api._pending_local_llm_messages.clear()
    api._declined_local_llm_sessions.clear()


def test_message_needs_local_llm_local_mode():
    with patch(
        "scripts.utilities.hermes_cli_tool.hermes_uses_local_backend",
        return_value=True,
    ):
        assert api._message_needs_local_llm("hello", "local") is True
        assert api._message_needs_local_llm("hello", "auto") is False
        assert api._message_needs_local_llm("/hermes list files", "auto") is True
        assert api._message_needs_local_llm("/HERMES x", "cloud") is True


def test_hermes_cloud_provider_does_not_need_local_llm():
    with patch(
        "scripts.utilities.hermes_cli_tool.hermes_uses_local_backend",
        return_value=False,
    ):
        assert api._message_needs_local_llm("/hermes what model?", "auto") is False
        assert api._message_needs_local_llm("/hermes what model?", "cloud") is False
        assert api._message_needs_local_llm("hello", "local") is True


def test_hermes_slash_detection():
    assert api._is_hermes_slash_command("/hermes hi") is True
    assert api._is_hermes_slash_command("/claude hi") is False


def test_gate_offers_for_auto_hermes_when_down():
    with patch("core.local_llm.is_llamacpp", return_value=True), patch(
        "core.local_llm.local_reachable", return_value=False
    ), patch(
        "scripts.utilities.hermes_cli_tool.hermes_uses_local_backend",
        return_value=True,
    ):
        reply, msg = api._handle_local_llm_launch_gate(
            "/hermes list the python files", "sess1", "auto"
        )
    assert reply is not None
    assert reply["type"] == "local_llm_launch_prompt"
    assert "launch llama.cpp" in reply["response"]
    assert api._pending_local_llm_messages["sess1"].startswith("/hermes")


def test_gate_skips_hermes_when_cloud_backend():
    with patch("core.local_llm.is_llamacpp", return_value=True), patch(
        "core.local_llm.local_reachable", return_value=False
    ), patch(
        "scripts.utilities.hermes_cli_tool.hermes_uses_local_backend",
        return_value=False,
    ):
        reply, msg = api._handle_local_llm_launch_gate(
            "/hermes what model are you?", "sess1", "auto"
        )
    assert reply is None
    assert msg == "/hermes what model are you?"
    assert "sess1" not in api._pending_local_llm_messages


def test_gate_skips_auto_chat_when_local_not_required():
    """Normal Auto chat must not prompt at the early gate (fallback asks when invoking)."""
    with patch("core.local_llm.is_llamacpp", return_value=True), patch(
        "core.local_llm.local_reachable", return_value=False
    ):
        reply, msg = api._handle_local_llm_launch_gate("what is 2+2?", "sess1", "auto")
    assert reply is None
    assert msg == "what is 2+2?"
    assert "sess1" not in api._pending_local_llm_messages


def test_gate_offers_for_local_mode_when_down():
    with patch("core.local_llm.is_llamacpp", return_value=True), patch(
        "core.local_llm.local_reachable", return_value=False
    ):
        reply, _ = api._handle_local_llm_launch_gate("hi", "sess1", "local")
    assert reply is not None
    assert reply["type"] == "local_llm_launch_prompt"
    assert "Local" in reply["response"]


def test_no_button_auto_continues_without_local():
    api._pending_local_llm_messages["sess1"] = "explain this repo"
    with patch.object(api, "emit_chat_status"):
        reply, msg = api._handle_local_llm_launch_gate(
            "[button:launch-local-llm-no]", "sess1", "auto"
        )
    assert reply is None
    assert msg == "explain this repo"
    assert "sess1" in api._declined_local_llm_sessions


def test_no_button_hermes_does_not_continue():
    api._pending_local_llm_messages["sess1"] = "/hermes do stuff"
    with patch(
        "scripts.utilities.hermes_cli_tool.hermes_uses_local_backend",
        return_value=True,
    ):
        reply, _ = api._handle_local_llm_launch_gate(
            "[button:launch-local-llm-no]", "sess1", "auto"
        )
    assert reply is not None
    assert reply["type"] == "local_llm_launch"
    assert "leaving llama.cpp off" in reply["response"]


def test_button_click_history_maps_friendly_label():
    text, meta = api._button_click_history("[button:launch-local-llm-no]")
    assert text == "Selected: Not now"
    assert meta["button_click"]["id"] == "launch-local-llm-no"


def test_offer_respects_decline_unless_required():
    api._declined_local_llm_sessions.add("sess1")
    with patch("core.local_llm.is_llamacpp", return_value=True), patch(
        "core.local_llm.local_reachable", return_value=False
    ), patch(
        "scripts.utilities.hermes_cli_tool.hermes_uses_local_backend",
        return_value=True,
    ):
        assert api._offer_local_llm_launch_if_needed("sess1", "hi", "auto") is None
        assert api._offer_local_llm_launch_if_needed(
            "sess1", "/hermes hi", "auto"
        ) is not None


def test_offer_prompts_auto_when_local_about_to_run():
    """Auto chat should ask to launch when local is invoked (pipeline / fallback), not at entry."""
    with patch("core.local_llm.is_llamacpp", return_value=True), patch(
        "core.local_llm.local_reachable", return_value=False
    ):
        offer = api._offer_local_llm_launch_if_needed("sess1", "what is 2+2?", "auto")
    assert offer is not None
    assert offer["type"] == "local_llm_launch_prompt"
    assert "launch llama.cpp" in offer["response"]
    assert api._pending_local_llm_messages["sess1"] == "what is 2+2?"
