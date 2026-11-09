"""Agent-facing view of stored chat message text.

Stored replies can carry another model's reasoning: harness adapters keep
interim narration in ``<think>`` blocks (the UI renders them collapsed), and
response post-processing keeps the unedited reply inside
``<cuttle_original_response>`` (plus ``metadata.postprocessing.original_response``).
Provider safeguards (Anthropic ``reasoning_extraction``) refuse a turn when
reasoning-shaped text from another model reaches it, whether through the
prompt or through a tool result such as ``api.chat_cli``. Every path that hands
transcript text to an agent goes through here; the stored rows stay unchanged.
"""
from __future__ import annotations

import json
import re
from typing import Any

_ORIGINAL_RESPONSE_RE = re.compile(
    r"<cuttle_original_response>(.*?)</cuttle_original_response>", re.DOTALL
)
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
# An unclosed block (truncated row, clipped preview) keeps its text but loses
# the tag that frames it as reasoning.
_STRAY_THINK_TAG_RE = re.compile(r"</?think(?:ing)?>", re.IGNORECASE)


def _unwrap_original(match: "re.Match[str]") -> str:
    try:
        original = json.loads(match.group(1))
    except ValueError:
        return ""
    return original if isinstance(original, str) else ""


def agent_visible_text(content: Any) -> str:
    """Message text with post-processing originals unwrapped and reasoning blocks removed.

    The original is the agent's own answer, so it stays; only its reasoning goes.
    """
    text = str(content or "")
    if "<" not in text:
        return text
    text = _ORIGINAL_RESPONSE_RE.sub(_unwrap_original, text)
    text = _THINK_RE.sub("", text)
    text = _STRAY_THINK_TAG_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def agent_visible_metadata(metadata: Any) -> Any:
    """Message metadata without the raw pre-post-processing reply."""
    if not isinstance(metadata, dict):
        return metadata
    post = metadata.get("postprocessing")
    if not isinstance(post, dict) or "original_response" not in post:
        return metadata
    post = {k: v for k, v in post.items() if k != "original_response"}
    return {**metadata, "postprocessing": post}
