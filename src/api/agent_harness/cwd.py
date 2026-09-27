"""Chat project chip → filesystem cwd for every harness agent.

The kernel owns this. Adapters may refine the path (WSL conversion, nested
git already applied here) but must not jump to a different project to
preserve a CLI ``--resume`` id.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _norm(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path or "")


def cwd_alias_candidates(cwd: str) -> list:
    """Same-repo aliases so git root and ``src/`` / ``source/`` share a session.

    Does **not** include unrelated projects. Cuttle vs Escape Purgatory must
    never alias each other.
    """
    out: list = []
    seen = set()

    def _add(raw: str) -> None:
        n = _norm(raw)
        if not n:
            return
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(n)

    _add(cwd)
    try:
        p = Path(cwd).resolve()
    except Exception:
        return out
    if p.name.lower() in ("src", "source") and p.parent.exists():
        _add(str(p.parent))
    for child_name in ("src", "source"):
        child = p / child_name
        if child.is_dir():
            _add(str(child))
    return out


def same_project(left: str, right: str) -> bool:
    """True when both paths are the same folder or a src/source alias of it."""
    if not left or not right:
        return False
    a = {p.lower() for p in cwd_alias_candidates(left)}
    b = {p.lower() for p in cwd_alias_candidates(right)}
    return bool(a & b)


def constrain_to_project(requested: str, candidate: str) -> str:
    """Keep ``candidate`` only when it is the requested project (or alias)."""
    if candidate and requested and same_project(requested, candidate):
        return _norm(candidate) or candidate
    return _norm(requested) or requested or candidate


def resolve_harness_cwd(
    project_path: Optional[str],
    *,
    fallback: Optional[str] = None,
) -> str:
    """Resolve the chat project chip to a directory the CLI should run in.

    Preference:
    1. Existing directory from ``project_path`` (absolute)
    2. Nested git worktree one level down (Unity ``source/``, Cuttle ``src/``)
    3. ``fallback`` (registered default project), then process cwd

    Never replace a valid project directory with process cwd. That is how
    Escape Purgatory chats silently ran inside Cuttle after the harness
    landed (CH-000164).
    """
    chosen = _existing_dir(project_path)
    if not chosen:
        chosen = _existing_dir(fallback)
    if not chosen:
        chosen = os.getcwd()
    try:
        from scripts.utilities.git_pending_changes import resolve_git_workdir

        git_ws = resolve_git_workdir(chosen)
        if git_ws:
            git_n = _norm(git_ws)
            if not chosen or same_project(chosen, git_n):
                return git_n or git_ws
            try:
                if Path(git_n).parent.resolve() == Path(chosen).resolve():
                    return git_n or git_ws
            except Exception:
                pass
    except Exception:
        pass
    return chosen


def _existing_dir(raw: Optional[str]) -> Optional[str]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        p = Path(text).expanduser()
        if not p.is_absolute():
            p = Path(os.path.abspath(str(p)))
        else:
            p = p.resolve()
        if p.is_dir():
            return str(p)
    except OSError:
        return None
    return None
