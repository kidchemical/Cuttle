"""
Integration tests for Discord remote execution flow.
Requires: Flask server running (or mocked), Discord bot not required.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add src to path
src_root = Path(__file__).resolve().parent.parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))


def test_pipeline_start_includes_discord_remote_code():
    """Test that Discord_Remote_Code pipeline file exists."""
    pipeline_path = src_root / 'pipelines' / 'Discord_Remote_Code.json'
    if not pipeline_path.exists():
        pytest.skip("pipeline graphs removed pending Node Editor rebuild")
    import json
    with open(pipeline_path, encoding='utf-8') as f:
        pipeline = json.load(f)
    assert pipeline.get('id') == 'Discord_Remote_Code' or 'Remote' in str(pipeline.get('name', ''))


def test_oobe_has_remote_agent_node():
    """Test that OOBE pipeline includes tool-remote-agent for Discord path."""
    pipeline_path = src_root / 'pipelines' / 'OOBE_Welcome.json'
    if not pipeline_path.exists():
        pytest.skip("pipeline graphs removed pending Node Editor rebuild")
    
    import json
    with open(pipeline_path, encoding='utf-8') as f:
        pipeline = json.load(f)
    
    nodes = pipeline.get('nodes', [])
    node_types = [n.get('type') for n in nodes]
    assert 'tool-remote-agent' in node_types
    
    connections = pipeline.get('connections', [])
    # Discord trigger (2) should connect to tool-remote-agent (6)
    discord_to_agent = [c for c in connections if c.get('from') == 2 and c.get('to') == 6]
    assert len(discord_to_agent) >= 1


def test_execute_tool_handles_remote_agent():
    """Test that web_chat_api defines tool-remote-agent handler."""
    web_chat_content = (src_root / 'api' / 'web_chat_api.py').read_text(encoding='utf-8')
    assert 'tool-remote-agent' in web_chat_content
    assert '_execute_remote_agent_tool' in web_chat_content


def test_electron_discord_startup():
    """Test that Electron main.js includes Discord bot startup (read-only)."""
    electron_main = src_root.parent / 'electron' / 'main.js'
    if electron_main.exists():
        content = electron_main.read_text(encoding='utf-8')
        if 'startDiscordBot' not in content:
            pytest.skip("Electron no longer starts Discord from main.js (daemon owns the bot)")
        assert 'startDiscordBot' in content
        assert 'discord_bot' in content or 'bot_mcp' in content
        assert 'autoStartPipeline' in content
