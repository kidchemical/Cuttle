"""Canonical coordinator response bubble — presentation + persistence helpers.

One supervised task owns one durable assistant message. Internal worker
activity updates that bubble in place; it must not become a second chat
participant.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from api.agent_router.supervised.types import TaskPhase

_TERMINAL = frozenset(
    {
        TaskPhase.APPROVED.value,
        TaskPhase.ESCALATED.value,
        TaskPhase.FAILED.value,
        TaskPhase.CANCELLED.value,
        TaskPhase.BUDGET_EXHAUSTED.value,
        TaskPhase.AWAITING_USER.value,
    }
)

# Quiet labels for the collapsed bubble (normal Cuttle working language).
VISIBLE_PHASE_LABELS = {
    TaskPhase.CREATED.value: "Thinking…",
    TaskPhase.COORDINATING.value: "Planning task…",
    TaskPhase.DELEGATED.value: "Working…",
    TaskPhase.WORKER_RUNNING.value: "Working…",
    TaskPhase.FOLLOW_UP.value: "Working…",
    TaskPhase.REVIEWING.value: "Reviewing results…",
    TaskPhase.AWAITING_USER.value: "Awaiting your input…",
    TaskPhase.APPROVED.value: "Done",
    TaskPhase.ESCALATED.value: "Escalated",
    TaskPhase.FAILED.value: "Failed",
    TaskPhase.CANCELLED.value: "Cancelled",
    TaskPhase.BUDGET_EXHAUSTED.value: "Budget exhausted",
}

WORKER_PHASES = frozenset(
    {
        TaskPhase.DELEGATED.value,
        TaskPhase.WORKER_RUNNING.value,
        TaskPhase.FOLLOW_UP.value,
    }
)

JUMP_WORDING = {
    TaskPhase.APPROVED.value: "Background task completed",
    TaskPhase.ESCALATED.value: "Background task escalated",
    TaskPhase.FAILED.value: "Background task failed",
    TaskPhase.CANCELLED.value: "Background task cancelled",
    TaskPhase.BUDGET_EXHAUSTED.value: "Background task exhausted budget",
    TaskPhase.AWAITING_USER.value: "Background task needs your input",
}


def visible_phase_label(phase: Optional[str]) -> str:
    p = str(phase or "").strip()
    return VISIBLE_PHASE_LABELS.get(p) or "Working…"


def activity_status_text(phase: Optional[str]) -> str:
    """Status line for the live activity indicator (not body text)."""
    p = str(phase or "").strip()
    if p in WORKER_PHASES:
        return "Working with a subagent…"
    return visible_phase_label(p)


def jump_notification_label(phase: Optional[str]) -> str:
    p = str(phase or "").strip()
    return JUMP_WORDING.get(p) or "Background task updated"


def is_terminal_phase(phase: Optional[str]) -> bool:
    return str(phase or "") in _TERMINAL


def _activity_details_for_task(task: Any, last_run: Any = None) -> Dict[str, Any]:
    """Rich Activity payload: worker/evidence ledger kept out of the default bubble."""
    from api.agent_router.supervised.packet import format_activity_details

    run = last_run
    report = getattr(run, "report", None) if run else None
    evidence = getattr(run, "evidence", None) if run else None
    if isinstance(evidence, dict):
        evidence_dict = evidence
    else:
        evidence_dict = evidence.to_dict() if hasattr(evidence, "to_dict") else None

    action = ""
    judgment = ""
    reviews = list(getattr(task, "reviews", None) or [])
    if reviews:
        last = reviews[-1]
        if isinstance(last, dict):
            action = str(last.get("action") or "")
            judgment = str(
                last.get("rationale")
                or last.get("user_question")
                or ""
            )
    if not action:
        for ev in reversed(list(getattr(task, "events", None) or [])):
            if isinstance(ev, dict) and (
                ev.get("type") == "review_decision" or ev.get("event") == "review_decision"
            ):
                action = str(ev.get("action") or "")
                break
    if not judgment:
        # Fall back to concise final text stripped of decision prefix noise.
        judgment = str(getattr(task, "final_response", "") or "")

    details_md = format_activity_details(
        judgment=judgment,
        report=report,
        action=action or str(getattr(task, "phase", "") or ""),
        task_id=getattr(task, "task_id", "") or "",
        evidence=evidence_dict,
    )
    evidence_items = []
    if isinstance(evidence_dict, dict) and isinstance(evidence_dict.get("items"), list):
        for item in evidence_dict["items"][:24]:
            if isinstance(item, dict):
                evidence_items.append(
                    {
                        "key": item.get("key"),
                        "source": item.get("source"),
                        "ok": item.get("ok"),
                        "detail": str(item.get("detail") or "")[:240],
                    }
                )
    findings = []
    if report is not None:
        for f in list(getattr(report, "findings", None) or [])[:12]:
            if isinstance(f, dict):
                findings.append(
                    {
                        "id": f.get("id"),
                        "file": f.get("file") or f.get("path"),
                        "symbol": f.get("symbol"),
                        "impact": str(f.get("impact") or f.get("evidence") or "")[:160],
                    }
                )
    return {
        "markdown": details_md,
        "worker_summary": getattr(report, "summary", "") if report else "",
        "worker_outcome": getattr(report, "outcome", "") if report else "",
        "findings": findings,
        "evidence_items": evidence_items,
        "files_changed": list(getattr(report, "files_changed", None) or [])[:20]
        if report
        else [],
    }


def _activity_payload(task: Any) -> Dict[str, Any]:
    pending = [
        p
        for p in (getattr(task, "pending_followups", None) or [])
        if isinstance(p, dict) and p.get("state") == "pending"
    ]
    last_run = task.runs[-1] if getattr(task, "runs", None) else None
    run_id = getattr(last_run, "run_id", None) if last_run else None
    coord = task.coordinator if isinstance(getattr(task, "coordinator", None), dict) else {}
    worker = task.worker if isinstance(getattr(task, "worker", None), dict) else {}
    coord_h = coord.get("harness") if isinstance(coord.get("harness"), dict) else {}
    worker_h = worker.get("harness") if isinstance(worker.get("harness"), dict) else {}
    timeline = []
    for ev in list(getattr(task, "events", None) or [])[-12:]:
        if not isinstance(ev, dict):
            continue
        timeline.append(
            {
                "type": str(ev.get("type") or ev.get("event") or ""),
                "at": str(ev.get("at") or ev.get("ts") or ""),
            }
        )
    terminal = is_terminal_phase(task.phase)
    details = _activity_details_for_task(task, last_run) if terminal else {}
    return {
        "task_id": task.task_id,
        "phase": task.phase,
        "phase_label": visible_phase_label(task.phase),
        "status_text": activity_status_text(task.phase),
        "terminal": terminal,
        "coordinator_label": coord.get("label")
        or coord_h.get("model")
        or coord.get("id")
        or "Coordinator",
        "worker_label": worker.get("label")
        or worker_h.get("model")
        or worker.get("id")
        or "Worker",
        "router_strategy": getattr(task, "router_strategy", "") or "",
        "decision_id": getattr(task, "decision_id", "") or "",
        "followups_used": int(getattr(task, "followups_used", 0) or 0),
        "max_followups": int(getattr(task.budget, "max_followups", 0) or 0)
        if getattr(task, "budget", None)
        else 0,
        "pending_followup_count": len(pending),
        "verification_mode": getattr(task, "verification_mode", "") or "",
        "run_id": run_id or "",
        "raw_report_url": (
            f"/api/supervised/tasks/{task.task_id}/runs/{run_id}/raw" if run_id else ""
        ),
        "timeline": timeline,
        "details": details,
        "details_markdown": details.get("markdown") or "",
        "parent_user_message_id": getattr(task, "parent_user_message_id", None),
        "coordinator_response_message_id": getattr(
            task, "coordinator_response_message_id", None
        ),
    }


def build_activity_tag(task: Any) -> str:
    """Embeddable Activity disclosure for formatMessage / refresh."""
    payload = _activity_payload(task)
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"<cuttle_supervised_activity>\n{body}\n</cuttle_supervised_activity>"


def build_restart_form_tag() -> str:
    """Programmatic restart choices — native control, not agent prompts."""
    try:
        from api.flask_restart import shared_restart_form_id

        group = shared_restart_form_id()
    except Exception:
        group = ""
    spec = {
        "mode": "choice",
        "title": "Flask restart required",
        "description": "Choose how to restart Flask. These call the native restart controller.",
        "lock": "form",
        "silent": True,
        "options": [
            {
                "id": "graceful",
                "label": "Restart gracefully",
                "action": "__native_restart__",
                "params": {"mode": "graceful"},
            },
            {
                "id": "when_idle",
                "label": "Restart when idle",
                "action": "__native_restart__",
                "params": {"mode": "when-idle"},
            },
            {
                "id": "status",
                "label": "Show active work",
                "action": "__native_restart__",
                "params": {"mode": "status"},
            },
            {
                "id": "not_now",
                "label": "Not now",
                "action": "__dismiss__",
                "params": {},
            },
        ],
    }
    if group:
        spec["id"] = group
        spec["restartFormGroup"] = group
    return (
        "<cuttle_action_form>\n"
        + json.dumps(spec, ensure_ascii=False)
        + "\n</cuttle_action_form>"
    )


def build_bubble_content(
    task: Any,
    *,
    restart_required: Optional[str] = None,
) -> str:
    """User-visible bubble body for the current phase."""
    phase = str(getattr(task, "phase", "") or "")
    terminal = is_terminal_phase(phase)
    parts = []

    if terminal:
        parts.append(
            str(getattr(task, "final_response", "") or "").strip()
            or visible_phase_label(phase)
        )
    # In-flight phases render as the chat's standard activity indicator, driven
    # by status_text in the Activity payload — never as plain body text.

    parts.append(build_activity_tag(task))

    if restart_required == "flask" or (
        isinstance(restart_required, str) and restart_required.lower() == "flask"
    ):
        parts.append(build_restart_form_tag())

    return "\n\n".join(parts).strip() + "\n"


def bubble_metadata(task: Any, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "origin": "supervised_coordinator",
        "supervised_task_id": task.task_id,
        "supervised_phase": task.phase,
        "supervised_canonical_bubble": True,
        "supervised_terminal": is_terminal_phase(task.phase),
    }
    delivery = getattr(task, "delivery", None)
    if isinstance(delivery, dict) and delivery.get("terminal_event_id"):
        meta["supervised_delivery_event_id"] = delivery.get("terminal_event_id")
    if extra:
        meta.update(extra)
    return meta


def bind_canonical_bubble(
    task: Any,
    *,
    parent_user_message_id: Any = None,
    coordinator_response_message_id: Any = None,
) -> Any:
    """Stamp durable message ids onto the task (idempotent)."""
    from api.agent_router.supervised.store import save_task

    changed = False
    if parent_user_message_id is not None and not getattr(
        task, "parent_user_message_id", None
    ):
        task.parent_user_message_id = parent_user_message_id
        changed = True
    if coordinator_response_message_id is not None and not getattr(
        task, "coordinator_response_message_id", None
    ):
        task.coordinator_response_message_id = coordinator_response_message_id
        changed = True
    if changed:
        save_task(task)
    return task


def update_canonical_bubble(
    task: Any,
    *,
    content: Optional[str] = None,
    restart_required: Optional[str] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """Update the durable assistant row in place. Returns message id or None."""
    mid = getattr(task, "coordinator_response_message_id", None)
    if mid is None:
        return None
    text = content if content is not None else build_bubble_content(
        task, restart_required=restart_required
    )
    meta = bubble_metadata(task, extra=extra_metadata)
    if restart_required:
        meta["restart_required"] = restart_required
    try:
        from api.auth_db import get_auth_db

        ok = get_auth_db().update_message_content(int(mid), text, metadata=meta)
        return mid if ok else None
    except Exception:
        return None


def ensure_canonical_bubble(
    task: Any,
    *,
    session_id: Any,
    user_text: str = "",
    restart_required: Optional[str] = None,
) -> Any:
    """Create the coordinator assistant row if missing; otherwise refresh it."""
    from api.agent_router.supervised.store import save_task

    sid = session_id
    if isinstance(sid, str) and sid.startswith("db_session_"):
        try:
            sid = int(sid[len("db_session_") :])
        except ValueError:
            pass

    text = build_bubble_content(task, restart_required=restart_required)
    meta = bubble_metadata(task)
    if restart_required:
        meta["restart_required"] = restart_required

    mid = getattr(task, "coordinator_response_message_id", None)
    if mid is not None:
        update_canonical_bubble(task, content=text, restart_required=restart_required)
        return task

    try:
        from api.auth_db import get_auth_db

        db = get_auth_db()
        if user_text and not getattr(task, "parent_user_message_id", None):
            uid = db.add_message(
                sid,
                "user",
                user_text,
                metadata={
                    "origin": "supervised_control",
                    "supervised_task_id": task.task_id,
                    "control_lane": True,
                },
            )
            task.parent_user_message_id = uid
        aid = db.add_message(sid, "assistant", text, metadata=meta)
        task.coordinator_response_message_id = aid
        save_task(task)
    except Exception:
        pass
    return task
