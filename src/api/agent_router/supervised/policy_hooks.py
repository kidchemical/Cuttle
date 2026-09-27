"""Policy hooks for supervised strategy selection (inactive by default)."""

from __future__ import annotations

from typing import Optional, Tuple

from api.agent_router.supervised.profiles import load_supervised_settings
from api.agent_router.supervised.types import EconomicSource, RoutingStrategy
from api.agent_router.types import (
    ExecutionTarget,
    RoutingDecision,
    TargetSource,
)


def make_supervised_decision(
    *,
    reason: str,
    task_type: str = "coding",
    difficulty: str = "medium",
    confidence: float = 0.7,
    profile_id: str = "diet-frontier",
    worker: Optional[ExecutionTarget] = None,
    source: str = TargetSource.ROUTER.value,
) -> RoutingDecision:
    """Build a typed supervised routing decision (not a fake agent name)."""
    from api.agent_router.supervised.profiles import get_profile, profile_objects

    prof = get_profile(profile_id)
    _coord, wprof, _budget = profile_objects(prof)
    tgt = worker or ExecutionTarget(
        agent=wprof.harness.agent or "cursor",
        model=wprof.harness.model or "auto",
    )
    return RoutingDecision(
        decision_id=RoutingDecision.new_id(),
        task_type=task_type,
        difficulty=difficulty,
        target=tgt,
        confidence=confidence,
        reason=reason,
        escalation_target=ExecutionTarget(agent="cursor", model="grok-4.6"),
        source=source,
        strategy=RoutingStrategy.SUPERVISED.value,
        supervised_profile=str(prof.get("id") or profile_id),
    )


def supervised_frontier_fallback_candidate(
    *,
    failure_reason: str = "",
) -> Tuple[Optional[RoutingDecision], Optional[str]]:
    """
    If configured, return a supervised decision as frontier fallback.

    Default settings keep this **disabled**. Never silently selects paid API
    economic sources — diet-frontier uses ChatGPT allocation + Cursor Auto promo.
    """
    settings = load_supervised_settings()
    if not settings.get("supervised_as_frontier_fallback"):
        return None, "supervised frontier fallback disabled"
    if settings.get("require_paid_approval", True):
        # Still allow diet-frontier (non-API) without paid approval.
        from api.agent_router.supervised.profiles import get_profile, profile_objects

        coord, worker, _ = profile_objects(get_profile())
        for label, h in (("coordinator", coord.harness), ("worker", worker.harness)):
            if h.economic_source in (
                EconomicSource.OPENAI_API.value,
                EconomicSource.PAID_REQUIRES_APPROVAL.value,
            ):
                return None, f"blocked paid {label} without approval"
    decision = make_supervised_decision(
        reason=f"frontier unavailable → supervised fallback ({failure_reason or 'transport'})",
        source=TargetSource.FALLBACK.value,
    )
    return decision, None
