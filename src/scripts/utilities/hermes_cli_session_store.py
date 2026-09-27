"""
Map Cuttle chat session → Hermes pins (model / effort) and CLI resume ids.

Stored under src/data/workspace/hermes_cli_session_map.json (gitignored).
- model/effort keys are per chat (``model\\x1f{sid}``, ``effort\\x1f{sid}``)
- resume keys are per cwd + chat (``{cwd}\\x1f{sid}``), matching Muse/OpenCode
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()

# Hermes session ids look like ``20260917_044611_6d8cec`` (not UUIDs).
_HERMES_SESSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_.-]{5,128}$")


def _repo_root() -> Path:
    # .../Cuttle/src/scripts/utilities/this_file.py -> Cuttle
    return Path(__file__).resolve().parents[3]


def _map_file() -> Path:
    d = _repo_root() / "src" / "data" / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d / "hermes_cli_session_map.json"


def _normalize_session_id(cuttle_session_id: Optional[Any]) -> str:
    if cuttle_session_id is None:
        return ""
    return str(cuttle_session_id).strip()


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
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _session_key(cwd: str, cuttle_session_id: Any) -> str:
    cwd_norm = str(Path(cwd).resolve())
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"{cwd_norm}\x1f{sid}"


def _model_key(cuttle_session_id: Any) -> str:
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"model\x1f{sid}"


def _effort_key(cuttle_session_id: Any) -> str:
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"effort\x1f{sid}"


from typing import Any, Dict, Optional, Tuple


def resolve_hermes_resume(
    cwd: str, cuttle_session_id: Optional[Any]
) -> Optional[Tuple[str, str]]:
    """Return ``(hermes_session_id, stored_cwd)``, preferring ``cwd`` then any match."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    sid = _normalize_session_id(cuttle_session_id)
    key = _session_key(cwd, sid)
    with _lock:
        data = _load_all()
    raw = data.get(key)
    if isinstance(raw, str) and _HERMES_SESSION_RE.match(raw.strip()):
        try:
            return raw.strip(), str(Path(cwd).resolve())
        except Exception:
            return raw.strip(), str(cwd)
    suffix = f"\x1f{sid}"
    for k, v in data.items():
        if not isinstance(k, str) or not k.endswith(suffix):
            continue
        if k.startswith("model\x1f") or k.startswith("effort\x1f") or k.startswith("ctx\x1f"):
            continue
        if isinstance(v, str) and _HERMES_SESSION_RE.match(v.strip()):
            stored = k.split("\x1f", 1)[0] if "\x1f" in k else cwd
            return v.strip(), stored
    return None


def load_hermes_resume_id(cwd: str, cuttle_session_id: Optional[Any]) -> Optional[str]:
    found = resolve_hermes_resume(cwd, cuttle_session_id)
    return found[0] if found else None


def _context_key(cuttle_session_id: Any) -> str:
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"ctx\x1f{sid}"


def save_hermes_context_snapshot(
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


def load_hermes_context_snapshot(
    cuttle_session_id: Optional[Any],
) -> Optional[Dict[str, Any]]:
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_context_key(cuttle_session_id))
    return dict(raw) if isinstance(raw, dict) else None


def save_hermes_resume_id(
    cwd: str,
    cuttle_session_id: Optional[Any],
    hermes_session_id: Optional[str],
) -> None:
    if not _normalize_session_id(cuttle_session_id):
        return
    if not hermes_session_id or not str(hermes_session_id).strip():
        return
    g = str(hermes_session_id).strip()
    if not _HERMES_SESSION_RE.match(g):
        return
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        data[key] = g
        _write_all(data)


def clear_hermes_resume_id(cwd: str, cuttle_session_id: Optional[Any]) -> None:
    if not _normalize_session_id(cuttle_session_id):
        return
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        if key in data:
            del data[key]
            _write_all(data)


def load_hermes_model(cuttle_session_id: Optional[Any]) -> Optional[str]:
    """Model this chat pinned via the slash palette, or None for the default."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_model_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def save_hermes_model(
    cuttle_session_id: Optional[Any], model: Optional[str]
) -> Optional[str]:
    """Pin (or, with a falsy model, unpin) the Hermes model for this chat."""
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


def load_hermes_effort(cuttle_session_id: Optional[Any]) -> Optional[str]:
    """Reasoning effort this chat pinned, or None for the Hermes config default."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_effort_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def save_hermes_effort(
    cuttle_session_id: Optional[Any], effort: Optional[str]
) -> Optional[str]:
    """Pin (or, with a falsy effort, unpin) Hermes reasoning effort for this chat."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    key = _effort_key(cuttle_session_id)
    e = str(effort or "").strip().lower()
    with _lock:
        data = _load_all()
        if e:
            data[key] = e
        elif key in data:
            del data[key]
        else:
            return None
        _write_all(data)
    return e or None
