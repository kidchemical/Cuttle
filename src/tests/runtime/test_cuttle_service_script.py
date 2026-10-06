"""Safe checks for `.cuttle/scripts/cuttle-service.sh` (renders only; never installs)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".cuttle" / "scripts" / "cuttle-service.sh"
RESTART = REPO_ROOT / ".cuttle" / "scripts" / "restart-daemon.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="cuttle-service.sh is the Linux systemd helper",
)


def _render(path_env: str) -> str:
    proc = subprocess.run(
        ["bash", str(SCRIPT), "print"],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=30,
        env={"PATH": path_env, "HOME": str(REPO_ROOT / "temp")},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_unit_runs_headless_daemon_from_this_checkout():
    unit = _render("/usr/bin:/bin")
    assert f"WorkingDirectory={REPO_ROOT}" in unit
    assert f'ExecStart="{REPO_ROOT}/start_cuttle.sh" --no-tray --no-ui' in unit
    assert "KillMode=mixed" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit


def test_unit_escapes_systemd_specifiers_in_path():
    unit = _render("/usr/bin:/bin:/opt/odd%dir:/opt/$weird")
    path_line = next(l for l in unit.splitlines() if l.startswith('Environment="PATH='))
    assert "/opt/odd%%dir" in path_line
    assert "/opt/$weird" in path_line


def test_unknown_command_prints_usage_without_side_effects():
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 2
    assert "install" in proc.stdout and "uninstall" in proc.stdout


def test_restart_script_defers_to_systemd_unit():
    src = RESTART.read_text(encoding="utf-8")
    assert "systemctl --user is-active --quiet cuttle.service" in src
    assert src.index("systemctl --user restart cuttle.service") < src.index("pgrep -f")


def test_unit_quotes_checkout_and_path_with_spaces(tmp_path):
    import shutil

    root = tmp_path / 'checkout with spaces%and$dollars'
    script = root / '.cuttle' / 'scripts' / SCRIPT.name
    script.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, script)
    proc = subprocess.run(
        ['bash', str(script), 'print'], capture_output=True, text=True, encoding="utf-8",
        env={'PATH': '/usr/bin:/bin:/opt/CLI tools', 'HOME': str(tmp_path)}, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert f'WorkingDirectory={str(root).replace("%", "%%")}' in proc.stdout
    escaped_exec = str(root).replace('%', '%%').replace('$', '$$')
    assert f'ExecStart="{escaped_exec}/start_cuttle.sh" --no-tray --no-ui' in proc.stdout
    assert 'Environment="PATH=/usr/bin:/bin:/opt/CLI tools"' in proc.stdout
