"""Can this agent account run right now? One answer for the router and orchestration.

Combines the two signals Cuttle has before spending a run:

- ``quota``: an earlier run failed with "out of usage" (cooldown until the reset it named);
- ``budget``: live plan windows (the ``/usage`` numbers), blocked or nearly spent.

States: ``blocked`` (would fail — skip it), ``low`` (little headroom — run it
after healthy ones), ``ok``, ``unknown`` (no usage API; keep declared order).
``low`` is only reported when ``agent_router.budget.enabled`` is on: that switch
opts into reordering by headroom. ``blocked`` from live usage is reported
whenever a snapshot is passed in.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from api.agent_router import budget, quota
from api.agent_router.types import ExecutionTarget

RANK = {"ok": 0, "unknown": 0, "low": 1, "blocked": 2}


def target_state(
    target: ExecutionTarget,
    *,
    accounts: Optional[Dict[str, budget.Account]],
    settings: Optional[Dict] = None,
    verbose: bool = False,
) -> Tuple[str, str]:
    """(state, short why). ``accounts=None`` skips live usage (cooldowns only);
    ``verbose`` adds a cooldown's reset time to the why."""
    if quota.is_exhausted(target):
        return "blocked", (quota.reason(target) if verbose else "") or "out of usage"
    if accounts is None:
        return "ok", ""
    bset = settings or budget.budget_settings()
    state, why = budget.target_state(target, accounts, low_headroom=bset["low_headroom"])
    if state == "low" and not bset["enabled"]:
        return "ok", ""
    return state, why
