"""Context delta — lightweight updates on resumed agent sessions.

Full Context Compiler envelope on fresh sessions; on resume, send only what changed
(global/project rules, new docs/actions) so agents stay current without re-injecting
the entire briefing every turn.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from api.cuttle_brain.context_compiler import (
    CONTEXT_SCHEMA_VERSION,
    _USER_REQUEST_HEADER,
    _cuttle_global_config,
    load_global_rules,
    load_project_rules,
    project_inventory,
)

_OPEN = "<cuttle_context>"
_CLOSE = "</cuttle_context>"

_DELTA_PREAMBLE = (
    "Context delta (not the user speaking). Apply these updates; do not narrate them. "
    f"Answer only the user request after {_CLOSE}."
)

_lock = threading.Lock()
_MAX_RULE_CHARS = 3500
_MAX_DELTA_CHARS = 6000


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _map_file() -> Path:
    d = _repo_root() / "src" / "data" / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d / "context_inject_snapshots.json"


def _sid_key(chat_session_id: Any) -> Optional[str]:
    if chat_session_id is None:
        return None
    s = str(chat_session_id).strip()
    return s or None


def _store_key(chat_session_id: Any, agent_id: str, project_path: str) -> Optional[str]:
    sid = _sid_key(chat_session_id)
    aid = (agent_id or "").strip()
    path = (project_path or "").strip()
    if not sid or not aid or not path:
        return None
    try:
        norm = str(Path(path).resolve())
    except OSError:
        norm = path
    return f"{sid}|{aid}|{norm}"


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _rules_map(rules: List[Tuple[str, str]]) -> Dict[str, str]:
    return {name: _hash_text(text) for name, text in rules}


@dataclass(frozen=True)
class ContextSnapshot:
    schema_version: int
    global_rules: Dict[str, str]
    project_rules: Dict[str, str]
    global_docs: Tuple[str, ...]
    project_docs: Tuple[str, ...]
    project_actions: Tuple[str, ...]
    project_commands: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "global_rules": dict(self.global_rules),
            "project_rules": dict(self.project_rules),
            "global_docs": list(self.global_docs),
            "project_docs": list(self.project_docs),
            "project_actions": list(self.project_actions),
            "project_commands": list(self.project_commands),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> ContextSnapshot:
        return cls(
            schema_version=int(raw.get("schema_version") or 0),
            global_rules=dict(raw.get("global_rules") or {}),
            project_rules=dict(raw.get("project_rules") or {}),
            global_docs=tuple(raw.get("global_docs") or ()),
            project_docs=tuple(raw.get("project_docs") or ()),
            project_actions=tuple(raw.get("project_actions") or ()),
            project_commands=tuple(raw.get("project_commands") or ()),
        )


def compute_snapshot(project_path: Optional[str]) -> ContextSnapshot:
    inv = project_inventory(project_path)
    global_config = _cuttle_global_config()
    global_docs: List[str] = []
    if global_config:
        docs_dir = global_config / "docs"
        if docs_dir.is_dir():
            global_docs = sorted(p.name for p in docs_dir.glob("*.md"))
    return ContextSnapshot(
        schema_version=CONTEXT_SCHEMA_VERSION,
        global_rules=_rules_map(load_global_rules()),
        project_rules=_rules_map(load_project_rules(project_path)),
        global_docs=tuple(global_docs),
        project_docs=tuple(sorted(inv.get("docs") or [])),
        project_actions=tuple(sorted(inv.get("actions") or [])),
        project_commands=tuple(sorted(Path(n).stem for n in (inv.get("commands") or []))),
    )


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


def load_injected_snapshot(
    chat_session_id: Any,
    agent_id: str,
    project_path: str,
) -> Optional[ContextSnapshot]:
    key = _store_key(chat_session_id, agent_id, project_path)
    if not key:
        return None
    with _lock:
        raw = _load_all().get(key)
    if not isinstance(raw, dict):
        return None
    snap = raw.get("snapshot")
    if not isinstance(snap, dict):
        return None
    try:
        return ContextSnapshot.from_dict(snap)
    except (TypeError, ValueError):
        return None


def record_injected_snapshot(
    chat_session_id: Any,
    agent_id: str,
    project_path: str,
) -> None:
    key = _store_key(chat_session_id, agent_id, project_path)
    if not key:
        return
    snap = compute_snapshot(project_path)
    with _lock:
        data = _load_all()
        data[key] = {"snapshot": snap.to_dict()}
        _write_all(data)


def clear_injected_snapshot(
    chat_session_id: Any,
    agent_id: Optional[str] = None,
    project_path: Optional[str] = None,
) -> None:
    sid = _sid_key(chat_session_id)
    if not sid:
        return
    with _lock:
        data = _load_all()
        if agent_id and project_path:
            key = _store_key(chat_session_id, agent_id, project_path)
            if key and key in data:
                del data[key]
                _write_all(data)
            return
        prefix = f"{sid}|"
        keys = [k for k in data if str(k).startswith(prefix)]
        if not keys:
            return
        for k in keys:
            del data[k]
        _write_all(data)


def _rule_text_by_name(
    rules: List[Tuple[str, str]],
    name: str,
) -> Optional[str]:
    for n, text in rules:
        if n == name:
            return text
    return None


def _format_rule_changes(
    label: str,
    old_map: Dict[str, str],
    new_map: Dict[str, str],
    load_rules_fn,
    project_path: Optional[str],
) -> List[str]:
    lines: List[str] = []
    old_names = set(old_map)
    new_names = set(new_map)
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    changed = sorted(n for n in old_names & new_names if old_map[n] != new_map[n])

    if not added and not removed and not changed:
        return lines

    lines.append(f"### {label}")
    if project_path is None:
        rules_list = load_global_rules()
    else:
        rules_list = load_project_rules(project_path)

    for name in removed:
        lines.append(f"- Removed rule `{name}`")
    for name in added:
        text = _rule_text_by_name(rules_list, name) or ""
        lines.append(f"- **New** rule `{name}`:")
        if len(text) <= _MAX_RULE_CHARS:
            lines.append(text)
        else:
            lines.append(text[:_MAX_RULE_CHARS].rstrip() + "\n… (truncated)")
    for name in changed:
        text = _rule_text_by_name(rules_list, name) or ""
        lines.append(f"- **Updated** rule `{name}`:")
        if len(text) <= _MAX_RULE_CHARS:
            lines.append(text)
        else:
            lines.append(text[:_MAX_RULE_CHARS].rstrip() + "\n… (truncated)")
    return lines


def _format_inventory_delta(
    label: str,
    old: Tuple[str, ...],
    new: Tuple[str, ...],
    *,
    path_hint: str,
) -> List[str]:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    if not added and not removed:
        return []
    lines = [f"### {label}"]
    for name in added:
        lines.append(f"- New: `{name}` ({path_hint})")
    for name in removed:
        lines.append(f"- Removed: `{name}`")
    return lines


def build_delta_text(
    previous: ContextSnapshot,
    current: ContextSnapshot,
    project_path: Optional[str],
) -> Optional[str]:
    parts: List[str] = ["## Context delta (since your session briefing)"]

    if previous.schema_version != current.schema_version:
        parts.append(
            f"- Context schema {previous.schema_version} → {current.schema_version}"
        )

    parts.extend(
        _format_rule_changes(
            "Cuttle global rules",
            previous.global_rules,
            current.global_rules,
            load_global_rules,
            None,
        )
    )
    parts.extend(
        _format_rule_changes(
            "Project rules",
            previous.project_rules,
            current.project_rules,
            load_project_rules,
            project_path,
        )
    )

    global_config = _cuttle_global_config()
    global_hint = f"`{global_config / 'docs'}`" if global_config else "Cuttle global `.cuttle_global/docs`"
    parts.extend(
        _format_inventory_delta(
            "Global docs",
            previous.global_docs,
            current.global_docs,
            path_hint=global_hint,
        )
    )
    parts.extend(
        _format_inventory_delta(
            "Project docs",
            previous.project_docs,
            current.project_docs,
            path_hint="project `.cuttle/docs`",
        )
    )
    parts.extend(
        _format_inventory_delta(
            "Project actions",
            previous.project_actions,
            current.project_actions,
            path_hint="project `.cuttle/actions`",
        )
    )
    parts.extend(
        _format_inventory_delta(
            "Project commands",
            previous.project_commands,
            current.project_commands,
            path_hint="project `.cuttle/commands`",
        )
    )

    if len(parts) <= 1:
        return None

    body = "\n".join(parts).strip()
    if len(body) > _MAX_DELTA_CHARS:
        body = body[: _MAX_DELTA_CHARS - 40].rstrip() + "\n\n… (delta truncated)"
    return body


def wrap_delta_block(delta_text: str) -> str:
    inner = f"{_DELTA_PREAMBLE}\n\n{delta_text.strip()}"
    return f"{_OPEN}\nschema_version: {CONTEXT_SCHEMA_VERSION}\n\n{inner}\n{_CLOSE}"


def build_resume_delta(
    chat_session_id: Any,
    agent_id: str,
    project_path: str,
) -> Optional[str]:
    """Return a compact delta block when context changed since last full inject."""
    previous = load_injected_snapshot(chat_session_id, agent_id, project_path)
    if previous is None:
        return None
    current = compute_snapshot(project_path)
    delta = build_delta_text(previous, current, project_path)
    if not delta:
        return None
    return wrap_delta_block(delta)


def append_user_request(delta_or_body: str, user_prompt: str) -> str:
    body = (delta_or_body or "").strip()
    prompt = (user_prompt or "").strip()
    if not body:
        return prompt
    if not prompt:
        return body
    return f"{body}\n\n{_USER_REQUEST_HEADER}\n{prompt}"
