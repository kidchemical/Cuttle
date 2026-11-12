"""Failure classification and deterministic policy helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from api.agent_router.types import ExecutionTarget, FailureKind


def _status_code(codes: str) -> str:
    """An HTTP status in an error line, not any number containing it.

    A bare ``\\b402\\b`` matched ``1.402`` in a weapon-balance table and marked
    Claude and Codex out of usage for three hours. Require the code as its own
    token (no digit/dot/word neighbours) next to an HTTP-ish word or reason."""
    code = rf"(?<![\w.])(?:{codes})(?![\w.])"
    return (
        rf"(?:\bHTTP(?:/[\d.]+)?|\bstatus(?:[ _]code)?|\bcode|\berror|\bAPI Error|\bresponse)"
        rf"[\s:=#(\[\"']{{0,4}}{code}"
        rf"|{code}\s*[-:)\]]?\s*(?:Payment Required|Unauthorized|Forbidden|Too Many Requests)"
    )


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
        _status_code("401|402|403|429"),
        r"usage.?limit",
        r"spend limit",
        r"hit your usage limit",
        r"out of usage",
        # Claude Code subscription limits ("You've hit your session limit · resets 3pm").
        r"you['’]ve hit your\b[^\n]*\blimit",
        r"you['’]ve reached your\b[^\n]*\blimit",
        r"out of usage credits",
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
        # Claude Code subscription limits ("You've hit your session limit · resets 3pm").
        r"you['’]ve hit your\b[^\n]*\blimit",
        r"you['’]ve reached your\b[^\n]*\blimit",
        r"out of usage credits",
        r"ActionRequiredError",
        _status_code("402"),
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

    text = (response_text or str(result.get("response") or result.get("error") or "")).strip()
    err = str(result.get("error") or "").strip()
    rtype = str(result.get("type") or "")

    # Only a failed run has a failure kind. Runners mark failure explicitly
    # (``*_error`` type, ``[FAIL]``/``[CANCELLED]`` header, ``success: False``);
    # kernel failures always carry ``{agent}_error``. A successful reply is
    # never scanned: its prose ("budget", "rate limit", "timed out", "1.402")
    # is the agent's work, not an error — scanning it marked whole vendor
    # accounts out of usage from ordinary game-dev replies.
    failed_flag = (
        text.startswith("[FAIL]")
        or text.startswith("[CANCELLED]")
        or rtype.endswith("_error")
        or result.get("success") is False
    )
    if not failed_flag:
        return FailureKind.NONE.value, ""

    combined = failure_excerpt(text, err)

    # Cancellation is the user's decision, not an agent defect. It must be
    # checked before transport/task so a Stop never buys an escalation.
    if is_cancellation(combined):
        return FailureKind.CANCELLED.value, _short_reason(combined) or "cancelled by user"

    for p in _TRANSPORT_PATTERNS:
        if p.search(combined):
            return FailureKind.TRANSPORT.value, _short_reason(combined)

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


# A failed run's response can be the agent's whole transcript followed by the
# CLI error. The error lives in the runner header / first lines or at the end;
# the middle is the agent's own prose and must not decide the failure kind.
_EXCERPT_HEAD = 1200
_EXCERPT_TAIL = 800


def failure_excerpt(text: str, err: str = "") -> str:
    """The part of a failed run's output that can name why it failed."""
    t = (text or "").strip()
    if len(t) > _EXCERPT_HEAD + _EXCERPT_TAIL:
        t = f"{t[:_EXCERPT_HEAD]}\n…\n{t[-_EXCERPT_TAIL:]}"
    e = (err or "").strip()
    if e and e not in t:
        t = f"{t}\n{e[:_EXCERPT_HEAD]}" if t else e[:_EXCERPT_HEAD]
    return t


def is_quota_failure(text: str) -> bool:
    """True when a failure means the vendor account is out of budget/usage.

    Callers pass failure text only (a failed run's output or error); this is
    applied to :func:`failure_excerpt` so a long transcript's prose cannot
    decide it."""
    s = failure_excerpt(text or "")
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
