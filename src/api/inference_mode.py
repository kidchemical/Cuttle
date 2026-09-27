"""Session-level inference mode overrides for web chat (local / cloud / auto)."""

from __future__ import annotations

from typing import Any, Dict, Optional

VALID_INFERENCE_MODES = frozenset({'auto', 'local', 'cloud'})


def normalize_inference_mode(value: Any) -> str:
    """Return auto, local, or cloud."""
    if value is None:
        return 'auto'
    v = str(value).strip().lower()
    if v in ('local', 'local-only', 'local_only', 'ollama', 'llamacpp'):
        return 'local'
    if v in ('cloud', 'remote', 'api', 'non-local', 'nonlocal'):
        return 'cloud'
    if v in VALID_INFERENCE_MODES:
        return v
    return 'auto'


def inference_mode_from_context(
    session: Optional[Dict[str, Any]] = None,
    user_context: Optional[Dict[str, Any]] = None,
) -> str:
    session = session or {}
    user_context = user_context or {}
    raw = session.get('inference_mode')
    if raw is None:
        raw = user_context.get('inference_mode')
    return normalize_inference_mode(raw)


def preferred_cloud_provider() -> str:
    """First non-local provider from global inference preferences."""
    try:
        from managers.inference_preferences import load_inference_preferences

        prefs = load_inference_preferences()
        chain = prefs.get('llm_fallback_chain') or ['local', 'anthropic', 'openai']
        for prov in chain:
            p = str(prov).strip().lower()
            if p and p != 'local':
                return 'anthropic' if p == 'anthropic' else 'openai'
    except Exception:
        pass
    return 'anthropic'


def is_cloud_cli_slash_command(message: str) -> bool:
    """True for cloud agent CLIs (/claude, /codex, /muse, /cursor, /opencode, …)."""
    if not isinstance(message, str):
        return False
    low = message.lstrip('\ufeff\u200b\u200c\u200d\u2060').strip().lower()
    if not low.startswith('/'):
        return False
    # Legacy alias /cursor-cli and canonical /cursor
    if low == '/cursor-cli' or low.startswith('/cursor-cli '):
        return True
    if low == '/cursor' or low.startswith('/cursor '):
        return True
    if low == '/claude' or low.startswith('/claude '):
        return True
    if low == '/codex' or low.startswith('/codex '):
        return True
    if low == '/muse' or low.startswith('/muse '):
        return True
    if low == '/opencode' or low.startswith('/opencode '):
        return True
    try:
        from api.agent_harness.catalog import list_agent_manifests, match_slash_command

        matched = match_slash_command(message)
        if matched:
            # Local-first connectors (e.g. /hermes) must stay usable in Local mode.
            agent_id = matched[0]
            for manifest in list_agent_manifests():
                if manifest.id == agent_id:
                    return bool(manifest.requires_cloud)
            return True
    except Exception:
        pass
    return False


def cloud_cli_slash_blocked_message(inference_mode: Any = None) -> Optional[str]:
    """If Local mode, return a user-facing block message for cloud CLI slash cmds."""
    if normalize_inference_mode(inference_mode) != 'local':
        return None
    return (
        "❌ **Cloud CLI slash commands aren't available in Local mode.**\n\n"
        "Switch the chat to **Auto** or **Cloud** to use `/claude`, "
        "`/codex`, `/muse`, `/opencode`, `/deepseek`, `/antigravity`, or `/cursor`.\n\n"
        "In Local mode, use `/hermes` for the on-device agent."
    )


def apply_inference_mode_to_llm_node(
    node: Dict[str, Any],
    mode: str,
) -> Dict[str, Any]:
    """Return a copy of an LLM node with provider/type overridden when mode is local or cloud."""
    mode = normalize_inference_mode(mode)
    if mode == 'auto':
        return node

    n = dict(node)
    cfg = dict(n.get('config') or {})
    node_type = n.get('type', '')

    if mode == 'local':
        cfg['provider'] = 'local'
        if node_type == 'llm' or node_type.startswith('llm-'):
            n['type'] = 'llm-local'
    elif mode == 'cloud':
        prov = preferred_cloud_provider()
        cfg['provider'] = prov
        if node_type == 'llm' or node_type.startswith('llm-'):
            n['type'] = 'llm-anthropic' if prov == 'anthropic' else 'llm-openai'

    n['config'] = cfg
    return n


def llm_fallback_chain_for_mode(mode: str, default_chain: Optional[list] = None) -> list:
    """Filter or replace the LLM fallback chain for remote-agent chat fallback."""
    chain = list(default_chain or ['local', 'anthropic', 'openai'])
    mode = normalize_inference_mode(mode)
    if mode == 'local':
        return ['local']
    if mode == 'cloud':
        filtered = [p for p in chain if str(p).strip().lower() != 'local']
        return filtered or ['anthropic', 'openai']
    return chain
