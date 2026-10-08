"""Behavioral regressions at production preference/mutation/broker seams."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is unavailable')
def test_ui_state_races_and_failure_recovery():
    result = subprocess.run(['node', str(Path(__file__).with_name('ui_state_sync.cjs'))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '17 regression scenarios passed' in result.stdout
