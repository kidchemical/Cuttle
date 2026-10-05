"""Failure classification and deterministic policy helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from api.agent_router.types import ExecutionTarget, FailureKind

_TRANSPORT_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"not found",
        r"CLI not found",
        r"authentication",
        r"unauthorized",
        r"forbidden",
        r"api key",
        r"quota",
        r"budget",
        r"billing",
        r"rate.?limit",
        r"too many requests",
        r"model .*unavailable",
        r"unsupported model",
        r"outage",
        r"connection refused",
        r"timed? ?out",
        r"temporarily unavailable",
        r"service unavailable",
        r"402\b",
        r"401\b",
        r"403\b",
        r"429\b",
        r"usage.?limit",
        r"spend limit",
        r"hit your usage limit",
        r"out of usage",
        r"ActionRequiredError",
    )
]

# Account-level exhaustion: every premium model on that vendor account will
# fail the same way, so the rest of the turn must not spend attempts on them.
_QUOTA_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"quota",
        r"budget",
        r"billing",
        r"usage.?limit",
        r"out of usage",
        r"spend limit",
        r"ActionRequiredError",
        r"\b402\b",
    )
]

_CANCELLED_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"^\[CANCELLED\]",
        r"\brun was cancell?ed\b",
        r"\bcancell?ed by (the )?user\b",
        r"\bstopped by (the )?user\b",
        r"\bchat deleted or stopped\b",
    )
]

_TASK_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"^\[FAIL\]",
        r"exited with code [1-9]",
        r"unable to complete",
        r"could not complete",
        r"build failed",
        r"tests? failed",
        r"validation failed",
        r"escalat",
    )
]


def classify_failure(
    result: Optional[Dict[str, Any]],
    *,
    response_text: str = "",
) -> Tuple[str, str]:
    """Return (FailureKind value, short reason)."""
    if not result:
        return FailureKind.TRANSPORT.value, "empty result"

    if result.get("success") and result.get("type") not in (
        "cursor_error",
        "codex_error",
        "claude_error",
        "hermes_error",
        "muse_error",
        "opencode_error",
        "antigravity_error",
        "router_error",
    ):
        text = response_text or str(result.get("response") or "")
        if text.startswith("[FAIL]") or text.startswith("[CANCELLED]"):
            # fall through to classification
            pass
        elif result.get("success"):
            # Chat handlers often return success=True even on agent failure.
            pass

    text = (response_text or str(result.get("response") or result.get("error") or "")).strip()
    err = str(result.get("error") or "").strip()
    combined = f"{text}\n{err}".strip()
    rtype = str(result.get("type") or "")

    # Cancellation is the user's decision, not an agent defect. It must be
    # checked before transport/task so a Stop never buys an escalation.
    if is_cancellation(combined):
        return FailureKind.CANCELLED.value, _short_reason(combined) or "cancelled by user"

    # Explicit transport markers from our runners
    if rtype.endswith("_error") and any(
        p.search(combined) for p in _TRANSPORT_PATTERNS
    ):
        return FailureKind.TRANSPORT.value, _short_reason(combined)

    for p in _TRANSPORT_PATTERNS:
        if p.search(combined):
            return FailureKind.TRANSPORT.value, _short_reason(combined)

    failed_flag = False
    if text.startswith("[FAIL]") or text.startswith("[CANCELLED]"):
        failed_flag = True
    if rtype.endswith("_error"):
        failed_flag = True
    if result.get("success") is False:
        failed_flag = True

    if not failed_flag:
        return FailureKind.NONE.value, ""

    for p in _TASK_PATTERNS:
        if p.search(combined):
            return FailureKind.TASK.value, _short_reason(combined)

    # Unknown failure → treat as task failure so Auto can escalate once.
    return FailureKind.TASK.value, _short_reason(combined) or "agent reported failure"


def is_cancellation(text: str) -> bool:
    """True when the failure text describes a user/chat cancellation."""
    s = (text or "").strip()
    if not s:
        return False
    return any(p.search(s) for p in _CANCELLED_PATTERNS)


# Runner headers like "[FAIL] **Cursor Agent** (`agent`):" carry no reason;
# the actual error is on a later line (often inside a ``` fence).
_HEADER_LINE = re.compile(r"^\[(?:FAIL|CANCELLED)\]\s*(?:\*\*[^*]+\*\*)?\s*(?:\(`[^`]*`\))?\s*:?\s*$")


def _short_reason(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines()]
    for ln in lines:
        if not ln or ln.startswith("```") or _HEADER_LINE.match(ln):
            continue
        return ln[:200]
    return (lines[0] if lines else "")[:200]


def is_quota_failure(text: str) -> bool:
    """True when a failure means the vendor account is out of budget/usage."""
    s = text or ""
    return any(p.search(s) for p in _QUOTA_PATTERNS)


_CODE_CHANGE_PHRASES = (
    "write a test",
    "write tests",
    "add a test",
    "add tests",
    "unit test",
    "integration test",
)
_CODE_CHANGE_VERBS = re.compile(
    r"\b(?:"
    r"fix|implement|add|create|refactor|patch|bug|change|update|"
    r"write|edit|delete|remove|migrate|commit"
    r")\b",
    re.I,
)


def looks_like_code_change_request(text: str) -> bool:
    """Heuristic flag for the routing brain / use-case table.

    Whole-word verbs only. Bare ``test`` used to substring-match every smoke
    prompt and skip the General chat use case.
    """
    low = (text or "").lower()
    if not low.strip():
        return False
    if any(p in low for p in _CODE_CHANGE_PHRASES):
        return True
    return bool(_CODE_CHANGE_VERBS.search(low))


def is_auto_target(target: ExecutionTarget) -> bool:
    return target.agent == "cursor" and (target.model or "auto").lower() in (
        "auto",
        "default",
        "",
    )


def is_cursor_grok(target: ExecutionTarget) -> bool:
    if target.agent != "cursor":
        return False
    m = (target.model or "").lower()
    return "grok" in m
