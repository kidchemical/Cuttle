"""Chat/Discord integration helpers for the agent router."""

from __future__ import annotations

from typing import Any, Dict, Optional

from api.agent_router.commands import (
    handle_retry_command,
    handle_route_command,
    handle_router_command,
    parse_retry_command,
    parse_route_command,
    parse_router_command,
)
from api.agent_router.config import load_router_config
from api.agent_router.dispatch import (
    execute_decision,
    execute_explicit_target,
    get_last_failure,
)
from api.agent_router.engine import build_context, decide, should_invoke_router
from api.agent_router.types import ExecutionTarget, RoutingDecision, TargetSource


def handle_router_family_command(
    message: str,
    *,
    session_id: Any = None,
    project_path: Optional[str] = None,
    status_queue=None,
) -> Optional[Dict[str, Any]]:
    """
    Handle /router, /route, /retry, /coordinator, /coordinate.
    Returns a chat reply dict, or None if not matched.

    /route and /retry may run an agent (returned dict is the execution result).
    /coordinate may start a supervised task.
    """
    from api.agent_router.supervised.commands import (
        handle_coordinate_command,
        handle_coordinator_command,
        parse_coordinate_command,
        parse_coordinator_command,
    )

    router_args = parse_router_command(message)
    if router_args is not None:
        body = handle_router_command(router_args, session_id=session_id)
        body.setdefault("session_id", session_id)
        return body

    coord_cfg = parse_coordinator_command(message)
    if coord_cfg is not None:
        body = handle_coordinator_command(coord_cfg, session_id=session_id)
        body.setdefault("session_id", session_id)
        body.setdefault("native_command", "/coordinator")
        return body

    coord_task = parse_coordinate_command(message)
    if coord_task is not None:
        body = handle_coordinate_command(
            coord_task,
            session_id=session_id,
            project_path=project_path,
            status_queue=status_queue,
        )
        body.setdefault("session_id", session_id)
        body.setdefault("native_command", "/coordinate")
        return body

    route_args = parse_route_command(message)
    if route_args is not None:
        err, prompt, target = handle_route_command(route_args)
        if err:
            err.setdefault("session_id", session_id)
            return err
        assert target is not None and prompt is not None
        result = execute_explicit_target(
            target,
            prompt,
            chat_session_id=session_id,
            status_queue=status_queue,
            project_path=project_path,
            source=TargetSource.MANUAL_OVERRIDE.value,
        )
        result.setdefault("session_id", session_id)
        return result

    retry_args = parse_retry_command(message)
    if retry_args is not None:
        err, mode = handle_retry_command(retry_args)
        if err:
            err.setdefault("session_id", session_id)
            return err
        last = get_last_failure(session_id)
        if not last:
            return {
                "success": True,
                "response": (
                    "❌ **Agent router:** No failed routed turn to retry in this session. "
                    "Run a routed task first."
                ),
                "type": "router_error",
                "session_id": session_id,
            }
        cfg = load_router_config()
        prompt = str(last.get("prompt") or "")
        path = project_path or last.get("project_path")
        if mode == "frontier":
            target = cfg.escalation_target
            source = TargetSource.RETRY.value
        else:
            if not cfg.fallbacks.ordered:
                return {
                    "success": True,
                    "response": "❌ **Agent router:** Fallback chain is empty.",
                    "type": "router_error",
                    "session_id": session_id,
                }
            target = cfg.fallbacks.ordered[0]
            source = TargetSource.RETRY.value
        result = execute_explicit_target(
            target,
            prompt,
            chat_session_id=session_id,
            status_queue=status_queue,
            project_path=path,
            source=source,
        )
        result.setdefault("session_id", session_id)
        return result

    return None


def maybe_route_plain_message(
    message: str,
    *,
    session_id: Any = None,
    project_id: str = "",
    project_name: str = "",
    project_path: Optional[str] = None,
    status_queue=None,
    coordinator_runner=None,
) -> Optional[Dict[str, Any]]:
    """
    If router is enabled and no session agent is selected, decide + execute.
    Returns result dict, or None to preserve existing Cuttle behavior.

    When a supervised task is active for this session, free-form messages go to
    the coordinator conversation (not the worker / ordinary router) — even while
    the worker process is running.
    """
    text = (message or "").strip()
    if text and not text.startswith("/"):
        try:
            from api.agent_router.supervised.store import get_active_task
            from api.agent_router.supervised.orchestrator import (
                handle_coordinator_conversation,
            )
            from api.agent_router.supervised.types import TaskPhase

            task = get_active_task(session_id)
            if task and task.phase not in (
                TaskPhase.APPROVED.value,
                TaskPhase.ESCALATED.value,
                TaskPhase.FAILED.value,
                TaskPhase.CANCELLED.value,
                TaskPhase.BUDGET_EXHAUSTED.value,
            ):
                return handle_coordinator_conversation(
                    text,
                    session_id=session_id,
                    project_path=project_path,
                    status_queue=status_queue,
                    coordinator_runner=coordinator_runner,
                )
        except Exception as e:
            print(f"[agent_router] supervised conversation hook failed: {e}", flush=True)

    cfg = load_router_config()
    should, reason = should_invoke_router(message, session_id=session_id, config=cfg)
    if not should:
        return None

    ctx = build_context(
        message,
        project_id=project_id or "",
        project_name=project_name or "",
        project_path=project_path or "",
        session_id=session_id,
    )
    decision = decide(ctx, cfg)

    # Silent-failure escalation: a re-sent prompt counts as the prior attempt
    # failing; escalate one hop per repeat (no tokens, user evidence only).
    run_prompt = message
    try:
        from api.agent_router.repeats import build_repeat_handoff, evaluate_repeat

        decision, repeat_meta = evaluate_repeat(decision, message, session_id, cfg=cfg)
        run_prompt = build_repeat_handoff(message, repeat_meta)
        if repeat_meta.get("frustration"):
            # Deep pass runs in parallel via a configurable agent (cursor/auto
            # default) — never blocks the escalated task below.
            from api.agent_router.rage_investigator import maybe_start_investigation

            maybe_start_investigation(
                session_id=session_id,
                frustration_meta=repeat_meta.get("frustration") or {},
                project_path=project_path,
            )
    except Exception as e:
        print(f"[agent_router] repeat evaluation failed: {e}", flush=True)

    result = execute_decision(
        decision,
        run_prompt,
        chat_session_id=session_id,
        status_queue=status_queue,
        project_path=project_path,
        config=cfg,
    )
    result.setdefault("session_id", session_id)
    result.setdefault("type", result.get("type") or "router_execution")
    return result
