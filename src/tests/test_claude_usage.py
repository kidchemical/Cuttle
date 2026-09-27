#!/usr/bin/env python3
"""Offline smoke for Claude Code usage shaping (no live CLI)."""

from __future__ import annotations

import sys
from pathlib import Path

src = Path(__file__).resolve().parents[1]
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from scripts.utilities.claude_cli_tool import (
    ClaudeCodeTool,
    get_claude_code_tool,
    usage_for_query_report,
)


def main() -> int:
    tool = get_claude_code_tool("test_session")
    assert isinstance(tool, ClaudeCodeTool)
    print(f"ClaudeCodeTool available={tool.claude_code_available} session={tool.session_id}")
    shaped = usage_for_query_report(
        {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.01},
        "sonnet",
    )
    assert shaped["input_tokens"] == 100
    assert shaped["output_tokens"] == 20
    assert shaped["total_tokens"] == 120
    print("usage_for_query_report OK:", shaped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
