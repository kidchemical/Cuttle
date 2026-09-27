"""Unit tests for Claude Code pattern matching + modern tool import."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, List

src_root = Path(__file__).resolve().parents[2]
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))


def test_claude_pattern_matching():
    def match_actions(user_input: str) -> List[Dict[str, str]]:
        user_input = user_input.strip().lower()
        actions = []
        pattern = r'^/claude\s+["\']?(.+?)["\']?$'
        m = re.search(pattern, user_input)
        if m:
            hint = m.group(1).strip() if m.group(1) else None
            action = {"tool": "run_program", "app": "claude_code"}
            if hint:
                action["hint"] = hint
            actions.append(action)
        return actions

    cases = [
        ("/claude Create a Hello World text file", True, "create a hello world text file"),
        ('/claude "Fix TypeScript errors"', True, "fix typescript errors"),
        ("just a regular message", False, None),
        ("/cursor something", False, None),
    ]
    for inp, should, hint in cases:
        result = match_actions(inp)
        if should:
            assert result and result[0]["app"] == "claude_code"
            assert result[0].get("hint") == hint
        else:
            assert not result


def test_claude_tool_import_and_cwd():
    from scripts.utilities.claude_cli_tool import ClaudeCliTool, ClaudeCodeTool
    from scripts.utilities.claude_code_tool import get_claude_code_tool

    tool = get_claude_code_tool("unit")
    assert isinstance(tool, ClaudeCodeTool)
    assert tool.wsl_enabled is False
    assert isinstance(tool.claude_code_available, bool)

    unit = str(Path(__file__).resolve().parent)
    resolved = tool._resolve_project_path(unit)
    assert os.path.isdir(resolved)

    cli = ClaudeCliTool(model="haiku")
    assert cli.model == "haiku"
