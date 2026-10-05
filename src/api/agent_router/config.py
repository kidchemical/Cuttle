"""Persist agent router config in settings.json via SettingsManager."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from api.agent_router.registry import (
    DEFAULT_CODEX_FRONTIER,
    DEFAULT_CURSOR_AUTO,
    DEFAULT_CURSOR_GROK,
    validate_execution_target,
    validate_openai_router_model,
)
from api.agent_router.types import (
    VALID_MODES,
    ExecutionTarget,
    FallbackPolicy,
    RouterConfig,
    RouterMode,
    RouterProviderConfig,
)

SETTINGS_KEY = "agent_router"


def _default_config() -> RouterConfig:
    return RouterConfig(
        provider=RouterProviderConfig(
            mode=RouterMode.API.value,
            api_provider="openai",
            api_model="gpt-4o-mini",
        ),
        default_target=DEFAULT_CURSOR_AUTO,
        escalation_target=DEFAULT_CURSOR_GROK,
        fallbacks=FallbackPolicy(ordered=[DEFAULT_CODEX_FRONTIER]),
    )


def _parse_target(raw: Any, fallback: ExecutionTarget) -> ExecutionTarget:
    t = ExecutionTarget.from_dict(raw)
    if t is None:
        return fallback
    validated, err = validate_execution_target(
        t.agent, t.model, allow_empty_model=True, effort=t.effort
    )
    return validated if validated and not err else fallback


def load_router_config() -> RouterConfig:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    try:
        # Agents edit settings.json on disk directly; never route on a stale copy.
        sm.reload()
    except Exception:
        pass
    raw = sm.get_setting(SETTINGS_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    base = _default_config()
    prov_raw = raw.get("provider") if isinstance(raw.get("provider"), dict) else {}
    mode = str(prov_raw.get("mode") or raw.get("mode") or base.provider.mode).strip().lower()
    if mode not in VALID_MODES:
        mode = RouterMode.API.value

    api_model = str(prov_raw.get("api_model") or raw.get("api_model") or base.provider.api_model).strip()
    ok_model, _ = validate_openai_router_model(api_model)
    if not ok_model:
        api_model = base.provider.api_model
    api_provider = str(prov_raw.get("api_provider") or "openai").strip() or "openai"
    try:
        from api.jev.config import is_jev_model_id

        if is_jev_model_id(api_model):
            api_provider = "jev"
    except Exception:
        pass

    provider = RouterProviderConfig(
        mode=mode,
        api_provider=api_provider,
        api_model=api_model,
        local_endpoint=str(prov_raw.get("local_endpoint") or "").strip(),
        local_model=str(prov_raw.get("local_model") or "").strip(),
        agent_id=str(prov_raw.get("agent_id") or "cursor").strip() or "cursor",
        agent_model=str(prov_raw.get("agent_model") or "auto").strip() or "auto",
    )

    default_target = _parse_target(raw.get("default_target"), base.default_target)
    escalation_target = _parse_target(raw.get("escalation_target"), base.escalation_target)

    fb_raw = raw.get("fallbacks")
    ordered: List[ExecutionTarget] = []
    if isinstance(fb_raw, dict) and isinstance(fb_raw.get("ordered"), list):
        items = fb_raw["ordered"]
    elif isinstance(fb_raw, list):
        items = fb_raw
    else:
        items = [t.to_dict() for t in base.fallbacks.ordered]
    for item in items:
        t = ExecutionTarget.from_dict(item)
        if not t:
            continue
        validated, err = validate_execution_target(
            t.agent, t.model, allow_empty_model=True, effort=t.effort
        )
        if validated and not err and validated.key() not in {x.key() for x in ordered}:
            ordered.append(validated)
    if not ordered:
        ordered = list(base.fallbacks.ordered)

    return RouterConfig(
        provider=provider,
        default_target=default_target,
        escalation_target=escalation_target,
        fallbacks=FallbackPolicy(ordered=ordered),
    )


def save_router_config(cfg: RouterConfig) -> RouterConfig:
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    # Merge — the agent_router key also holds subkeys owned by other modules
    # (use_cases, demotions) that must survive a config save.
    try:
        sm.reload()
    except Exception:
        pass
    raw = sm.get_setting(SETTINGS_KEY)
    if not isinstance(raw, dict):
        raw = {}
    raw.update(cfg.to_dict())
    sm.set_setting(SETTINGS_KEY, raw)
    return load_router_config()


def update_router_config(**updates: Any) -> Tuple[RouterConfig, Optional[str]]:
    """Apply validated updates. Returns (config, error)."""
    cfg = load_router_config()
    prov = cfg.provider

    if "mode" in updates and updates["mode"] is not None:
        mode = str(updates["mode"]).strip().lower()
        if mode not in VALID_MODES:
            return cfg, f"Invalid mode `{mode}`. Use: {', '.join(sorted(VALID_MODES))}."
        prov.mode = mode

    if "api_model" in updates and updates["api_model"] is not None:
        mid, err = validate_openai_router_model(str(updates["api_model"]))
        if err:
            return cfg, err
        prov.api_model = mid or prov.api_model
        try:
            from api.jev.config import is_jev_model_id

            prov.api_provider = "jev" if is_jev_model_id(prov.api_model) else "openai"
        except Exception:
            prov.api_provider = "openai"

    if "local_model" in updates and updates["local_model"] is not None:
        mid = str(updates["local_model"]).strip()
        if not mid:
            return cfg, "Local model id is required."
        prov.local_model = mid

    if "local_endpoint" in updates and updates["local_endpoint"] is not None:
        ep = str(updates["local_endpoint"]).strip()
        if not ep:
            return cfg, "Local endpoint is required."
        prov.local_endpoint = ep

    if "agent_id" in updates and updates["agent_id"] is not None:
        from api.agent_router.registry import normalize_agent_id

        aid = normalize_agent_id(str(updates["agent_id"]))
        if not aid:
            return cfg, f"Unknown router agent `{updates['agent_id']}`."
        prov.agent_id = aid

    if "agent_model" in updates and updates["agent_model"] is not None:
        mid = str(updates["agent_model"]).strip()
        if not mid:
            return cfg, "Router agent model is required."
        # Soft-validate when agent is cursor
        if prov.agent_id == "cursor":
            t, err = validate_execution_target("cursor", mid)
            if err:
                return cfg, err
            mid = t.model if t else mid
        prov.agent_model = mid

    if "default_target" in updates and updates["default_target"] is not None:
        raw = updates["default_target"]
        if isinstance(raw, ExecutionTarget):
            t, err = validate_execution_target(
                raw.agent, raw.model, strict_cursor_models=True, effort=raw.effort
            )
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            t, err = validate_execution_target(
                str(raw[0]), str(raw[1]), strict_cursor_models=True
            )
        elif isinstance(raw, dict):
            t, err = validate_execution_target(
                str(raw.get("agent") or ""),
                str(raw.get("model") or ""),
                strict_cursor_models=True,
                effort=raw.get("effort"),
            )
        else:
            return cfg, "default_target must be {agent, model}."
        if err or not t:
            return cfg, err or "Invalid default target."
        cfg.default_target = t

    if "escalation_target" in updates and updates["escalation_target"] is not None:
        raw = updates["escalation_target"]
        if isinstance(raw, ExecutionTarget):
            t, err = validate_execution_target(
                raw.agent, raw.model, strict_cursor_models=True, effort=raw.effort
            )
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            t, err = validate_execution_target(
                str(raw[0]), str(raw[1]), strict_cursor_models=True
            )
        elif isinstance(raw, dict):
            t, err = validate_execution_target(
                str(raw.get("agent") or ""),
                str(raw.get("model") or ""),
                strict_cursor_models=True,
                effort=raw.get("effort"),
            )
        else:
            return cfg, "escalation_target must be {agent, model}."
        if err or not t:
            return cfg, err or "Invalid escalation target."
        cfg.escalation_target = t

    if "fallbacks" in updates and updates["fallbacks"] is not None:
        items = updates["fallbacks"]
        if not isinstance(items, list):
            return cfg, "fallbacks must be a list of {agent, model}."
        ordered: List[ExecutionTarget] = []
        for item in items:
            if isinstance(item, ExecutionTarget):
                t, err = validate_execution_target(
                    item.agent, item.model, allow_empty_model=True, effort=item.effort
                )
            elif isinstance(item, dict):
                t, err = validate_execution_target(
                    str(item.get("agent") or ""),
                    str(item.get("model") or ""),
                    allow_empty_model=True,
                    effort=item.get("effort"),
                )
            else:
                return cfg, f"Invalid fallback entry: {item!r}"
            if err or not t:
                return cfg, err or "Invalid fallback."
            if t.key() not in {x.key() for x in ordered}:
                ordered.append(t)
        cfg.fallbacks = FallbackPolicy(ordered=ordered)

    cfg.provider = prov
    return save_router_config(cfg), None


def reset_router_config() -> RouterConfig:
    return save_router_config(_default_config())


def add_fallback(agent: str, model: str) -> Tuple[RouterConfig, Optional[str]]:
    cfg = load_router_config()
    t, err = validate_execution_target(agent, model, allow_empty_model=True)
    if err or not t:
        return cfg, err or "Invalid fallback."
    ordered = list(cfg.fallbacks.ordered)
    if t.key() in {x.key() for x in ordered}:
        return cfg, f"Fallback `{t.agent} {t.model or '(default)'}` already present."
    ordered.append(t)
    return update_router_config(fallbacks=ordered)


def remove_fallback(agent: str, model: str) -> Tuple[RouterConfig, Optional[str]]:
    cfg = load_router_config()
    t, err = validate_execution_target(agent, model, allow_empty_model=True)
    if err or not t:
        return cfg, err or "Invalid fallback."
    ordered = [x for x in cfg.fallbacks.ordered if x.key() != t.key()]
    if len(ordered) == len(cfg.fallbacks.ordered):
        return cfg, f"Fallback `{t.agent} {t.model or '(default)'}` not found."
    return update_router_config(fallbacks=ordered)
