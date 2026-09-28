"""Inbound Discord gateway is not part of Cuttle Core."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"


def test_discord_bot_module_absent():
    assert not (SRC / "bots" / "discord_bot.py").exists()
    assert not (SRC / "api" / "discord_chat_bridge.py").exists()


def test_daemon_does_not_spawn_discord_bot():
    text = (SRC / "scripts" / "cuttle_daemon.py").read_text(encoding="utf-8")
    assert "discord_bot.py" not in text
    assert "start_discord_bot" not in text


def test_flask_has_no_pipeline_trigger_discord_route():
    from api.web_chat_api import app

    rules = [rule.rule for rule in app.url_map.iter_rules()]
    assert not any("pipeline-trigger-discord" in r for r in rules)
    assert not any(r.rstrip("/") == "/api/bot-status" for r in rules)


def test_token_does_not_imply_gateway_in_discord_ops_docs():
    token_mod = (SRC / "api" / "discord_ops" / "token.py").read_text(encoding="utf-8")
    assert "Does not start a gateway" in token_mod
