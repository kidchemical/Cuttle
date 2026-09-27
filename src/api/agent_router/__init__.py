"""
Cuttle Agent Router — select agent CLI + model above sticky-slash dispatch.

See ``docs/guides/AGENT_ROUTER.md``.
"""

from __future__ import annotations

from api.agent_router.commands import (
    format_status,
    handle_retry_command,
    handle_route_command,
    handle_router_command,
    parse_retry_command,
    parse_route_command,
    parse_router_command,
)
from api.agent_router.config import load_router_config, reset_router_config, save_router_config
from api.agent_router.dispatch import (
    execute_decision,
    execute_explicit_target,
    get_last_failure,
    remember_failure,
)
from api.agent_router.engine import (
    build_context,
    decide,
    decide_with_outcome,
    routing_brain_active,
    should_invoke_router,
)
from api.agent_router.types import (
    ExecutionTarget,
    RouterConfig,
    RoutingDecision,
    TargetSource,
)

__all__ = [
    "ExecutionTarget",
    "RouterConfig",
    "RoutingDecision",
    "TargetSource",
    "build_context",
    "decide",
    "decide_with_outcome",
    "execute_decision",
    "execute_explicit_target",
    "format_status",
    "get_last_failure",
    "handle_retry_command",
    "handle_route_command",
    "handle_router_command",
    "load_router_config",
    "parse_retry_command",
    "parse_route_command",
    "parse_router_command",
    "remember_failure",
    "reset_router_config",
    "routing_brain_active",
    "save_router_config",
    "should_invoke_router",
]
