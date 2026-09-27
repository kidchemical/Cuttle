"""Small HTTPS GET helper for public JSON feeds."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "Cuttle-Dashboards/1.0 (+https://github.com/cuttle)"
DEFAULT_TIMEOUT = 20
MAX_BODY_BYTES = 16 * 1024 * 1024


def get_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BODY_BYTES + 1)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc
    if len(raw) > MAX_BODY_BYTES:
        raise RuntimeError(f"GET {url}: body exceeded {MAX_BODY_BYTES} bytes")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GET {url}: invalid JSON") from exc


def get_text(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain, */*"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BODY_BYTES + 1)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc
    if len(raw) > MAX_BODY_BYTES:
        raise RuntimeError(f"GET {url}: body exceeded {MAX_BODY_BYTES} bytes")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"GET {url}: invalid UTF-8 text") from exc
