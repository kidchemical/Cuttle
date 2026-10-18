"""Guided setup flow with fake installers; no downloads or live services."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import setup_cuttle as setup


@pytest.fixture
def install(monkeypatch, tmp_path):
    root = tmp_path / "checkout with spaces"
    (root / "electron").mkdir(parents=True)
    (root / "electron" / "package.json").write_text(json.dumps({"engines": {"node": ">=22.12.0"}}))
    calls = []
    monkeypatch.setattr(setup.shutil, "which", lambda name: name)
    monkeypatch.setattr(setup.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(setup.sys, "maxsize", 2**63 - 1)

    def run(argv, cwd, *, capture=False):
        calls.append((argv, cwd))
        if argv == ["node.exe", "--version"]:
            return "v22.12.0"
        if "venv" in argv:
            python = root / ".venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
        if argv == ["npm.cmd", "ci"]:
            installer = root / "electron/node_modules/electron/install.js"
            installer.parent.mkdir(parents=True, exist_ok=True)
            installer.touch()
        if argv[0] == "node.exe" and argv[-1].endswith("install.js"):
            binary = root / "electron/node_modules/electron/dist/electron.exe"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.touch()
        return ""

    monkeypatch.setattr(setup, "run", run)
    monkeypatch.setattr(setup, "current_platform", lambda: setup.WINDOWS)
    return root, calls, run


def test_fresh_setup_installs_then_launches_explicit_host(install):
    root, calls, _ = install
    setup.setup(root, browser=False, no_launch=False, launch_only=False)
    commands = [argv for argv, _ in calls]
    assert "venv" in commands[1]
    assert commands[2][1:4] == ["-m", "pip", "install"]
    assert commands[3][1:] == ["-m", "pip", "check"]
    assert commands[4] == ["npm.cmd", "ci"]
    assert commands[5][-1].endswith("install.js")
    assert commands[6] == [str(root / "electron/node_modules/electron/dist/electron.exe"), str(root / "electron"), "--mode=host"]
    assert calls[4][1] == root / "electron"
    assert not any("cuttle_daemon.py" in arg for argv in commands for arg in argv)
    assert not (root / ".env").exists()


def test_install_only_and_repeat_preserve_existing_environment(install):
    root, calls, _ = install
    setup.setup(root, browser=False, no_launch=True, launch_only=False)
    calls.clear()
    sentinel = root / ".venv" / "keep-me"
    sentinel.write_text("existing")
    setup.setup(root, browser=False, no_launch=True, launch_only=False)
    assert sentinel.read_text() == "existing"
    assert not any("venv" in argv for argv, _ in calls)
    assert calls[-1][0][-1].endswith("install.js")


def test_browser_needs_no_node_and_delegates_to_daemon(install, monkeypatch):
    root, calls, _ = install
    monkeypatch.setattr(setup.shutil, "which", lambda _: pytest.fail("Browser queried Node"))
    setup.setup(root, browser=True, no_launch=False, launch_only=False)
    assert calls[-1][0] == [str(root / ".venv/Scripts/python.exe"), str(root / "src/scripts/cuttle_daemon.py"), "--browser"]
    assert not any(argv[0] in ("node.exe", "npm.cmd") for argv, _ in calls)


@pytest.mark.parametrize("version", ["v18.20.0", "v22.11.0", "invalid"])
def test_old_node_fails_before_install(install, monkeypatch, version):
    root, calls, _ = install
    monkeypatch.setattr(setup, "run", lambda *args, **kwargs: version)
    with pytest.raises(RuntimeError, match="Node.js >=22.12.0"):
        setup.setup(root, browser=False, no_launch=True, launch_only=False)
    assert not (root / ".venv").exists()


@pytest.mark.parametrize("missing", ["node.exe", "npm.cmd"])
def test_missing_desktop_tool_offers_browser(install, monkeypatch, missing):
    root, calls, _ = install
    monkeypatch.setattr(setup.shutil, "which", lambda name: None if name == missing else name)
    with pytest.raises(RuntimeError, match="-Browser"):
        setup.setup(root, browser=False, no_launch=True, launch_only=False)
    assert calls == []


@pytest.mark.parametrize("version,bits", [((3, 10), 2**63 - 1), ((3, 12), 2**31 - 1)])
def test_unsupported_python_fails_before_commands(install, monkeypatch, version, bits):
    root, calls, _ = install
    monkeypatch.setattr(setup.sys, "version_info", version)
    monkeypatch.setattr(setup.sys, "maxsize", bits)
    with pytest.raises(RuntimeError, match="64-bit Python 3.11"):
        setup.setup(root, browser=False, no_launch=True, launch_only=False)
    assert calls == []


def test_launch_only_requires_venv(install):
    root, calls, _ = install
    with pytest.raises(RuntimeError, match="setup_windows.ps1 first"):
        setup.setup(root, browser=False, no_launch=False, launch_only=True)
    assert not any("ci" in argv for argv, _ in calls)


@pytest.mark.parametrize("partial", [False, True])
def test_launch_only_repairs_partial_desktop_without_reinstalling_python(install, partial):
    root, calls, _ = install
    python = root / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    if partial:
        installer = root / "electron/node_modules/electron/install.js"
        installer.parent.mkdir(parents=True)
        installer.touch()
    setup.setup(root, browser=False, no_launch=False, launch_only=True)
    assert not any("pip" in argv or "venv" in argv for argv, _ in calls)
    assert any(argv == ["npm.cmd", "ci"] for argv, _ in calls) is not partial
    assert calls[-1][0][-1] == "--mode=host"


@pytest.mark.parametrize("failure", ["pip", "ci", "install.js"])
def test_install_errors_prevent_launch(install, monkeypatch, failure):
    root, calls, fake_run = install

    def run(argv, cwd, **kwargs):
        if any(failure in arg for arg in argv):
            raise subprocess.CalledProcessError(9, argv)
        return fake_run(argv, cwd, **kwargs)

    monkeypatch.setattr(setup, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        setup.setup(root, browser=False, no_launch=False, launch_only=False)
    assert not any("--mode=host" in argv for argv, _ in calls)


def test_missing_binary_after_download_prevents_launch(install, monkeypatch):
    root, calls, fake_run = install

    def run(argv, cwd, **kwargs):
        if argv[-1].endswith("install.js"):
            return ""
        return fake_run(argv, cwd, **kwargs)

    monkeypatch.setattr(setup, "run", run)
    with pytest.raises(RuntimeError, match="did not produce electron.exe"):
        setup.setup(root, browser=False, no_launch=False, launch_only=False)


def test_command_runner_propagates_failure_and_handles_spaces(tmp_path):
    root = tmp_path / "path with spaces"
    root.mkdir()
    assert setup.run([sys.executable, "-c", "print('v22.12.0')"], root, capture=True) == "v22.12.0"
    with pytest.raises(subprocess.CalledProcessError):
        setup.run([sys.executable, "-c", "raise SystemExit(9)"], root)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_powershell_entry_passes_options_and_exit_code(tmp_path, exit_code):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is required")
    entry = tmp_path / "setup_windows.ps1"
    shutil.copyfile(setup.ROOT / "setup_windows.ps1", entry)
    # Fake py.exe is a PowerShell function. No interpreter/install is launched.
    script = """
