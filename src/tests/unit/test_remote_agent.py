"""
Unit tests for the Discord remote code execution agent (tool-remote-agent).
"""
import os
import sys
from pathlib import Path

# Add src to path
src_root = Path(__file__).resolve().parent.parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))


def test_remote_agent_routing_keywords():
    """Test coding keywords used for routing (mirrors web_chat_api logic)."""
    coding_keywords = [
        'create', 'make', 'add', 'build', 'write', 'edit', 'fix', 'implement',
        'cron', 'job', 'schedule', 'file', 'files', 'folder', 'directory',
        'code', 'script', 'function', 'module', 'package', 'setup',
        'personalized news', 'news', 'automate', 'run', 'execute'
    ]
    msg = "create a cron job system for 8:30am"
    msg_lower = msg.lower()
    looks_like_coding = any(kw in msg_lower for kw in coding_keywords)
    assert looks_like_coding is True
    
    msg2 = "hey what's up"
    looks_chat = any(kw in msg2.lower() for kw in coding_keywords)
    assert looks_chat is False


def test_remote_agent_project_root():
    """Test that Cuttle project root is resolvable (script_dir convention)."""
    # Same convention as web_chat_api
    script_dir = Path(__file__).resolve().parent  # tests/unit
    project_root = script_dir.parent  # tests
    actual_root = project_root.parent  # src
    cuttle_root = actual_root.parent  # Cuttle
    assert cuttle_root.exists()
    assert (cuttle_root / 'src').exists()


def test_remote_agent_tool_exists():
    """tool-remote-agent lives on the chat API, not a graph executor."""
    from api import web_chat_api as wca

    assert hasattr(wca, "_execute_remote_agent_tool")


def test_claude_code_direct_path():
    """ClaudeCodeTool resolves absolute project paths as cwd."""
    from scripts.utilities.claude_code_tool import ClaudeCodeTool

    tool = ClaudeCodeTool(session_id="test_session", model="haiku")
    test_path = str(Path(__file__).resolve().parent)  # tests/unit/

    resolved = tool._resolve_project_path(test_path, None)
    assert resolved is not None
    assert Path(resolved).exists()
    assert Path(resolved).is_absolute()
