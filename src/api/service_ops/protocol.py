"""Service-op protocol — the pluggable contract for external side effects.

A **service op** is a nameable external action type (``discord.post``, and — via
the same seam — Slack/Mattermost/… tomorrow) that a project action can invoke.
The owner of the *behavior* stays its own module (``api.discord_ops``); the op is
a thin adapter that registers the action type(s) and executes it. ``shell`` is a
built-in Cuttle capability, not a service op.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable


@dataclass(frozen=True)
class ServiceOpInfo:
    id: str
    label: str
    action_types: Tuple[str, ...]
    description: str = ""
    source: str = ""


@runtime_checkable
class ServiceOp(Protocol):
    """Every external side-effect handler implements this."""

    id: str
    label: str
    action_types: Tuple[str, ...]

    def available(self, action: Dict[str, Any], project_path: Optional[str] = None) -> bool:
        """True when the op may run for this action (feature/opt-in gate)."""
        ...

    def execute(self, action: Dict[str, Any], params: Dict[str, Any], *,
                session_id: Optional[str] = None) -> Dict[str, Any]:
        """Run the side effect. Returns ``{success, response?, error?, url?}``."""
        ...
