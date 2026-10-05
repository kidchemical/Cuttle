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


def is_cloud_cli_slash_command(message: str) -> bool:
    """True for cloud agent CLIs (/claude, /codex, /muse, /cursor, /opencode, …)."""
    if not isinstance(message, str):
        return False
    low = message.lstrip('\ufeff\u200b\u200c\u200d\u2060').strip().lower()
    if not low.startswith('/'):
        return False
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
