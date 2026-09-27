"""OpenAI API router provider (lightweight chat model as routing brain)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from api.agent_router.providers.base import ProviderError, build_routing_prompt
from api.agent_router.types import (
    VALID_DIFFICULTIES,
    VALID_TASK_TYPES,
    ExecutionTarget,
    RouterConfig,
    RoutingContext,
    RoutingDecision,
    TargetSource,
)
from api.agent_router.registry import validate_execution_target


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def parse_and_validate_decision(
    payload: Dict[str, Any],
    config: RouterConfig,
) -> RoutingDecision:
    task_type = str(payload.get("task_type") or "").strip().lower()
    difficulty = str(payload.get("difficulty") or "").strip().lower()
    if task_type not in VALID_TASK_TYPES:
        raise ProviderError(f"Invalid task_type `{task_type}`", retryable=True)
    if difficulty not in VALID_DIFFICULTIES:
        raise ProviderError(f"Invalid difficulty `{difficulty}`", retryable=True)

    agent = str(payload.get("target_agent") or "").strip()
    model = str(payload.get("target_model") or "").strip()
    target, err = validate_execution_target(agent, model, allow_empty_model=True)
    if err or not target:
        raise ProviderError(err or "Invalid target", retryable=True)

    esc_raw = payload.get("escalation_target")
    if isinstance(esc_raw, dict):
        esc, esc_err = validate_execution_target(
            str(esc_raw.get("agent") or ""),
            str(esc_raw.get("model") or ""),
            allow_empty_model=True,
        )
        if esc_err or not esc:
            esc = config.escalation_target
    else:
        esc = config.escalation_target

    try:
        confidence = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    reason = str(payload.get("reason") or "").strip()[:240] or "routed"

    return RoutingDecision(
        decision_id=RoutingDecision.new_id(),
        task_type=task_type,
        difficulty=difficulty,
        target=target,
        confidence=confidence,
        reason=reason,
        escalation_target=esc,
        source=TargetSource.ROUTER.value,
        raw=payload,
    )


class OpenAIApiRouterProvider:
    name = "openai_api"

    def decide(
        self,
        context: RoutingContext,
        config: RouterConfig,
    ) -> RoutingDecision:
        api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise ProviderError("OPENAI_API_KEY not set", retryable=False)

        try:
            from openai import OpenAI
        except ImportError as e:
            raise ProviderError(f"openai package missing: {e}", retryable=False) from e

        prompts = build_routing_prompt(context, config)
        model = (config.provider.api_model or "gpt-4o-mini").strip()
        client = OpenAI(api_key=api_key, timeout=30.0)

        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    temperature=0,
                    max_tokens=300,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": prompts["system"]},
                        {"role": "user", "content": prompts["user"]},
                    ],
                )
                content = ""
                if resp.choices:
                    content = (resp.choices[0].message.content or "").strip()
                payload = _extract_json(content)
                if not payload:
                    raise ProviderError("Router response was not valid JSON", retryable=True)
                return parse_and_validate_decision(payload, config)
            except ProviderError as e:
                last_err = e
                if not e.retryable or attempt >= 1:
                    raise
            except Exception as e:
                last_err = e
                # One repair/retry for malformed / transient
                if attempt >= 1:
                    raise ProviderError(str(e), retryable=True) from e
        raise ProviderError(str(last_err or "router failed"), retryable=True)
