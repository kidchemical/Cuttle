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


def _read_md_files(directory: Path, *, limit: Optional[int] = 24) -> List[tuple[str, str]]:
    """Read ``*.md`` in a directory, appending ``personal/`` deltas.

    ``limit=None`` reads all sorted files (governing rule bodies).
    """
    from api.cuttle_brain.personal_overlay import read_merged_md

    return read_merged_md(directory, limit=limit)


def _list_names(directory: Path, patterns: Sequence[str], *, limit: int = 40) -> List[str]:
    """List basenames (personal twins share the basename; see delta names)."""
    from api.cuttle_brain.personal_overlay import list_merged_names

    return list_merged_names(directory, patterns, limit=limit)


def _delta_names(directory: Path, patterns: Sequence[str]) -> List[str]:
    """Basenames in ``directory`` carrying an appended personal delta."""
    from api.cuttle_brain.personal_overlay import list_merged_delta_names

    try:
        return list_merged_delta_names(directory, tuple(patterns))
    except Exception:
        return []


def _apply_router_to_global_rules(
    all_global: List[tuple[str, str]],
    project_names: List[str],
    router: Any,
) -> List[tuple[str, str]]:
    """Enforce GLOBAL.ini rules mode; safety rules always survive."""
    from api.cuttle_brain.global_layers import SAFETY_RULE_FILES

    safety = [(n, t) for n, t in all_global if n in SAFETY_RULE_FILES]
    policy = [(n, t) for n, t in all_global if n not in SAFETY_RULE_FILES]
    mode = (router.rules_mode if router is not None else "append") or "append"
    if mode == "off":
        return safety
    if mode == "shadow":
        shadowed = {n.lower() for n in project_names}
        return safety + [(n, t) for n, t in policy if n.lower() not in shadowed]
    return all_global


def _cuttle_install_root() -> Optional[Path]:
    """Cuttle install root (contains global ``.cuttle_global/``)."""
    try:
        install = Path(__file__).resolve().parents[3]
    except IndexError:
        return None
    if (install / ".cuttle_global" / "rules").is_dir():
        return install
    return None


def _cuttle_global_config() -> Optional[Path]:
    """Global shared-config root: ``{install}/.cuttle_global/`` (rules/docs/actions/…)."""
    install = _cuttle_install_root()
    if not install:
        return None
    config = install / ".cuttle_global"
    return config if config.is_dir() else None


def load_global_rules() -> List[tuple[str, str]]:
    """Always-on rules from the Cuttle global ``.cuttle_global/rules/`` (every registered project)."""
    config = _cuttle_global_config()
    if not config:
        return []
    return _read_md_files(config / "rules", limit=None)


def load_project_rules(project_path: Optional[str]) -> List[tuple[str, str]]:
    """Load always-on ``.cuttle/rules/*.md`` (registered root, then source/)."""
    files: List[tuple[str, str]] = []
    seen: set[str] = set()
    for cuttle in _cuttle_dirs(project_path):
        for name, text in _read_md_files(cuttle / "rules", limit=None):
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


def skill_inventory(project_path: Optional[str]) -> List[str]:
    """Effective scoped skill summaries, independent of optional Jev ranking.

    Reuse the skill owner for personal overrides, disabled units, project
    precedence and GLOBAL.ini policy. List metadata/paths, never inject bodies.
    """
    from api.markdown_skills import list_markdown_skills

    return [
        f"{skill['ref']}: {skill['description']} ({skill['path']})"
        for skill in list_markdown_skills(project_path)
    ]


_ENVELOPE_PREAMBLE = (
    "This <cuttle_context> block is Cuttle system context, not the user speaking. "
    "Do not acknowledge, paraphrase, summarize, or confirm receipt of it. "
    f"Answer only the user-request section that follows after {_CLOSE}."
)