$ErrorActionPreference = 'Stop'
function global:py.exe {
    if (($args -join '|') -notmatch '-3\\|.*setup_cuttle.py\\|--browser\\|--no-launch') { throw 'Incorrect setup arguments' }
    Write-Output 'setup helper invoked'
    $global:LASTEXITCODE = __EXIT_CODE__
}
& $args[0] -Browser -NoLaunch
# A nested script's exit sets LASTEXITCODE; forward it from this outer -File script.
exit $LASTEXITCODE
""".replace("__EXIT_CODE__", str(exit_code))
    wrapper = tmp_path / "test.ps1"
    wrapper.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), str(entry)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert "setup helper invoked" in result.stdout


@pytest.fixture
def linux_install(monkeypatch, tmp_path):
    root = tmp_path / "checkout with spaces"
    (root / "electron").mkdir(parents=True)
    (root / "electron" / "package.json").write_text(json.dumps({"engines": {"node": ">=22.12.0"}}))
    calls = []
    monkeypatch.setattr(setup.shutil, "which", lambda name: name)
    monkeypatch.setattr(setup.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(setup.sys, "maxsize", 2**63 - 1)
    monkeypatch.setattr(setup, "current_platform", lambda: setup.LINUX)
    monkeypatch.setattr(setup, "sandbox_problem", lambda binary: "")

    def run(argv, cwd, *, capture=False):
        calls.append((argv, cwd))
        if argv == ["node", "--version"]:
            return "v22.12.0"
        if "venv" in argv:
            python = root / ".venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.touch()
        if argv == ["npm", "ci"]:
            installer = root / "electron/node_modules/electron/install.js"
            installer.parent.mkdir(parents=True, exist_ok=True)
            installer.touch()
        if argv[0] == "node" and argv[-1].endswith("install.js"):
            binary = root / "electron/node_modules/electron/dist/electron"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.touch()
        return ""

    monkeypatch.setattr(setup, "run", run)
    return root, calls, run


def test_linux_fresh_setup_launches_host_through_launcher(linux_install):
    root, calls, _ = linux_install
    setup.setup(root, browser=False, no_launch=False, launch_only=False)
    commands = [argv for argv, _ in calls]
    assert commands[2][0] == str(root / ".venv/bin/python")
    assert ["npm", "ci"] in commands
    assert commands[-1] == [str(root / ".cuttle/scripts/launch-cuttle-host.sh")]
    assert not (root / ".env").exists()


def test_linux_browser_delegates_to_daemon(linux_install):
    root, calls, _ = linux_install
    setup.setup(root, browser=True, no_launch=False, launch_only=False)
    assert calls[-1][0] == [str(root / ".venv/bin/python"), str(root / "src/scripts/cuttle_daemon.py"), "--browser"]


def test_linux_missing_node_offers_browser_flag(linux_install, monkeypatch):
    root, calls, _ = linux_install
    monkeypatch.setattr(setup.shutil, "which", lambda name: None if name == "node" else name)
    with pytest.raises(RuntimeError, match="--browser"):
        setup.setup(root, browser=False, no_launch=True, launch_only=False)
    assert calls == []


def test_linux_unusable_sandbox_blocks_launch_but_not_install(linux_install, monkeypatch):
    root, calls, _ = linux_install
    monkeypatch.setattr(setup, "sandbox_problem", lambda binary: "sudo chmod 4755 helper")
    setup.setup(root, browser=False, no_launch=True, launch_only=False)
    with pytest.raises(RuntimeError, match="sudo chmod 4755"):
        setup.setup(root, browser=False, no_launch=False, launch_only=False)
    assert not any("launch-cuttle-host.sh" in argv[0] for argv, _ in calls)


def test_linux_failed_venv_removes_partial_env_and_names_apt_package(linux_install, monkeypatch):
    root, calls, fake_run = linux_install

    def run(argv, cwd, **kwargs):
        if "venv" in argv:
            (root / ".venv" / "bin").mkdir(parents=True)
            raise subprocess.CalledProcessError(1, argv)
        return fake_run(argv, cwd, **kwargs)

    monkeypatch.setattr(setup, "run", run)
    with pytest.raises(RuntimeError, match=r"apt install python3\.12-venv"):
        setup.setup(root, browser=True, no_launch=True, launch_only=False)
    assert not (root / ".venv").exists()


def test_sandbox_problem_follows_kernel_restrictions_and_opt_out(tmp_path, monkeypatch):
    binary = tmp_path / "dist" / "electron"
    binary.parent.mkdir()
    (binary.parent / "chrome-sandbox").touch()
    flags = {"/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "1"}
    monkeypatch.setattr(setup, "_read_flag", lambda path, default: flags.get(path, default))
    monkeypatch.delenv("CUTTLE_ELECTRON_NO_SANDBOX", raising=False)
    monkeypatch.setattr(setup, "_nosuid", lambda path: False)
    assert "sudo chown root:root" in setup.sandbox_problem(binary)
    monkeypatch.setattr(setup, "_nosuid", lambda path: True)
    assert "nosuid filesystem" in setup.sandbox_problem(binary)
    assert "sudo" not in setup.sandbox_problem(binary)
    monkeypatch.setenv("CUTTLE_ELECTRON_NO_SANDBOX", "1")
    assert setup.sandbox_problem(binary) == ""
    monkeypatch.delenv("CUTTLE_ELECTRON_NO_SANDBOX")
    flags.clear()
    assert setup.sandbox_problem(binary) == ""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell entry")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_shell_entry_passes_options_and_exit_code(tmp_path, exit_code):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required")
    root = tmp_path / "checkout with spaces"
    (root / "src" / "scripts").mkdir(parents=True)
    shutil.copyfile(setup.ROOT / "setup_linux.sh", root / "setup_linux.sh")
    # Fake setup helper: no install runs.
    (root / "src" / "scripts" / "setup_cuttle.py").write_text(
        f"import sys\nprint('setup helper invoked', sys.argv[1:])\nraise SystemExit({exit_code})\n"
    )
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    result = subprocess.run(
        [bash, str(root / "setup_linux.sh"), "--browser", "--no-launch"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert "setup helper invoked ['--browser', '--no-launch']" in result.stdout
