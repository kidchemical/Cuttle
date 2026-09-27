"""Minimal structured event logging for the agent router."""

from __future__ import annotations

from typing import Any, Dict, Optional


def router_log(event: str, **fields: Any) -> None:
    """Emit a single-line structured log. Never log secrets or full source."""
    safe: Dict[str, Any] = {}
    for k, v in fields.items():
        if v is None:
            continue
        key = str(k)
        if any(s in key.lower() for s in ("key", "token", "secret", "password", "auth")):
            continue
        if isinstance(v, str) and len(v) > 240:
            v = v[:237] + "..."
        safe[key] = v
    parts = [f"{k}={safe[k]!r}" for k in sorted(safe)]
    msg = " ".join(parts)
    print(f"[AGENT-ROUTER] event={event} {msg}".rstrip(), flush=True)


def log_invoked(decision_id: Optional[str] = None, **kw: Any) -> None:
    router_log("router_invoked", decision_id=decision_id, **kw)


def log_bypassed(reason: str, **kw: Any) -> None:
    router_log("router_bypassed", reason=reason, **kw)


def log_decision(decision_id: str, **kw: Any) -> None:
    router_log("routing_decision", decision_id=decision_id, **kw)


def log_invalid(reason: str, **kw: Any) -> None:
    router_log("invalid_decision_rejected", reason=reason, **kw)


def log_default(reason: str, **kw: Any) -> None:
    router_log("default_route_used", reason=reason, **kw)


def log_session_bypass(agent: str, model: str = "", **kw: Any) -> None:
    router_log(
        "session_selection_bypass",
        agent=agent,
        model=model or "(unset)",
        **kw,
    )


def log_target_started(agent: str, model: str, source: str, **kw: Any) -> None:
    router_log("execution_target_started", agent=agent, model=model, source=source, **kw)


def log_fallback(agent: str, model: str, reason: str, **kw: Any) -> None:
    router_log("fallback_selected", agent=agent, model=model, reason=reason, **kw)


def log_escalation(agent: str, model: str, reason: str, **kw: Any) -> None:
    router_log("escalation_selected", agent=agent, model=model, reason=reason, **kw)


def log_cancelled(agent: str, model: str, reason: str, **kw: Any) -> None:
    router_log("run_cancelled_terminal", agent=agent, model=model, reason=reason, **kw)


def log_stale_turn(reason: str, **kw: Any) -> None:
    router_log("stale_turn_discarded", reason=reason, **kw)


def log_exhausted(attempts: int, **kw: Any) -> None:
    router_log("fallback_exhausted", attempts=attempts, **kw)


def log_quality_drift(agent: str, model: str, **kw: Any) -> None:
    router_log("quality_drift", agent=agent, model=model or "(default)", **kw)


def log_quality_recovered(agent: str, model: str, **kw: Any) -> None:
    router_log("quality_recovered", agent=agent, model=model or "(default)", **kw)
