"""Windows CLI discovery helpers shared by harness adapters.

npm / installer shims often put ``tool.cmd`` on PATH. Those shims re-parse
arguments through ``cmd.exe`` (``%*``), which truncates at newlines and hits the
~8,191-character CreateProcess limit faster. Prefer a packaged ``.exe`` next to
the shim when available; keep the shim as discovery fallback.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional, Sequence


_SHELL_SUFFIXES = (".cmd", ".bat", ".ps1")


def prefer_native_binary(
    path: str,
    *,
    extra_candidates: Sequence[Path] = (),
    is_windows: Optional[bool] = None,
) -> str:
    """If ``path`` is a Windows shell shim, return a sibling/packaged ``.exe`` when found."""
    resolved = Path(path)
    windows = os.name == "nt" if is_windows is None else is_windows
    if not windows or resolved.suffix.lower() not in _SHELL_SUFFIXES:
        return path

    candidates = [resolved.with_suffix(".exe"), *list(extra_candidates)]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return path


def which_preferring_native(
    names: Iterable[str],
    *,
    env_var: str = "",
    packaged_relpaths: Sequence[str] = (),
) -> Optional[str]:
    """Resolve a CLI from env or PATH, preferring native binaries over ``.cmd`` shims."""
    env_name = (env_var or "").strip()
    if env_name:
        configured = (os.environ.get(env_name) or "").strip()
        if configured and os.path.isfile(configured):
            extras = []
            base = Path(configured)
            for rel in packaged_relpaths:
                extras.append(base.parent / rel)
            return prefer_native_binary(configured, extra_candidates=extras)

    for name in names:
        found = shutil.which(name)
        if not found:
            continue
        extras = []
        base = Path(found)
        for rel in packaged_relpaths:
            extras.append(base.parent / rel)
        return prefer_native_binary(found, extra_candidates=extras)
    return None
