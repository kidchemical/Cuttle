"""Trusted CLI installation for bundled harness connectors."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from api.agent_harness.catalog import get_agent

_ALLOWED_SCRIPT_HOSTS = frozenset({"antigravity.google", "opencode.ai"})
_INSTALL_TIMEOUT = 300.0
_INSTALL_LOCK = threading.Lock()


def _result(success: bool, status: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": success, "status": status, "message": message, **extra}


def _refresh_windows_path() -> None:
    """Merge current user/machine PATH after an installer updates the registry."""
    if os.name != "nt":
        return
    try:
        import winreg

        paths = [os.environ.get("PATH", "")]
        locations = (
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        )
        for hive, key_name in locations:
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    value, _kind = winreg.QueryValueEx(key, "Path")
                    if value:
                        paths.append(os.path.expandvars(str(value)))
            except OSError:
                pass
        merged = []
        seen = set()
        for raw in paths:
            for item in raw.split(os.pathsep):
                item = item.strip()
                key = os.path.normcase(item)
                if item and key not in seen:
                    seen.add(key)
                    merged.append(item)
        os.environ["PATH"] = os.pathsep.join(merged)
    except Exception:
        pass


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_INSTALL_TIMEOUT,
        check=False,
    )


def _install_npm(package: str) -> subprocess.CompletedProcess[str]:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if not npm:
        raise RuntimeError("Node.js/npm is required for this installer but was not found.")
    return _run([npm, "install", "--global", package])


def _install_script(url: str) -> subprocess.CompletedProcess[str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_SCRIPT_HOSTS:
        raise RuntimeError(f"Installer URL is not allowlisted: {url}")
    suffix = ".ps1" if os.name == "nt" else ".sh"
    with tempfile.TemporaryDirectory(prefix="cuttle-agent-install-") as tmp:
        script = Path(tmp) / f"install{suffix}"
        with urllib.request.urlopen(url, timeout=30) as response:
            script.write_bytes(response.read())
        if os.name == "nt":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                raise RuntimeError("PowerShell is required for this installer.")
            return _run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
            )
        shell = shutil.which("bash") or shutil.which("sh")
        if not shell:
            raise RuntimeError("A POSIX shell is required for this installer.")
        return _run([shell, str(script)])


def install_agent_cli(
    agent_id: str,
    *,
    project_path: Optional[str] = None,
    automatic: bool = False,
) -> Dict[str, Any]:
    """Install one bundled connector's CLI from its declarative manifest."""
    pair = get_agent(agent_id, project_path=project_path)
    if not pair:
        return _result(False, "unknown_agent", f"Unknown harness agent `{agent_id}`.")
    manifest, adapter = pair
    label = manifest.label or manifest.id
    try:
        if adapter.available():
            return _result(True, "already_ready", f"{label} CLI is already installed.")
    except Exception:
        pass
    if manifest.source != "bundled":
        return _result(
            False,
            "untrusted_installer",
            "Cuttle does not execute installers from project or user drop-ins.",
        )
    if automatic and not manifest.auto_install:
        return _result(
            False,
            "confirmation_required",
            f"{label} requires an explicit install request.",
        )
    kind = (manifest.install_kind or "").strip().lower()
    if not kind:
        return _result(
            False,
            "not_installable",
            manifest.install_hint or f"{label} has no automated installer.",
        )
    try:
        with _INSTALL_LOCK:
            # Another request may have completed while this one waited.
            if adapter.available():
                return _result(True, "already_ready", f"{label} CLI is already installed.")
            if kind == "npm_global":
                if not manifest.install_package:
                    raise RuntimeError("Manifest is missing install_package.")
                proc = _install_npm(manifest.install_package)
            elif kind == "script_url":
                url = manifest.install_url_windows if os.name == "nt" else manifest.install_url_posix
                if not url:
                    raise RuntimeError(f"No installer is declared for {os.name}.")
                proc = _install_script(url)
            else:
                raise RuntimeError(f"Unsupported installer kind `{kind}`.")
    except subprocess.TimeoutExpired:
        return _result(False, "install_failed", f"{label} installation timed out.")
    except Exception as exc:
        return _result(False, "install_failed", f"{label} installation failed: {exc}")

    _refresh_windows_path()
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = (stderr or stdout or f"exit {proc.returncode}")[-2000:]
        return _result(
            False,
            "install_failed",
            f"{label} installer failed (exit {proc.returncode}): {detail}",
        )
    try:
        ready = bool(adapter.available())
    except Exception:
        ready = False
    if not ready:
        return _result(
            False,
            "path_not_ready",
            f"{label} was installed, but its executable is not visible to Cuttle yet.",
        )
    return _result(
        True,
        "installed",
        f"{label} CLI installed successfully.",
        stdout=stdout[-2000:],
    )
