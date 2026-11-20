"""How long one agent turn may sit idle waiting on its own background tasks.

A Claude Code turn that ends a reply while the CLI still lists background tasks
stays open so their completion can continue it (``scripts.utilities.claude_cli_tool``).
That idle time is one budget for the whole turn: a follow-up reply does not
refill it. It used to restart at every root ``result``, so a turn whose leftover
waiters never exited (a ``pgrep -f`` loop matching its own shell, CH-002669)
stayed open for 78 minutes after its answer.

A sub-agent child turn gets a much smaller budget: its parent's batch is blocked
until the turn ends, and nobody steers it. The turn's owner passes that cap
(``WaitCap`` from ``api.subagents.turns``); a capped turn also never detaches,
because the process running it does not outlive the batch. The owner may name
work that earns the full budget (``WaitCap.hold``: the child is supervising
sub-agents of its own), checked once when the cap runs out.

Pure: no Cuttle imports, clock injected.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

CHILD_WAIT_ENV = "CLAUDE_SUBAGENT_BACKGROUND_WAIT_SEC"
CHILD_WAIT_DEFAULT_SEC = 300.0


def child_wait_seconds() -> float:
    """Idle-wait cap for a sub-agent child turn (0 = end at the first reply)."""
    raw = (os.getenv(CHILD_WAIT_ENV) or "").strip()
    try:
        return max(0.0, float(raw)) if raw else CHILD_WAIT_DEFAULT_SEC
    except (TypeError, ValueError):
        return CHILD_WAIT_DEFAULT_SEC


@dataclass(frozen=True)
class WaitCap:
    """A turn owner's shorter idle-wait cap.

    ``hold`` answers, when the cap runs out, whether the turn still owns work
    that deserves the ordinary budget; an error counts as no.
    """

    seconds: float
    hold: Optional[Callable[[], bool]] = None

    def held(self) -> bool:
        if self.hold is None:
            return False
        try:
            return bool(self.hold())
        except Exception:
            return False


def capped(total: float, cap: Optional[WaitCap]) -> float:
    """``total`` limited by an optional per-turn ``cap`` (``None`` = no cap)."""
    if cap is None:
        return total
    try:
        return min(total, max(0.0, float(cap.seconds)))
    except (TypeError, ValueError):
        return total


class WaitBudget:
    """Idle seconds a turn may still wait; only time between ``arm`` and ``pause`` counts."""

    def __init__(self, total: float, clock: Callable[[], float] = time.monotonic):
        self.total = max(0.0, float(total))
        self.spent = 0.0
        self._clock = clock
        self._since: Optional[float] = None

    def arm(self) -> float:
        """A wait starts (or restarts): seconds left before the turn gives up."""
        self.pause()
        self._since = self._clock()
        return max(0.0, self.total - self.spent)

    def extend(self, total: float) -> bool:
        """Raise the budget to ``total``; False when it was already that large."""
        if float(total) <= self.total:
            return False
        self.total = float(total)
        return True

    def pause(self) -> None:
        """The turn is working again (or over): stop counting."""
        if self._since is not None:
            self.spent += max(0.0, self._clock() - self._since)
            self._since = None
