"""Helpers for supervised-task worker prompts and review-loop formatting.

Small, side-effect-free utilities shared by the orchestrator (and tests). Keep
harness adapters and durable state out of this module.
"""

from __future__ import annotations

from typing import List, Optional, Sequence


def format_follow_up_prompt(
    *,
    review_loop: int,
    user_objective: str,
    instruction: str,
    acceptance_criteria: Optional[Sequence[str]] = None,
) -> str:
    """Build the Cursor worker prompt for a coordinator follow-up turn."""
    criteria: List[str] = [str(c) for c in (acceptance_criteria or []) if str(c).strip()]
    lines = [
        f"# Follow-up (review loop {review_loop})",
        "",
        f"Original objective: {user_objective}",
        "",
        f"Instruction: {instruction}",
        "",
        "Preserve acceptance criteria:",
    ]
    if criteria:
        lines.extend(f"- {c}" for c in criteria)
    else:
        lines.append("- (none specified)")
    return "\n".join(lines)
