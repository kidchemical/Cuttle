"""Execute a routed target with escalation + ordered fallbacks."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from api.agent_router import logging_events as log
from api.agent_router.config import load_router_config
from api.agent_router.outcomes import record_attempt
from api.agent_router.policy import (
    classify_failure,
    is_auto_target,
)
from api.agent_router.types import (
    ExecutionOutcome,
    ExecutionTarget,
    FailureKind,
    RouterConfig,
    RoutingDecision,
    TargetSource,
)

RunnerFn = Callable[..., Dict[str, Any]]

# Last failed routed turn per session (for /retry frontier|fallback).
_LAST_FAILURE: Dict[str, Dict[str, Any]] = {}


def _sid_key(session_id: Any) -> str:
    return str(session_id) if session_id is not None else "_none"


def remember_failure(session_id: Any, payload: Dict[str, Any]) -> None:
    _LAST_FAILURE[_sid_key(session_id)] = payload


def get_last_failure(session_id: Any) -> Optional[Dict[str, Any]]:
    return _LAST_FAILURE.get(_sid_key(session_id))


def clear_last_failure(session_id: Any) -> None:
    _LAST_FAILURE.pop(_sid_key(session_id), None)


def _default_runners() -> Dict[str, RunnerFn]:
    """Late-bind runners. Harness agents (cursor/codex/muse/claude/…) come from catalog."""
    from api import web_chat_api as w

    def harness_runner_factory(agent_id: str) -> RunnerFn:
        def _runner(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **_kw):
            return w._run_harness_web_command(
                agent_id,
                prompt,
                chat_session_id,
                status_queue=status_queue,
                project_path=project_path,
                model_override=model,
            )

        return _runner

    runners: Dict[str, RunnerFn] = {}
    try:
        from api.agent_harness.catalog import list_agents

        for aid in list_agents():
            runners[aid] = harness_runner_factory(aid)
    except Exception as exc:
        print(f"[agent_router] harness runners skipped: {exc}", flush=True)
    return runners


def _run_one(
    target: ExecutionTarget,
    prompt: str,
    *,
    chat_session_id: Any,
    status_queue=None,
    project_path: Optional[str] = None,
    runners: Optional[Dict[str, RunnerFn]] = None,
    source: str = TargetSource.ROUTER.value,
    decision_id: Optional[str] = None,
) -> Dict[str, Any]:
    runners = runners or _default_runners()
    fn = runners.get(target.agent)
    log.log_target_started(
        target.agent,
        target.model or "(default)",
        source,
        decision_id=decision_id,
    )
    if not fn:
        return {
            "success": True,
            "response": f"❌ No runner registered for agent `{target.agent}`.",
            "type": "router_error",
            "error": f"unsupported agent {target.agent}",
        }
    try:
        return fn(
            prompt,
            chat_session_id,
            status_queue=status_queue,
            project_path=project_path,
            model=target.model or None,
        )
    except Exception as e:
        return {
            "success": True,
            "response": f"❌ **{target.agent}:** {e}",
            "type": "router_error",
            "error": str(e),
        }


def _current_turn(chat_session_id: Any) -> Optional[int]:
    try:
        from api import chat_delivery

        return chat_delivery.current_turn(chat_session_id)
    except Exception:
        return None


def _turn_superseded(chat_session_id: Any, turn: Optional[int]) -> bool:
    """True once a newer user turn owns this chat — stop spending on this one."""
    if turn is None:
        return False
    try:
        from api import chat_delivery

        return chat_delivery.is_stale_turn(chat_session_id, turn)
    except Exception:
        return False


def _annotate_result(
    result: Dict[str, Any],
    *,
    target: ExecutionTarget,
    decision: Optional[RoutingDecision],
    source: str,
    attempts: List[Dict[str, Any]],
    routed_note: str = "",
) -> Dict[str, Any]:
    out = dict(result or {})
    out["router"] = {
        "decision_id": decision.decision_id if decision else None,
        "target": target.to_dict(),
        "source": source,
        "attempts": list(attempts),
        "note": routed_note,
    }
    if routed_note and isinstance(out.get("response"), str):
        # Prepend a short routing footer only once for visibility.
        if not out["response"].startswith("🔀"):
            out["response"] = f"🔀 {routed_note}\n\n{out['response']}"
    return out


def execute_decision(
    decision: RoutingDecision,
    prompt: str,
    *,
    chat_session_id: Any,
    status_queue=None,
    project_path: Optional[str] = None,
    config: Optional[RouterConfig] = None,
    runners: Optional[Dict[str, RunnerFn]] = None,
    allow_escalation: bool = True,
) -> Dict[str, Any]:
    """
    Run the selected target. On Auto task failure → escalate once.
    On transport failure → walk fallbacks. Never unbounded loop.

    When ``decision.strategy == supervised``, delegates to the supervised
    orchestrator (does not invent a fake agent name).
    """
    strategy = (getattr(decision, "strategy", None) or "direct").strip().lower()
    if strategy == "supervised":
        from api.agent_router.supervised.orchestrator import start_supervised_task

        return start_supervised_task(
            prompt,
            parent_session_id=chat_session_id,
            project_path=project_path or "",
            decision_id=decision.decision_id,
            profile_id=getattr(decision, "supervised_profile", None),
            status_queue=status_queue,
            background=True,
        )

    cfg = config or load_router_config()
    attempts: List[Dict[str, Any]] = []
    tried_keys = set()
    turn = _current_turn(chat_session_id)

    # Phase B — never start (or fall forward into) a demoted target.
    try:
        from api.agent_router.drift import active_demotions

        demoted_keys = set(active_demotions().keys())
    except Exception:
        demoted_keys = set()

    def try_target(target: ExecutionTarget, source: str, run_prompt: str):
        key = target.key()
        if key in tried_keys:
            dup = {
                "success": True,
                "response": f"Skipped duplicate target `{key}`.",
                "type": "router_error",
                "error": "duplicate",
            }
            return dup, FailureKind.TRANSPORT.value, "duplicate", target, source
        tried_keys.add(key)
        started = time.perf_counter()
        result = _run_one(
            target,
            run_prompt,
            chat_session_id=chat_session_id,
            status_queue=status_queue,
            project_path=project_path,
            runners=runners,
            source=source,
            decision_id=decision.decision_id,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        kind, reason = classify_failure(result, response_text=str(result.get("response") or ""))
        attempt = {
            "agent": target.agent,
            "model": target.model,
            "source": source,
            "failure_kind": kind,
            "reason": reason,
            "latency_ms": round(latency_ms, 3),
        }
        attempts.append(attempt)
        record_attempt(
            decision=decision,
            attempt_index=len(attempts) - 1,
            target=target,
            source=source,
            failure_kind=kind,
            reason=reason,
            latency_ms=latency_ms,
            result=result,
            session_id=chat_session_id,
            project_path=project_path,
        )
        return result, kind, reason, target, source

    def cancelled_out(result, used, source, reason):
        """A cancelled turn ends here — no escalation, no fallback, no spend."""
        log.log_cancelled(used.agent, used.model or "(default)", reason, decision_id=decision.decision_id)
        clear_last_failure(chat_session_id)
        return _annotate_result(
            result, target=used, decision=decision, source=source, attempts=attempts, routed_note=""
        )

    def stale_out(result, used, source):
        """The user moved on — don't start another target for a dead turn."""
        log.log_stale_turn(
            "newer user turn owns this chat",
            decision_id=decision.decision_id,
            attempts=len(attempts),
        )
        return _annotate_result(
            result, target=used, decision=decision, source=source, attempts=attempts, routed_note=""
        )

    # Primary
    primary = decision.target
    result, kind, reason, used, source = try_target(primary, decision.source, prompt)

    if kind == FailureKind.CANCELLED.value:
        return cancelled_out(result, used, source, reason)

    if kind == FailureKind.NONE.value:
        clear_last_failure(chat_session_id)
        note = (
            f"Routed to `{used.agent}` / `{used.model or 'default'}` "
            f"({decision.reason or decision.source})"
        )
        return _annotate_result(
            result, target=used, decision=decision, source=source, attempts=attempts, routed_note=note
        )

    if _turn_superseded(chat_session_id, turn):
        return stale_out(result, used, source)

    # Compact handoff for escalation
    if kind == FailureKind.TASK.value and allow_escalation and is_auto_target(used):
        esc = decision.escalation_target or cfg.escalation_target
        if esc.key() not in demoted_keys:
            log.log_escalation(esc.agent, esc.model, reason, decision_id=decision.decision_id)
            summary = (str(result.get("response") or ""))[:2500]
            prompt = (
                f"{prompt}\n\n---\n"
                f"[Cuttle router escalation handoff]\n"
                f"Prior target: {used.agent} / {used.model} failed ({reason}).\n"
                f"Compact prior output:\n{summary}\n"
                f"Continue from this state; do not rediscover the whole attempt.\n"
            )
            result, kind, reason, used, source = try_target(
                esc, TargetSource.ESCALATION.value, prompt
            )
            if kind == FailureKind.CANCELLED.value:
                return cancelled_out(result, used, source, reason)
            if kind == FailureKind.NONE.value:
                clear_last_failure(chat_session_id)
                note = f"Escalated to `{used.agent}` / `{used.model}` after Auto task failure"
                return _annotate_result(
                    result, target=used, decision=decision, source=source, attempts=attempts, routed_note=note
                )

    # Transport failures (and remaining failures after escalation) → fallbacks
    chain: List[ExecutionTarget] = []
    if kind == FailureKind.TRANSPORT.value and is_auto_target(primary):
        esc = decision.escalation_target or cfg.escalation_target
        if esc.key() not in tried_keys and esc.key() not in demoted_keys:
            chain.append(esc)
    ordered_fallbacks = (
        decision.fallbacks if decision.fallbacks is not None else cfg.fallbacks.ordered
    )
    for fb in ordered_fallbacks:
        if fb.key() not in tried_keys and fb.key() not in demoted_keys:
            chain.append(fb)

    for fb in chain:
        if _turn_superseded(chat_session_id, turn):
            return stale_out(result, used, source)
        log.log_fallback(fb.agent, fb.model or "(default)", reason, decision_id=decision.decision_id)
        result, kind, reason, used, source = try_target(fb, TargetSource.FALLBACK.value, prompt)
        if kind == FailureKind.CANCELLED.value:
            return cancelled_out(result, used, source, reason)
        if kind == FailureKind.NONE.value:
            clear_last_failure(chat_session_id)
            note = (
                f"Fallback `{used.agent}` / `{used.model or 'default'}` "
                f"after: {reason or 'prior failure'}"
            )
            return _annotate_result(
                result, target=used, decision=decision, source=source, attempts=attempts, routed_note=note
            )

    log.log_exhausted(len(attempts), decision_id=decision.decision_id)

    if _turn_superseded(chat_session_id, turn):
        return stale_out(result, used, source)

    # Optional supervised fallback (disabled by default; never silent paid).
    try:
        from api.agent_router.supervised.policy_hooks import supervised_frontier_fallback_candidate

        supervised_decision, skip_reason = supervised_frontier_fallback_candidate(
            failure_reason=reason or "targets exhausted"
        )
        if supervised_decision is not None:
            log.log_fallback(
                "supervised",
                supervised_decision.supervised_profile or "diet-frontier",
                reason or "frontier unavailable",
                decision_id=decision.decision_id,
            )
            return execute_decision(
                supervised_decision,
                prompt,
                chat_session_id=chat_session_id,
                status_queue=status_queue,
                project_path=project_path,
                config=cfg,
                runners=runners,
                allow_escalation=False,
            )
        if skip_reason:
            log.log_fallback("supervised", "(skipped)", skip_reason, decision_id=decision.decision_id)
    except Exception as _sf_err:
        log.log_fallback("supervised", "(error)", str(_sf_err)[:120], decision_id=decision.decision_id)

    remember_failure(
        chat_session_id,
        {
            "prompt": prompt,
            "decision": decision.to_dict(),
            "attempts": attempts,
            "project_path": project_path,
        },
    )
    lines = [
        "❌ **Agent router:** All execution targets failed.",
        "",
        f"Decision `{decision.decision_id}` — {decision.reason}",
        "",
        "Attempts:",
    ]
    for a in attempts:
        lines.append(
            f"- `{a['agent']}` / `{a.get('model') or 'default'}` "
            f"[{a.get('source')}] {a.get('failure_kind')}: {a.get('reason') or 'n/a'}"
        )
    lines.append("")
    lines.append("Try `/retry frontier`, `/retry fallback`, or `/route <agent> <model> …`.")
    exhausted = {
        "success": True,
        "response": "\n".join(lines),
        "type": "router_error",
        "session_id": chat_session_id,
    }
    return _annotate_result(
        exhausted,
        target=used,
        decision=decision,
        source=TargetSource.FALLBACK.value,
        attempts=attempts,
        routed_note="",
    )


def execute_explicit_target(
    target: ExecutionTarget,
    prompt: str,
    *,
    chat_session_id: Any,
    status_queue=None,
    project_path: Optional[str] = None,
    runners: Optional[Dict[str, RunnerFn]] = None,
    source: str = TargetSource.MANUAL_OVERRIDE.value,
) -> Dict[str, Any]:
    cfg = load_router_config()
    decision = RoutingDecision(
        decision_id=RoutingDecision.new_id(),
        task_type="other",
        difficulty="medium",
        target=target,
        confidence=1.0,
        reason="explicit override",
        escalation_target=cfg.escalation_target,
        source=source,
    )
    return execute_decision(
        decision,
        prompt,
        chat_session_id=chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        config=cfg,
        runners=runners,
        allow_escalation=source != TargetSource.MANUAL_OVERRIDE.value,
    )
