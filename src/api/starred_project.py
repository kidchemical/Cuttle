"""Starred default project — exclusive (at most one) for new Cuttle chats.

Mirrors starred sticky slash agents: the chat palette star used to be
client-only intent; persist in settings.json so LAN clients share the same
default. Starring project B replaces project A.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

SETTINGS_KEY = "starred_project"


def _norm_path(raw: Any) -> str:
    s = str(raw or "").strip().replace("\\", "/")
    while s.endswith("/") and len(s) > 1:
        s = s[:-1]
    return s


def normalize_starred_project(raw: Any) -> Optional[Dict[str, Any]]:
    """Accept a project dict / id+path / path string; return a compact record or None."""
    if raw is None:
        return None
    if isinstance(raw, str):
        path = _norm_path(raw)
        if not path:
            return None
        return {"id": None, "path": path, "name": path.rsplit("/", 1)[-1] or path}
    if not isinstance(raw, dict):
        return None

    path = _norm_path(raw.get("path") or raw.get("project_path") or raw.get("projectPath"))
    name = str(raw.get("name") or raw.get("project_name") or raw.get("projectName") or "").strip()
    pid = raw.get("id")
    if pid is None:
        pid = raw.get("project_id", raw.get("projectId"))
    if pid is not None:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = None

    if not path and pid is None and not name:
        return None
    if not name and path:
        name = path.rsplit("/", 1)[-1] or path
    return {"id": pid, "path": path or "", "name": name or "Project"}


def get_starred_project() -> Optional[Dict[str, Any]]:
    try:
        from managers.settings_manager import get_settings_manager

        raw = get_settings_manager().get_setting(SETTINGS_KEY)
    except Exception:
        return None
    rec = normalize_starred_project(raw)
    if rec and rec.get("path"):
        try:
            from core.runtime_paths import rewrite_windows_lab_path

            rec["path"] = rewrite_windows_lab_path(rec["path"]) or rec["path"]
        except Exception:
            pass
    return rec


def set_starred_project(payload: Any) -> Optional[Dict[str, Any]]:
    """Set or clear the exclusive starred project. Empty / null clears."""
    if payload in (None, "", [], {}):
        normalized = None
    elif isinstance(payload, list):
        # Exclusive: keep only the first usable entry.
        normalized = None
        for item in payload:
            normalized = normalize_starred_project(item)
            if normalized:
                break
    else:
        normalized = normalize_starred_project(payload)

    from managers.settings_manager import get_settings_manager

    get_settings_manager().set_setting(SETTINGS_KEY, normalized)
    return normalized
