"""
Standardized chat status phases for streaming UI + logs.

Phases are short labels prefixed on messages: ``[phase] detail``.
"""

from __future__ import annotations

# Vocabulary (align with pipeline / remote-agent flow)
PHASE_ROUTE = "route"
PHASE_CLASSIFY = "classify"
PHASE_TOOL = "tool"
PHASE_LLM = "llm"
PHASE_SANDBOX = "sandbox"
PHASE_OUTPUT = "output"
PHASE_LIMIT = "limit"
PHASE_DONE = "done"


def emit_pipeline_status(session_id: str, phase: str, detail: str) -> None:
    """Emit a single status line with a normalized ``[phase]`` prefix."""
    if not session_id or not phase:
        return
    text = f"[{phase}] {detail}".strip() if detail else f"[{phase}]"
    try:
        from api.web_chat_api import emit_chat_status

        emit_chat_status(session_id, text)
    except Exception:
        pass
