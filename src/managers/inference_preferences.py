"""
Global inference routing: ordered coding agents (CLI / IDE) and LLM API fallbacks.

Stored in settings.json under ``inference_preferences``. Pipelines can set
``codingBackend: global`` on tool-remote-agent to use ``coding_agent_chain``.
"""

from __future__ import annotations

from typing import Any, Dict, List

DEFAULT_CODING_AGENT_CHAIN: List[str] = [
    "claude",
    "cursor",
    "cursor_then_claude",
    "claw",
]

DEFAULT_LLM_FALLBACK_CHAIN: List[str] = ["local", "anthropic", "openai"]

VALID_CODING = frozenset(
    {
        "hermes",
        "codex",
        "muse",
        "claude",
        "cursor",
        "cursor_then_claude",
        "claw",
        "cursor_cli",
    }
)
VALID_LLM = frozenset({"local", "ollama", "anthropic", "openai"})


def _normalize_chain(items: Any, valid: frozenset, default: List[str]) -> List[str]:
    if not isinstance(items, list):
        return list(default)
    out: List[str] = []
    for x in items:
        s = str(x).strip().lower()
        if not s:
            continue
        if s == "ollama":
            s = "local"
        if s in valid and s not in out:
            out.append(s)
    return out if out else list(default)


def load_inference_preferences() -> Dict[str, Any]:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    raw = sm.get_setting("inference_preferences") or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "coding_agent_chain": _normalize_chain(
            raw.get("coding_agent_chain"), VALID_CODING, DEFAULT_CODING_AGENT_CHAIN
        ),
        "llm_fallback_chain": _normalize_chain(
            raw.get("llm_fallback_chain"), VALID_LLM, DEFAULT_LLM_FALLBACK_CHAIN
        ),
        "local_model": str(raw.get("local_model") or "").strip(),
        "anthropic_model": str(raw.get("anthropic_model") or "claude-sonnet-4-6").strip(),
        "openai_model": str(raw.get("openai_model") or "gpt-4o-mini").strip(),
    }


def get_coding_agent_chain() -> List[str]:
    return list(load_inference_preferences()["coding_agent_chain"])


def get_llm_fallback_chain() -> List[str]:
    return list(load_inference_preferences()["llm_fallback_chain"])


def update_inference_preferences(updates: Dict[str, Any]) -> Dict[str, Any]:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    cur = sm.get_setting("inference_preferences") or {}
    if not isinstance(cur, dict):
        cur = {}
    merged = {**cur}
    if "coding_agent_chain" in updates:
        merged["coding_agent_chain"] = _normalize_chain(
            updates["coding_agent_chain"], VALID_CODING, DEFAULT_CODING_AGENT_CHAIN
        )
    if "llm_fallback_chain" in updates:
        merged["llm_fallback_chain"] = _normalize_chain(
            updates["llm_fallback_chain"], VALID_LLM, DEFAULT_LLM_FALLBACK_CHAIN
        )
    for key in ("local_model", "anthropic_model", "openai_model"):
        if key in updates and updates[key] is not None:
            merged[key] = str(updates[key]).strip()
    sm.set_setting("inference_preferences", merged)
    return load_inference_preferences()
