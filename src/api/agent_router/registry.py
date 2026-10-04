"""Registered execution agents and models the router may select."""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from api.agent_router.types import ExecutionTarget

# Agents the chat dispatcher can run today.
KNOWN_AGENTS: Dict[str, Dict[str, str]] = {
    "cursor": {
        "label": "Cursor Agent CLI",
        "slash": "/cursor",
        "notes": "Harness connector; Auto is temporarily included at no extra cost.",
    },
    "codex": {
        "label": "Codex CLI",
        "slash": "/codex",
        "notes": "Harness connector — OpenAI Codex frontier fallback.",
    },
    "claude": {
        "label": "Claude Code",
        "slash": "/claude",
        "notes": "Harness connector — Anthropic Claude Code CLI.",
    },
    "muse": {
        "label": "Muse Code",
        "slash": "/muse",
        "notes": "Harness connector — Meta Muse Code CLI (native).",
    },
    "hermes": {
        "label": "Hermes",
        "slash": "/hermes",
        "notes": "Harness connector — Hermes Agent (local or cloud via Hermes config).",
    },
    "opencode": {
        "label": "OpenCode",
        "slash": "/opencode",
        "notes": "Harness connector — OpenCode CLI.",
    },
    "antigravity": {
        "label": "Antigravity CLI",
        "slash": "/antigravity",
        "notes": "Harness connector — Google Antigravity CLI (`agy`).",
    },
    "deepseek": {
        "label": "DeepSeek Harness",
        "slash": "/deepseek",
        "notes": "Harness connector — DeepSeek `dsh --profile headless` (Flash by default).",
    },
}

# Soft-known Cursor model ids (CLI may expose more; unknown ids soft-fail validation).
CURSOR_KNOWN_MODELS: Set[str] = {
    "auto",
    "default",
    "grok-4.6",
    "cursor-grok-4.6-high",
    "grok-4.5",
    "composer-2.5-fast",
    "claude-sonnet-5-thinking-high",
    "claude-opus-5-thinking-high",
    "claude-fable-5-thinking-high",
    "gpt-5.6-sol-medium",
    "gpt-5.6-terra-medium",
    "gemini-3.7-flash-high",
}

# Lightweight OpenAI chat models suitable as a routing brain.
OPENAI_ROUTER_MODELS: Set[str] = {
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-5-mini",
    "gpt-5-nano",
    "o4-mini",
    "gpt-3.5-turbo",
}

DEFAULT_CURSOR_AUTO = ExecutionTarget(agent="cursor", model="auto")
DEFAULT_CURSOR_GROK = ExecutionTarget(agent="cursor", model="grok-4.6")
DEFAULT_CODEX_FRONTIER = ExecutionTarget(agent="codex", model="")


def normalize_agent_id(raw: str) -> Optional[str]:
    s = (raw or "").strip().lower().replace("_", "-")
    aliases = {
        "cursor-agent": "cursor",
        "cursor-cli": "cursor",
        "cursoragent": "cursor",
        "openai-codex": "codex",
        "claude-code": "claude",
        "muse-code": "muse",
        "muse-cli": "muse",
        "meta-muse": "muse",
        "open-code": "opencode",
        "open_code": "opencode",
        "agy": "antigravity",
        "antigravity-cli": "antigravity",
        "dsh": "deepseek",
        "deepseek-harness": "deepseek",
        "deepseek-ai": "deepseek",
    }
    s = aliases.get(s, s)
    if s in KNOWN_AGENTS:
        return s
    return None


def agent_slash_prefix(agent: str) -> str:
    info = KNOWN_AGENTS.get(normalize_agent_id(agent) or "", {})
    return info.get("slash", f"/{agent}")


def list_agent_ids() -> List[str]:
    return sorted(KNOWN_AGENTS.keys())


def list_cursor_models_for_validation(*, live: bool = False) -> List[str]:
    """Soft-known Cursor model ids; optionally merge live `agent models`."""
    known = set(CURSOR_KNOWN_MODELS)
    if live:
        try:
            from api.cursor_agent_commands import list_cursor_agent_models

            for m in list_cursor_agent_models() or []:
                mid = str(m.get("id") or "").strip()
                if mid:
                    known.add(mid)
        except Exception:
            pass
    return sorted(known)


def validate_execution_target(
    agent: str,
    model: str,
    *,
    allow_empty_model: bool = True,
    strict_cursor_models: bool = False,
) -> Tuple[Optional[ExecutionTarget], Optional[str]]:
    aid = normalize_agent_id(agent)
    if not aid:
        return None, (
            f"Unknown agent `{agent}`. Known: {', '.join(list_agent_ids())}."
        )
    mid = (model or "").strip()
    if not mid:
        if allow_empty_model and aid in (
            "codex",
            "claude",
            "hermes",
            "muse",
            "opencode",
            "antigravity",
            "deepseek",
        ):
            return ExecutionTarget(agent=aid, model=""), None
        if aid == "cursor":
            mid = "auto"
        else:
            return None, f"Model required for agent `{aid}`."

    if aid == "cursor":
        if mid.lower() in ("auto", "default"):
            mid = "auto"
        elif strict_cursor_models:
            known = {m.lower() for m in list_cursor_models_for_validation(live=True)}
            if mid.lower() not in known:
                return None, (
                    f"Unknown Cursor model `{mid}`. "
                    f"Use `/cursor /model` to list models, or pick a registered id."
                )
        else:
            # Soft path: allow plausible ids without calling the agent CLI
            # (CLI listing can take tens of seconds and must not block routing).
            if " " in mid:
                return None, f"Invalid Cursor model id `{mid}`."
    return ExecutionTarget(agent=aid, model=mid), None


def validate_openai_router_model(model: str) -> Tuple[Optional[str], Optional[str]]:
    mid = (model or "").strip()
    if not mid:
        return None, "OpenAI router model id is required."
    # Prefer configured inference preference / known set; allow other ids with warning
    # only if they look like OpenAI model names (no spaces).
    if " " in mid or "/" in mid:
        return None, f"Invalid OpenAI model id `{mid}`."
    if mid.lower() in {m.lower() for m in OPENAI_ROUTER_MODELS}:
        return mid, None
    try:
        from api.jev.config import is_jev_model_id

        if is_jev_model_id(mid):
            return "jev-latest" if mid.lower() in ("jev", "typesafe", "typesafe-jev") else mid, None
    except Exception:
        pass
    # Accept unknown but plausible ids (OpenAI adds models often); caller may soft-warn.
    if mid.startswith("gpt-") or mid.startswith("o") or mid.startswith("chatgpt-"):
        return mid, None
    return None, (
        f"Model `{mid}` is not a recognized lightweight OpenAI chat model. "
        f"Suggested: {', '.join(sorted(OPENAI_ROUTER_MODELS))} or `jev`."
    )


def capability_metadata() -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for aid, info in KNOWN_AGENTS.items():
        out.append(
            {
                "agent": aid,
                "label": info["label"],
                "slash": info["slash"],
                "notes": info.get("notes") or "",
            }
        )
    return out
