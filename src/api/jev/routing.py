"""Router-brain recipe: Choice / Score / Noul → RoutingDecision fields."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.agent_router.providers.base import ProviderError
from api.agent_router.registry import validate_execution_target
from api.agent_router.types import (
    VALID_DIFFICULTIES,
    VALID_TASK_TYPES,
    ExecutionTarget,
    RouterConfig,
    RoutingContext,
    RoutingDecision,
    TargetSource,
)
from api.jev import thresholds as T
from api.jev.client import JevError, get_client
from api.jev.types import choice, noul, score

_TASK_CRITERIA = {
    "basic_ask": "Short question, no code edits, can be answered in chat",
    "coding": "Implement or change code",
    "debugging": "Find and fix a bug",
    "architecture": "Design, refactor plan, or cross-cutting structure",
    "research": "Look something up, compare options, no immediate patch",
    "other": "Does not fit the other buckets",
}

_DIFFICULTY_LEVELS = [
    "Low — ordinary, cheap Auto is enough",
    "Medium — standard coding, Auto or a mid model",
    "High — ambiguous, architecture, or likely to fail on Auto",
]


def _target_key(t: ExecutionTarget) -> str:
    model = (t.model or "default").replace(".", "").replace("-", "")
    return f"{t.agent}__{model}"


def _parse_target_key(key: str, catalog: List[ExecutionTarget]) -> Optional[ExecutionTarget]:
    by_key = {_target_key(t): t for t in catalog}
    if key in by_key:
        return by_key[key]
    # Tolerate the model returning agent:model
    if ":" in key:
        agent, _, model = key.partition(":")
        t, err = validate_execution_target(agent, model, allow_empty_model=True)
        return t if t and not err else None
    if "__" in key:
        agent, _, model = key.partition("__")
        t, err = validate_execution_target(agent, model, allow_empty_model=True)
        return t if t and not err else None
    return None


def _catalog(context: RoutingContext, config: RouterConfig) -> List[ExecutionTarget]:
    targets = list(context.available_targets or [])
    if not targets:
        targets = [config.default_target, config.escalation_target, *config.fallbacks.ordered]
    seen = set()
    out: List[ExecutionTarget] = []
    for t in targets:
        k = t.key()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out[:40]


def decide_routing(
    context: RoutingContext,
    config: RouterConfig,
    *,
    client=None,
) -> RoutingDecision:
    """Ask Jev, then apply deterministic policy. Raises ProviderError."""
    catalog = _catalog(context, config)
    criteria = {
        _target_key(t): f"{t.agent} / {t.model or '(default)'}"
        for t in catalog
    }
    if not criteria:
        raise ProviderError("no routing targets", retryable=False)

    state = {
        "user_request": (context.user_request or "")[:2000],
        "project_name": context.project_name or "",
        "code_changes_requested": bool(context.code_changes_requested),
        "constraints": (context.explicit_constraints or "")[:400],
        "default_target": config.default_target.key(),
        "escalation_target": config.escalation_target.key(),
        "allowed_targets": [t.key() for t in catalog],
    }
    questions = {
        "task_type": choice(
            "Which task type best describes `user_request`?",
            _TASK_CRITERIA,
        ),
        "difficulty": score(
            "How hard is `user_request` for a coding agent on this machine",
            _DIFFICULTY_LEVELS,
        ),
        "target": choice(
            "Which allowed execution target should run this turn? Prefer cheaper when difficulty is low.",
            criteria,
        ),
        "needs_execution": noul(
            "Does `user_request` need a coding agent (file edits, shell, debugging) rather than a short chat answer?",
            true="Needs an execution harness",
            false="A short direct answer is enough",
        ),
    }
    try:
        c = client or get_client()
        result = c.system_one(state, questions)
    except JevError as e:
        raise ProviderError(str(e), retryable=e.retryable) from e
    except Exception as e:
        raise ProviderError(str(e), retryable=True) from e

    task = (result.get("task_type").choice or "").strip().lower()
    if task not in VALID_TASK_TYPES:
        task = "coding" if context.code_changes_requested else "basic_ask"

    diff_ans = result.get("difficulty")
    diff_score = float(diff_ans.score) if diff_ans.score is not None else 1.0
    if diff_score < 0.5:
        difficulty = "low"
    elif diff_score < T.ESCALATE_DIFFICULTY_SCORE:
        difficulty = "medium"
    else:
        difficulty = "high"
    if difficulty not in VALID_DIFFICULTIES:
        difficulty = "medium"

    target_ans = result.get("target")
    picked = _parse_target_key(target_ans.choice or "", catalog) or config.default_target
    conf = float(target_ans.confidence) if target_ans.confidence is not None else 0.5
    needs = float(result.get("needs_execution").noul or 0.0)

    # Policy: uncertain or clearly-not-coding → default (usually Cursor Auto).
    if conf < T.TARGET_CONFIDENCE_FLOOR or needs < T.NEEDS_EXECUTION_NOUL:
        if difficulty != "high":
            picked = config.default_target
            conf = min(conf, 0.45)
    if difficulty == "high" and picked.key() == config.default_target.key():
        picked = config.escalation_target

    reason = (
        f"jev task={task} difficulty={difficulty} ({diff_score:.2f}) "
        f"needs={needs:.2f} target_conf={conf:.2f}"
    )[:240]
    return RoutingDecision(
        decision_id=RoutingDecision.new_id(),
        task_type=task,
        difficulty=difficulty,
        target=picked,
        confidence=max(0.0, min(1.0, conf)),
        reason=reason,
        escalation_target=config.escalation_target,
        source=TargetSource.ROUTER.value,
        raw={
            "provider": "jev",
            "model": result.model,
            "answers": result.to_dict()["answers"],
            "usage": result.usage,
            "policy": {
                "needs_execution": needs,
                "difficulty_score": diff_score,
                "target_confidence": conf,
            },
        },
    )
