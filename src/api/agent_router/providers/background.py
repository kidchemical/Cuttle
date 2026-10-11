"""Routing decisions using the configured background text execution chain."""
from api.agent_router.providers.base import ProviderError, build_routing_prompt
from api.agent_router.providers.api_openai import _extract_json, parse_and_validate_decision
from api.background_helpers import generate
from api.model_providers.types import TextRequest


def decide_background(context, config, *, chain=None):
    prompts = build_routing_prompt(context, config)
    result = generate('router', TextRequest(prompts['user'], system=prompts['system'],
                      max_tokens=300, temperature=0, timeout=30, json_object=True), chain=chain)
    payload = _extract_json(result.text if result else '')
    if not payload:
        raise ProviderError('Background routing brain returned no valid JSON; check Background helpers and its login', retryable=True)
    return parse_and_validate_decision(payload, config)


class BackgroundRouterProvider:
    name = 'background'

    def decide(self, context, config):
        return decide_background(context, config)
