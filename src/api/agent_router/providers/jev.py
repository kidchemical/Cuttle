"""Jev (TypeSafe System One) as the agent-router brain."""

from __future__ import annotations

from api.agent_router.providers.base import ProviderError
from api.agent_router.types import RouterConfig, RoutingContext, RoutingDecision
from api.jev.routing import decide_routing


class JevRouterProvider:
    name = "jev"

    def decide(
        self,
        context: RoutingContext,
        config: RouterConfig,
    ) -> RoutingDecision:
        return decide_routing(context, config)
