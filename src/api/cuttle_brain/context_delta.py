"""Context delta — lightweight updates on resumed agent sessions.

Full Context Compiler envelope on fresh sessions; on resume, send only what changed
(global/project rules, new docs/actions) so agents stay current without re-injecting
the entire briefing every turn.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.runtime_paths import runtime_state_path
from api.cuttle_brain.key_store import KeyStore

from api.cuttle_brain.context_compiler import (
    CONTEXT_SCHEMA_VERSION,
    _USER_REQUEST_HEADER,
    _apply_router_to_global_rules,
    _cuttle_global_config,
    load_global_rules,
    load_project_rules,
    project_inventory,
    skill_inventory,
)
from api.cuttle_brain.personal_overlay import list_merged_names

_OPEN = "<cuttle_context>"
_CLOSE = "</cuttle_context>"

_DELTA_PREAMBLE = (
    "Context delta (not the user speaking). Apply these updates; do not narrate them. "
    f"Answer only the user request after {_CLOSE}."
)

_lock = threading.Lock()
_MAX_RULE_CHARS = 3500
_MAX_DELTA_CHARS = 6000

# Truncation markers. A delta carrying either marker withholds instructions
# the agent never saw, so it must never be acknowledged as delivered: the
# kernel falls back to a full briefing instead (see prepare_resume_delta).
RULE_TRUNC_MARKER = "\n… (truncated)"
DELTA_TRUNC_MARKER = "\n\n… (delta truncated)"


def is_truncated_delta_text(text: str) -> bool:
    """True when a delta body withholds instructions behind a truncation marker."""
    body = text or ""
    return RULE_TRUNC_MARKER in body or DELTA_TRUNC_MARKER in body



def _map_file() -> Path:
    return runtime_state_path("brain", "context_inject_snapshots.json")


def _sid_key(chat_session_id: Any) -> Optional[str]:
    if chat_session_id is None:
        return None
    s = str(chat_session_id).strip()
    return s or None


def sid_variants(chat_session_id: Any) -> List[str]:
    """Key spellings one chat may be stored under (``959`` / ``db_session_959``).

    Callers hand the kernel either form; prefix-based cleanup must catch both.
    """
    sid = _sid_key(chat_session_id)
    if not sid:
        return []
    out = [sid]
    digits = sid[len("db_session_"):] if sid.startswith("db_session_") else sid
    if digits.isdigit():
        for form in (digits, f"db_session_{digits}"):
            if form not in out:
                out.append(form)
    return out


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
    skills: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "global_rules": dict(self.global_rules),
            "project_rules": dict(self.project_rules),
            "global_docs": list(self.global_docs),
            "project_docs": list(self.project_docs),
            "project_actions": list(self.project_actions),
            "project_commands": list(self.project_commands),
            "skills": list(self.skills),
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
            skills=tuple(raw.get("skills") or ()),
        )


def compute_snapshot(project_path: Optional[str]) -> ContextSnapshot:
    """Capture the exact context state the fresh envelope would send.

    Applies the same GLOBAL.ini policy as ``compile_context`` (rules
    off/shadow, docs off; safety core always retained) and the same merged
    inventory owners (install-local personal overlays included), so a resume
    delta diffs what the agent actually saw — never suppressed legs.
    """
    from api.cuttle_brain.global_layers import load_global_layers

    router = load_global_layers(project_path)
    project_rules = load_project_rules(project_path)
    visible_global = _apply_router_to_global_rules(
        load_global_rules(), [n for n, _ in project_rules], router
    )
    inv = project_inventory(project_path)
    global_docs: List[str] = []
    if router.docs:
        global_config = _cuttle_global_config()
        if global_config is not None:
            docs_dir = global_config / "docs"
            if docs_dir.is_dir():
                global_docs = list_merged_names(docs_dir, ("*.md",), limit=40)
    return ContextSnapshot(
        schema_version=CONTEXT_SCHEMA_VERSION,
        global_rules=_rules_map(visible_global),
        project_rules=_rules_map(project_rules),
        global_docs=tuple(global_docs),
        project_docs=tuple(sorted(inv.get("docs") or [])),
        project_actions=tuple(sorted(inv.get("actions") or [])),
        project_commands=tuple(sorted(Path(n).stem for n in (inv.get("commands") or []))),
        skills=tuple(skill_inventory(project_path)),
    )


def _store() -> KeyStore:
    return KeyStore(_map_file())


def _load_all() -> Dict[str, Any]:
    """Maintenance-only enumeration; normal turns use indexed keys."""
    return _store().all()


def _write_all(data: Dict[str, Any]) -> None:
    _store().replace_all(data)


def load_injected_snapshot(
    chat_session_id: Any,
    agent_id: str,
    project_path: str,
) -> Optional[ContextSnapshot]:
    key = _store_key(chat_session_id, agent_id, project_path)
    if not key:
        return None
    with _lock:
        raw = _store().get(key)
    if not isinstance(raw, dict):
        return None
    snap = raw.get("snapshot")
    if not isinstance(snap, dict):
        return None
    try:
        return ContextSnapshot.from_dict(snap)
    except (TypeError, ValueError):
        return None


def record_snapshot(
    chat_session_id: Any,
    agent_id: str,
    project_path: str,
    snapshot: ContextSnapshot,
) -> None:
    """Acknowledge a previously prepared snapshot as delivered.

    Callers must pass the exact snapshot captured for the prompt that was
    sent — never a recomputed one — so files edited mid-turn stay pending
    for the next delta instead of being silently marked as known.
    """
    key = _store_key(chat_session_id, agent_id, project_path)
    if not key:
        return
    with _lock:
        _store().put(key, {"snapshot": snapshot.to_dict()})


def clear_injected_snapshot(
    chat_session_id: Any,
    agent_id: Optional[str] = None,
    project_path: Optional[str] = None,
) -> None:
    """Forget acknowledged briefings so the next turn sends a full one.

    ``agent_id`` + ``project_path`` drops one record; ``agent_id`` alone drops
    that agent's records for every project (e.g. after it compacted);
    neither drops the whole chat (session delete / reset).
    """
    sid = _sid_key(chat_session_id)
    if not sid:
        return
    with _lock:
        if agent_id and project_path:
            key = _store_key(chat_session_id, agent_id, project_path)
            if key:
                _store().delete([key])
            return
        aid = (agent_id or "").strip()
        prefixes = [f"{form}|{aid}|" if aid else f"{form}|" for form in sid_variants(chat_session_id)]
        _store().delete_prefixes(prefixes)


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
            lines.append(text[:_MAX_RULE_CHARS].rstrip() + RULE_TRUNC_MARKER)
    for name in changed:
        text = _rule_text_by_name(rules_list, name) or ""
        lines.append(f"- **Updated** rule `{name}`:")
        if len(text) <= _MAX_RULE_CHARS:
            lines.append(text)
        else:
            lines.append(text[:_MAX_RULE_CHARS].rstrip() + RULE_TRUNC_MARKER)
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

    parts.extend(_format_inventory_delta(
        "Available Cuttle skills", previous.skills, current.skills,
        path_hint="effective scoped SKILL.md paths (personal/project precedence applies)",
    ))

    if len(parts) <= 1:
        return None

    body = "\n".join(parts).strip()
    if len(body) > _MAX_DELTA_CHARS:
        body = body[: _MAX_DELTA_CHARS - 40].rstrip() + DELTA_TRUNC_MARKER
    return body


def wrap_delta_block(delta_text: str) -> str:
    inner = f"{_DELTA_PREAMBLE}\n\n{delta_text.strip()}"
    return f"{_OPEN}\nschema_version: {CONTEXT_SCHEMA_VERSION}\n\n{inner}\n{_CLOSE}"


@dataclass(frozen=True)
class PreparedDelta:
    """A resume delta ready to send, with the exact snapshot it describes.

    ``snapshot`` is the state the delta text was computed from. Acknowledge
    it (via ``record_snapshot``) only after the delta text is successfully
    delivered — never at prepare time, so interrupted or failed turns keep
    their notices pending. ``truncated`` means the text withholds
    instructions and must fall back to a full briefing instead.
    """

    text: str
    snapshot: ContextSnapshot
    truncated: bool


class UnstablePreparationError(Exception):
    """Delta preparation raced a context edit: text may not match the receipt.

    Callers must not send the prepared text as a delta and must not
    acknowledge anything. The kernel handles this as a preparation failure
    and falls back to the full briefing (which prepares its own receipt).
    """


def prepare_resume_delta(
    chat_session_id: Any,
    agent_id: str,
    project_path: str,
) -> Optional[PreparedDelta]:
    """Diff against the last acknowledged snapshot without acknowledging.

    Returns None when no snapshot was acknowledged or nothing changed.
    Raises :class:`UnstablePreparationError` when preparation raced a
    context edit (the formatter re-reads rule bodies after the snapshot,
    so the text may not match the receipt) — the notice must be
    delivered via the full fallback, never as a bare resume that drops it.
    """
    previous = load_injected_snapshot(chat_session_id, agent_id, project_path)
    if previous is None:
        return None
    current = compute_snapshot(project_path)
    delta = build_delta_text(previous, current, project_path)
    if not delta:
        return None
    text = wrap_delta_block(delta)
    if compute_snapshot(project_path) != current:
        raise UnstablePreparationError(
            "context changed during delta preparation; falling back to full"
        )
    return PreparedDelta(
        text=text, snapshot=current, truncated=is_truncated_delta_text(text)
    )


def append_user_request(delta_or_body: str, user_prompt: str) -> str:
    body = (delta_or_body or "").strip()
    prompt = (user_prompt or "").strip()
    if not body:
        return prompt
    if not prompt:
        return body
    return f"{body}\n\n{_USER_REQUEST_HEADER}\n{prompt}"