def _core_contract_block(*, inject_capabilities: bool, project_path: Optional[str] = None) -> str:
    parts: List[str] = [_ENVELOPE_PREAMBLE]
    if inject_capabilities:
        try:
            from api.cuttle_ui_capabilities import cuttle_ui_capabilities_block

            caps = cuttle_ui_capabilities_block(project_path=project_path)
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
    global_rules: List[tuple[str, str]],
    project_rules: List[tuple[str, str]],
) -> str:
    parts: List[str] = []
    if global_rules:
        parts.extend(["## Cuttle global rules", ""])
        for name, text in global_rules:
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
    chat_session_id: Any = None,
    project_path: Optional[str] = None,
) -> str:
    """Inventory + active Tasks digest. Handoff and chat-store hint are
    appended by ``compile_context`` after this block."""
    parts: List[str] = ["## Runtime context"]
    active_root = _project_root(project_path) if project_path else None
    if active_root is not None:
        parts.append(f"Active project root: `{active_root}`")
    cmd = inventory.get("commands") or []
    docs = inventory.get("docs") or []
    actions = inventory.get("actions") or []
    rules = inventory.get("rules") or []
    skills = skill_inventory(project_path)
    from api.cuttle_brain.global_layers import load_global_layers

    router = load_global_layers(project_path)
    global_config = _cuttle_global_config()
    if global_config is not None and router.docs:
        from api.cuttle_brain.global_layers import global_doc_enabled
        global_docs = [name for name in _list_names(global_config / "docs", ("*.md",))
                       if global_doc_enabled(name, project_path)]
        deltas = [name for name in _delta_names(global_config / "docs", ("*.md",)) if name in global_docs]
    else:
        global_docs = []
        deltas = []
    if cmd or docs or actions or rules or global_docs or skills:
        parts.append(
            "Project `.cuttle/` inventory (open docs/commands when needed; "
            "do not invent parallel paths):"
        )
        if global_docs and global_config is not None:
            global_docs_path = global_config / "docs"
            delta_note = (
                f"; install-local deltas appended for: {', '.join(deltas)}"
                if deltas
                else ""
            )
            parts.append(
                f"- global docs (Cuttle): {', '.join(global_docs)} "
                f"— under `{global_docs_path}` when path differs from project"
                f"{delta_note}"
            )
        if not router.docs and global_config is not None:
            parts.append("- global docs: off per this project's GLOBAL.ini")
        if rules:
            parts.append(f"- rules: {', '.join(rules)}")
        if cmd:
            parts.append(f"- commands: {', '.join(f'/{Path(n).stem}' for n in cmd)}")
        if docs:
            proj_deltas: List[str] = []
            for cuttle_dir in _cuttle_dirs(project_path) if project_path else []:
                for name in _delta_names(cuttle_dir / "docs", ("*.md",)):
                    if name not in proj_deltas:
                        proj_deltas.append(name)
            delta_note = (
                f"; install-local deltas appended for: {', '.join(proj_deltas)}"
                if proj_deltas
                else ""
            )
            parts.append(f"- docs: {', '.join(docs)}{delta_note}")
        if skills:
            parts.append("- Available Cuttle skills (open the matching SKILL.md when relevant):")
            parts.extend(f"  - {skill}" for skill in skills)
        if actions:
            parts.append(f"- actions: {', '.join(Path(n).stem for n in actions)}")
    else:
        parts.append(
            "No project `.cuttle/` inventory found at this cwd "
            "(commands/rules/docs/actions)."
        )

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
    chat_session_id: Any = None,
) -> CompiledContext:
    """Assemble the layered envelope and return ``CompiledContext``.

    The user request is never truncated. Empty layers are omitted.
    Callers continuing a native resume with no agent switch should skip the
    compiler entirely and send only the user prompt.
    """
    layers: List[str] = []
    sections: List[str] = []
    layer_chars: Dict[str, int] = {}

    core = _core_contract_block(inject_capabilities=inject_capabilities, project_path=project_path)
    if core:
        sections.append(core)
        layers.append("core_contract")
        layer_chars["core_contract"] = len(core)

    rules = load_project_rules(project_path) if include_rules else []
    if include_rules:
        from api.cuttle_brain.global_layers import load_global_layers

        router = load_global_layers(project_path)
        # Global policy is additive by default; GLOBAL.ini shadow/off trims it.
        # Safety rules always survive (enforced inside the helper).
        global_rules = _apply_router_to_global_rules(
            load_global_rules(), [n for n, _ in rules], router
        )
    else:
        global_rules = []
    rules_text = _rules_block(global_rules, rules)
    if global_rules:
        layer_chars["global_rules"] = len(_rules_block(global_rules, []))
    if rules:
        layer_chars["project_rules"] = len(_rules_block([], rules))
    if rules_text:
        sections.append(rules_text)
        if global_rules:
            layers.append("global_rules")
        if rules:
            layers.append("project_rules")

    if include_profile:
        sections.append(_profile_block(profile))
        layers.append("profile")
        layer_chars["profile"] = len(sections[-1])

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
                chat_session_id=chat_session_id,
                project_path=project_path,
            )
        )
        layer_chars["runtime"] = len(runtime_sections[-1])
    if handoff and handoff.text.strip():
        runtime_sections.append(handoff.text.strip())
        layer_chars["handoff"] = len(runtime_sections[-1])
    if include_chat_store_hint:
        try:
            from api.cuttle_ui_capabilities import cuttle_chat_store_addon

            addon = cuttle_chat_store_addon(current_session_id=chat_session_id)
            if addon.strip():
                runtime_sections.append(addon.strip())
                layer_chars["chat_store"] = len(addon.strip())
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
                layer_chars["ranked_context"] = len(block)
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
            "rules_count": len(rules) + len(global_rules),
            "global_rules_count": len(global_rules),
            "inventory": inv,
            "handoff_from": handoff.from_agent if handoff else None,
            "handoff_to": handoff.to_agent if handoff else None,
            "ranked_context": ranked_meta,
            "layer_chars": layer_chars,
        },
    )


def strip_cuttle_context(text: Optional[str]) -> str:
    """Remove echoed ``<cuttle_context>`` blocks from an agent reply."""
    if not text:
        return text or ""
    return _CTX_RE.sub("", text).strip()
