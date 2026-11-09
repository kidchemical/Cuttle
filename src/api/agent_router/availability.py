"""Can this agent account run right now? One answer for the router and orchestration.

Combines the two signals Cuttle has before spending a run:

- ``quota``: an earlier run failed with "out of usage" (cooldown until the reset it named);
- ``budget``: live plan windows (the ``/usage`` numbers), blocked or nearly spent.

States: ``blocked`` (would fail — skip it), ``low`` (little headroom — run it
after healthy ones), ``ok``, ``unknown`` (no usage API; keep declared order).
A cooldown whose error named its reset wins. One whose reset was guessed (the
default re-probe window) yields to a live usage snapshot fetched after it was
marked that does not show the account blocked: live plan windows are facts, a
guessed cooldown is not.

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
    rec = (quota.record(target) or {}) if quota.is_exhausted(target) else None
    live = None
    if accounts is not None:
        bset = settings or budget.budget_settings()
        live = budget.target_state(target, accounts, low_headroom=bset["low_headroom"])
    if rec is not None and not _live_overrides(rec, target, accounts, live):
        return "blocked", (quota.reason(target) if verbose else "") or "out of usage"
    if live is None:
        return "ok", ""
    state, why = live
    if state == "low" and not bset["enabled"]:
        return "ok", ""
    return state, why


def _live_overrides(rec: Dict, target: ExecutionTarget, accounts, live) -> bool:
    """A guessed cooldown yields to a newer live snapshot that is not blocked."""
    if rec.get("reset_named") or live is None or live[0] in ("blocked", "unknown"):
        return False
    marked_at = rec.get("marked_at")
    if not isinstance(marked_at, (int, float)):
        return False  # a mark from before marked_at existed: let it expire on its own
    acc = (accounts or {}).get(target.agent)
    return bool(acc and not acc.error and acc.fetched_at > marked_at)
