"""Map Cuttle chat sessions to Antigravity conversation IDs."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.runtime_paths import runtime_state_path

_lock = threading.Lock()


def _map_file() -> Path:
    root = Path(__file__).resolve().parents[5]
    return runtime_state_path("sessions", "antigravity_cli_session_map.json",
                              project_root=root, legacy="workspace/antigravity_cli_session_map.json")


def _key(cwd: str, cuttle_session_id: Any) -> str:
    sid = str(cuttle_session_id).strip() if cuttle_session_id is not None else ""
    return f"{Path(cwd).resolve()}\x1f{sid or '_no_session'}"


def _load() -> Dict[str, Any]:
    path = _map_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    path = _map_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_antigravity_resume_id(cwd: str, cuttle_session_id: Optional[str]) -> Optional[str]:
    found = resolve_antigravity_resume(cwd, cuttle_session_id)
    return found[0] if found else None


def resolve_antigravity_resume(
    cwd: str, cuttle_session_id: Optional[str]
) -> Optional[Tuple[str, str]]:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return None
    sid = str(cuttle_session_id).strip()
    with _lock:
        data = _load()
    exact = data.get(_key(cwd, sid))
    if isinstance(exact, str) and exact.strip():
        try:
            return exact.strip(), str(Path(cwd).resolve())
        except Exception:
            return exact.strip(), str(cwd)
    suffix = f"\x1f{sid}"
    for k, v in data.items():
        if not isinstance(k, str) or not k.endswith(suffix):
            continue
        if isinstance(v, str) and v.strip():
            stored = k.split("\x1f", 1)[0] if "\x1f" in k else cwd
            return v.strip(), stored
    return None


def save_antigravity_resume_id(
    cwd: str,
    cuttle_session_id: Optional[str],
    conversation_id: Optional[str],
) -> None:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    if not conversation_id or not str(conversation_id).strip():
        return
    with _lock:
        data = _load()
        data[_key(cwd, cuttle_session_id)] = str(conversation_id).strip()
        _save(data)


def clear_antigravity_resume_id(cwd: str, cuttle_session_id: Optional[str]) -> None:
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    with _lock:
        data = _load()
        if data.pop(_key(cwd, cuttle_session_id), None) is not None:
            _save(data)
