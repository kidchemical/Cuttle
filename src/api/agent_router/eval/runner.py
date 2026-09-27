"""Run router evaluation without invoking any execution agent."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from api.agent_router.config import load_router_config
from api.agent_router.engine import build_context, decide_with_outcome, default_decision
from api.agent_router.eval.scoring import score_case
from api.agent_router.eval.stability import aggregate_repeated_runs, summarize_stability
from api.agent_router.eval.suites import EvalSuite, EvalTestCase
from api.agent_router.types import RouterConfig, RoutingDecision

DecideFn = Callable[..., Any]


def evaluate_prompt(
    prompt: str,
    *,
    config: Optional[RouterConfig] = None,
    project_name: str = "Cuttle",
    project_path: str = "",
    project_id: str = "",
    decide_fn: Optional[DecideFn] = None,
) -> Dict[str, Any]:
    """Single-prompt routing evaluation (no executor)."""
    cfg = config or load_router_config()
    ctx = build_context(
        prompt,
        project_id=project_id,
        project_name=project_name,
        project_path=project_path,
        session_id=None,
    )
    t0 = time.perf_counter()
    fn = decide_fn or decide_with_outcome
    decision, meta = fn(ctx, cfg)
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    assert isinstance(decision, RoutingDecision)
    return {
        "prompt": prompt,
        "decision": decision.to_dict(),
        "used_fallback": bool(meta.get("used_fallback")),
        "api_error": meta.get("api_error"),
        "invalid_rejected": bool(meta.get("invalid_rejected")),
        "provider": meta.get("provider"),
        "latency_ms": latency_ms,
        "router_config": {
            "mode": cfg.provider.mode,
            "api_model": cfg.provider.api_model,
            "default_target": cfg.default_target.to_dict(),
            "escalation_target": cfg.escalation_target.to_dict(),
        },
    }


def _run_one_case(
    case: EvalTestCase,
    *,
    cfg: RouterConfig,
    project_name: str,
    project_path: str,
    decide_fn: DecideFn,
    run_index: int = 0,
) -> Dict[str, Any]:
    ctx = build_context(
        case.prompt,
        project_name=project_name,
        project_path=project_path,
        session_id=None,
    )
    t0 = time.perf_counter()
    api_error = None
    used_fallback = False
    invalid_rejected = False
    try:
        decision, meta = decide_fn(ctx, cfg)
        used_fallback = bool(meta.get("used_fallback"))
        api_error = meta.get("api_error")
        invalid_rejected = bool(meta.get("invalid_rejected"))
    except Exception as e:
        api_error = str(e)
        used_fallback = True
        decision = default_decision(cfg, f"router error: {e}")
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    scored = score_case(
        case,
        decision,
        used_fallback=used_fallback,
        api_error=api_error,
        invalid_rejected=invalid_rejected,
    )
    return {
        "id": case.id,
        "run_index": run_index,
        "category": case.category,
        "prompt": case.prompt,
        "notes": case.notes,
        "allowed_task_types": case.allowed_task_types,
        "allowed_difficulties": case.allowed_difficulties,
        "allowed_targets": case.allowed_targets,
        "preferred_target": case.preferred_target,
        "manual_review_requested": case.manual_review,
        "decision": decision.to_dict(),
        "task_type": decision.task_type,
        "difficulty": decision.difficulty,
        "target_agent": decision.target.agent,
        "target_model": decision.target.model,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "decision_id": decision.decision_id,
        "source": decision.source,
        "used_fallback": used_fallback,
        "api_error": api_error,
        "invalid_rejected": invalid_rejected,
        "latency_ms": latency_ms,
        **scored,
    }


def run_suite(
    suite: EvalSuite,
    *,
    concurrency: int = 3,
    repeats: int = 1,
    config: Optional[RouterConfig] = None,
    project_name: str = "Cuttle",
    project_path: str = "",
    decide_fn: Optional[DecideFn] = None,
) -> Dict[str, Any]:
    """Evaluate all suite cases with bounded concurrency. Never runs executors.

    When ``repeats`` > 1, each case is decided that many times; every raw run is
    preserved and per-case stability is aggregated.
    """
    cfg = config or load_router_config()
    fn = decide_fn or decide_with_outcome
    workers = max(1, min(8, int(concurrency or 3)))
    n_repeats = max(1, min(20, int(repeats or 1)))
    started = time.time()

    # Schedule (case, run_index) jobs
    jobs: List[Tuple[EvalTestCase, int]] = [
        (case, ri) for case in suite.cases for ri in range(n_repeats)
    ]
    raw_runs: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_one_case,
                case,
                cfg=cfg,
                project_name=project_name,
                project_path=project_path,
                decide_fn=fn,
                run_index=ri,
            ): (case.id, ri)
            for case, ri in jobs
        }
        for fut in as_completed(futures):
            cid, ri = futures[fut]
            try:
                raw_runs.append(fut.result())
            except Exception as e:
                raw_runs.append(
                    {
                        "id": cid,
                        "run_index": ri,
                        "category": "error",
                        "prompt": "",
                        "result": "api_error",
                        "validation_pass": False,
                        "expectation_pass": False,
                        "preference_warning": False,
                        "manual_review": False,
                        "api_error": str(e),
                        "used_fallback": True,
                        "latency_ms": 0,
                        "expected_summary": "",
                        "actual_summary": "(crashed)",
                        "decision": None,
                        "target_agent": "",
                        "target_model": "",
                        "task_type": "",
                        "difficulty": "",
                        "confidence": None,
                    }
                )

    # Group by case id, preserve suite order, sort runs by run_index
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    for r in raw_runs:
        by_id.setdefault(str(r.get("id")), []).append(r)
    for cid in by_id:
        by_id[cid].sort(key=lambda x: int(x.get("run_index") or 0))

    ordered: List[Dict[str, Any]] = []
    for case in suite.cases:
        runs = by_id.get(case.id, [])
        if not runs:
            continue
        if n_repeats == 1:
            ordered.append(runs[0])
            continue
        meta = {
            "id": case.id,
            "category": case.category,
            "prompt": case.prompt,
            "notes": case.notes,
            "allowed_task_types": case.allowed_task_types,
            "allowed_difficulties": case.allowed_difficulties,
            "allowed_targets": case.allowed_targets,
            "preferred_target": case.preferred_target,
            "manual_review_requested": case.manual_review,
            "expected_summary": runs[0].get("expected_summary"),
        }
        ordered.append(aggregate_repeated_runs(meta, runs))

    elapsed_ms = round((time.time() - started) * 1000.0, 1)
    total_decisions = len(raw_runs)
    report: Dict[str, Any] = {
        "suite": suite.name,
        "suite_version": suite.version,
        "description": suite.description,
        "concurrency": workers,
        "repeats": n_repeats,
        "case_count": len(ordered),
        "decision_count": total_decisions,
        "elapsed_ms": elapsed_ms,
        "router": {
            "mode": cfg.provider.mode,
            "api_provider": cfg.provider.api_provider,
            "api_model": cfg.provider.api_model,
            "default_target": cfg.default_target.to_dict(),
            "escalation_target": cfg.escalation_target.to_dict(),
            "fallbacks": [t.to_dict() for t in cfg.fallbacks.ordered],
        },
        "counts": _summarize_counts(ordered),
        "cases": ordered,
    }
    if n_repeats > 1:
        report["stability_summary"] = summarize_stability(ordered)
    return report


def _summarize_counts(cases: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {
        "total": len(cases),
        "passed": 0,
        "preference_warnings": 0,
        "expectation_failures": 0,
        "invalid": 0,
        "manual_review": 0,
        "fallback": 0,
        "api_error": 0,
    }
    for c in cases:
        r = c.get("result")
        if r == "pass":
            counts["passed"] += 1
        elif r == "preference_warning":
            counts["preference_warnings"] += 1
            counts["passed"] += 1
        elif r == "expectation_failure":
            counts["expectation_failures"] += 1
        elif r == "invalid":
            counts["invalid"] += 1
        elif r == "manual_review":
            counts["manual_review"] += 1
            counts["passed"] += 1
        elif r == "fallback":
            counts["fallback"] += 1
        elif r == "api_error":
            counts["api_error"] += 1
    return counts
