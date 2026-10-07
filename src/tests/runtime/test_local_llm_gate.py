"""Turns that need the user's local model server, while it is down."""

from unittest.mock import patch

import api.web_chat_api as api


def test_message_needs_local_llm_local_mode():
    with patch("scripts.utilities.hermes_cli_tool.hermes_uses_local_backend", return_value=True):
        assert api._message_needs_local_llm("hello", "local") is True
        assert api._message_needs_local_llm("hello", "auto") is False
        assert api._message_needs_local_llm("/hermes list files", "auto") is True
        assert api._message_needs_local_llm("/HERMES x", "cloud") is True


def test_hermes_cloud_provider_does_not_need_local_llm():
    with patch("scripts.utilities.hermes_cli_tool.hermes_uses_local_backend", return_value=False):
        assert api._message_needs_local_llm("/hermes what model?", "auto") is False


def test_hermes_slash_detection():
    assert api._is_hermes_slash_command("/hermes hi") is True
    assert api._is_hermes_slash_command("/claude hi") is False


def test_local_mode_down_explains_and_never_launches():
    with patch("core.local_llm.local_reachable", return_value=False), \
            patch("subprocess.Popen") as popen, patch("subprocess.run") as run:
        reply, msg = api._local_llm_gate("hi", "sess1", "local")
    assert reply["type"] == "local_llm_unavailable"
    assert "isn't reachable" in reply["response"]
    assert "doesn't start local model servers" in reply["response"]
    assert "<cuttle_button" not in reply["response"]
    assert msg == "hi"
    popen.assert_not_called()
    run.assert_not_called()


def test_local_hermes_down_explains():
    with patch("core.local_llm.local_reachable", return_value=False), \
            patch("scripts.utilities.hermes_cli_tool.hermes_uses_local_backend", return_value=True):
        reply, _ = api._local_llm_gate("/hermes list files", "sess1", "auto")
    assert reply["type"] == "local_llm_unavailable"


def test_gate_passes_when_local_not_required_or_up():
    with patch("core.local_llm.local_reachable", return_value=False):
        assert api._local_llm_gate("what is 2+2?", "sess1", "auto") == (None, "what is 2+2?")
    with patch("core.local_llm.local_reachable", return_value=True):
        assert api._local_llm_gate("hi", "sess1", "local") == (None, "hi")


def test_cuttle_no_longer_manages_local_model_servers():
    import core.local_llm as local_llm
    from api.web_chat_api import app

    for name in ("launch_llamacpp_detached", "stop_llamacpp", "get_llamacpp_start_script"):
        assert not hasattr(local_llm, name)
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/local-llm/status" in rules
    assert "/api/local-llm/start" not in rules and "/api/local-llm/stop" not in rules
    daemon = (api.project_root / "scripts" / "cuttle_daemon.py").read_text(encoding="utf-8")
    assert "llama-server" not in daemon.replace("never starts or\nstops it", "")
    assert "pkill" not in daemon or "llama" not in daemon.split("pkill", 1)[1][:80]
