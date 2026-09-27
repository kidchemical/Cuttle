"""Sandbox allow/deny evaluation for pipeline tool nodes (mirrors deny-list + allowlist pattern)."""

from __future__ import annotations

from typing import Optional, Set, Tuple


def sandbox_evaluate_tool_node(
    node_type: str,
    allowed: Optional[Set[str]],
    denied_tools: list,
    denied_prefixes: list,
) -> Tuple[bool, Optional[str]]:
    """
    Decide whether a tool-* node may run under sandbox.

    Deny rules override allowlist. When ``allowed`` is None, sandbox tool rules are inactive.

    Returns:
        (True, None) if the node may run.
        (False, reason) if skipped (user-visible explanation fragment).
    """
    if allowed is None:
        return True, None
    if not node_type.startswith("tool-"):
        return True, None

    nt = node_type.strip()
    nt_lower = nt.lower()
    denied_names = {str(x).lower() for x in (denied_tools or []) if x}
    if nt_lower in denied_names:
        return False, f"blocked by sandbox denied_tools ({nt})"

    prefixes = tuple(str(p).lower() for p in (denied_prefixes or []) if p and str(p).strip())
    for p in prefixes:
        if nt_lower.startswith(p):
            return False, f"blocked by sandbox denied_tool_prefixes (matches {p!r})"

    if nt not in allowed:
        return False, "not listed in sandbox allowed_tools"

    return True, None
