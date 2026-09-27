"""Durable delivery tracking for supervised-task terminal events.

Labels states accurately. Does not invent client acknowledgements when none exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from api.agent_router.supervised.store import append_event, load_task, save_task
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

# Delivery states we actually track (no fake client_ack).
DELIVERY_PERSISTED = "event_persisted"
DELIVERY_QUEUED = "event_queued"
DELIVERY_SENT = "event_sent"
DELIVERY_RECONCILED = "reconciled_after_reconnect"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delivery_record(task: Any) -> Dict[str, Any]:
    d = getattr(task, "delivery", None)
    if isinstance(d, dict):
        return dict(d)
    return {
        "terminal_event_id": "",
        "state": "",
        "persisted_at": "",
        "queued_at": "",
        "sent_at": "",
        "reconciled_at": "",
        "history_message_id": None,
        "client_ack": False,  # reserved; only set if a real ack exists
        "notes": "",
    }


def mark_terminal_persisted(task: Any, *, event_id: str = "") -> Any:
    """Call after final_response is written to durable task JSON."""
    rec = _delivery_record(task)
    if not event_id:
        from api.agent_router.supervised.types import new_id

        event_id = new_id("de_")
    rec["terminal_event_id"] = event_id
    rec["state"] = DELIVERY_PERSISTED
    rec["persisted_at"] = _now()
    task.delivery = rec
    save_task(task)
    append_event(
        task,
        "delivery_state",
        state=DELIVERY_PERSISTED,
        event_id=event_id,
    )
    return task


def deliver_terminal_to_parent(task: Any) -> Dict[str, Any]:
    """
    Persist to chat history + park in chat_delivery for reconnect.

    Does not claim the client observed the message.
    """
    if not task or not getattr(task, "final_response", None):
        return {"ok": False, "reason": "no_final_response"}

    task = load_task(task.task_id) or task
    rec = _delivery_record(task)
    if not rec.get("terminal_event_id"):
        task = mark_terminal_persisted(task)
        rec = _delivery_record(task)

    # Idempotent: already sent and still marked — skip duplicate history insert.
    if rec.get("state") in (DELIVERY_SENT, DELIVERY_RECONCILED) and rec.get(
        "history_message_id"
    ):
        return {
            "ok": True,
            "idempotent": True,
            "state": rec["state"],
            "event_id": rec.get("terminal_event_id"),
        }

    from api.agent_router.supervised.bubble import (
        bubble_metadata,
        build_bubble_content,
        jump_notification_label,
    )

    restart_required = None
    if isinstance(getattr(task, "delivery", None), dict):
        restart_required = (task.delivery or {}).get("restart_required")
    text = build_bubble_content(task, restart_required=restart_required)
    metadata = bubble_metadata(
        task,
        extra={
            "supervised_delivery_event_id": rec.get("terminal_event_id"),
            "supervised_terminal": True,
            "jump_label": jump_notification_label(task.phase),
        },
    )
    if restart_required:
        metadata["restart_required"] = restart_required
    history_id = None
    try:
        from api.auth_db import get_auth_db

        sid = task.parent_session_id
        if isinstance(sid, str) and sid.startswith("db_session_"):
            try:
                sid = int(sid[len("db_session_") :])
            except ValueError:
                pass
        canonical_id = getattr(task, "coordinator_response_message_id", None)
        if canonical_id and hasattr(get_auth_db(), "update_message_content"):
            metadata["parent_user_message_id"] = getattr(task, "parent_user_message_id", None)
            metadata["coordinator_response_message_id"] = canonical_id
            if get_auth_db().update_message_content(int(canonical_id), text, metadata):
                history_id = canonical_id
        elif canonical_id:
            # Minimal/fake DB implementations may not expose updates. Preserve
            # exactly-one identity instead of appending a second row.
            history_id = canonical_id
        if history_id is None:
            # Legacy tasks had no canonical id: safely reuse a delivery row or
            # append one terminal response once.
            existing = _find_history_by_delivery_event(sid, rec.get("terminal_event_id") or "")
            if existing is not None:
                history_id = existing
                try:
                    get_auth_db().update_message_content(int(existing), text, metadata)
                except Exception:
                    pass
            else:
                history_id = get_auth_db().add_message(sid, "assistant", text, metadata=metadata)
                # Bind for future updates when possible.
                try:
                    task.coordinator_response_message_id = history_id
                    save_task(task)
                except Exception:
                    pass
    except Exception as e:
        append_event(task, "delivery_persist_failed", error=str(e)[:200])

    rec["state"] = DELIVERY_QUEUED
    rec["queued_at"] = _now()
    if history_id is not None:
        rec["history_message_id"] = history_id
    task.delivery = rec
    save_task(task)
    append_event(
        task,
        "delivery_state",
        state=DELIVERY_QUEUED,
        event_id=rec.get("terminal_event_id"),
        history_message_id=history_id,
    )

    # Park for clients that dropped SSE (same mechanism as agent turns).
    try:
        from api import chat_delivery

        payload = {
            "success": True,
            "response": text,
            "type": "supervised_complete",
            "session_id": task.parent_session_id,
            "task_id": task.task_id,
            "phase": task.phase,
            "supervised_delivery_event_id": rec.get("terminal_event_id"),
            "delivery_state": DELIVERY_QUEUED,
            # Honest label: we queued for delivery; no client ack channel yet.
            "client_observed": False,
            "coordinator_response_message_id": getattr(
                task, "coordinator_response_message_id", None
            ),
            "parent_user_message_id": getattr(task, "parent_user_message_id", None),
            "update_existing_bubble": True,
            "jump_label": jump_notification_label(task.phase),
            "supervised_terminal": True,
        }
        if restart_required:
            payload["restart_required"] = restart_required
        chat_delivery.store_result(task.parent_session_id, payload)
        rec["state"] = DELIVERY_SENT
        rec["sent_at"] = _now()
        task.delivery = rec
        save_task(task)
        append_event(
            task,
            "delivery_state",
            state=DELIVERY_SENT,
            event_id=rec.get("terminal_event_id"),
        )
    except Exception as e:
        append_event(task, "delivery_queue_failed", error=str(e)[:200])

    return {
        "ok": True,
        "state": rec.get("state"),
        "event_id": rec.get("terminal_event_id"),
        "history_message_id": history_id,
        "client_observed": False,
    }


def _find_history_by_delivery_event(session_id: Any, event_id: str) -> Optional[Any]:
    if not event_id or session_id is None:
        return None
    try:
        from api.auth_db import get_auth_db

        db = get_auth_db()
        messages = db.get_messages(session_id, limit=80) or []
        for m in reversed(messages):
            meta = m.get("metadata") if isinstance(m, dict) else None
            if isinstance(meta, str):
                import json

                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if isinstance(meta, dict) and meta.get("supervised_delivery_event_id") == event_id:
                return m.get("id")
    except Exception:
        return None
    return None


def reconcile_session_delivery(session_id: Any) -> Dict[str, Any]:
    """
    On reconnect/refresh: ensure terminal supervised results are available.

    Compares durable task delivery state with chat history / pending park.
    Does not invent client acknowledgements.
    """
    from api.agent_router.supervised.store import get_active_task, list_session_tasks

    tasks = list_session_tasks(session_id)
    active = get_active_task(session_id)
    reconciled: List[Dict[str, Any]] = []
    for task in tasks:
        if task.phase not in _TERMINAL and not (
            task.phase == TaskPhase.AWAITING_USER.value and task.final_response
        ):
            continue
        if not task.final_response:
            continue
        rec = _delivery_record(task)
        if rec.get("state") in (DELIVERY_SENT, DELIVERY_RECONCILED) and rec.get(
            "history_message_id"
        ):
            # Re-park pending so refresh can collect without duplicating history.
            try:
                from api import chat_delivery

                if not chat_delivery.peek_result(session_id):
                    chat_delivery.store_result(
                        session_id,
                        {
                            "success": True,
                            "response": task.final_response,
                            "type": "supervised_complete",
                            "session_id": session_id,
                            "task_id": task.task_id,
                            "phase": task.phase,
                            "supervised_delivery_event_id": rec.get("terminal_event_id"),
                            "delivery_state": DELIVERY_RECONCILED,
                            "client_observed": False,
                            "reconciled": True,
                        },
                    )
            except Exception:
                pass
            rec["state"] = DELIVERY_RECONCILED
            rec["reconciled_at"] = _now()
            task.delivery = rec
            save_task(task)
            reconciled.append(
                {
                    "task_id": task.task_id,
                    "state": DELIVERY_RECONCILED,
                    "event_id": rec.get("terminal_event_id"),
                    "duplicate_history": False,
                }
            )
            continue
        result = deliver_terminal_to_parent(task)
        result["task_id"] = task.task_id
        reconciled.append(result)

    return {
        "session_id": str(session_id) if session_id is not None else None,
        "active_task_id": active.task_id if active else None,
        "reconciled": reconciled,
    }


def public_indicator(task: Any) -> Optional[Dict[str, Any]]:
    """Session-scoped background indicator payload (not the generating flag)."""
    if task is None:
        return None
    from api.agent_router.supervised.bubble import (
        build_bubble_content,
        jump_notification_label,
        visible_phase_label,
    )

    pending = [
        p
        for p in (task.pending_followups or [])
        if isinstance(p, dict) and p.get("state") == "pending"
    ]
    terminal = task.phase in _TERMINAL
    last_run = task.runs[-1] if task.runs else None
    run_id = getattr(last_run, "run_id", None) if last_run else None
    raw_path = ""
    if last_run and getattr(last_run, "report", None):
        raw_path = getattr(last_run.report, "raw_artifact_path", "") or ""
    objective = (task.user_objective or "")[:160]
    if task.packet and getattr(task.packet, "objective", None):
        objective = str(task.packet.objective)[:160]
    started = task.created_at or ""
    coord = task.coordinator if isinstance(task.coordinator, dict) else {}
    worker = task.worker if isinstance(task.worker, dict) else {}
    return {
        "task_id": task.task_id,
        "phase": task.phase,
        "phase_label": visible_phase_label(task.phase),
        "jump_label": jump_notification_label(task.phase) if terminal else "",
        "updated_at": task.updated_at,
        "created_at": started,
        "worker": worker.get("id"),
        "worker_label": worker.get("label") or worker.get("id") or "Worker",
        "coordinator_label": coord.get("label") or coord.get("id") or "Coordinator",
        "pending_followup_count": len(pending),
        "followups_used": task.followups_used,
        "max_followups": task.budget.max_followups if task.budget else 0,
        "decision_id": task.decision_id,
        "parent_session_id": task.parent_session_id,
        "terminal": terminal,
        "delivery": _delivery_record(task),
        "objective_preview": objective,
        "run_id": run_id,
        "raw_report_url": (
            f"/api/supervised/tasks/{task.task_id}/runs/{run_id}/raw" if run_id else ""
        ),
        "raw_artifact_path": raw_path,
        "verification_mode": getattr(task, "verification_mode", "") or "",
        "report_generation": int(getattr(task, "report_generation", 0) or 0),
        "parent_user_message_id": getattr(task, "parent_user_message_id", None),
        "coordinator_response_message_id": getattr(
            task, "coordinator_response_message_id", None
        ),
        "coordinator": coord or None,
        "router_strategy": getattr(task, "router_strategy", "supervised"),
        "events": list(task.events or [])[-30:],
        # Quiet bubble body for in-place UI refresh (not a second history row).
        "bubble_content": build_bubble_content(task) if not terminal else None,
        "show_worker_card": False,
        "canonical_bubble": True,
    }
