"""Deprecated import path — use ``scripts.utilities.claude_cli_tool``.

Kept so older pipeline / Discord call sites keep importing
``ClaudeCodeTool`` / ``send_prompt_to_claude_code_sync`` without the
sandbox/WSL hardcoding implementation.
"""

from scripts.utilities.claude_cli_tool import (  # noqa: F401
    ClaudeCliTool,
    ClaudeCodeTool,
    claude_executable,
    get_claude_code_tool,
    send_prompt_to_claude_code,
    send_prompt_to_claude_code_sync,
    usage_for_query_report,
)
