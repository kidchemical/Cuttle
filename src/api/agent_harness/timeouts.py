"""Idle-aware turn budgets shared by streaming harness adapters.

Cuttle's kernel hands every turn a wall-clock budget (default 3600s). For
adapters that stream subprocess output (e.g. OpenCode JSONL events), that
budget is better spent as an **idle** budget: the deadline resets whenever the
CLI produces output, so long productive turns are not killed mid-task — only
turns that are actually stuck (silent for the whole budget) are aborted.

An absolute cap (default 4x the idle budget) still applies so a chatty
runaway turn cannot run forever. Buffer-everything adapters (single
``communicate()`` call) cannot observe intermediate output and keep the old
absolute semantics.
"""

from __future__ import annotations

import time
import threading
from typing import Callable, Optional

ABSOLUTE_MULTIPLIER = 4.0
ABSOLUTE_MIN_EXTRA = 600.0

# "No limit" as a finite budget: every adapter does deadline arithmetic and
# passes the value to asyncio/threading waits, which reject float('inf').
# Windows caps those waits at threading.TIMEOUT_MAX (~49.7 days), and the
# absolute cap multiplies the idle budget, so stay well under it there.
UNLIMITED_TURN_SECONDS = min(
    10 * 365 * 86400.0, threading.TIMEOUT_MAX / (2 * ABSOLUTE_MULTIPLIER)
)


def turn_budget(seconds: Optional[float]) -> float:
    """Usable turn budget: ``None`` / ``<= 0`` (Settings "unlimited") → no limit."""
    try:
        value = float(seconds) if seconds is not None else 0.0
    except (TypeError, ValueError):
        value = 0.0
    return value if value > 0 else UNLIMITED_TURN_SECONDS


def is_unlimited(seconds: Optional[float]) -> bool:
    return turn_budget(seconds) >= UNLIMITED_TURN_SECONDS


class ActivityDeadline:
    """Expires when the turn goes silent for ``idle_timeout`` seconds.

    ``poke()`` marks observed activity (a stdout/stderr line, a tool event…).
    ``check()`` returns ``None`` while healthy or a human-readable expiry
    reason once the idle budget or the absolute cap is exhausted.
    """

    def __init__(
        self,
        idle_timeout: float,
        *,
        absolute_timeout: Optional[float] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.idle_timeout = max(1.0, float(idle_timeout))
        if absolute_timeout is None:
            absolute_timeout = max(
                self.idle_timeout * ABSOLUTE_MULTIPLIER,
                self.idle_timeout + ABSOLUTE_MIN_EXTRA,
            )
        self.absolute_timeout = max(self.idle_timeout, float(absolute_timeout))
        self._mono = monotonic
        self._started = monotonic()
        self._last_activity = self._started

    def poke(self) -> None:
        self._last_activity = self._mono()

    def elapsed(self) -> float:
        return self._mono() - self._started

    def idle_seconds(self) -> float:
        return self._mono() - self._last_activity

    def remaining_idle(self) -> float:
        return max(0.0, self.idle_timeout - self.idle_seconds())

    def remaining_absolute(self) -> float:
        return max(0.0, self.absolute_timeout - self.elapsed())

    def next_wake(self, *, poll: float = 1.0) -> float:
        """How long a waiter may sleep before it must re-check expiry."""
        return max(0.0, min(poll, self.remaining_idle(), self.remaining_absolute()))

    def check(self) -> Optional[str]:
        """None while healthy; expiry reason string when the budget is spent."""
        if self.idle_seconds() >= self.idle_timeout:
            return (
                f"idle for {self.idle_timeout:.0f}s with no output "
                f"(activity timeout)"
            )
        if self.elapsed() >= self.absolute_timeout:
            return (
                f"exceeded the absolute turn cap of {self.absolute_timeout:.0f}s "
                f"(still producing output — likely a runaway run)"
            )
        return None
