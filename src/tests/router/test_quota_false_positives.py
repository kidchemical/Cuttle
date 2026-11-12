"""Out-of-usage memory must come from real failures, never from reply prose.

Regression: two *successful* sub-agent replies (a weapon-balance table with
``1.600 → 1.402`` and "the high-frame-rate limit") were classified as transport
failures, ``\\b402\\b`` read ``1.402`` as HTTP 402, and Claude and Codex were
marked out of usage for 3 h. An orchestrator then parked a lane for 2 h 15 m
while both accounts had plenty of budget.
"""

from __future__ import annotations

import time

from api.agent_router import availability, budget, quota
from api.agent_router.policy import classify_failure, failure_excerpt, is_quota_failure
from api.agent_router.types import ExecutionTarget, FailureKind

# Excerpts of the replies that triggered the false marks (CH-002349 / CH-002350).
CLAUDE_REVIEW = (
    "**Verdict: accept with fixes.** I reviewed it only and changed no project files.\n\n"
    "| weapon | new sprint node |\n|---|---|\n| Shotgun / Dart Rifle | 1.402 |\n| RPG-7 | 1.311 |\n"
)
CODEX_REPLY = (
    "I'll read the brief, make the requested code change, then rebuild and measure.\n\n"
    "The threshold now uses the high-frame-rate limit and reads both weapon multipliers; "
    "the C# compile check passes.\n\n| Shotgun | 1.600 → 1.402 | 1.000 | 1.000 | 0.998 | 1.000 |\n"
)


def test_successful_replies_are_never_failures():
    for text, rtype in ((CLAUDE_REVIEW, "claude_command"), (CODEX_REPLY, "codex_command"),
                        ("Raised the shop budget; billing UI not found yet; request timed out once.", "codex_command")):
        kind, _ = classify_failure({"success": True, "type": rtype, "response": text}, response_text=text)
        assert kind == FailureKind.NONE.value


def test_status_codes_need_an_http_context():
    assert not is_quota_failure("| Shotgun | 1.600 → 1.402 |")
    assert not is_quota_failure("allocated 402 items")
    for real in ("API Error: 402 {\"type\":\"error\"}", "HTTP 402 Payment Required",
                 "status code 402", "(402) Payment Required"):
        assert is_quota_failure(real), real
    kind, _ = classify_failure({"success": True, "type": "codex_error", "response": "error: 429 Too Many Requests"})
    assert kind == FailureKind.TRANSPORT.value


def test_failed_run_ignores_transcript_middle():
    prose = "x" * 3000 + "\nThe shop budget is 50 crystals.\n" + "y" * 3000
    text = "[FAIL] **Codex** (`codex`):\n" + prose + "\nexited with code 1"
    assert "budget" not in failure_excerpt(text)
    kind, _ = classify_failure({"success": True, "type": "codex_error", "response": text})
    assert kind == FailureKind.TASK.value


def test_real_limits_still_classify():
    cursor = ("[FAIL] **Cursor Agent** (`agent`):\n```\nActionRequiredError: You've hit your usage limit "
              "Your usage limits will reset when your monthly cycle ends on 11/21/2026.\n```")
    kind, _ = classify_failure({"success": True, "type": "cursor_error", "response": cursor})
    assert kind == FailureKind.TRANSPORT.value and is_quota_failure(cursor)
    claude = "You've hit your session limit · resets 3pm"
    kind, _ = classify_failure({"success": True, "type": "claude_error", "response": claude})
    assert kind == FailureKind.TRANSPORT.value and is_quota_failure(claude)


def test_cursor_monthly_reset_date_is_parsed():
    msg = "Your usage limits will reset when your monthly cycle ends on 11/21/2026."
    now = time.mktime((2026, 11, 18, 13, 36, 0, 0, 0, -1))
    assert time.localtime(quota.reset_at(msg, now))[:3] == (2026, 11, 21)
    early = time.mktime((2026, 11, 9, 13, 36, 0, 0, 0, -1))
    assert quota.reset_at(msg, early) == early + quota.MAX_COOLDOWN_S  # capped: re-probe weekly


def test_subagent_success_never_marks_the_account():
    from api.subagents.turns import _note_account_usage
    from api.subagents.types import ChildSpec

    for agent, text in (("claude", CLAUDE_REVIEW), ("codex", CODEX_REPLY)):
        _note_account_usage(ChildSpec(title="t", message="m", agent=agent),
                            {"success": True, "type": f"{agent}_command", "response": text})
    assert quota.snapshot() == []


def _acc(agent, *, fetched_at, blocked=False):
    return {agent: budget.Account(agent=agent, blocked=blocked, headroom=0.8, fetched_at=fetched_at)}


def test_newer_live_usage_beats_a_guessed_cooldown():
    target = ExecutionTarget("codex", "gpt-6")
    quota.mark_exhausted(target, "[FAIL] connection reset; quota exceeded")  # no reset named
    marked = quota.record(target)["marked_at"]
    assert not quota.record(target)["reset_named"]
    state, why = availability.target_state(target, accounts=None, verbose=True)
    assert state == "blocked" and "reset was not named" in why
    assert availability.target_state(target, accounts=_acc("codex", fetched_at=marked - 5))[0] == "blocked"
    assert availability.target_state(target, accounts=_acc("codex", fetched_at=marked + 5))[0] == "ok"
    assert availability.target_state(
        target, accounts=_acc("codex", fetched_at=marked + 5, blocked=True))[0] == "blocked"


def test_a_named_reset_holds_against_live_usage():
    target = ExecutionTarget("codex", "gpt-6")
    quota.mark_exhausted(target, "You've hit your usage limit. Try again at 11:59 PM.")
    rec = quota.record(target)
    assert rec["reset_named"]
    live = _acc("codex", fetched_at=rec["marked_at"] + 5)
    assert availability.target_state(target, accounts=live)[0] == "blocked"


def test_pinned_success_clears_the_cooldown():
    from api.agent_router.pinned_outcomes import _note_account_usage

    target = ExecutionTarget("claude", "claude-opus-5-5")
    quota.mark_exhausted(target, "API Error: 402")
    assert quota.is_exhausted(target)
    _note_account_usage("claude", "claude-opus-5-5", "none", {"response": CLAUDE_REVIEW})
    assert not quota.is_exhausted(target)
