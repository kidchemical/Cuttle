"""
Unit tests for Discord integration - bot_mcp API error handling and pipeline trigger flow.
"""
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

import pytest

# Add src to path
src_root = Path(__file__).resolve().parent.parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))


def test_should_ignore_guild_chatter_without_mention():
    """Guild messages that don't @mention / reply to the bot are ignored."""
    from bots.bot_mcp import should_ignore_guild_message, strip_bot_mentions

    me = Mock(id=999)
    guild_msg = Mock()
    guild_msg.guild = Mock(id=1)
    guild_msg.channel = Mock()
    guild_msg.channel.type = Mock(name="text")
    guild_msg.mentions = []
    guild_msg.reference = None
    assert should_ignore_guild_message(guild_msg, me) is True

    guild_msg.mentions = [me]
    assert should_ignore_guild_message(guild_msg, me) is False

    dm = Mock()
    dm.guild = None
    dm.channel = Mock()
    dm.mentions = []
    dm.reference = None
    assert should_ignore_guild_message(dm, me) is False

    assert strip_bot_mentions("<@999> hello", me) == "hello"
    assert strip_bot_mentions("<@!999>", me) == ""


def test_parse_api_error_404_no_agent():
    """Test _parse_api_error returns helpful message for 404 (no slash agent/router hit)."""
    from bots.bot_mcp import _parse_api_error, API_BASE

    mock_resp = Mock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"error": "No agent handled this message."}
    result = _parse_api_error(mock_resp, "Web server")
    assert "/cursor" in result or "agent" in result.lower()
    assert API_BASE in result


def test_parse_api_error_503():
    """Test _parse_api_error for 503 service unavailable."""
    from bots.bot_mcp import _parse_api_error

    mock_resp = Mock()
    mock_resp.status_code = 503
    mock_resp.json.return_value = {"error": "Pipeline functionality not available"}
    result = _parse_api_error(mock_resp, "Web server")
    assert "temporarily unavailable" in result or "503" in result


def test_parse_api_error_custom_response():
    """Test _parse_api_error uses response body when present."""
    from bots.bot_mcp import _parse_api_error

    mock_resp = Mock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"response": "No message provided"}
    result = _parse_api_error(mock_resp, "API")
    assert "No message provided" in result


def test_parse_api_error_uses_error_field():
    """Test _parse_api_error uses error field when response empty."""
    from bots.bot_mcp import _parse_api_error

    mock_resp = Mock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"error": "Internal server error"}
    result = _parse_api_error(mock_resp, "API")
    assert "Internal server error" in result


def test_parse_api_error_non_json_fallback():
    """Test _parse_api_error falls back to HTTP status when JSON parse fails."""
    from bots.bot_mcp import _parse_api_error, API_BASE

    mock_resp = Mock()
    mock_resp.status_code = 502
    mock_resp.json.side_effect = ValueError("Invalid JSON")
    result = _parse_api_error(mock_resp, "Web server")
    assert "502" in result
    assert API_BASE in result


def test_api_base_format():
    """Test API_BASE is a valid URL with port 8080."""
    from bots.bot_mcp import API_BASE

    assert "8080" in API_BASE
    assert API_BASE.startswith("http")
    assert "127.0.0.1" in API_BASE or "localhost" in API_BASE


def test_pipeline_trigger_discord_endpoint_exists():
    """Test web_chat_api defines pipeline-trigger-discord endpoint."""
    web_api = (src_root / "api" / "web_chat_api.py").read_text(encoding="utf-8")
    assert "/api/pipeline-trigger-discord" in web_api
    assert "process_message_with_bot" in web_api


def test_bot_mcp_posts_to_pipeline_trigger():
    """Test bot_mcp uses pipeline-trigger-discord API."""
    bot_content = (src_root / "bots" / "bot_mcp.py").read_text(encoding="utf-8")
    assert "pipeline-trigger-discord" in bot_content
    assert "API_BASE" in bot_content or "127.0.0.1" in bot_content
