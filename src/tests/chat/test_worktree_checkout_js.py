"""Worktree checkout client model (chat_project.js / chat_worktrees.js)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_worktree_checkout_client_model():
    script = Path(__file__).with_name("worktree_checkout.cjs")
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "worktree checkout scenarios passed" in result.stdout
