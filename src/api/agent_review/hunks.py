"""Pure hunk logic for agent review: baseline ↔ current, keep, undo, fold.

No Cuttle imports, no I/O. Texts are ``str`` (``None`` = file absent); lines
keep their endings so a round trip is byte-exact. A hunk id hashes the
removed and added lines plus their occurrence index, so it stays stable
while other hunks in the same file are kept or undone.
"""

from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Sequence, Tuple

__all__ = ["compute", "keep", "undo", "fold", "stats"]

Text = Optional[str]


def _lines(text: Text) -> List[str]:
    return text.splitlines(keepends=True) if text else []


def _ops(a: Sequence[str], b: Sequence[str]):
    return SequenceMatcher(None, a, b, autojunk=False).get_opcodes()


def _changes(a: Sequence[str], b: Sequence[str]) -> List[Tuple[str, Tuple[int, int, int, int, str]]]:
    """(hunk id, opcode) for every non-equal opcode, ids unique per file."""
    seen: Dict[str, int] = {}
    out = []
    for op in _ops(a, b):
        if op[0] == "equal":
            continue
        _, i1, i2, j1, j2 = op
        digest = hashlib.sha1(("".join(a[i1:i2]) + "\0" + "".join(b[j1:j2])).encode("utf-8", "surrogateescape")).hexdigest()[:12]
        n = seen.get(digest, 0)
        seen[digest] = n + 1
        out.append((f"{digest}-{n}", op))
    return out


def compute(baseline: Text, current: Text) -> List[dict]:
    """Hunks turning ``baseline`` into ``current`` (1-based line numbers)."""
    a, b = _lines(baseline), _lines(current)
    hunks = []
    for hid, (_, i1, i2, j1, j2) in _changes(a, b):
        hunks.append({
            "id": hid,
            "old_start": i1 + 1, "old_count": i2 - i1,
            "new_start": j1 + 1, "new_count": j2 - j1,
            "removed": a[i1:i2], "added": b[j1:j2],
        })
    return hunks


def stats(baseline: Text, current: Text) -> Dict[str, int]:
    hunks = compute(baseline, current)
    return {"hunks": len(hunks),
            "additions": sum(h["new_count"] for h in hunks),
            "deletions": sum(h["old_count"] for h in hunks)}


def _rebuild(baseline: Text, current: Text, take_new: Callable[[str], bool]) -> Tuple[str, bool]:
    """Merge text: each hunk takes its new side when ``take_new(id)``."""
    a, b = _lines(baseline), _lines(current)
    changes = dict((op, hid) for hid, op in _changes(a, b))
    out: List[str] = []
    any_new = False
    for op in _ops(a, b):
        tag, i1, i2, j1, j2 = op
        if tag == "equal":
            out.extend(b[j1:j2])
        elif take_new(changes[op]):
            any_new = True
            out.extend(b[j1:j2])
        else:
            out.extend(a[i1:i2])
    return "".join(out), any_new


def keep(baseline: Text, current: Text, ids: Optional[Sequence[str]] = None) -> Text:
    """New baseline with the chosen hunks (all when ``ids`` is None) accepted."""
    if ids is None:
        return current
    wanted = set(ids)
    text, any_new = _rebuild(baseline, current, lambda hid: hid in wanted)
    if baseline is None and not any_new:
        return None
    if current is None and text == "" and any_new:
        return None
    return text


def undo(baseline: Text, current: Text, ids: Optional[Sequence[str]] = None) -> Text:
    """New file content with the chosen hunks (all when ``ids`` is None) reverted."""
    if ids is None:
        return baseline
    wanted = set(ids)
    text, any_new = _rebuild(baseline, current, lambda hid: hid not in wanted)
    if baseline is None and not any_new:
        return None  # every hunk of an agent-created file undone → no file
    if current is None and not any_new and text == "" and baseline == "":
        return ""
    return text


def fold(baseline: Text, agent: Text, user: Text) -> Tuple[Text, int]:
    """Carry the user's edits (``agent`` → ``user``) into ``baseline``.

    Edits that do not touch an agent hunk are applied to the baseline, so
    they never show up as agent changes. Edits overlapping or adjacent to an
    agent hunk stay in that hunk. Returns (new baseline, conflicting edits).
    """
    if agent == user:
        return baseline, 0
    if user is None or agent is None:
        # Whole-file deletion/creation by the user: nothing safe to fold.
        return baseline, 1
    base, mid, mine = _lines(baseline), _lines(agent), _lines(user)
    agent_ops = _ops(base, mid)
    # Equal blocks map agent line ranges onto baseline line ranges.
    blocks = [(j1, j2, i1) for tag, i1, i2, j1, j2 in agent_ops if tag == "equal"]
    changed = [(j1, j2) for tag, i1, i2, j1, j2 in agent_ops if tag != "equal"]

    def touches_agent(s: int, e: int) -> bool:
        for c1, c2 in changed:
            if s == e and c1 == c2:
                hit = s == c1  # two insertions/deletions at one point
            elif s == e:
                hit = c1 <= s <= c2  # user insertion at or inside an agent hunk
            elif c1 == c2:
                hit = s < c1 < e  # agent deletion point inside the user's lines
            else:
                hit = s < c2 and c1 < e
            if hit:
                return True
        return False

    def to_base(s: int, e: int) -> Optional[Tuple[int, int]]:
        for j1, j2, i1 in blocks:
            if j1 <= s and e <= j2:
                return i1 + (s - j1), i1 + (e - j1)
        if not mid and s == e == 0:
            return 0, 0
        return None

    edits: List[Tuple[int, int, List[str]]] = []
    conflicts = 0
    for tag, s, e, k1, k2 in _ops(mid, mine):
        if tag == "equal":
            continue
        mapped = None if touches_agent(s, e) else to_base(s, e)
        if mapped is None:
            conflicts += 1
            continue
        edits.append((mapped[0], mapped[1], mine[k1:k2]))
    for s, e, repl in sorted(edits, key=lambda x: x[0], reverse=True):
        base[s:e] = repl
    new = "".join(base)
    return (None if baseline is None and new == "" else new), conflicts
