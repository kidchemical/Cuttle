"""
Map Cuttle chat session + working directory → Claude Code session UUID for resume,
plus per-chat model pins.

Stored under src/data/sessions/ (gitignored). Thread-safe.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.runtime_paths import runtime_state_path

_lock = threading.Lock()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _map_file() -> Path:
    return runtime_state_path("sessions", "claude_cli_session_map.json", project_root=_repo_root(),
                              legacy="workspace/claude_cli_session_map.json")


def _normalize_session_id(cuttle_session_id: Optional[Any]) -> str:
    if cuttle_session_id is None:
        return ""
    return str(cuttle_session_id).strip()


def _session_key(cwd: str, cuttle_session_id: str) -> str:
    cwd_norm = str(Path(cwd).resolve())
    sid = str(cuttle_session_id or "").strip() or "_no_session"
    return f"{cwd_norm}\x1f{sid}"


def _model_key(cuttle_session_id: Any) -> str:
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"model\x1f{sid}"


def _load_all() -> Dict[str, Any]:
    path = _map_file()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_all(data: Dict[str, Any]) -> None:
    path = _map_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _valid_session_id(raw: Optional[str]) -> Optional[str]:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    if _UUID_RE.match(s):
        return s
    # Named sessions / short ids Claude accepts with --resume.
    if 4 <= len(s) <= 128 and not any(c in s for c in "\n\r\t"):
        return s
    return None


def load_claude_resume_id(cwd: str, cuttle_session_id: Optional[Any]) -> Optional[str]:
    found = resolve_claude_resume(cwd, cuttle_session_id)
    return found[0] if found else None


def resolve_claude_resume(
    cwd: str, cuttle_session_id: Optional[Any]
) -> Optional[Tuple[str, str]]:
    """Return ``(claude_session_id, stored_cwd)``, preferring ``cwd``."""
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return None
    sid = str(cuttle_session_id).strip()
    key = _session_key(cwd, sid)
    with _lock:
        data = _load_all()
    hit = _valid_session_id(data.get(key) if isinstance(data.get(key), str) else None)
    if hit:
        try:
            return hit, str(Path(cwd).resolve())
        except Exception:
            return hit, str(cwd)
    suffix = f"\x1f{sid}"
    for k, v in data.items():
        if not isinstance(k, str) or not k.endswith(suffix):
            continue
        if k.startswith("model\x1f") or k.startswith("ctx\x1f") or k.startswith("effort\x1f"):
            continue
        tid = _valid_session_id(v if isinstance(v, str) else None)
        if tid:
            stored = k.split("\x1f", 1)[0] if "\x1f" in k else cwd
            return tid, stored
    return None


def _context_key(cuttle_session_id: Any) -> str:
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"ctx\x1f{sid}"


def save_claude_context_snapshot(
    cuttle_session_id: Optional[Any], snapshot: Optional[Dict[str, Any]]
) -> None:
    if not _normalize_session_id(cuttle_session_id):
        return
    key = _context_key(cuttle_session_id)
    with _lock:
        data = _load_all()
        if not snapshot:
            if key in data:
                del data[key]
                _write_all(data)
            return
        data[key] = dict(snapshot)
        _write_all(data)


def load_claude_context_snapshot(
    cuttle_session_id: Optional[Any],
) -> Optional[Dict[str, Any]]:
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_context_key(cuttle_session_id))
    return dict(raw) if isinstance(raw, dict) else None


def save_claude_resume_id(
    cwd: str,
    cuttle_session_id: Optional[Any],
    claude_session_id: Optional[str],
) -> None:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    sid = _valid_session_id(claude_session_id)
    if not sid:
        return
    key = _session_key(cwd, str(cuttle_session_id))
    with _lock:
        data = _load_all()
        data[key] = sid
        _write_all(data)


def clear_claude_resume_id(cwd: str, cuttle_session_id: Optional[Any]) -> None:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    key = _session_key(cwd, str(cuttle_session_id))
    with _lock:
        data = _load_all()
        if key in data:
            del data[key]
            _write_all(data)


def load_claude_model(cuttle_session_id: Optional[Any]) -> Optional[str]:
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_model_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def save_claude_model(
    cuttle_session_id: Optional[Any], model: Optional[str]
) -> Optional[str]:
    if not _normalize_session_id(cuttle_session_id):
        return None
    key = _model_key(cuttle_session_id)
    m = str(model or "").strip()
    with _lock:
        data = _load_all()
        if m:
            data[key] = m
        elif key in data:
            del data[key]
        else:
            return None
        _write_all(data)
    return m or None


def load_claude_effort(cuttle_session_id: Optional[Any]) -> Optional[str]:
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        value = _load_all().get("effort\x1f" + _normalize_session_id(cuttle_session_id))
    return value.strip().lower() or None if isinstance(value, str) else None


def save_claude_effort(cuttle_session_id: Optional[Any], effort: Optional[str]) -> Optional[str]:
    if not _normalize_session_id(cuttle_session_id):
        return None
    key = "effort\x1f" + _normalize_session_id(cuttle_session_id)
    value = str(effort or "").strip().lower()
    with _lock:
        data = _load_all()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        _write_all(data)
    return value or None
