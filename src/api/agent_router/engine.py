"""Core routing engine — decide execution target without running the task."""

from __future__ import annotations

import contextvars
import re
from typing import Any, Dict, Optional, Tuple

from api.agent_router import logging_events as log
from api.agent_router.config import load_router_config
from api.agent_router.policy import looks_like_code_change_request
from api.agent_router.providers import (
    AgentCliRouterProvider,
    JevRouterProvider,
    LocalRouterProvider,
    OpenAIApiRouterProvider,
    ProviderError,
)
from api.agent_router.types import (
    RouterConfig,
    RouterMode,
    RoutingContext,
    RoutingDecision,
    TargetSource,
)

# Prevent router-provider → chat → router recursion.
_routing_brain_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "cuttle_routing_brain_depth", default=0
)


def routing_brain_active() -> bool:
    return int(_routing_brain_depth.get() or 0) > 0


class _BrainGuard:
    def __enter__(self):
        self._token = _routing_brain_depth.set(int(_routing_brain_depth.get() or 0) + 1)
        return self

    def __exit__(self, *exc):
        _routing_brain_depth.reset(self._token)


def _provider_for(config: RouterConfig):
    mode = (config.provider.mode or RouterMode.OFF.value).lower()
    if mode == RouterMode.API.value:
        from api.jev.config import is_jev_model_id

        prov = (config.provider.api_provider or "openai").strip().lower()
        if prov in ("jev", "typesafe") or is_jev_model_id(config.provider.api_model or ""):
            return JevRouterProvider()
        return OpenAIApiRouterProvider()
    if mode == RouterMode.LOCAL.value:
        return LocalRouterProvider()
    if mode == RouterMode.AGENT.value:
        return AgentCliRouterProvider()
    return None


def build_context(
    user_request: str,
    *,
    project_id: str = "",
    project_name: str = "",
    project_path: str = "",
    session_id: Any = None,
    explicit_constraints: str = "",
) -> RoutingContext:
    from api.agent_router.config import load_router_config as _load

    cfg = _load()
    targets = [cfg.default_target, cfg.escalation_target, *cfg.fallbacks.ordered]
    return RoutingContext(
        user_request=user_request or "",
        project_id=project_id or "",
        project_name=project_name or "",
        project_path=project_path or "",
        code_changes_requested=looks_like_code_change_request(user_request or ""),
        explicit_constraints=explicit_constraints or "",
        session_id=session_id,
        available_targets=targets,
    )


def default_decision(config: RouterConfig, reason: str) -> RoutingDecision:
    return RoutingDecision(
        decision_id=RoutingDecision.new_id(),
        task_type="coding",
        difficulty="medium",
        target=config.default_target,
        confidence=0.0,
        reason=reason[:240],
        escalation_target=config.escalation_target,
        source=TargetSource.DEFAULT.value,
    )


def decide_with_outcome(
    context: RoutingContext,
    config: Optional[RouterConfig] = None,
) -> Tuple[RoutingDecision, Dict[str, Any]]:
    """Production routing decision plus outcome metadata for evaluation.

    Never invokes an execution agent. On provider failure returns the
    deterministic default target and marks ``used_fallback``.

    Authority layers applied on top of the brain's choice:
    1. Phase 0 use-case table (declared preferences override the brain)
    2. Phase B drift demotions (route around flagged targets)
    """
    cfg = config or load_router_config()
    meta: Dict[str, Any] = {
        "used_fallback": False,
        "api_error": None,
        "invalid_rejected": False,
        "provider": None,
        "mode_off": False,
    }
    if not cfg.enabled():
        log.log_bypassed("mode_off")
        meta["mode_off"] = True
        meta["used_fallback"] = True
        return default_decision(cfg, "router mode off — default target"), meta

    if routing_brain_active():
        log.log_bypassed("recursive_guard")
        meta["used_fallback"] = True
        meta["api_error"] = "router recursion blocked"
        return default_decision(cfg, "router recursion blocked — default target"), meta

    provider = _provider_for(cfg)
    if provider is None:
        meta["used_fallback"] = True
        meta["api_error"] = "no provider"
        return default_decision(cfg, "no provider — default target"), meta

    meta["provider"] = getattr(provider, "name", "?")
    log.log_invoked(mode=cfg.provider.mode, provider=meta["provider"])
    decision: RoutingDecision
    try:
        with _BrainGuard():
            decision = provider.decide(context, cfg)
    except ProviderError as e:
        log.log_invalid(str(e))
        log.log_default(str(e))
        msg = str(e)
        meta["used_fallback"] = True
        meta["api_error"] = msg
        meta["invalid_rejected"] = bool(
            re.search(r"invalid|unknown|invent|unregistered|malformed|not valid json", msg, re.I)
        )
        decision = default_decision(cfg, f"router error: {e}")
    except Exception as e:
        log.log_invalid(str(e))
        log.log_default(str(e))
        meta["used_fallback"] = True
        meta["api_error"] = str(e)
        decision = default_decision(cfg, f"router error: {e}")

    # Phase 0 — declared use-case table overrides the brain's target choice.
    try:
        from api.agent_router.use_cases import apply_table

        decision, table_meta = apply_table(decision, context, cfg)
        meta.update(table_meta)
    except Exception as exc:
        meta["table_error"] = str(exc)[:160]

    # Phase B — avoid targets currently flagged for quality regression.
    try:
        from api.agent_router.drift import apply_demotion_avoidance

        decision, drift_meta = apply_demotion_avoidance(decision, cfg)
        meta.update(drift_meta)
    except Exception as exc:
        meta["drift_error"] = str(exc)[:160]

    log.log_decision(
        decision.decision_id,
        agent=decision.target.agent,
        model=decision.target.model,
        task_type=decision.task_type,
        difficulty=decision.difficulty,
        confidence=decision.confidence,
        reason=decision.reason,
    )
    return decision, meta


