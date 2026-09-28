"""Trusted CLI installation for bundled harness connectors."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from api.agent_harness.catalog import get_agent

_ALLOWED_SCRIPT_HOSTS = frozenset({"antigravity.google", "opencode.ai"})
_INSTALL_TIMEOUT = 300.0
_INSTALL_LOCK = threading.Lock()
# Remote install scripts are executed, so bound what we will buffer before
# refusing. Large enough for real vendor installers, small enough to cap abuse.
_MAX_SCRIPT_BYTES = 8 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# npm spec without flags, URLs, or whitespace: [@scope/]name[@version].
_NPM_SPEC_RE = re.compile(
    r"^(?:@[A-Za-z0-9~][A-Za-z0-9._~-]*/)?"
    r"[A-Za-z0-9~][A-Za-z0-9._~-]*"
    r"(?:@[A-Za-z0-9._~^-]+)?$"
)


class _InstallerRefused(RuntimeError):
    """Fail-closed refusal with a machine-readable status (never executed)."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


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


def _validate_npm_spec(package: str) -> str:
    """Reject flag/URL/whitespace specs before they reach the npm argv."""
    spec = (package or "").strip()
    if not spec or not _NPM_SPEC_RE.match(spec):
        raise _InstallerRefused(
            "invalid_installer",
            f"Manifest install_package is not a plain npm spec: {package!r}",
        )
    return spec


def _install_npm(package: str) -> subprocess.CompletedProcess[str]:
    spec = _validate_npm_spec(package)
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if not npm:
        raise RuntimeError("Node.js/npm is required for this installer but was not found.")
    return _run([npm, "install", "--global", spec])


def _download_script(url: str) -> Tuple[bytes, str]:
    """Fetch a script with a size cap; return (payload, sha256 hex)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_SCRIPT_HOSTS:
        raise _InstallerRefused(
            "invalid_installer", f"Installer URL is not allowlisted: {url}"
        )
    chunks: list[bytes] = []
    total = 0
    with urllib.request.urlopen(url, timeout=30) as response:
        while total <= _MAX_SCRIPT_BYTES:
            chunk = response.read(min(65536, _MAX_SCRIPT_BYTES - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    if total > _MAX_SCRIPT_BYTES:
        raise _InstallerRefused(
            "installer_too_large",
            f"Installer exceeds {_MAX_SCRIPT_BYTES} bytes: {url}",
        )
    payload = b"".join(chunks)
    if not payload:
        raise _InstallerRefused("installer_empty", f"Installer is empty: {url}")
    return payload, hashlib.sha256(payload).hexdigest()


def _install_script(
    url: str, expected_sha256: str = ""
) -> Tuple[subprocess.CompletedProcess[str], str, int]:
    """Download, optionally pin-check, then execute. Returns (proc, sha256, bytes)."""
    payload, digest = _download_script(url)
    want = (expected_sha256 or "").strip().lower()
    if want:
        if not _SHA256_RE.match(want):
            raise _InstallerRefused(
                "invalid_installer",
                "Manifest install_sha256 is not a 64-char hex digest.",
            )
        if not hmac.compare_digest(digest, want):
            raise _InstallerRefused(
                "checksum_mismatch",
                f"Installer sha256 {digest} does not match the manifest pin.",
            )
    # No pin declared: proceed (no known-good hashes exist yet) but the digest
    # is returned for audit and future pinning. Residual risk, not silent risk.
    suffix = ".ps1" if os.name == "nt" else ".sh"
    with tempfile.TemporaryDirectory(prefix="cuttle-agent-install-") as tmp:
        script = Path(tmp) / f"install{suffix}"
        script.write_bytes(payload)
        if os.name == "nt":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                raise RuntimeError("PowerShell is required for this installer.")
            proc = _run(
                [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
            )
            return proc, digest, len(payload)
        shell = shutil.which("bash") or shutil.which("sh")
        if not shell:
            raise RuntimeError("A POSIX shell is required for this installer.")
        proc = _run([shell, str(script)])
        return proc, digest, len(payload)


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
    script_digest = ""
    script_bytes = 0
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
                want = (
                    manifest.install_sha256_windows
                    if os.name == "nt"
                    else manifest.install_sha256_posix
                )
                proc, script_digest, script_bytes = _install_script(url, want)
            else:
                raise RuntimeError(f"Unsupported installer kind `{kind}`.")
    except subprocess.TimeoutExpired:
        return _result(False, "install_failed", f"{label} installation timed out.")
    except _InstallerRefused as exc:
        return _result(False, exc.status, f"{label} installer refused: {exc}")
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
    extra: Dict[str, Any] = {"stdout": stdout[-2000:]}
    if script_digest:
        extra["script_sha256"] = script_digest
        extra["script_bytes"] = script_bytes
    return _result(
        True,
        "installed",
        f"{label} CLI installed successfully.",
        **extra,
    )
