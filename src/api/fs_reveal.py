"""Reveal a local path in the OS file manager (Explorer / Finder).

Used by chat file chips (file://) so a click opens the folder with the
item selected, instead of navigating to a blocked file:// URL.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse


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
        subprocess.Popen(["xdg-open", target], close_fds=True)
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
        subprocess.Popen(["open", resolved], close_fds=True)
    else:
        subprocess.Popen(["xdg-open", resolved], close_fds=True)
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
