"""Service-ops: external side-effect handlers (protocol + registry)."""

from __future__ import annotations

from api.service_ops.catalog import find_op, list_ops, ops_status, register, reset_cache
from api.service_ops.protocol import ServiceOp, ServiceOpInfo

__all__ = [
    "ServiceOp", "ServiceOpInfo", "find_op", "list_ops", "ops_status",
    "register", "reset_cache",
]
