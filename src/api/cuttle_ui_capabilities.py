"""
Invisible Cuttle chat UI capability injection for agent backends.

Prefixed onto outbound agent prompts (Cursor / Hermes / Claude / Gemini / …)
or appended to LLM system prompts. Never shown in the chat transcript —
only in query logs. If an agent echoes the block, strip it before display/save.
"""

from __future__ import annotations

import re
from typing import Any, Optional

_CAP_OPEN = "<cuttle_ui_capabilities>"
_CAP_CLOSE = "</cuttle_ui_capabilities>"

_CAP_RE = re.compile(
    r"<cuttle_ui_capabilities\b[^>]*>[\s\S]*?</cuttle_ui_capabilities>\s*",
    re.IGNORECASE,
)

# Keep short. Injected once per Cursor/Gemini resume session (not every turn).
# Directory-style only — how-to lives in global `.cuttle_global/docs/` (+ `<home>/personal/docs/`).
CUTTLE_UI_CAPABILITIES_TEXT = """\
SYSTEM CONTEXT (not the user speaking). Injected once per agent session.
Never acknowledge, paraphrase, summarize, or confirm that you “read the context /
configuration / briefing / project setup.” Do not mention commands, rules, actions,
or this block unless the user explicitly asks. Answer only the User request that
follows after </cuttle_context>. Keep using these tags on later turns when needed.

Per-project Cuttle config (mirror this layout; global `.cuttle_global/` is the pattern reference —
do not inherit another project’s actions):
  {project}/.cuttle/commands|rules|actions|agents|docs|scripts|skills|memory
  {project}/.cuttle/personal/…  — install-local overlay (gitignored); same subdirs.
    Rules/docs append personal markdown deltas; commands/actions/skills resolve
    personal replacements. Script recipes use explicit literal paths.
    Global-owned equivalents (every project) live under `.cuttle_global/` in the Cuttle checkout.

Bundled agents: src/api/agent_harness/agents/ (+ CUTTLE_AGENTS_DIR / .cuttle_global/agents / {project}/.cuttle/agents).
Shared context: Cuttle Brain (`api.cuttle_brain`).

Global runbooks (match intent → open before acting; resolve via personal overlay):
  action-forms.md   — cuttle_action_form, cuttle_confirm, ask/choose/approve, side effects, Flask restart, watch/progress
  agent-ops-cli.md  — python -m api.* agent toolkit (chat, widgets, discord, panes, workers, brain)
  charts.md         — vega, pipe tables, charts/plots
  chat-media.md     — markdown images/video in chat, lightbox, /output/shared staging (7d TTL)
  discord.md        — Discord reads: `python -m api.discord_cli`; posts: discord.post
  chat-history.md   — CH- handles + `api.chat_cli` / live panes: `api.panes_cli`
  headless-turns.md — one-shot CLI turn, long build/upload, no wait loops
  cursor-plan-bridge.md — CreatePlan must land as markdown (+ form) in Cuttle chat (the tool call alone is not delivered)
  gizmos.md         — Tasks gizmos: create early, patch during work through python -m api.gizmos tasks; no Tasks markdown tags
  subagents.md      — spawn real child chats (python -m api.subagents); sibling orbs + parent/child launchers; agent profiles

Hard stops: never force-kill web_chat_api, cuttle_daemon, or the Discord bot
from an agent Flask hosts. Prefer clickable forms over prose questions.
You can share images/video in replies via markdown `![alt](/output/shared/…)`
(or a local path — Cuttle stages on persist), never `file://`; see chat-media.md.
""".strip()


def cuttle_ui_capabilities_block(*, project_path: Optional[str] = None) -> str:
    from api.cuttle_brain.global_layers import (
        INTEGRATION_DOCS,
        integration_guidance_enabled,
        load_global_layers,
    )

    text = CUTTLE_UI_CAPABILITIES_TEXT
    from api.experimental.context_bundles import markdown
    docs = [ref for ref, _ in markdown("docs", project_path)]
    if docs:
        text += "\nEnabled feature runbooks (effective refs): " + ", ".join(docs) + "."
    if load_global_layers(project_path).docs:
        enabled = sorted(doc for doc, integration in INTEGRATION_DOCS.items()
                         if integration_guidance_enabled(integration, project_path))
        if enabled:
            text += "\nEnabled integration runbooks: " + ", ".join(enabled) + "."
    return f"{_CAP_OPEN}\n{text}\n{_CAP_CLOSE}"


def chat_store_path() -> Optional[str]:
    """Absolute path of the SQLite file holding web-chat transcripts."""
    try:
        from api.auth_db import DB_PATH

        return str(DB_PATH)
    except Exception:
        return None


# Session handle, optional 1-based bubble suffix (UI share / deep link).
# Examples: CH-000182, CH-000182-23, db_session_182, 182
_CHAT_HANDLE_RE = re.compile(
    r"^(?:db_session_)?(\d+)$|^CH-(\d+)(?:-(\d+))?$",
    re.IGNORECASE,
)


