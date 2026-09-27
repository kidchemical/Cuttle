"""Retired OS/MCP leftovers must not import as live modules."""

from pathlib import Path
import importlib
import subprocess
import sys

import pytest


def test_core_tool_manager_import_is_gone():
    sys.modules.pop("core.tool_manager", None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core.tool_manager")


def test_agent_tools_import_is_gone():
    sys.modules.pop("agent_tools", None)
    sys.modules.pop("core.agent_tools", None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core.agent_tools")


def test_run_cuttle_mcp_exits_retired():
    script = Path(__file__).resolve().parents[1] / "run_cuttle_mcp.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode != 0
    blob = (proc.stdout or "") + (proc.stderr or "")
    assert "retired" in blob.lower()
