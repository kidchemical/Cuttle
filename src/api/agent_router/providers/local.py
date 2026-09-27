"""Local / OpenAI-compatible router provider (schema-ready; not fully wired)."""

from __future__ import annotations

from api.agent_router.providers.base import ProviderError
from api.agent_router.types import RouterConfig, RoutingContext, RoutingDecision


class LocalRouterProvider:
    name = "local"

    def decide(
        self,
        context: RoutingContext,
        config: RouterConfig,
    ) -> RoutingDecision:
        endpoint = (config.provider.local_endpoint or "").strip()
        model = (config.provider.local_model or "").strip()
        if not endpoint or not model:
            raise ProviderError(
                "Local router mode requires `/router local endpoint …` and "
                "`/router local model …`. Falling back to default target.",
                retryable=False,
            )
        # Future: OpenAI-compatible chat.completions against endpoint.
        raise ProviderError(
            "Local router provider is configured but not fully implemented yet. "
            "Use `/router mode api` or `/router mode off`.",
            retryable=False,
        )
