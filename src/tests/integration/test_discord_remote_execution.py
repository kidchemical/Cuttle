"""Regression: Discord inbound gateway and remote-exec graphs are not shipped."""

from pathlib import Path

src_root = Path(__file__).resolve().parent.parent.parent
repo = src_root.parent


def test_gateway_files_absent():
    assert not (src_root / "bots" / "discord_bot.py").exists()
    assert not (src_root / "api" / "discord_chat_bridge.py").exists()


def test_discord_remote_code_pipeline_absent():
    assert not (src_root / "pipelines" / "Discord_Remote_Code.json").exists()


def test_electron_does_not_start_discord_gateway():
    electron_main = repo / "electron" / "main.js"
    assert electron_main.exists()
    content = electron_main.read_text(encoding="utf-8")
    assert "startDiscordBot" not in content


def test_chat_still_has_harness_dispatch():
    web_chat_content = (src_root / "api" / "web_chat_api.py").read_text(encoding="utf-8")
    assert "_run_harness_web_command" in web_chat_content
