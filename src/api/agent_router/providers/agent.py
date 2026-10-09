"""Restricted agent CLI routing brain; never re-enters chat dispatch."""
from api.agent_router.providers.background import decide_background
from api.background_helpers import has_selection


class AgentCliRouterProvider:
    name = 'agent'

    def decide(self, context, config):
        chain = None if has_selection('router') else [{'kind':'cli','target':config.provider.agent_id,
            'model':config.provider.agent_model if config.provider.agent_model != 'auto' else '',
            'auth':'subscription'}]
        return decide_background(context, config, chain=chain)
