"""
Map Cuttle chat session + cwd → OpenCode session id for ``opencode run --session``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from core.runtime_paths import runtime_state_path

_lock = threading.Lock()


def _repo_root() -> Path:
    # .../Cuttle/src/api/agent_harness/agents/opencode/this_file.py -> Cuttle
    return Path(__file__).resolve().parents[5]


def _map_file() -> Path:
    return runtime_state_path("sessions", "opencode_cli_session_map.json", project_root=_repo_root(),
                              legacy="workspace/opencode_cli_session_map.json")


def _session_key(cwd: str, cuttle_session_id: Any) -> str:
    cwd_norm = str(Path(cwd).resolve())
    # The kernel passes the raw DB session id (an int); coerce before stripping.
    sid = str(cuttle_session_id).strip() if cuttle_session_id is not None else ""
    sid = sid or "_no_session"
    return f"{cwd_norm}\x1f{sid}"


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


def load_opencode_resume_id(cwd: str, cuttle_session_id: Optional[str]) -> Optional[str]:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return None
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
    raw = data.get(key)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    return s or None


def save_opencode_resume_id(
    cwd: str,
    cuttle_session_id: Optional[str],
    opencode_session_id: Optional[str],
) -> None:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    if not opencode_session_id or not str(opencode_session_id).strip():
        return
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        data[key] = str(opencode_session_id).strip()
        _write_all(data)


def clear_opencode_resume_id(cwd: str, cuttle_session_id: Optional[str]) -> None:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    key = _session_key(cwd, cuttle_session_id)
    with _lock:
        data = _load_all()
        if key in data:
            del data[key]
            _write_all(data)


def _model_key(cuttle_session_id: Any) -> str:
    """Model preference is per chat, not per directory."""
    sid = str(cuttle_session_id).strip() if cuttle_session_id is not None else ""
    sid = sid or "_no_session"
    return f"model\x1f{sid}"


def load_opencode_model(cuttle_session_id: Optional[Any]) -> Optional[str]:
    """Model this chat pinned via `/opencode model`, or None for the default."""
    if cuttle_session_id is None or not str(cuttle_session_id).strip():
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_model_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def save_opencode_model(cuttle_session_id: Optional[Any], model: Optional[str]) -> Optional[str]:
    """Pin (or, with a falsy model, unpin) the OpenCode model for this chat."""
    if cuttle_session_id is None or not str(cuttle_session_id).strip():
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


def _effort_key(cuttle_session_id: Any) -> str:
    """Reasoning-effort preference is per chat, not per directory."""
    sid = str(cuttle_session_id).strip() if cuttle_session_id is not None else ""
    sid = sid or "_no_session"
    return f"effort\x1f{sid}"


def load_opencode_effort(cuttle_session_id: Optional[Any]) -> Optional[str]:
    """Variant / reasoning effort this chat pinned, or None for the CLI default."""
    if cuttle_session_id is None or not str(cuttle_session_id).strip():
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_effort_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def save_opencode_effort(
    cuttle_session_id: Optional[Any], effort: Optional[str]
) -> Optional[str]:
    """Pin (or, with a falsy effort, unpin) OpenCode ``--variant`` for this chat."""
    if cuttle_session_id is None or not str(cuttle_session_id).strip():
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
