#!/usr/bin/env python3
"""Retired entrypoint — Cuttle no longer hosts an MCP tool server.

Agents: ``python -m api.<module>`` (``.cuttle/docs/agent-ops-cli.md``).
Guest harnesses (Cursor/Codex) keep their own MCP configs.
"""
raise SystemExit(
    "run_cuttle_mcp.py is retired. Cuttle does not start an MCP tool server. "
    "Use python -m api.* for Cuttle-owned agent ops (.cuttle/docs/agent-ops-cli.md)."
)
