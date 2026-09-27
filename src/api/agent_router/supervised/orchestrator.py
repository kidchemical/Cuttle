"""Supervised-task orchestrator — async worker, event-driven review (no model polling)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from api.agent_router.supervised import events as slog
from api.agent_router.supervised.adapters import (
    CodexCoordinatorAdapter,
    CursorWorkerAdapter,
    economic_source_for,
)
from api.agent_router.supervised.control import session_owns_task
from api.agent_router.supervised.evidence import collect_programmatic_evidence, snapshot_worktree
from api.agent_router.supervised.helpers import format_follow_up_prompt
from api.agent_router.supervised.packet import (
    coordinator_plan_prompt,
    coordinator_review_prompt,
    format_final_user_message,
    packet_from_coordinator_text,
    parse_review_decision,
    parse_worker_report,
)
from api.agent_router.supervised.profiles import get_profile, profile_objects
from api.agent_router.supervised.store import (
    append_event,
    control_event_response_body,
    find_control_event,
    get_active_task,
    load_task,
    persist_raw_worker_output,
    record_control_event,
    save_task,
)
from api.agent_router.supervised.types import (
    EconomicSource,
    ReviewAction,
    RoutingStrategy,
    SupervisedTask,
    TaskPhase,
    WorkerRun,
    new_id,
)
from api.agent_router.supervised.verification import (
    collect_mode_evidence,
    infer_verification_mode,
)

# In-flight background workers (task_id -> Thread). Durable state is authoritative.
_THREADS: Dict[str, threading.Thread] = {}
_THREADS_LOCK = threading.Lock()
_CANCEL: Dict[str, threading.Event] = {}
# Serialize coordinator model turns per coordinator_session_id.
_COORD_LOCKS: Dict[str, threading.Lock] = {}
_COORD_LOCKS_GUARD = threading.Lock()

_TERMINAL = frozenset(
    {
        TaskPhase.APPROVED.value,
        TaskPhase.ESCALATED.value,
        TaskPhase.FAILED.value,
        TaskPhase.CANCELLED.value,
        TaskPhase.BUDGET_EXHAUSTED.value,
    }
)

_ACTIVE_WORKER_PHASES = frozenset(
    {
        TaskPhase.WORKER_RUNNING.value,
        TaskPhase.COORDINATING.value,
        TaskPhase.REVIEWING.value,
        TaskPhase.FOLLOW_UP.value,
        TaskPhase.DELEGATED.value,
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coord_lock(session_key: str) -> threading.Lock:
    with _COORD_LOCKS_GUARD:
        lock = _COORD_LOCKS.get(session_key)
        if lock is None:
            lock = threading.Lock()
            _COORD_LOCKS[session_key] = lock
        return lock


def _persist_parent_message(session_id: Any, text: str, *, metadata: Optional[Dict] = None) -> None:
    if session_id is None or not text:
        return
    try:
        from api.auth_db import get_auth_db

        sid = session_id
        if isinstance(sid, str) and sid.startswith("db_session_"):
            try:
                sid = int(sid[len("db_session_") :])
            except ValueError:
                pass
        get_auth_db().add_message(
            sid,
            "assistant",
            text,
            metadata=metadata or {"origin": "supervised_coordinator"},
        )
    except Exception as e:
        slog.supervised_log("persist_failed", error=str(e)[:200])


_PHASE_COPY = {
    TaskPhase.CREATED.value: "Thinking…",
    TaskPhase.COORDINATING.value: "Planning task…",
    TaskPhase.DELEGATED.value: "Working…",
    TaskPhase.WORKER_RUNNING.value: "Working…",
    TaskPhase.FOLLOW_UP.value: "Working…",
    TaskPhase.REVIEWING.value: "Reviewing results…",
}


def _chat_session_id(session_id: Any) -> Any:
    sid = session_id
    if isinstance(sid, str) and sid.startswith("db_session_"):
        try:
            sid = int(sid[len("db_session_") :])
        except ValueError:
            pass
    return sid


def _canonical_metadata(task: SupervisedTask) -> Dict[str, Any]:
    from api.agent_router.supervised.bubble import bubble_metadata

    return bubble_metadata(task)


def ensure_canonical_response(task: SupervisedTask) -> SupervisedTask:
    """Create the one ordinary assistant row owned by this supervised task."""
    if task.coordinator_response_message_id:
        return task
    try:
        from api.agent_router.supervised.bubble import build_bubble_content
        from api.auth_db import get_auth_db

        db = get_auth_db()
        sid = _chat_session_id(task.parent_session_id)
        if task.parent_user_message_id is None:
            rows = db.get_messages(sid, limit=12) or []
            for row in reversed(rows):
                if row.get("role") == "user":
                    task.parent_user_message_id = row.get("id")
                    break
        body = build_bubble_content(task)
        mid = db.add_message(sid, "assistant", body, metadata=_canonical_metadata(task))
        task.coordinator_response_message_id = mid
        save_task(task)
        if hasattr(db, "update_message_content"):
            db.update_message_content(mid, body, _canonical_metadata(task))
        append_event(task, "canonical_response_created", message_id=mid)
    except Exception as e:
        # Anonymous/non-DB and historical compatibility keep the task usable.
        slog.supervised_log("canonical_response_create_failed", task_id=task.task_id, error=str(e)[:200])
    return task


def update_canonical_response(task: SupervisedTask, content: Optional[str] = None) -> bool:
    """Persist a phase/final update into the canonical coordinator bubble."""
    from api.agent_router.supervised.bubble import build_bubble_content

    task = ensure_canonical_response(task)
    mid = task.coordinator_response_message_id
    if not mid:
        return False
    text = content if content is not None else build_bubble_content(task)
    try:
        from api.auth_db import get_auth_db

        ok = get_auth_db().update_message_content(int(mid), text, _canonical_metadata(task))
        if ok:
            append_event(task, "canonical_response_updated", message_id=mid, phase=task.phase)
        return bool(ok)
    except Exception as e:
        slog.supervised_log("canonical_response_update_failed", task_id=task.task_id, error=str(e)[:200])
        return False


def _deliver_terminal(task: SupervisedTask) -> None:
    """Persist terminal result then queue for client delivery (idempotent)."""
    try:
        from api.agent_router.supervised.delivery import (
            deliver_terminal_to_parent,
            mark_terminal_persisted,
        )

        # Full bubble (final judgment + Activity). deliver_terminal also updates.
        update_canonical_response(task)
        mark_terminal_persisted(task)
        deliver_terminal_to_parent(task)
    except Exception as e:
        slog.supervised_log("delivery_failed", error=str(e)[:200], task_id=task.task_id)
        _persist_parent_message(
            task.parent_session_id,
            task.final_response,
            metadata={
                "origin": "supervised_coordinator",
                "supervised_task_id": task.task_id,
            },
        )


def _emit_parent_progress(session_id: Any, message: str) -> None:
    """Informational parent progress — must NOT mark the chat as generating.

    Worker activity owns its own session/run registration. Publishing
    active=True on the parent was the smoke-test root cause: the frontend
    treated the parent as busy and queued /coordinate status.
    """
    if not session_id or not message:
        return
    try:
        from api.web_chat_api import set_chat_live_status

        set_chat_live_status(session_id, message, active=False)
    except Exception:
        pass


def _worker_process_live(worker_session_id: str) -> bool:
    try:
        from api.chat_run_registry import has_live_process

        return bool(has_live_process(worker_session_id))
    except Exception:
        return False


def create_supervised_task(
    prompt: str,
    *,
    parent_session_id: Any,
    project_path: str = "",
    decision_id: str = "",
    profile_id: Optional[str] = None,
    paid_approval_granted: bool = False,
) -> SupervisedTask:
    prof = get_profile(profile_id)
    coord, worker, budget = profile_objects(prof)
    tid = new_id("st_")
    parent = str(parent_session_id or "")
    task = SupervisedTask(
        task_id=tid,
        parent_session_id=parent,
        coordinator_session_id=f"supervised_coord_{tid}",
        worker_session_id=f"supervised_worker_{tid}",
        profile_id=str(prof.get("id") or "diet-frontier"),
        phase=TaskPhase.CREATED.value,
        user_objective=prompt,
        decision_id=decision_id or new_id("rd_"),
        router_strategy=RoutingStrategy.SUPERVISED.value,
        budget=budget,
        project_path=project_path or "",
        coordinator=coord.to_dict(),
        worker=worker.to_dict(),
        paid_approval_granted=bool(paid_approval_granted),
        verification_mode=infer_verification_mode(user_objective=prompt, packet=None),
        created_at=_now(),
        updated_at=_now(),
    )
    save_task(task)
    append_event(
        task,
        "task_created",
        decision_id=task.decision_id,
        profile=task.profile_id,
        parent_session=parent,
    )
    # Canonical assistant bubble is created after the user row is persisted
    # (see web_chat_api / start_supervised_task) so history order stays correct.
    return task


def start_supervised_task(
    prompt: str,
    *,
    parent_session_id: Any,
    project_path: str = "",
    decision_id: str = "",
    profile_id: Optional[str] = None,
    status_queue=None,
    coordinator_runner=None,
    worker_runner=None,
    background: bool = True,
    paid_approval_granted: bool = False,
) -> Dict[str, Any]:
    """
    Plan via coordinator, then start worker asynchronously.

    Returns a chat reply immediately after the worker is kicked off (or after
    synchronous completion when ``background=False`` — tests only).
    """
    existing = get_active_task(parent_session_id)
    if existing and existing.phase in _ACTIVE_WORKER_PHASES:
        return {
            "success": True,
            "response": (
                f"❌ A supervised task is already active (`{existing.task_id}`, "
                f"phase `{existing.phase}`). Use `/coordinate status` or `/coordinate cancel`."
            ),
            "type": "supervised_busy",
            "task_id": existing.task_id,
        }

    task = create_supervised_task(
        prompt,
        parent_session_id=parent_session_id,
        project_path=project_path,
        decision_id=decision_id,
        profile_id=profile_id,
        paid_approval_granted=paid_approval_granted,
    )
    coord, worker, _budget = profile_objects(get_profile(task.profile_id))

    for label, harness in (("coordinator", coord.harness), ("worker", worker.harness)):
        src = economic_source_for(
            harness.economic_source, paid_ok=task.paid_approval_granted
        )
        if src == EconomicSource.PAID_REQUIRES_APPROVAL.value:
            task.phase = TaskPhase.FAILED.value
            task.final_response = (
                f"Blocked: `{label}` economic source requires explicit paid-tier approval "
                f"(`{harness.economic_source}`). Re-run with approval or change the profile."
            )
            save_task(task)
            _deliver_terminal(task)
            return {
                "success": True,
                "response": task.final_response,
                "type": "supervised_paid_blocked",
                "task_id": task.task_id,
                "skip_history_persist": True,
                "coordinator_response_message_id": task.coordinator_response_message_id,
            }

    coord_adapter = CodexCoordinatorAdapter(coord, runner=coordinator_runner)
    worker_adapter = CursorWorkerAdapter(worker, runner=worker_runner)

    task.phase = TaskPhase.COORDINATING.value
    save_task(task)
    update_canonical_response(task)
    _emit_parent_progress(parent_session_id, "Planning task…")

    plan_prompt = coordinator_plan_prompt(prompt, project_path)
    task.coordinator_invocations += 1
    save_task(task)
    slog.supervised_log(
        "coordinator_invoke",
        task_id=task.task_id,
        kind="plan",
        model=coord.harness.model,
        reasoning=coord.harness.reasoning,
        economic_source=coord.harness.economic_source,
    )
    with _coord_lock(task.coordinator_session_id):
        plan_result = coord_adapter.invoke(
            plan_prompt,
            session_id=task.coordinator_session_id,
            project_path=project_path,
            status_queue=status_queue,
        )
    plan_text = str(plan_result.get("response") or plan_result.get("output") or "")
    packet = packet_from_coordinator_text(
        plan_text, user_objective=prompt, workspace=project_path
    )
    task.packet = packet
    task.verification_mode = infer_verification_mode(
        user_objective=prompt, packet=packet
    )
    task.phase = TaskPhase.DELEGATED.value
    append_event(task, "delegated", objective=packet.objective[:200])
    update_canonical_response(task)

    from api.agent_router.supervised.bubble import build_bubble_content

    kickoff = build_bubble_content(task)

    if background:
        cancel = threading.Event()
        with _THREADS_LOCK:
            _CANCEL[task.task_id] = cancel
        th = threading.Thread(
            target=_worker_then_review,
            kwargs={
                "task_id": task.task_id,
                "worker_adapter": worker_adapter,
                "coord_adapter": coord_adapter,
                "status_queue": None,  # do not bind parent SSE queue to worker
                "cancel": cancel,
            },
            name=f"supervised-{task.task_id}",
            daemon=True,
        )
        with _THREADS_LOCK:
            _THREADS[task.task_id] = th
        th.start()
        _emit_parent_progress(parent_session_id, "Working…")
        return {
            "success": True,
            "response": kickoff,
            "type": "supervised_started",
            "task_id": task.task_id,
            "decision_id": task.decision_id,
            "supervised": task.to_dict(),
            # Signal clients: parent turn is done; worker is separate.
            "parent_chat_available": True,
            "worker_running": True,
            "skip_history_persist": True,
            "canonical_bubble": True,
            "update_existing_bubble": True,
            "show_worker_card": False,
            "coordinator_response_message_id": task.coordinator_response_message_id,
            "parent_user_message_id": task.parent_user_message_id,
        }

    _worker_then_review(
        task_id=task.task_id,
        worker_adapter=worker_adapter,
        coord_adapter=coord_adapter,
        status_queue=status_queue,
        cancel=threading.Event(),
    )
    task = load_task(task.task_id) or task
    return {
        "success": True,
        "response": build_bubble_content(task) if task.final_response else kickoff,
        "type": "supervised_complete",
        "task_id": task.task_id,
        "decision_id": task.decision_id,
        "supervised": task.to_dict(),
        "skip_history_persist": bool(task.coordinator_response_message_id),
        "canonical_bubble": True,
        "update_existing_bubble": True,
        "coordinator_response_message_id": task.coordinator_response_message_id,
        "parent_user_message_id": task.parent_user_message_id,
    }


def _task_cancelled(task: SupervisedTask) -> bool:
    return bool(task.cancel_requested) or task.phase == TaskPhase.CANCELLED.value


def _worker_then_review(
    *,
    task_id: str,
    worker_adapter: CursorWorkerAdapter,
    coord_adapter: CodexCoordinatorAdapter,
    status_queue=None,
    cancel: Optional[threading.Event] = None,
    follow_up_prompt: Optional[str] = None,
) -> None:
    task = load_task(task_id)
    if not task or not task.packet:
        return
    if (cancel and cancel.is_set()) or _task_cancelled(task):
        task.phase = TaskPhase.CANCELLED.value
        task.cancel_requested = True
        task.final_response = task.final_response or "Supervised task cancelled before worker start."
        save_task(task)
        return

    attempt = len(task.runs) + 1
    review_loop = task.followups_used
    run = WorkerRun(
        run_id=new_id("wr_"),
        attempt=attempt,
        review_loop=review_loop,
        phase=TaskPhase.WORKER_RUNNING.value,
        worker_session_id=task.worker_session_id,
        prompt=follow_up_prompt or task.packet.to_worker_prompt(),
        started_at=_now(),
        economic_source=(task.worker or {}).get("harness", {}).get(
            "economic_source", EconomicSource.CURSOR_AUTO_PROMO.value
        ),
    )
    task.runs.append(run)
    task.phase = TaskPhase.WORKER_RUNNING.value
    # Capture dirty-worktree baseline before the worker starts (first run or missing).
    if not task.worktree_baseline and (task.project_path or ""):
        try:
            task.worktree_baseline = snapshot_worktree(task.project_path)
            append_event(
                task,
                "worktree_baseline",
                path_count=int((task.worktree_baseline or {}).get("path_count") or 0),
            )
        except Exception as e:
            slog.supervised_log("baseline_failed", error=str(e)[:200])
    save_task(task)
    update_canonical_response(task)
    append_event(task, "worker_started", run_id=run.run_id, attempt=attempt)
    _emit_parent_progress(task.parent_session_id, "Working…")
    slog.supervised_log(
        "worker_invoke",
        task_id=task.task_id,
        run_id=run.run_id,
        attempt=attempt,
        economic_source=run.economic_source,
    )

    result = worker_adapter.invoke(
        run.prompt,
        session_id=task.worker_session_id,
        project_path=task.project_path or None,
        status_queue=status_queue,
    )

    # Reload — cancel may have won the race while the worker ran.
    task = load_task(task_id) or task
    if (cancel and cancel.is_set()) or _task_cancelled(task):
        task.phase = TaskPhase.CANCELLED.value
        task.cancel_requested = True
        if not task.final_response:
            task.final_response = "Supervised task cancelled while worker was running."
        # Preserve last run metadata without promoting to approved.
        if task.runs:
            task.runs[-1].result = {
                "success": False,
                "cancelled": True,
                "type": result.get("type") if isinstance(result, dict) else None,
            }
            task.runs[-1].finished_at = _now()
        save_task(task)
        append_event(task, "late_worker_discarded", reason="cancelled")
        _deliver_terminal(task)
        return

    raw = str(result.get("response") or result.get("output") or "")
    usage = {}
    if isinstance(result.get("usage"), dict):
        usage = result["usage"]
    report = parse_worker_report(raw, usage=usage)
    try:
        art = persist_raw_worker_output(task.task_id, task.runs[-1].run_id, raw)
        report.raw_artifact_path = str(art.get("path") or "")
        report.raw_output_chars = int(art.get("chars") or report.raw_output_chars or 0)
        report.raw_output_bytes = int(art.get("bytes") or report.raw_output_bytes or 0)
        append_event(
            task,
            "raw_output_persisted",
            run_id=task.runs[-1].run_id,
            chars=art.get("chars"),
            bytes=art.get("bytes"),
            path=art.get("path"),
            truncated=False,
        )
    except Exception as e:
        slog.supervised_log("raw_persist_failed", error=str(e)[:200])
    run = task.runs[-1]
    run.report = report
    run.result = {
        "success": bool(result.get("success", True)),
        "type": result.get("type"),
        "error": result.get("error"),
    }
    run.finished_at = _now()
    run.harness_session_id = str(
        result.get("resume_id") or result.get("cursor_session_id") or ""
    )

    evidence = collect_programmatic_evidence(
        workspace=task.project_path or "",
        worker_report=report,
        worker_result=run.result,
        acceptance_criteria=(task.packet.acceptance_criteria if task.packet else []),
        baseline_snapshot=task.worktree_baseline,
    )
    if not task.verification_mode:
        task.verification_mode = infer_verification_mode(
            user_objective=task.user_objective,
            packet=task.packet,
        )
    evidence = collect_mode_evidence(
        mode=task.verification_mode,
        workspace=task.project_path or "",
        worker_report=report,
        base_bundle=evidence,
    )
    run.evidence = evidence.to_dict()
    task.report_generation = int(task.report_generation or 0) + 1
    task.phase = TaskPhase.REVIEWING.value
    save_task(task)
    update_canonical_response(task)
    append_event(
        task,
        "worker_completed",
        run_id=run.run_id,
        outcome=report.outcome,
        parse_ok=report.parse_ok,
        raw_chars=report.raw_output_chars,
        parse_error=report.parse_error or "",
    )

    # Apply pending user follow-ups at the safe boundary before review.
    if task.pending_followups and task.followups_used < task.budget.max_followups:
        pending = list(task.pending_followups)
        task.pending_followups = []
        instr = "\n".join(
            f"{i + 1}. {p.get('text')}" for i, p in enumerate(pending) if p.get("text")
        )
        for p in pending:
            p["state"] = "delivered"
            p["delivered_at"] = _now()
        append_event(task, "pending_followups_applied", count=len(pending))
        task.followups_used += 1
        task.phase = TaskPhase.FOLLOW_UP.value
        save_task(task)
        update_canonical_response(task)
        follow_prompt = format_follow_up_prompt(
            review_loop=task.followups_used,
            user_objective=task.user_objective,
            instruction=instr,
            acceptance_criteria=task.packet.acceptance_criteria if task.packet else [],
        )
        _worker_then_review(
            task_id=task_id,
            worker_adapter=worker_adapter,
            coord_adapter=coord_adapter,
            status_queue=status_queue,
            cancel=cancel or _CANCEL.get(task_id),
            follow_up_prompt=follow_prompt,
        )
        return

    _run_review(
        task_id,
        coord_adapter=coord_adapter,
        worker_adapter=worker_adapter,
        status_queue=status_queue,
    )


def _run_review(
    task_id: str,
    *,
    coord_adapter: CodexCoordinatorAdapter,
    worker_adapter: Optional[CursorWorkerAdapter] = None,
    status_queue=None,
) -> None:
    task = load_task(task_id)
    if not task or not task.packet or not task.runs:
        return
    if _task_cancelled(task):
        slog.supervised_log("review_skipped_cancelled", task_id=task_id)
        return

    run = task.runs[-1]
    report = run.report
    if not report:
        return

    followups_remaining = max(0, task.budget.max_followups - task.followups_used)
    if task.coordinator_invocations >= task.budget.max_coordinator_invocations:
        task.phase = TaskPhase.BUDGET_EXHAUSTED.value
        task.final_response = format_final_user_message(
            judgment="Coordinator invocation budget exhausted.",
            report=report,
            action="fail",
            task_id=task.task_id,
            evidence=run.evidence,
        )
        save_task(task)
        _deliver_terminal(task)
        return

    review_gen = int(task.report_generation or 0)
    task.review_generation = review_gen
    review_prompt = coordinator_review_prompt(
        user_objective=task.user_objective,
        packet=task.packet,
        report=report,
        followups_remaining=followups_remaining,
        evidence=run.evidence,
        pending_followups=task.pending_followups,
        verification_mode=task.verification_mode or "",
    )
    task.coordinator_invocations += 1
    task.phase = TaskPhase.REVIEWING.value
    save_task(task)
    update_canonical_response(task)
    slog.supervised_log("coordinator_invoke", task_id=task.task_id, kind="review")
    with _coord_lock(task.coordinator_session_id):
        review_result = coord_adapter.invoke(
            review_prompt,
            session_id=task.coordinator_session_id,
            project_path=task.project_path or None,
            status_queue=status_queue,
        )

    task = load_task(task_id) or task
    if _task_cancelled(task):
        slog.supervised_log("review_result_discarded_cancelled", task_id=task_id)
        return

    # Compare-and-set: a follow-up accepted during REVIEWING invalidates this review.
    pending_open = [
        p
        for p in (task.pending_followups or [])
        if isinstance(p, dict) and p.get("state") == "pending"
    ]
    if pending_open or int(task.report_generation or 0) != review_gen:
        append_event(
            task,
            "review_superseded",
            reason="pending_followup_or_new_report",
            review_generation=review_gen,
            report_generation=task.report_generation,
            pending=len(pending_open),
        )
        slog.supervised_log("review_superseded", task_id=task_id)
        if pending_open and task.followups_used < task.budget.max_followups:
            pending = list(pending_open)
            task.pending_followups = [
                p
                for p in (task.pending_followups or [])
                if not (isinstance(p, dict) and p.get("state") == "pending")
            ]
            instr = "\n".join(
                f"{i + 1}. {p.get('text')}" for i, p in enumerate(pending) if p.get("text")
            )
            for p in pending:
                p["state"] = "delivered"
                p["delivered_at"] = _now()
            task.followups_used += 1
            task.phase = TaskPhase.FOLLOW_UP.value
            save_task(task)
            follow_prompt = format_follow_up_prompt(
                review_loop=task.followups_used,
                user_objective=task.user_objective,
                instruction=instr,
                acceptance_criteria=task.packet.acceptance_criteria if task.packet else [],
            )
            w_adapter = worker_adapter or CursorWorkerAdapter(
                profile_objects(get_profile(task.profile_id))[1]
            )
            _worker_then_review(
                task_id=task_id,
                worker_adapter=w_adapter,
                coord_adapter=coord_adapter,
                status_queue=status_queue,
                cancel=_CANCEL.get(task_id),
                follow_up_prompt=follow_prompt,
            )
        return

    # Terminal phases must not be overwritten by a stale review commit.
    if task.phase in _TERMINAL:
        append_event(task, "review_discarded_terminal", phase=task.phase)
        return

    review_text = str(review_result.get("response") or review_result.get("output") or "")
    decision = parse_review_decision(review_text)
    task.reviews.append(decision.to_dict())
    append_event(
        task,
        "review_decision",
        action=decision.action,
        report_generation=review_gen,
    )

    if decision.action == ReviewAction.APPROVE.value:
        task.phase = TaskPhase.APPROVED.value
        task.final_response = format_final_user_message(
            judgment=decision.rationale or review_text[:800],
            report=report,
            action="approve",
            task_id=task.task_id,
            evidence=run.evidence,
        )
        save_task(task)
        _deliver_terminal(task)
        return

    if decision.action == ReviewAction.FOLLOW_UP.value:
        if followups_remaining <= 0:
            task.phase = TaskPhase.BUDGET_EXHAUSTED.value
            task.final_response = format_final_user_message(
                judgment="Follow-up budget exhausted. " + (decision.rationale or ""),
                report=report,
                action="fail",
                task_id=task.task_id,
                evidence=run.evidence,
            )
            save_task(task)
            _deliver_terminal(task)
            return
        task.followups_used += 1
        task.phase = TaskPhase.FOLLOW_UP.value
        save_task(task)
        instr = decision.follow_up_instruction or "Address the review feedback."
        follow_prompt = format_follow_up_prompt(
            review_loop=task.followups_used,
            user_objective=task.user_objective,
            instruction=instr,
            acceptance_criteria=task.packet.acceptance_criteria,
        )
        w_adapter = worker_adapter or CursorWorkerAdapter(
            profile_objects(get_profile(task.profile_id))[1]
        )
        _worker_then_review(
            task_id=task_id,
            worker_adapter=w_adapter,
            coord_adapter=coord_adapter,
            status_queue=status_queue,
            cancel=_CANCEL.get(task_id),
            follow_up_prompt=follow_prompt,
        )
        return

    if decision.action == ReviewAction.ASK_USER.value:
        task.phase = TaskPhase.AWAITING_USER.value
        task.final_response = format_final_user_message(
            judgment=decision.user_question or decision.rationale or review_text[:800],
            report=report,
            action="ask_user",
            task_id=task.task_id,
            evidence=run.evidence,
        )
        save_task(task)
        _deliver_terminal(task)
        return

    if decision.action == ReviewAction.ESCALATE.value:
        task.phase = TaskPhase.ESCALATED.value
        task.final_response = format_final_user_message(
            judgment=decision.rationale or "Coordinator requests escalation.",
            report=report,
            action="escalate",
            task_id=task.task_id,
            evidence=run.evidence,
        )
        save_task(task)
        _deliver_terminal(task)
        return

    if decision.action == ReviewAction.CANCEL.value:
        cancel_task(task.parent_session_id, reason=decision.rationale or "Coordinator cancelled.")
        return

    task.phase = TaskPhase.FAILED.value
    task.final_response = format_final_user_message(
        judgment=decision.rationale or review_text[:800],
        report=report,
        action="fail",
        task_id=task.task_id,
        evidence=run.evidence,
    )
    save_task(task)
    _deliver_terminal(task)


def cancel_task(
    session_id: Any,
    *,
    reason: str = "Cancelled by user.",
    control_request_id: Optional[str] = None,
) -> Dict[str, Any]:
    task = get_active_task(session_id)
    if not task:
        # Idempotent: look for a recently cancelled task on this session.
        from api.agent_router.supervised.store import list_session_tasks

        recent = list_session_tasks(session_id)
        if recent and recent[-1].phase == TaskPhase.CANCELLED.value:
            t = recent[-1]
            return {
                "success": True,
                "response": (
                    f"Task `{t.task_id}` is already cancelled. "
                    f"(idempotent — no further action)"
                ),
                "type": "supervised_cancelled",
                "task_id": t.task_id,
                "worker_state": "already_cancelled",
                "idempotent": True,
            }
        return {
            "success": True,
            "response": "No active supervised task to cancel.",
            "type": "supervised_idle",
            "worker_state": "none",
        }

    if control_request_id:
        prior = find_control_event(task, control_request_id)
        if prior:
            body = control_event_response_body(prior, control_id=control_request_id)
            body.setdefault("type", "supervised_cancelled")
            body.setdefault("task_id", task.task_id)
            return body

    if not session_owns_task(session_id, task):
        return {
            "success": True,
            "response": "❌ Cannot cancel a supervised task owned by another session.",
            "type": "supervised_forbidden",
        }

    if task.phase == TaskPhase.CANCELLED.value or task.cancel_requested:
        return {
            "success": True,
            "response": (
                f"Task `{task.task_id}` is already cancelled. "
                f"(idempotent — no further action)"
            ),
            "type": "supervised_cancelled",
            "task_id": task.task_id,
            "worker_state": "already_cancelled",
            "idempotent": True,
            "skip_history_persist": True,
        }

    was_running = task.phase == TaskPhase.WORKER_RUNNING.value
    worker_live = _worker_process_live(task.worker_session_id)
    already_terminal = task.phase in _TERMINAL

    with _THREADS_LOCK:
        ev = _CANCEL.get(task.task_id)
        if ev:
            ev.set()

    killed = {"cancelled": False, "killed_procs": 0}
    try:
        from api.chat_run_registry import cancel_session_runs

        # Only the owned worker process identity — never Flask/daemon/unrelated.
        killed = cancel_session_runs(task.worker_session_id) or killed
    except Exception:
        pass

    if was_running or task.phase == TaskPhase.REVIEWING.value:
        try:
            from api.chat_run_registry import cancel_session_runs

            cancel_session_runs(task.coordinator_session_id)
        except Exception:
            pass

    prior_phase = task.phase
    task.cancel_requested = True
    task.phase = TaskPhase.CANCELLED.value
    if already_terminal and prior_phase != TaskPhase.CANCELLED.value:
        # Should not happen for active index, but keep durable truth.
        pass
    worker_state = (
        "was_running"
        if (was_running or worker_live)
        else ("already_complete" if prior_phase in _TERMINAL else "idle")
    )
    task.final_response = (
        f"**Supervised task** `{task.task_id}` cancelled. {reason}\n\n"
        f"- Prior phase: `{prior_phase}`\n"
        f"- Worker state: `{worker_state}`\n"
        f"- Processes killed: {int(killed.get('killed_procs') or 0)}"
    )
    save_task(task)
    append_event(
        task,
        "cancelled",
        reason=reason[:200],
        worker_state=worker_state,
        killed_procs=int(killed.get("killed_procs") or 0),
    )
    # Exactly-once terminal delivery — owns the history row (event id stamped).
    _deliver_terminal(task)
    task = load_task(task.task_id) or task
    body = {
        "success": True,
        "response": task.final_response,
        "type": "supervised_cancelled",
        "task_id": task.task_id,
        "worker_state": worker_state,
        "killed_procs": int(killed.get("killed_procs") or 0),
        "idempotent": False,
        "control_request_id": control_request_id or None,
        "delivery_handled": True,
        "skip_history_persist": True,
        "supervised_delivery_event_id": (task.delivery or {}).get("terminal_event_id")
        if isinstance(task.delivery, dict)
        else None,
    }
    if control_request_id:
        record_control_event(
            task,
            control_id=control_request_id,
            kind="cancel",
            result=body,
            assistant_message_id=(task.delivery or {}).get("history_message_id")
            if isinstance(task.delivery, dict)
            else None,
        )
        body["control_request_id"] = control_request_id
    return body


def status_for_session(session_id: Any) -> str:
    """Model-free status from durable state (+ optional live process hint)."""
    task = get_active_task(session_id)
    if not task:
        from api.agent_router.supervised.store import list_session_tasks

        all_t = list_session_tasks(session_id)
        if not all_t:
            return "No supervised tasks for this session."
        task = all_t[-1]
        active = False
    else:
        active = True

    worker_live = _worker_process_live(task.worker_session_id)
    pending = list(task.pending_followups or [])
    pending_open = [p for p in pending if p.get("state") == "pending"]

    activity = {
        TaskPhase.COORDINATING.value: "Coordinator activity (planning)",
        TaskPhase.REVIEWING.value: "Review activity (coordinator)",
        TaskPhase.WORKER_RUNNING.value: "Worker activity",
        TaskPhase.FOLLOW_UP.value: "Follow-up / worker activity",
        TaskPhase.DELEGATED.value: "Delegated — worker starting",
        TaskPhase.AWAITING_USER.value: "Awaiting user",
    }.get(task.phase, "Terminal / idle" if task.phase in _TERMINAL else f"Phase `{task.phase}`")

    lines = [
        f"**Supervised task** `{task.task_id}` ({'active' if active else 'latest'})",
        f"- Phase: `{task.phase}`",
        f"- Activity: {activity}",
        f"- Profile: `{task.profile_id}`",
        f"- Decision id: `{task.decision_id}`",
        f"- Strategy: `{task.router_strategy}`",
        f"- Coordinator session: `{task.coordinator_session_id}`",
        f"- Worker session: `{task.worker_session_id}`",
        f"- Worker process (live): **{'yes' if worker_live else 'no'}** "
        f"({'live process' if worker_live else 'durable state only'})",
        f"- Pending follow-ups: **{len(pending_open)}** (order preserved)",
        f"- Follow-ups used: {task.followups_used}/{task.budget.max_followups}",
        f"- Coordinator invocations: {task.coordinator_invocations}/"
        f"{task.budget.max_coordinator_invocations}",
        f"- Last event / updated: `{task.updated_at or '(unknown)'}`",
        f"- Objective: {task.user_objective[:200]}",
        "",
        "_Status is model-free (durable task JSON"
        + (" + live process registry" if worker_live else "")
        + ")._",
    ]
    if pending_open:
        lines.append("")
        lines.append("**Pending follow-ups**")
        for i, p in enumerate(pending_open[:10], 1):
            lines.append(f"{i}. {str(p.get('text') or '')[:200]}")
    if task.runs:
        last = task.runs[-1]
        lines.append("")
        lines.append(
            f"- Last run: `{last.run_id}` attempt {last.attempt} "
            f"(report parse={'ok' if last.report and last.report.parse_ok else 'no'})"
        )
        if last.evidence:
            lines.append("- Evidence collected: yes (see final judgment when review completes)")
    if task.final_response and task.phase in _TERMINAL:
        lines.append("")
        lines.append(task.final_response[:1500])
    return "\n".join(lines)


def user_followup(
    session_id: Any,
    instruction: str,
    *,
    worker_runner=None,
    control_request_id: Optional[str] = None,
) -> Dict[str, Any]:
    task = get_active_task(session_id)
    if not task:
        from api.agent_router.supervised.store import list_session_tasks

        recent = list_session_tasks(session_id)
        if recent and recent[-1].phase in _TERMINAL:
            t = recent[-1]
            if t.phase == TaskPhase.AWAITING_USER.value:
                task = t
            else:
                return {
                    "success": True,
                    "response": (
                        f"Task `{t.task_id}` is already terminal (`{t.phase}`). "
                        "Late follow-up rejected — start a new `/coordinate` task or retry "
                        "explicitly rather than mutating a finished task."
                    ),
                    "type": "supervised_error",
                    "followup_state": "rejected_terminal",
                    "task_id": t.task_id,
                }
        if not task:
            return {
                "success": True,
                "response": "No active supervised task for follow-up.",
                "type": "supervised_idle",
                "followup_state": "rejected",
            }
    if control_request_id:
        prior = find_control_event(task, control_request_id)
        if prior:
            body = control_event_response_body(prior, control_id=control_request_id)
            body.setdefault("type", "supervised_queued")
            body.setdefault("task_id", task.task_id)
            body.setdefault("followup_state", "idempotent")
            pending_n = len(
                [p for p in (task.pending_followups or []) if p.get("state") == "pending"]
            )
            body.setdefault("pending_count", pending_n)
            return body
    if not session_owns_task(session_id, task):
        return {
            "success": True,
            "response": "❌ Cannot follow up on a task owned by another session.",
            "type": "supervised_forbidden",
            "followup_state": "rejected",
        }
    if task.phase in _TERMINAL and task.phase != TaskPhase.AWAITING_USER.value:
        return {
            "success": True,
            "response": (
                f"Task `{task.task_id}` is already terminal (`{task.phase}`). "
                "Late follow-up rejected — start a new `/coordinate` task or retry "
                "explicitly rather than mutating a finished task."
            ),
            "type": "supervised_error",
            "followup_state": "rejected_terminal",
            "task_id": task.task_id,
        }
    if task.phase not in (
        TaskPhase.AWAITING_USER.value,
        TaskPhase.WORKER_RUNNING.value,
        TaskPhase.FOLLOW_UP.value,
        TaskPhase.REVIEWING.value,
        TaskPhase.DELEGATED.value,
    ):
        return {
            "success": True,
            "response": (
                f"Task `{task.task_id}` is in phase `{task.phase}` and cannot accept follow-up."
            ),
            "type": "supervised_error",
            "followup_state": "rejected",
            "task_id": task.task_id,
        }
    if task.followups_used >= task.budget.max_followups and task.phase != TaskPhase.AWAITING_USER.value:
        return {
            "success": True,
            "response": "Follow-up budget exhausted.",
            "type": "supervised_budget",
            "followup_state": "rejected",
            "task_id": task.task_id,
        }

    text = instruction.strip()
    # Deduplicate identical pending instructions (guards double control-lane apply).
    for p in task.pending_followups or []:
        if (
            isinstance(p, dict)
            and p.get("state") == "pending"
            and str(p.get("text") or "").strip() == text
        ):
            pending_n = len(
                [x for x in task.pending_followups if x.get("state") == "pending"]
            )
            pos = next(
                (
                    i
                    for i, x in enumerate(
                        [x for x in task.pending_followups if x.get("state") == "pending"],
                        1,
                    )
                    if str(x.get("text") or "").strip() == text
                ),
                1,
            )
            body = {
                "success": True,
                "response": (
                    f"**Follow-up already pending** for task `{task.task_id}` "
                    f"(duplicate text ignored).\n\n"
                    f"Pending follow-ups: {pending_n}\n"
                    f"Queue position: {pos}\n"
                    f"Follow-ups used: {task.followups_used}/{task.budget.max_followups}\n"
                    f"Instruction ID: `{p.get('id')}`\n"
                    f"Delivery state: pending"
                ),
                "type": "supervised_queued",
                "followup_state": "pending",
                "task_id": task.task_id,
                "followup_id": p.get("id"),
                "pending_count": pending_n,
                "queue_position": pos,
                "idempotent": True,
            }
            if control_request_id:
                record_control_event(
                    task, control_id=control_request_id, kind="followup", result=body
                )
                body["control_request_id"] = control_request_id
            return body

    entry = {
        "id": new_id("fu_"),
        "text": text,
        "state": "pending",
        "accepted_at": _now(),
        "delivered_at": "",
        "control_request_id": control_request_id or "",
    }

    # Live injection into an active Cursor process is not supported safely.
    # REVIEWING / DELEGATED / WORKER_RUNNING: queue only — never start a parallel worker
    # while a review or worker is in flight.
    if task.phase in (
        TaskPhase.WORKER_RUNNING.value,
        TaskPhase.REVIEWING.value,
        TaskPhase.DELEGATED.value,
    ):
        task.pending_followups.append(entry)
        save_task(task)
        append_event(
            task,
            "user_instruction_queued",
            text=text[:300],
            id=entry["id"],
            phase=task.phase,
        )
        pending_list = [p for p in task.pending_followups if p.get("state") == "pending"]
        n = len(pending_list)
        pos = next(
            (i for i, p in enumerate(pending_list, 1) if p.get("id") == entry["id"]),
            n,
        )
        boundary = (
            "after the in-flight coordinator review finishes (or is superseded)"
            if task.phase == TaskPhase.REVIEWING.value
            else "after the current worker run finishes, before coordinator approval"
        )
        body = {
            "success": True,
            "response": (
                f"**Follow-up accepted** (`pending`) for task `{task.task_id}`.\n\n"
                f"Live injection into the running Cursor process is **not** supported. "
                f"This instruction will be applied at the next safe boundary — {boundary} "
                f"(respecting the follow-up budget).\n\n"
                f"Pending follow-ups: {n}\n"
                f"Queue position: {pos}\n"
                f"Follow-ups used: {task.followups_used}/{task.budget.max_followups}\n"
                f"Instruction ID: `{entry['id']}`\n"
                f"Delivery state: pending\n"
                f"Current phase: `{task.phase}`\n\n"
                f"> {text[:400]}"
            ),
            "type": "supervised_queued",
            "followup_state": "pending",
            "task_id": task.task_id,
            "followup_id": entry["id"],
            "pending_count": n,
            "queue_position": pos,
        }
        if control_request_id:
            record_control_event(
                task, control_id=control_request_id, kind="followup", result=body
            )
            body["control_request_id"] = control_request_id
        return body

    # Worker not mid-run — deliver as a new follow-up turn.
    task.pending_followups.append(entry)
    entry["state"] = "delivered"
    entry["delivered_at"] = _now()
    task.followups_used += 1
    task.phase = TaskPhase.FOLLOW_UP.value
    save_task(task)
    coord, worker, _ = profile_objects(get_profile(task.profile_id))
    cancel = _CANCEL.setdefault(task.task_id, threading.Event())
    th = threading.Thread(
        target=_worker_then_review,
        kwargs={
            "task_id": task.task_id,
            "worker_adapter": CursorWorkerAdapter(worker, runner=worker_runner),
            "coord_adapter": CodexCoordinatorAdapter(coord),
            "cancel": cancel,
            "follow_up_prompt": (
                f"# User follow-up\n\n{instruction}\n\n"
                f"Original objective: {task.user_objective}"
            ),
        },
        daemon=True,
    )
    th.start()
    body = {
        "success": True,
        "response": (
            f"**Follow-up accepted** (`delivered`) for task `{task.task_id}`.\n\n"
            f"A new worker turn was started with your instruction.\n\n"
            f"Pending follow-ups: 0\n"
            f"Follow-ups used: {task.followups_used}/{task.budget.max_followups}\n"
            f"Instruction ID: `{entry['id']}`\n"
            f"Delivery state: delivered"
        ),
        "type": "supervised_followup",
        "followup_state": "delivered",
        "task_id": task.task_id,
        "followup_id": entry["id"],
        "pending_count": 0,
    }
    if control_request_id:
        record_control_event(
            task, control_id=control_request_id, kind="followup", result=body
        )
        body["control_request_id"] = control_request_id
    return body


def handle_coordinator_conversation(
    message: str,
    *,
    session_id: Any,
    project_path: Optional[str] = None,
    status_queue=None,
    coordinator_runner=None,
) -> Dict[str, Any]:
    """
    Free-form conversation with the coordinator while a supervised worker may run.

    Does not forward the message to the worker unless the coordinator explicitly
    chooses to queue a follow-up instruction (left to the model response JSON).
    """
    task = get_active_task(session_id)
    if not task:
        return {
            "success": True,
            "response": (
                "No active supervised task. Use `/coordinate <prompt>` to start one, "
                "or `/coordinator mode supervised`."
            ),
            "type": "supervised_idle",
        }
    if not session_owns_task(session_id, task):
        return {
            "success": True,
            "response": "❌ Session does not own the active supervised task.",
            "type": "supervised_forbidden",
        }

    coord, _worker, _ = profile_objects(get_profile(task.profile_id))
    adapter = CodexCoordinatorAdapter(coord, runner=coordinator_runner)
    pending = [p for p in (task.pending_followups or []) if p.get("state") == "pending"]
    prompt = "\n".join(
        [
            "You are the Cuttle supervised-task coordinator in a side conversation.",
            "A worker may be running in parallel. Do NOT pretend you are the worker.",
            "Answer the user's conversational question about the task, plan, or criteria.",
            "If they want the worker to change course, say so clearly and include JSON:",
            '```json\n{"queue_worker_instruction": "..."}\n```',
            "Otherwise omit that key. Never claim you verified worker results without evidence.",
            "",
            f"Task id: {task.task_id}",
            f"Phase: {task.phase}",
            f"Objective: {task.user_objective}",
            f"Pending worker instructions: {len(pending)}",
            f"Economic source for this turn: {coord.harness.economic_source}",
            "",
            f"User message: {message}",
        ]
    )
    task.coordinator_invocations += 1
    save_task(task)
    slog.supervised_log(
        "coordinator_conversation",
        task_id=task.task_id,
        economic_source=coord.harness.economic_source,
    )
    with _coord_lock(task.coordinator_session_id):
        result = adapter.invoke(
            prompt,
            session_id=task.coordinator_session_id,
            project_path=project_path or task.project_path or None,
            status_queue=status_queue,
        )
    text = str(result.get("response") or result.get("output") or "")

    # Optional explicit queue request from coordinator
    queued_note = ""
    try:
        from api.agent_router.supervised.packet import extract_json_object

        obj = extract_json_object(text) or {}
        instr = str(obj.get("queue_worker_instruction") or "").strip()
        if instr:
            fu = user_followup(session_id, instr, worker_runner=None)
            queued_note = (
                f"\n\n---\n_Coordinator queued a worker instruction "
                f"(`{fu.get('followup_state')}`)._"
            )
    except Exception:
        pass

    usage_note = (
        f"\n\n_Coordinator conversation · `{coord.harness.economic_source}` · "
        f"task `{task.task_id}` (worker not auto-forwarded)._"
    )
    return {
        "success": True,
        "response": (text or "(empty coordinator reply)") + queued_note + usage_note,
        "type": "supervised_conversation",
        "task_id": task.task_id,
        "session_id": session_id,
        "usage": result.get("usage"),
        "economic_source": coord.harness.economic_source,
    }


def parent_blocked_by_worker(session_id: Any) -> bool:
    """True only if the parent chat's own busy lock is held — never inferred from worker."""
    try:
        from api import chat_delivery

        return bool(chat_delivery.is_busy(session_id))
    except Exception:
        return False


def supervised_active_work_entries() -> List[Dict[str, Any]]:
    """Entries for restart safety: one task per active supervised worker."""
    from api.agent_router.supervised.store import _load_index, load_task

    out: List[Dict[str, Any]] = []
    try:
        idx = _load_index()
        active = dict(idx.get("active") or {})
    except Exception:
        return out
    seen = set()
    for _sid, tid in active.items():
        if not tid or tid in seen:
            continue
        task = load_task(str(tid))
        if not task or task.phase in _TERMINAL:
            continue
        seen.add(tid)
        out.append(
            {
                "kind": "supervised_worker",
                "session_id": task.worker_session_id,
                "parent_session_id": task.parent_session_id,
                "task_id": task.task_id,
                "phase": task.phase,
                "query_id": None,
                "pipeline_name": f"supervised:{task.task_id}",
                "has_live_process": _worker_process_live(task.worker_session_id),
            }
        )
    return out
