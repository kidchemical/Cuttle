"""Fail-closed isolation so unit tests cannot launch real external runners.

Activation is process-local and irreversible. Spoofed environment variables
cannot grant a bypass (same class of bug as the old restart-guard env auth).
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

_lock = threading.Lock()
_ACTIVE = False
_ALLOW_DEPTH = 0
_ALLOW_REASON: Optional[str] = None


def activate_test_isolation(*, reason: str = "pytest") -> None:
    """Enable fail-closed runner guards for this process (idempotent)."""
    global _ACTIVE
    with _lock:
        _ACTIVE = True
        # Document intent; env alone never authorizes a bypass.
        os.environ.setdefault("CUTTLE_TEST_MODE", "1")
        _ = reason


def is_test_isolation_active() -> bool:
    with _lock:
        if _ACTIVE:
            return True
        # Fail closed if pytest (or an explicit env) is present even before
        # conftest runs — never treat env as an allow signal.
        if os.environ.get("CUTTLE_TEST_MODE", "").strip() == "1":
            return True
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return True
        return False


def external_runners_allowed() -> bool:
    with _lock:
        return _ALLOW_DEPTH > 0


@contextmanager
def allow_external_runners(reason: str) -> Iterator[None]:
    """Narrow escape hatch for explicitly marked integration tests only."""
    global _ALLOW_DEPTH, _ALLOW_REASON
    if not reason or not str(reason).strip():
        raise ValueError("allow_external_runners requires a non-empty reason")
    with _lock:
        _ALLOW_DEPTH += 1
        _ALLOW_REASON = str(reason).strip()
    try:
        yield
    finally:
        with _lock:
            _ALLOW_DEPTH = max(0, _ALLOW_DEPTH - 1)
            if _ALLOW_DEPTH == 0:
                _ALLOW_REASON = None


def guard_external_runner(name: str) -> None:
    """Raise if a real external runner would execute under test isolation."""
    if not is_test_isolation_active():
        return
    # Spoofed allow env must never bypass — only the in-process context manager.
    spoof = os.environ.get("CUTTLE_TEST_ALLOW_EXTERNAL_RUNNERS", "").strip()
    if spoof and not external_runners_allowed():
        pass  # intentionally ignored
    if external_runners_allowed():
        return
    raise RuntimeError(
        f"CUTTLE_TEST_MODE blocked real {name} execution. "
        "Unit tests must mock runners; integration tests must use "
        "allow_external_runners(...) explicitly."
    )


def reset_test_isolation_for_tests() -> None:
    """Test helper only — clears allow depth; does not deactivate isolation."""
    global _ALLOW_DEPTH, _ALLOW_REASON
    with _lock:
        _ALLOW_DEPTH = 0
        _ALLOW_REASON = None
