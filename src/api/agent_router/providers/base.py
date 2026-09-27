"""Router provider protocol — routing brain only (never the task executor)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from api.agent_router.types import RouterConfig, RoutingContext, RoutingDecision


class RouterProvider(Protocol):
    name: str

    def decide(
        self,
        context: RoutingContext,
        config: RouterConfig,
    ) -> RoutingDecision:
        """Return a validated RoutingDecision or raise ProviderError."""
        ...


class ProviderError(Exception):
    """Routing brain failed (transport, auth, timeout, empty response)."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def build_routing_prompt(context: RoutingContext, config: RouterConfig) -> Dict[str, str]:
    """Compact system + user prompts — no full repo or chat history."""
    targets = context.available_targets or [
        config.default_target,
        config.escalation_target,
        *config.fallbacks.ordered,
    ]
    # Dedupe
    seen = set()
    target_lines = []
    for t in targets:
        k = t.key()
        if k in seen:
            continue
        seen.add(k)
        target_lines.append(f"- agent={t.agent} model={t.model or '(default)'}")

    system = (
        "You are Cuttle's agent router. Select ONE execution target for the user request. "
        "Minimize cost and latency while preserving a high chance of success. "
        "Prefer Cursor Agent with model auto for ordinary low/medium coding work. "
        "Reserve Cursor grok-4.6 for clearly hard/ambiguous/architecture/debugging work. "
        "Do not invent agents or models. Reply with ONLY compact JSON matching the schema."
    )
    user = (
        f"project_name: {context.project_name or '(unknown)'}\n"
        f"project_id: {context.project_id or '(none)'}\n"
        f"code_changes_requested: {bool(context.code_changes_requested)}\n"
        f"constraints: {(context.explicit_constraints or '')[:400] or '(none)'}\n"
        f"allowed_targets:\n" + "\n".join(target_lines) + "\n"
        f"default_target: {config.default_target.agent} {config.default_target.model}\n"
        f"escalation_target: {config.escalation_target.agent} {config.escalation_target.model}\n"
        f"user_request:\n{(context.user_request or '')[:2000]}\n\n"
        "JSON schema:\n"
        '{"task_type":"basic_ask|coding|debugging|architecture|research|other",'
        '"difficulty":"low|medium|high",'
        '"target_agent":"<id>","target_model":"<id>",'
        '"confidence":0.0,"reason":"short",'
        '"escalation_target":{"agent":"<id>","model":"<id>"}}'
    )
    return {"system": system, "user": user}
