"""Frustration investigator recipe — verdicts without a Cursor Auto turn."""

from __future__ import annotations

from typing import Any, Dict, List

from api.jev import thresholds as T
from api.jev.client import JevError, get_client
from api.jev.types import noul


def investigate_attempts(
    *,
    complaint: str,
    phrase: str,
    asks: List[Dict[str, Any]],
    outcomes_by_decision: Dict[str, List[Dict[str, Any]]],
    client=None,
) -> List[Dict[str, str]]:
    """Return [{decision_id, verdict, reason}] for apply_verdicts."""
    attempts = []
    for a in asks:
        did = str(a.get("decision_id") or "")
        if not did:
            continue
        rows = outcomes_by_decision.get(did) or []
        final = rows[0] if rows else {}
        attempts.append(
            {
                "decision_id": did,
                "asked": str(a.get("norm") or "")[:300],
                "target": f"{final.get('target_agent', '?')}/{final.get('target_model') or 'default'}",
                "failure_kind": final.get("failure_kind") or "n/a",
                "feedback": final.get("user_feedback"),
            }
        )
    if not attempts:
        return []

    state = {
        "complaint": (complaint or "")[:800],
        "matched_phrase": phrase or "",
        "attempts": attempts,
    }
    questions: Dict[str, Any] = {
        "same_issue": noul(
            "Does `complaint` refer to the same problem the listed `attempts` were trying to solve?",
            true="Same unresolved issue",
            false="A new or unrelated request",
        )
    }
    # Cap fan-out: one noul per attempt (TypeSafe runs them in parallel).
    for i, att in enumerate(attempts[:12]):
        questions[f"failed_{i}"] = noul(
            f"Did attempt `{att['decision_id']}` fail to solve the user's actual problem "
            f"(asked: {att['asked'][:160]}) given the later complaint?",
            true="Failed / did not solve it",
            false="Solved it, or unrelated",
        )

    try:
        c = client or get_client(timeout_s=20.0)
        result = c.system_one(state, questions)
    except JevError:
        raise
    except Exception as e:
        raise JevError(str(e), retryable=True) from e

    same = float(result.get("same_issue").noul or 0.0)
    out: List[Dict[str, str]] = []
    if same < T.SAME_ISSUE_NOUL:
        # Complaint is a new issue — do not smear prior attempts.
        return out
    for i, att in enumerate(attempts[:12]):
        ans = result.get(f"failed_{i}")
        n = float(ans.noul or 0.0)
        if n >= T.ATTEMPT_FAILED_NOUL:
            verdict = "bad"
        elif n <= (1.0 - T.ATTEMPT_FAILED_NOUL):
            verdict = "good"
        else:
            verdict = "unclear"
        out.append(
            {
                "decision_id": att["decision_id"],
                "verdict": verdict,
                "reason": f"jev same={same:.2f} failed={n:.2f}",
            }
        )
    return out