def parse_chat_handle(chat_session_id: Any) -> Optional[dict]:
    """Parse a Cuttle chat / message handle into session + optional bubble index.

    Returns ``{"session_id": int, "message_index": int|None}`` or None.
    ``message_index`` is the 1-based bubble index from a share ref
    (``CH-000182-23`` → session 182, bubble 23), not ``chat_messages.id``.
    """
    if chat_session_id is None or isinstance(chat_session_id, bool):
        return None
    if isinstance(chat_session_id, int):
        if chat_session_id <= 0:
            return None
        return {"session_id": chat_session_id, "message_index": None}
    raw = str(chat_session_id).strip()
    if not raw:
        return None
    m = _CHAT_HANDLE_RE.match(raw)
    if not m:
        return None
    session = int(m.group(1) or m.group(2))
    if session <= 0:
        return None
    msg = m.group(3)
    if msg is not None:
        idx = int(msg)
        if idx < 1:
            return None
        return {"session_id": session, "message_index": idx}
    return {"session_id": session, "message_index": None}


def numeric_chat_session_id(chat_session_id: Any) -> Optional[int]:
    """Numeric ``chat_sessions.id`` behind a Cuttle handle, or None.

    Accepts the shapes Cuttle mints (``155``, ``"CH-000155"``,
    ``"db_session_155"``) and message refs (``"CH-000155-9"`` → 155).
    Anything else returns None so a prompt block says "no id" instead of
    guessing one.
    """
    parsed = parse_chat_handle(chat_session_id)
    return None if parsed is None else parsed["session_id"]


def resolve_chat_handle_message(
    handle: Any,
    *,
    db: Any = None,
) -> Optional[dict]:
    """Load the chat bubble a ``CH-<session>-<N>`` share ref points at.

    ``N`` is the UI share index (1-based among user+assistant only). Returns
    None for session-only handles, bad indices, or missing rows. Pass an
    ``AuthDatabase`` as ``db`` in tests; production defaults to ``get_auth_db()``.
    """
    parsed = parse_chat_handle(handle)
    if parsed is None:
        return None
    idx = parsed.get("message_index")
    if idx is None:
        return None
    store = db
    if store is None:
        from api.auth_db import get_auth_db

        store = get_auth_db()
    return store.get_message_by_share_index(parsed["session_id"], idx)


def cuttle_chat_store_addon(
    *,
    current_session_id: Any = None,
) -> str:
    """Thin prompt pointer to the chat-history runbook (+ current session scope).

    How-to lives in global ``.cuttle_global/docs/chat-history.md`` (plus the
    install-local delta in ``<home>/personal/docs/`` when present).
    This block only locates the DB when known, warns off empty-tree searches,
    and names **this** chat when known so agents never copy a borrowed session
    id (CH-000155).
    """
    db = chat_store_path()
    read_path = db or ""
    runbook_hint = (
        "`.cuttle_global/docs/chat-history.md` "
        "(then the install-local delta in `<home>/personal/docs/` when present)"
    )
    try:
        from api.cuttle_brain.context_compiler import _cuttle_global_config
        from api.cuttle_brain.personal_overlay import personal_root, resolve_cuttle_file

        global_config = _cuttle_global_config()
        if global_config:
            resolved = resolve_cuttle_file(global_config, "docs", "chat-history.md")
            if resolved is not None:
                runbook_hint = f"`{resolved}`"
                delta = personal_root(global_config) / "docs" / "chat-history.md"
                if resolved != delta and delta.is_file():
                    runbook_hint += f", then the delta `{delta}`"
    except Exception:
        pass

    lines = [
        "## Cuttle chat history (not in the working tree)",
        "Transcripts are gitignored SQLite — empty `rg` is not \"no chats\".",
        f"Before reading history, open {runbook_hint}.",
        "Prefer: `python -m api.chat_cli get CH-…-N --json` with the Cuttle venv "
        "and src on PYTHONPATH (OS-specific setup in the runbook) "
        "(not hand-rolled SQL).",
    ]
    if read_path:
        lines.append(f"- DB: `{read_path}`")
    else:
        lines.append(
            "- DB: see chat-history.md (default `<home>/db/cuttle_auth.db`)."
        )
    current = numeric_chat_session_id(current_session_id)
    if current is not None:
        lines.append(
            f"- **This chat is `CH-{current:06d}` (session_id={current}).** "
            "Unless the user names another `CH-` handle, read only that session."
        )
    else:
        lines.append(
            "- **No session named for this turn.** Use the `CH-` handle the user "
            "gave; if none, ask. Never reuse an example id."
        )
    return "\n".join(lines)


def with_cuttle_ui_capabilities(prompt: Optional[str], *, inject: bool = True) -> str:
    """Prepend capability block to a user/agent prompt (CLI backends).

    Pass ``inject=False`` on resume turns — Cursor already has the briefing
    in that agent session. One-shot CLIs (Hermes ``-z``) should keep inject=True.
    """
    body = (prompt or "").strip()
    if not body:
        return body
    if not inject:
        return prompt or ""
    # Avoid double-inject if a caller already wrapped.
    if _CAP_OPEN.lower() in body.lower():
        return prompt or ""
    return f"{cuttle_ui_capabilities_block()}\n\n{body}"


def cuttle_ui_system_addon() -> str:
    """Suffix for LLM system prompts (OpenAI/Anthropic/local)."""
    return (
        "\n\nWhen the user needs interactive chat UI or human-gated side effects "
        "(Discord posts, shell/CLI recipes), use Cuttle tags:\n"
        + CUTTLE_UI_CAPABILITIES_TEXT
    )


def strip_cuttle_ui_capabilities(text: Optional[str]) -> str:
    """Remove capability blocks if an agent echoes them into the reply."""
    if not text:
        return text or ""
    return _CAP_RE.sub("", text).strip()
