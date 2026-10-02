"""Open local files with desktop associations or reveal them in the file manager."""
from __future__ import annotations

import os
import logging
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

_log = logging.getLogger(__name__)


def desktop_launch_environment() -> Dict[str, str]:
    """Recover stale Xwayland authorization inherited by a persistent daemon.

    Only use a live display server owned by this user, matching DISPLAY. Keep
    valid inherited credentials and all other environment settings unchanged.
    """
    env = os.environ.copy()
    if not sys.platform.startswith("linux"):
        return env
    display = env.get("DISPLAY", "")
    if not re.fullmatch(r":\d+(?:\.\d+)?", display):
        return env
    authority = env.get("XAUTHORITY")
    if authority and Path(authority).is_file():
        return env
    if not authority and (Path.home() / ".Xauthority").is_file():
        return env
    display = display.split(".", 1)[0]
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                argv = proc.joinpath("cmdline").read_bytes().decode().split("\0")
                if Path(argv[0]).name not in ("Xwayland", "Xorg") or display not in argv:
                    continue
                if "-auth" not in argv:
                    continue
                candidate = Path(argv[argv.index("-auth") + 1])
                if candidate.is_file() and candidate.stat().st_uid == os.getuid():
                    env["XAUTHORITY"] = str(candidate)
                    break
            except (OSError, UnicodeError, IndexError):
                continue  # Processes can exit while the directory is being scanned.
    except OSError:
        pass
    return env


def _launch_default_app(argv: list[str]) -> None:
    """Report immediate launcher failures; let long-lived desktop apps continue."""
    proc = subprocess.Popen(
        argv, env=desktop_launch_environment(), stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, close_fds=True,
    )
    try:
        _, stderr = proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        # Some desktop associations run the app in the foreground. Drain its
        # output without blocking the route or stopping the user's application.
        def drain() -> None:
            _, stderr = proc.communicate()
            if proc.returncode:
                _log.warning("Desktop opener exited %s: %s", proc.returncode, (stderr or "")[:1000])
        threading.Thread(target=drain, daemon=True).start()
        return
    if proc.returncode:
        detail = (stderr or "").strip()[:1000] or f"exit code {proc.returncode}"
        raise OSError(f"{argv[0]} could not open the file: {detail}")


def parse_local_path(url_or_path: str) -> str:
    """Turn a file:// URL or absolute path into a normalized filesystem path.

    Raises ValueError for empty / non-local / non-absolute inputs.
    """
    raw = str(url_or_path or "").strip()
    if not raw or "\x00" in raw:
        raise ValueError("path is required")

    # Agents sometimes emit /G:/Dev/... (leading slash before drive).
    if re.match(r"^/[A-Za-z]:[\\/]", raw):
        raw = raw[1:]

    # Windows drive paths look like a URL scheme to a naive parser (E:\…).
    if re.match(r"^[A-Za-z]:[\\/]", raw) or raw.startswith("\\\\"):
        path = os.path.expanduser(raw)
        path = os.path.normpath(path)
        if not os.path.isabs(path):
            raise ValueError("path must be absolute")
        return path

    if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I):
        if not re.match(r"^file:", raw, re.I):
            raise ValueError("only file:// URLs or absolute paths are allowed")
        parsed = urlparse(raw)
        # file:///E:/foo  → path=/E:/foo ; file://localhost/E:/foo
        path = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc.lower() not in ("", "localhost", "127.0.0.1"):
            # UNC: file://server/share/path
            path = "\\\\" + parsed.netloc + path.replace("/", "\\")
        elif sys.platform == "win32" and re.match(r"^/[A-Za-z]:", path):
            # /C:/Projects/... → C:/Projects/...
            path = path[1:]
        elif sys.platform == "win32" and path.startswith("//"):
            path = path.replace("/", "\\")
        path = path.replace("/", os.sep) if sys.platform == "win32" else path
    else:
        path = raw

    path = os.path.expanduser(path)
    # Normalize separators without requiring the file to exist yet
    path = os.path.normpath(path)
    if not os.path.isabs(path):
        raise ValueError("path must be absolute")
    return path


def reveal_in_file_manager(url_or_path: str) -> Dict[str, Any]:
    """Open the OS file manager and select ``url_or_path`` when possible.

    Returns a small result dict. Raises ValueError / FileNotFoundError.
    """
    path = parse_local_path(url_or_path)
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"path does not exist: {path}")

    resolved = str(p.resolve(strict=False))
    selected = p.is_file() or (not p.is_dir())

    if sys.platform == "win32":
        # /select,<path> must be a single argv token; explorer often exits 1 even on success.
        subprocess.Popen(
            ["explorer", f"/select,{resolved}"],
            close_fds=True,
        )
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", resolved], close_fds=True)
    else:
        target = resolved if p.is_dir() else str(p.parent)
        subprocess.Popen(["xdg-open", target], env=desktop_launch_environment(), close_fds=True)
        selected = False

    return {
        "path": resolved,
        "selected": selected,
        "platform": sys.platform,
    }


def open_in_default_app(url_or_path: str) -> Dict[str, Any]:
    """Open a local file with the operating system's registered application."""
    path = parse_local_path(url_or_path)
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"file does not exist: {path}")
    resolved = str(p.resolve(strict=True))
    if sys.platform == "win32":
        os.startfile(resolved)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        _launch_default_app(["open", resolved])
    else:
        _launch_default_app(["xdg-open", resolved])
    return {"path": resolved, "platform": sys.platform}


def reveal_path_or_error(url_or_path: str) -> tuple[Optional[Dict[str, Any]], Optional[str], int]:
    """API helper: (result, error_message, http_status)."""
    try:
        return reveal_in_file_manager(url_or_path), None, 200
    except FileNotFoundError as e:
        return None, str(e), 404
    except ValueError as e:
        return None, str(e), 400
    except OSError as e:
        return None, str(e), 500
