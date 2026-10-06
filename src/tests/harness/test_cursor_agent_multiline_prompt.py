"""Windows agent.cmd %* truncates multiline prompts; Cuttle must spawn node+index."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.utilities.cursor_cli_tool import (
    _resolve_cursor_agent_argv,
    _resolve_cursor_agent_node_entry,
)


def test_cmd_star_truncates_at_newline(tmp_path: Path):
    """Document the Windows bug: .cmd %* drops everything after the first newline."""
    if os.name != "nt":
        pytest.skip("Windows-only %* truncation")

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys, json\nprint(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "wrap.cmd"
    wrapper.write_text(
        f'@echo off\n"{sys.executable}" "{probe}" %*\n',
        encoding="utf-8",
    )
    prompt = "i also said:\n\nFor the insight of running services"
    r = subprocess.run(
        [str(wrapper), "-p", "--force", prompt],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=15,
    )
    assert r.returncode == 0
    # Only the first line survives through %*
    assert '"i also said:"' in r.stdout
    assert "insight" not in r.stdout


def test_direct_createprocess_keeps_newlines(tmp_path: Path):
    """CreateProcess argv list (no .cmd) preserves embedded newlines."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys, json\nprint(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    prompt = "i also said:\n\nFor the insight of running services"
    r = subprocess.run(
        [sys.executable, str(probe), "-p", "--force", prompt],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=15,
    )
    assert r.returncode == 0
    assert "insight of running services" in r.stdout
    assert "\\n\\n" in r.stdout or "\n\n" in r.stdout


def test_resolve_node_entry_picks_latest_version(tmp_path: Path):
    root = tmp_path / "cursor-agent"
    versions = root / "versions"
    old = versions / "2026.01.01-aaaaaaa"
    new = versions / "2026.07.23-bbbbbbb"
    for d in (old, new):
        d.mkdir(parents=True)
        node_name = "node.exe" if os.name == "nt" else "node"
        (d / node_name).write_bytes(b"MZ" if os.name == "nt" else b"#!/bin/sh\n")
        (d / "index.js").write_text("console.log('ok')\n", encoding="utf-8")

    pair = _resolve_cursor_agent_node_entry(root)
    assert pair is not None
    assert pair[0].endswith(str(new / ("node.exe" if os.name == "nt" else "node")))
    assert pair[1].endswith(str(new / "index.js"))


def test_resolve_argv_prefers_real_install():
    argv = _resolve_cursor_agent_argv()
    if argv is None:
        pytest.skip("Cursor Agent not installed on this machine")
    # Must not be a .cmd/.ps1 wrapper (those truncate multiline prompts on Windows)
    for part in argv:
        assert not part.lower().endswith((".cmd", ".bat", ".ps1"))
    assert any(Path(p).name.lower() in ("index.js", "agent", "agent.exe", "cursor-agent", "cursor-agent.exe", "node.exe", "node") for p in argv)