def decide(
    context: RoutingContext,
    config: Optional[RouterConfig] = None,
) -> RoutingDecision:
    """Produce a routing decision. Never raises — falls back to default target."""
    decision, _meta = decide_with_outcome(context, config)
    return decision


def session_has_agent_selection(
    message: str,
    session_id: Any = None,
) -> Tuple[bool, str, str]:
    """
    Return (bypass, agent_or_prefix, detail).

    Sticky slash on the message or inferred from session history means the user
    (or a starred command that ran as a normal command) already selected an agent.
    """
    try:
        from api.starred_slash import infer_session_sticky_prefix, sticky_prefix_from_text
    except Exception:
        sticky_prefix_from_text = None  # type: ignore
        infer_session_sticky_prefix = None  # type: ignore

    if sticky_prefix_from_text:
        prefix = sticky_prefix_from_text(message or "")
        if prefix:
            agent = prefix.strip().lstrip("/").split()[0]
            return True, agent, f"message starts with `{prefix.strip()}`"

    if session_id is not None and infer_session_sticky_prefix:
        prefix = infer_session_sticky_prefix(session_id)
        if prefix:
            agent = prefix.strip().lstrip("/").split()[0]
            return True, agent, f"session sticky `{prefix.strip()}`"

    return False, "", ""


def should_invoke_router(
    message: str,
    *,
    session_id: Any = None,
    config: Optional[RouterConfig] = None,
) -> Tuple[bool, str]:
    """Whether this turn should ask the routing brain (not yet run an agent)."""
    cfg = config or load_router_config()
    if not cfg.enabled():
        return False, "router mode off"
    if routing_brain_active():
        return False, "routing brain active (recursion guard)"

    text = (message or "").strip()
    if not text:
        return False, "empty message"

    # Meta / non-task commands — leave to existing handlers.
    low = text.lower()
    if low.startswith("/router"):
        return False, "router config command"
    if low.startswith("/retry"):
        return False, "retry command"
    if low.startswith("/route ") or low == "/route":
        return False, "explicit route command"
    if low.startswith("/coordinator"):
        return False, "coordinator config command"
    if low.startswith("/coordinate"):
        return False, "coordinate command"
    if low.startswith("/restart"):
        return False, "restart control command"
    meta_prefixes = (
        "/help",
        "/pipelines",
        "/pipeline ",
        "/invite",
        "/cmd ",
        "[button:",
        "[action-form:",
    )
    for p in meta_prefixes:
        if low.startswith(p):
            return False, f"meta command {p.strip()}"

    bypass, agent, detail = session_has_agent_selection(text, session_id)
    if bypass:
        log.log_session_bypass(agent, detail=detail)
        return False, detail

    # Explicit harness slash without going through sticky helper (same idea).
    try:
        from api.agent_harness.catalog import match_slash_command

        matched = match_slash_command(text)
        if matched:
            return False, f"explicit /{matched[0]}"
    except Exception:
        for agent_slash in (
            "/cursor",
            "/codex",
            "/claude",
            "/hermes",
            "/muse",
            "/deepseek",
            "/antigravity",
            "/opencode",
        ):
            if low == agent_slash or low.startswith(agent_slash + " ") or low.startswith(
                agent_slash + "-cli"
            ):
                return False, f"explicit {agent_slash}"

    return True, "no session agent selected"
