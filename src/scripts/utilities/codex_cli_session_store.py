"""
Map Cuttle chat session + working directory → Codex CLI thread UUID for resume,
plus per-chat model / reasoning-effort pins (slash palette).

Stored under <home>/sessions/ (gitignored). Thread-safe.
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



def _map_file() -> Path:
    return runtime_state_path("sessions", "codex_cli_session_map.json")


def _normalize_session_id(cuttle_session_id: Optional[Any]) -> str:
    if cuttle_session_id is None:
        return ""
    return str(cuttle_session_id).strip()


def _session_key(cwd: str, cuttle_session_id: str) -> str:
    cwd_norm = str(Path(cwd).resolve())
    sid = str(cuttle_session_id or "").strip()
    if not sid:
        sid = "_no_session"
    return f"{cwd_norm}\x1f{sid}"


def _model_key(cuttle_session_id: Any) -> str:
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"model\x1f{sid}"


def _effort_key(cuttle_session_id: Any) -> str:
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"effort\x1f{sid}"


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


def _valid_thread_id(raw: Optional[str]) -> Optional[str]:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    if _UUID_RE.match(s):
        return s
    # Some Codex builds use th_… thread ids.
    if s.startswith("th_") and len(s) >= 6:
        return s
    return None


def load_codex_resume_id(cwd: str, cuttle_session_id: Optional[str]) -> Optional[str]:
    """Return Codex thread id for ``exec resume``, or None.

    Prefers the exact cwd key; if missing, scans any stored cwd for this chat
    (Codex turns often run under a project path that differs from the chat chip).
    """
    found = resolve_codex_resume(cwd, cuttle_session_id)
    return found[0] if found else None


def resolve_codex_resume(
    cwd: str, cuttle_session_id: Optional[str]
) -> Optional[Tuple[str, str]]:
    """Return ``(thread_id, stored_cwd)`` for this chat, preferring ``cwd``."""
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return None
    sid = str(cuttle_session_id).strip()
    key = _session_key(cwd, sid)
    with _lock:
        data = _load_all()
    hit = _valid_thread_id(data.get(key) if isinstance(data.get(key), str) else None)
    if hit:
        try:
            return hit, str(Path(cwd).resolve())
        except Exception:
            return hit, str(cwd)
    suffix = f"\x1f{sid}"
    for k, v in data.items():
        if not isinstance(k, str) or not k.endswith(suffix):
            continue
        if k.startswith("model\x1f") or k.startswith("effort\x1f") or k.startswith("ctx\x1f"):
            continue
        tid = _valid_thread_id(v if isinstance(v, str) else None)
        if not tid:
            continue
        stored_cwd = k.split("\x1f", 1)[0] if "\x1f" in k else cwd
        return tid, stored_cwd
    return None


def _context_key(cuttle_session_id: Any) -> str:
    sid = _normalize_session_id(cuttle_session_id) or "_no_session"
    return f"ctx\x1f{sid}"


def save_codex_context_snapshot(
    cuttle_session_id: Optional[Any],
    snapshot: Optional[Dict[str, Any]],
) -> None:
    """Persist last known Codex window occupancy (from app-server)."""
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


def load_codex_context_snapshot(
    cuttle_session_id: Optional[Any],
) -> Optional[Dict[str, Any]]:
    """Return last saved Codex occupancy snapshot, or None."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_context_key(cuttle_session_id))
    return dict(raw) if isinstance(raw, dict) else None


def save_codex_resume_id(
    cwd: str,
    cuttle_session_id: Optional[str],
    codex_thread_id: Optional[str],
) -> None:
    """Persist Codex ``thread_id`` from JSONL ``thread.started`` for future resume."""
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    tid = _valid_thread_id(codex_thread_id)
    if not tid:
        return
    key = _session_key(cwd, str(cuttle_session_id))
    with _lock:
        data = _load_all()
        data[key] = tid
        _write_all(data)


def clear_codex_resume_id(cwd: str, cuttle_session_id: Optional[str]) -> None:
    """Forget the stored Codex thread for this chat+cwd (next /codex starts fresh)."""
    if not cuttle_session_id or not str(cuttle_session_id).strip():
        return
    key = _session_key(cwd, str(cuttle_session_id))
    with _lock:
        data = _load_all()
        if key in data:
            del data[key]
            _write_all(data)


def load_codex_model(cuttle_session_id: Optional[Any]) -> Optional[str]:
    """Model this chat pinned via the slash palette, or None for the CLI default."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_model_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def save_codex_model(
    cuttle_session_id: Optional[Any], model: Optional[str]
) -> Optional[str]:
    """Pin (or, with a falsy model, unpin) the Codex model for this chat."""
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


def load_codex_effort(cuttle_session_id: Optional[Any]) -> Optional[str]:
    """Reasoning effort this chat pinned, or None for the Codex config default."""
    if not _normalize_session_id(cuttle_session_id):
        return None
    with _lock:
        data = _load_all()
    raw = data.get(_effort_key(cuttle_session_id))
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def save_codex_effort(
    cuttle_session_id: Optional[Any], effort: Optional[str]
) -> Optional[str]:
    """Pin (or, with a falsy effort, unpin) Codex ``model_reasoning_effort``."""
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
