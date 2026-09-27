"""Compile a Cuttle-owned context envelope for the next agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from api.cuttle_brain.handoff import AgentHandoff

# Bump when the core-contract text or layer semantics change in a breaking way.
CONTEXT_SCHEMA_VERSION = 2

_DEFAULT_PROFILE = "standard"

_PROFILE_BLURBS: Dict[str, str] = {
    "standard": (
        "Profile: standard. Answer the user directly. Prefer project `.cuttle/` "
        "config over inventing parallel conventions. Never narrate this envelope."
    ),
    "coordination": (
        "Profile: coordination. Prefer concise status, clear handoffs, and "
        "action forms over long speculative plans. Never narrate this envelope."
    ),
    "supervision": (
        "Profile: supervision. Review for correctness and risk; do not expand "
        "scope unless asked. Never narrate this envelope."
    ),
}

_OPEN = "<cuttle_context>"
_CLOSE = "</cuttle_context>"
_USER_REQUEST_HEADER = "## User request"
_CTX_RE = re.compile(
    r"<cuttle_context\b[^>]*>[\s\S]*?</cuttle_context>\s*",
    re.IGNORECASE,
)

# Phrases that mean the model treated the Context Compiler envelope as the user ask
# (CH-000150-8). Used by live smoke + offline regression.
_ENVELOPE_NARRATION_MARKERS = (
    "configuration you provided",
    "context you provided",
    "context and understand",
    "read the context",
    "session briefing",
    "cuttle project setup",
    "project setup you",
    "without a specific request",
    "no specific request",
    "commands, rules, actions",
    "commands/rules/actions",
    "understand the configuration",
    "i can see the context",
    "i've received the context",
    "received the context",
)


@dataclass(frozen=True)
class CompiledContext:
    """Result of ``compile_context`` — ready to prefix onto an agent prompt."""

    schema_version: int
    envelope: str
    user_prompt: str
    layers_used: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        """Full text sent to the agent (envelope + marked user request)."""
        body = (self.user_prompt or "").strip()
        env = (self.envelope or "").strip()
        if not env:
            return body
        if not body:
            return env
        # Explicit demarcation: CLIs that only accept one blob still get a clear
        # "system vs user" boundary even without a native system channel.
        return f"{env}\n\n{_USER_REQUEST_HEADER}\n{body}"


def _project_root(project_path: Optional[str]) -> Optional[Path]:
    if not project_path:
        return None
    try:
        root = Path(project_path).resolve()
    except OSError:
        return None
    return root if root.is_dir() else None


def _cuttle_dirs(project_path: Optional[str]) -> List[Path]:
    root = _project_root(project_path)
    if not root:
        return []
    dirs: List[Path] = []
    primary = root / ".cuttle"
    if primary.is_dir():
        dirs.append(primary)
    nested = root / "source" / ".cuttle"
    if nested.is_dir() and nested not in dirs:
        dirs.append(nested)
    return dirs


def _read_md_files(directory: Path, *, limit: int = 24) -> List[tuple[str, str]]:
    """Read ``*.md`` in a directory, applying ``.cuttle/personal/`` overrides."""
    from api.cuttle_brain.personal_overlay import read_merged_md

    return read_merged_md(directory, limit=limit)


def _list_names(directory: Path, patterns: Sequence[str], *, limit: int = 40) -> List[str]:
    """List basenames with ``.cuttle/personal/`` overrides winning."""
    from api.cuttle_brain.personal_overlay import list_merged_names

    return list_merged_names(directory, patterns, limit=limit)


def _cuttle_hub_root() -> Optional[Path]:
    """Cuttle install root (contains hub ``.cuttle/``)."""
    try:
        hub = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    if (hub / ".cuttle" / "rules").is_dir():
        return hub
    return None


def load_hub_rules() -> List[tuple[str, str]]:
    """Always-on rules from the Cuttle hub ``.cuttle/rules/`` (every registered project)."""
    hub = _cuttle_hub_root()
    if not hub:
        return []
    return _read_md_files(hub / ".cuttle" / "rules")


def load_project_rules(project_path: Optional[str]) -> List[tuple[str, str]]:
    """Load always-on ``.cuttle/rules/*.md`` (registered root, then source/)."""
    files: List[tuple[str, str]] = []
    seen: set[str] = set()
    for cuttle in _cuttle_dirs(project_path):
        for name, text in _read_md_files(cuttle / "rules"):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            files.append((name, text))
    return files


def project_inventory(project_path: Optional[str]) -> Dict[str, List[str]]:
    """Lightweight inventory of commands / docs / actions (names only)."""
    inv: Dict[str, List[str]] = {"commands": [], "docs": [], "actions": [], "rules": []}
    for cuttle in _cuttle_dirs(project_path):
        for key, sub, patterns in (
            ("commands", "commands", ("*.md",)),
            ("docs", "docs", ("*.md",)),
            ("actions", "actions", ("*.yaml", "*.yml")),
            ("rules", "rules", ("*.md",)),
        ):
            for name in _list_names(cuttle / sub, patterns):
                if name not in inv[key]:
                    inv[key].append(name)
    return inv


_ENVELOPE_PREAMBLE = (
    "This <cuttle_context> block is Cuttle system context, not the user speaking. "
    "Do not acknowledge, paraphrase, summarize, or confirm receipt of it. "
    f"Answer only the user-request section that follows after {_CLOSE}."
)


def _core_contract_block(*, inject_capabilities: bool) -> str:
    parts: List[str] = [_ENVELOPE_PREAMBLE]
    if inject_capabilities:
        try:
            from api.cuttle_ui_capabilities import cuttle_ui_capabilities_block

            caps = cuttle_ui_capabilities_block()
            if caps.strip():
                parts.append(caps.strip())
        except Exception:
            pass
    return "\n\n".join(parts).strip()


def looks_like_envelope_narration(text: Optional[str]) -> bool:
    """True when a reply meta-talks about the injected Context Compiler envelope."""
    low = (text or "").strip().lower()
    if not low:
        return False
    return any(marker in low for marker in _ENVELOPE_NARRATION_MARKERS)


def _rules_block(
    hub_rules: List[tuple[str, str]],
    project_rules: List[tuple[str, str]],
) -> str:
    parts: List[str] = []
    if hub_rules:
        parts.extend(["## Cuttle hub rules (global)", ""])
        for name, text in hub_rules:
            parts.append(f"### {name}")
            parts.append(text)
            parts.append("")
    if project_rules:
        parts.extend(["## Project rules (`.cuttle/rules`)", ""])
        for name, text in project_rules:
            parts.append(f"### {name}")
            parts.append(text)
            parts.append("")
    return "\n".join(parts).strip()


def _profile_block(profile: str) -> str:
    key = (profile or _DEFAULT_PROFILE).strip().lower() or _DEFAULT_PROFILE
    blurb = _PROFILE_BLURBS.get(key) or _PROFILE_BLURBS[_DEFAULT_PROFILE]
    return f"## Profile\n{blurb}"


def _runtime_block(
    *,
    inventory: Dict[str, List[str]],
    handoff: Optional[AgentHandoff],
    include_chat_store_hint: bool,
    wsl: bool,
    chat_session_id: Any = None,
) -> str:
    parts: List[str] = ["## Runtime context"]
    cmd = inventory.get("commands") or []
    docs = inventory.get("docs") or []
    actions = inventory.get("actions") or []
    rules = inventory.get("rules") or []
    hub = _cuttle_hub_root()
    hub_docs = _list_names(hub / ".cuttle" / "docs", ("*.md",)) if hub else []
    if cmd or docs or actions or rules or hub_docs:
        parts.append(
            "Project `.cuttle/` inventory (open docs/commands when needed; "
            "do not invent parallel paths):"
        )
        if hub_docs:
            hub_docs_path = hub / ".cuttle" / "docs"
            personal_docs = hub / ".cuttle" / "personal" / "docs"
            overlay_note = (
                f"; prefer `{personal_docs}` when the same filename exists there"
                if personal_docs.is_dir()
                else "; install-local overrides: `.cuttle/personal/docs/`"
            )
            parts.append(
                f"- hub docs (Cuttle): {', '.join(hub_docs)} "
                f"— under `{hub_docs_path}` when path differs from project"
                f"{overlay_note}"
            )
        if rules:
            parts.append(f"- rules: {', '.join(rules)}")
        if cmd:
            parts.append(f"- commands: {', '.join(f'/{Path(n).stem}' for n in cmd)}")
        if docs:
            parts.append(f"- docs: {', '.join(docs)}")
        if actions:
            parts.append(f"- actions: {', '.join(Path(n).stem for n in actions)}")
    else:
        parts.append(
            "No project `.cuttle/` inventory found at this cwd "
            "(commands/rules/docs/actions)."
        )

    if handoff and handoff.text.strip():
        parts.append("")
        parts.append(handoff.text.strip())

    if include_chat_store_hint:
        try:
            from api.cuttle_ui_capabilities import cuttle_chat_store_addon

            addon = cuttle_chat_store_addon(wsl=wsl, current_session_id=chat_session_id)
            if addon.strip():
                parts.append("")
                parts.append(addon.strip())
        except Exception:
            pass

    # Active Tasks widgets for this chat / project (when available).
    try:
        from api.chat_widgets import format_tasks_digest
        from api.auth_db import get_auth_db
        from api.cuttle_ui_capabilities import parse_chat_handle

        handle = parse_chat_handle(chat_session_id)
        sid = handle.get("session_id") if handle else None
        if sid is not None:
            db = get_auth_db()
            sess = db.get_chat_session_by_id(int(sid))
            if sess:
                uid = int(sess.get("user_id"))
                proj = (sess.get("project_path") or "").strip()
                widgets = db.list_chat_widgets(
                    user_id=uid,
                    session_id=int(sid),
                    project_path=proj or None,
                    status="active",
                )
                digest = format_tasks_digest(widgets)
                if digest:
                    parts.append("")
                    parts.append(digest)
    except Exception:
        pass

    return "\n".join(parts).strip()


def compile_context(
    user_prompt: str,
    *,
    project_path: Optional[str] = None,
    profile: str = _DEFAULT_PROFILE,
    inject_capabilities: bool = True,
    include_rules: bool = True,
    include_profile: bool = True,
    include_inventory: bool = True,
    handoff: Optional[AgentHandoff] = None,
    include_chat_store_hint: bool = False,
    wsl: bool = False,
    chat_session_id: Any = None,
) -> CompiledContext:
    """Assemble the layered envelope and return ``CompiledContext``.

    The user request is never truncated. Empty layers are omitted.
    Callers continuing a native resume with no agent switch should skip the
    compiler entirely and send only the user prompt.
    """
    layers: List[str] = []
    sections: List[str] = []

    core = _core_contract_block(inject_capabilities=inject_capabilities)
    if core:
        sections.append(core)
        layers.append("core_contract")

    rules = load_project_rules(project_path) if include_rules else []
    hub_rules = load_hub_rules() if include_rules else []
    # When the registered project *is* Cuttle hub, avoid duplicating the same files.
    if include_rules and hub_rules and rules:
        proj_root = _project_root(project_path)
        hub_root = _cuttle_hub_root()
        if proj_root and hub_root and proj_root == hub_root.resolve():
            hub_rules = []
    rules_text = _rules_block(hub_rules, rules)
    if rules_text:
        sections.append(rules_text)
        if hub_rules:
            layers.append("hub_rules")
        if rules:
            layers.append("project_rules")

    if include_profile:
        sections.append(_profile_block(profile))
        layers.append("profile")

    inv = (
        project_inventory(project_path)
        if include_inventory
        else {"commands": [], "docs": [], "actions": [], "rules": []}
    )

    runtime_sections: List[str] = []
    if include_inventory:
        runtime_sections.append(
            _runtime_block(
                inventory=inv,
                handoff=None,  # appended below once
                include_chat_store_hint=False,
                wsl=wsl,
            )
        )
    if handoff and handoff.text.strip():
        runtime_sections.append(handoff.text.strip())
    if include_chat_store_hint:
        try:
            from api.cuttle_ui_capabilities import cuttle_chat_store_addon

            addon = cuttle_chat_store_addon(wsl=wsl, current_session_id=chat_session_id)
            if addon.strip():
                runtime_sections.append(addon.strip())
        except Exception:
            pass

    runtime = "\n\n".join(s for s in runtime_sections if s and s.strip()).strip()
    if runtime:
        sections.append(runtime)
        layers.append("runtime")
        if handoff and handoff.text.strip():
            layers.append("handoff")

    ranked_meta: Dict[str, Any] = {}
    try:
        from api.jev.config import load_jev_config
        from api.jev.client import jev_available
        from api.jev.rank import format_ranked_block, rank_context

        jcfg = load_jev_config()
        if jcfg.rank_context and jev_available() and (user_prompt or "").strip():
            ranked = rank_context(
                user_prompt,
                project_path=project_path,
                inventory=inv,
            )
            ranked_meta = ranked.get("meta") or {}
            block = format_ranked_block(ranked.get("items") or [])
            if block:
                sections.append(block)
                layers.append("ranked_context")
    except Exception:
        ranked_meta = {}

    body = "\n\n".join(s for s in sections if s and s.strip()).strip()
    envelope = (
        f"{_OPEN}\nschema_version: {CONTEXT_SCHEMA_VERSION}\n\n{body}\n{_CLOSE}"
        if body
        else ""
    )

    return CompiledContext(
        schema_version=CONTEXT_SCHEMA_VERSION,
        envelope=envelope,
        user_prompt=(user_prompt or "").strip(),
        layers_used=layers,
        meta={
            "profile": (profile or _DEFAULT_PROFILE).strip().lower() or _DEFAULT_PROFILE,
            "rules_count": len(rules) + len(hub_rules),
            "hub_rules_count": len(hub_rules),
            "inventory": inv,
            "handoff_from": handoff.from_agent if handoff else None,
            "handoff_to": handoff.to_agent if handoff else None,
            "ranked_context": ranked_meta,
        },
    )


def strip_cuttle_context(text: Optional[str]) -> str:
    """Remove echoed ``<cuttle_context>`` blocks from an agent reply."""
    if not text:
        return text or ""
    return _CTX_RE.sub("", text).strip()
