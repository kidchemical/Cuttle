"""Per-agent starred model/effort defaults + canonical resolution order.

User policy (CH-000419):

1. This chat's model/effort pin (if any)
2. Else the globally starred model/effort for that agent
3. Else the **CLI's own default**
4. Cuttle must never invent a third default that silently overrides the CLI.

Agent-agnostic storage lives here (settings.json). CLI-specific default
lookup stays in each adapter (``resolve_*_default_model``) — this module only
combines the three layers and names the winning source for badges.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

MODELS_KEY = "agent_starred_models"
EFFORTS_KEY = "agent_starred_efforts"

SOURCE_SESSION = "session"
SOURCE_STARRED = "starred"
SOURCE_OVERRIDE = "override"
SOURCE_CLI_DEFAULT = "cli_default"
SOURCE_NONE = "none"


def normalize_agent_id(agent_id: Any) -> str:
    aid = (str(agent_id or "").strip().lower().replace("_", "-"))
    return aid


def _settings() -> Any:
    from managers.settings_manager import get_settings_manager

    return get_settings_manager()


def _read_map(key: str) -> Dict[str, str]:
    try:
        raw = _settings().get_setting(key) or {}
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        aid = normalize_agent_id(k)
        val = str(v or "").strip()
        if aid and val:
            out[aid] = val
    return out


def _write_map(key: str, data: Dict[str, str]) -> None:
    _settings().set_setting(key, dict(data))


def get_starred_model(agent_id: str) -> Optional[str]:
    return _read_map(MODELS_KEY).get(normalize_agent_id(agent_id))


def set_starred_model(agent_id: str, model: Optional[str]) -> Optional[str]:
    aid = normalize_agent_id(agent_id)
    if not aid:
        return None
    data = _read_map(MODELS_KEY)
    m = str(model or "").strip()
    if m:
        data[aid] = m
    else:
        data.pop(aid, None)
    _write_map(MODELS_KEY, data)
    return m or None


def get_starred_effort(agent_id: str) -> Optional[str]:
    raw = _read_map(EFFORTS_KEY).get(normalize_agent_id(agent_id))
    return raw.strip().lower() if raw else None


def set_starred_effort(agent_id: str, effort: Optional[str]) -> Optional[str]:
    aid = normalize_agent_id(agent_id)
    if not aid:
        return None
    data = _read_map(EFFORTS_KEY)
    e = str(effort or "").strip().lower()
    if e:
        data[aid] = e
    else:
        data.pop(aid, None)
    _write_map(EFFORTS_KEY, data)
    return e or None


def resolve_effective_model(
    agent_id: str,
    *,
    session_model: Optional[str] = None,
    kernel_override: Optional[str] = None,
    cli_default: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """Return ``(model_or_None, source)`` in user policy order.

    ``kernel_override`` is an explicit per-turn caller override (router /
    supervised profile) — it beats the global star but not the chat pin.
    Empty ``cli_default`` means "omit the flag, the CLI decides" → source
    ``cli_default`` with value None so badges can say "CLI default" honestly.
    """
    sess = (str(session_model or "").strip() or None)
    if sess:
        return sess, SOURCE_SESSION
    over = (str(kernel_override or "").strip() or None)
    if over:
        return over, SOURCE_OVERRIDE
    starred = get_starred_model(agent_id)
    if starred:
        return starred, SOURCE_STARRED
    cli = (str(cli_default or "").strip() or None)
    if cli:
        return cli, SOURCE_CLI_DEFAULT
    return None, SOURCE_CLI_DEFAULT


def resolve_effective_effort(
    agent_id: str,
    *,
    session_effort: Optional[str] = None,
    kernel_override: Optional[str] = None,
    cli_default: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    sess = (str(session_effort or "").strip().lower() or None)
    if sess:
        return sess, SOURCE_SESSION
    over = (str(kernel_override or "").strip().lower() or None)
    if over:
        return over, SOURCE_OVERRIDE
    starred = get_starred_effort(agent_id)
    if starred:
        return starred, SOURCE_STARRED
    cli = (str(cli_default or "").strip().lower() or None)
    if cli:
        return cli, SOURCE_CLI_DEFAULT
    return None, SOURCE_NONE


def agent_capabilities(agent_id: str) -> Dict[str, bool]:
    """Manifest-declared CLI capabilities (palette + API gate on these).

    Unknown agents default to full support so drop-ins are not locked out.
    """
    aid = normalize_agent_id(agent_id)
    try:
        from api.agent_harness.catalog import get_agent

        pair = get_agent(aid)
        if pair is not None:
            manifest = pair[0]
            return {
                "supports_model_pin": bool(manifest.supports_model_pin),
                "supports_effort": bool(manifest.supports_effort),
            }
    except Exception:
        pass
    return {"supports_model_pin": True, "supports_effort": True}


def badge_meta(
    agent_id: str,
    model: Optional[str],
    model_source: str,
    effort: Optional[str] = None,
    effort_source: str = SOURCE_NONE,
) -> Dict[str, Any]:
    """Canonical badge payload — one shape for every agent.

    Generic keys only (``agent_model`` / ``model_source`` / ``agent_effort``
    / ``effort_source``). Per-agent ``<id>_model`` / ``<id>_effort`` duplicates
    were dropped (CH-000419 #2): readers accept them on old payloads, but no
    new turn emits them.
    """
    meta: Dict[str, Any] = {
        "agent_model": (str(model or "").strip() or None),
        "model_source": model_source,
    }
    if effort:
        meta["agent_effort"] = str(effort).strip().lower()
        meta["effort_source"] = effort_source
    else:
        meta["effort_source"] = SOURCE_NONE
    return meta
