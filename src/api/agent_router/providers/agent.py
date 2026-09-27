"""Agent-CLI router provider (schema-ready; avoids recursive routing)."""

from __future__ import annotations

from api.agent_router.providers.base import ProviderError
from api.agent_router.types import RouterConfig, RoutingContext, RoutingDecision


class AgentCliRouterProvider:
    name = "agent"

    def decide(
        self,
        context: RoutingContext,
        config: RouterConfig,
    ) -> RoutingDecision:
        # Must never call back into the chat dispatcher (would re-enter the router).
        raise ProviderError(
            "Agent router mode is configured but not fully implemented yet. "
            "Use `/router mode api` or `/router mode off`.",
            retryable=False,
        )
