"""Laptop Client updates preserve their Host address and Electron profile."""
from pathlib import Path
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required')
def test_client_restore_and_update_paths():
    result = subprocess.run(
        ['node', str(REPO / 'electron/tests/client-reconnect.cjs')],
        cwd=REPO, capture_output=True, text=True, encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
