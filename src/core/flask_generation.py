"""Flask generation counter that never repeats across daemon cold boots.

Restart cards share the id ``flask-restart-g{generation}`` and every surface
decides "is this card still live?" by comparing generations. A counter that
restarts at 1 on each daemon boot makes a new card collide with an old,
already-clicked one (it renders as used and never shows its progress bar), so
the daemon persists the last generation in the instance state dir and always
hands out a larger one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

# First boot after this counter existed: earlier daemon lives each counted from
# 1, so jump clear of any id an old transcript may still hold.
LEGACY_FLOOR = 1000


def _read_int(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def next_generation(path: Path, current: int = 0, seen: Iterable[Optional[int]] = ()) -> int:
    """Return a generation larger than ``current``, the stored one, and ``seen``.

    ``seen`` takes other known generations (e.g. the restart status file).
    The result is persisted atomically; a write failure still returns a value
    larger than ``current`` so Flask can start.
    """
    stored = _read_int(path)
    known = [int(current or 0)] + [int(g) for g in seen if isinstance(g, int) and g >= 0]
    base = max(known + [stored or 0])
    if stored is None:
        base = max(base, LEGACY_FLOOR)
    nxt = base + 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(str(nxt), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    return nxt
