"""Score a routing decision against flexible suite expectations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.agent_router.eval.suites import EvalTestCase
from api.agent_router.types import RoutingDecision


RESULT_PASS = "pass"
RESULT_PREFERENCE_WARNING = "preference_warning"
RESULT_EXPECTATION_FAILURE = "expectation_failure"
RESULT_INVALID = "invalid"
RESULT_FALLBACK = "fallback"
RESULT_MANUAL_REVIEW = "manual_review"
RESULT_API_ERROR = "api_error"


def _target_key(agent: str, model: str) -> str:
    return f"{(agent or '').strip().lower()}:{(model or '').strip().lower()}"


def _target_in_allowed(agent: str, model: str, allowed: List[Dict[str, str]]) -> bool:
    if not allowed:
        return True
    a = (agent or "").strip().lower()
    m = (model or "").strip().lower()
    for t in allowed:
        ta = str(t.get("agent") or "").strip().lower()
        tm = str(t.get("model") or "").strip().lower()
        if ta != a:
            continue
        if not tm or tm == m:
            return True
        # soft alias: empty preferred model matches any / default
        if m in ("", "default") and tm in ("", "default"):
            return True
    return False


def score_case(
    case: EvalTestCase,
    decision: RoutingDecision,
    *,
    used_fallback: bool = False,
    api_error: Optional[str] = None,
    invalid_rejected: bool = False,
) -> Dict[str, Any]:
    """Return scoring fields for one evaluated case."""
    actual_agent = decision.target.agent
    actual_model = decision.target.model
    expected_bits = []
    if case.allowed_task_types:
        expected_bits.append("types=" + "|".join(case.allowed_task_types))
    if case.allowed_difficulties:
        expected_bits.append("diff=" + "|".join(case.allowed_difficulties))
    if case.allowed_targets:
        expected_bits.append(
            "targets="
            + ",".join(f"{t['agent']}/{t.get('model') or '*'}" for t in case.allowed_targets)
        )
    if case.preferred_target:
        expected_bits.append(
            "pref="
            + f"{case.preferred_target['agent']}/{case.preferred_target.get('model') or '*'}"
        )

    actual_label = f"{actual_agent}/{actual_model or 'default'}"
    expected_label = "; ".join(expected_bits) or "(none)"

    out: Dict[str, Any] = {
        "validation_pass": not invalid_rejected and not (used_fallback and api_error and invalid_rejected),
        "expectation_pass": True,
        "preference_warning": False,
        "manual_review": bool(case.manual_review),
        "result": RESULT_PASS,
        "expected_summary": expected_label,
        "actual_summary": actual_label,
        "notes": case.notes,
    }

    # Invalid / fallback / API first
    if invalid_rejected:
        out["validation_pass"] = False
        out["expectation_pass"] = False
        out["result"] = RESULT_INVALID
        return out

    if used_fallback and api_error:
        out["validation_pass"] = False
        out["expectation_pass"] = False
        # Prefer INVALID when the error was a malformed/invented decision;
        # otherwise mark as API/fallback.
        out["result"] = RESULT_FALLBACK if not invalid_rejected else RESULT_INVALID
        if "timeout" in (api_error or "").lower() or "429" in (api_error or "") or "auth" in (api_error or "").lower():
            out["result"] = RESULT_API_ERROR
        return out

    if used_fallback:
        out["result"] = RESULT_FALLBACK
        out["expectation_pass"] = False
        return out

    out["validation_pass"] = True

    fails: List[str] = []
    if case.allowed_task_types and decision.task_type not in case.allowed_task_types:
        fails.append(f"task_type `{decision.task_type}` not in {case.allowed_task_types}")
    if case.allowed_difficulties and decision.difficulty not in case.allowed_difficulties:
        fails.append(f"difficulty `{decision.difficulty}` not in {case.allowed_difficulties}")
    if case.allowed_targets and not _target_in_allowed(actual_agent, actual_model, case.allowed_targets):
        fails.append(f"target `{actual_label}` not in allowed set")

    if fails:
        out["expectation_pass"] = False
        out["result"] = RESULT_EXPECTATION_FAILURE
        out["failure_reasons"] = fails
        return out

    out["expectation_pass"] = True

    if case.preferred_target:
        pref_a = case.preferred_target["agent"]
        pref_m = case.preferred_target.get("model") or ""
        if _target_key(actual_agent, actual_model) != _target_key(pref_a, pref_m):
            # Only warn when actual is still allowed (or no allowed list).
            out["preference_warning"] = True
            out["result"] = RESULT_PREFERENCE_WARNING

    if case.manual_review and out["result"] == RESULT_PASS:
        out["result"] = RESULT_MANUAL_REVIEW

    return out
