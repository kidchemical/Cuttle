"""
Opt-in Claw Code Python workspace (github.com/instructkr/claw-code).

This is NOT the official Claude Code CLI; it is the open Python port / harness simulator
used for routing, bootstrap session reports, and mirrored command-tool metadata.
Not shipped with Cuttle: users clone it into vendor/claw-code themselves (gitignored).
See vendor/claw-code/README.md upstream.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Cuttle repo root: src/scripts/utilities/ -> parents[3]
_CUTTLE_ROOT = Path(__file__).resolve().parents[3]
_CLAW_ROOT = _CUTTLE_ROOT / "vendor" / "claw-code"


def claw_harness_available() -> bool:
    return _CLAW_ROOT.is_dir() and (_CLAW_ROOT / "src" / "runtime.py").is_file()


def _ensure_claw_import_path() -> None:
    root = str(_CLAW_ROOT.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def run_claw_bootstrap(prompt: str, limit: int = 5) -> str:
    """
    Run PortRuntime.bootstrap_session and return markdown for chat/UI.

    If vendor/claw-code is missing, returns a short help string.
    """
    if not claw_harness_available():
        return (
            "[Claw] vendor/claw-code not found. From the Cuttle repo root run:\n"
            "  git clone --depth 1 https://github.com/instructkr/claw-code.git vendor/claw-code"
        )
    _ensure_claw_import_path()
    try:
        from src.runtime import PortRuntime  # type: ignore  # vendored package name
    except ImportError as e:
        return f"[Claw] Import failed: {e}"

    session = PortRuntime().bootstrap_session(prompt, limit=limit)
    return session.as_markdown()


def run_claw_turn_loop(
    prompt: str,
    *,
    limit: int = 5,
    max_turns: int = 3,
    structured_output: bool = False,
) -> str:
    """Run PortRuntime.run_turn_loop and concatenate turn outputs as markdown."""
    if not claw_harness_available():
        return (
            "[Claw] vendor/claw-code not found. Clone instructkr/claw-code into vendor/claw-code."
        )
    _ensure_claw_import_path()
    try:
        from src.runtime import PortRuntime  # type: ignore
    except ImportError as e:
        return f"[Claw] Import failed: {e}"

    results = PortRuntime().run_turn_loop(
        prompt,
        limit=limit,
        max_turns=max_turns,
        structured_output=structured_output,
    )
    chunks: list[str] = []
    for idx, result in enumerate(results, start=1):
        chunks.append(f"## Turn {idx}\n\n{result.output}\n\nstop_reason={result.stop_reason}")
    return "\n\n".join(chunks) if chunks else "(no turns)"
