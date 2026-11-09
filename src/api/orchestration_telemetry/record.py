"""Recorders the owners call where things happen. Every entry point swallows
its own errors: telemetry must never break a spawn, a check or a send."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from api.orchestration_telemetry import store

log = logging.getLogger(__name__)

ERROR_SNIPPET = 400


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — observers never raise into owners
            log.debug("telemetry %s failed", fn.__name__, exc_info=True)
            return None
    wrapper.__name__ = fn.__name__
    return wrapper


def _sqlite_epoch(value: Any) -> Optional[float]:
    """``CURRENT_TIMESTAMP`` text (UTC) → epoch seconds."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def resolve_tier(user_id: int, parent_session_id: int, *, profile_id: str = "", agent: str = "",
                 model: str = "", effort: str = "") -> Dict[str, str]:
    """Playbook + tier of a child spawned from ``parent_session_id`` right now."""
    from api.orchestrator import model as orch_model, store as orch_store
    from api.orchestrator.service import OrchestratorError, get_playbook

    binding = orch_store.binding(int(parent_session_id))
    if not binding:
        return {}
    out = {"playbook_id": binding["playbook_id"]}
    try:
        doc = get_playbook(int(user_id), binding["playbook_id"])
    except OrchestratorError:
        return out
    hit = orch_model.tier_of(doc, profile_id=profile_id, agent=agent, model=model, effort=effort)
    if hit:
        out.update(tier=hit["tier"], role=hit["role"])
    return out


def _escalation_source(parent_session_id: int) -> Dict[str, Any]:
    """The worker an escalation most likely took over: the latest worker-tier
    round in the same orchestrator chat that failed, else its latest worker spawn."""
    failed = store.latest("child_round_end", parent_session_id=parent_session_id, role="worker")
    if failed and failed.get("status") == "failed":
        return {"from_child": failed["child_id"], "from_agent": failed["agent"], "from_model": failed["model"],
                "link": "failed_worker"}
    last = store.latest("child_spawn", parent_session_id=parent_session_id, role="worker")
    if last:
        return {"from_child": last["child_id"], "from_agent": last["agent"], "from_model": last["model"],
                "link": "last_worker"}
    return {"link": "none"}


@_safe
def child_spawned(db, child, *, at: Optional[float] = None, backfilled: bool = False) -> None:
    from api.subagents import store as sub_store

    batch = sub_store.get_batch(db, child.batch_id)
    if not batch:
        return
    parent = db.get_chat_session_by_id(int(batch.parent_session_id)) or {}
    tier = resolve_tier(batch.user_id, batch.parent_session_id, profile_id=child.profile_id or "",
                        agent=child.agent or "", model=child.model or "", effort=child.effort or "")
    detail: Dict[str, Any] = {"title": (child.label or "")[:200]}
    if tier.get("role") == "escalation":
        detail.update(_escalation_source(int(batch.parent_session_id)))
    if backfilled:
        detail["backfilled"] = True
    store.append("child_spawn", at=at, user_id=batch.user_id, session_id=child.session_id,
                 parent_session_id=batch.parent_session_id, project_path=parent.get("project_path") or "",
                 profile_id=child.profile_id, agent=child.agent, model=child.model, effort=child.effort,
                 child_id=child.id, batch_id=batch.id, turn_id=batch.turn_id, detail=detail, **tier)


@_safe
def child_round(db, child_id: str, *, started: bool = False, finished: bool = False) -> None:
    """A child's round began or ended (``subagents.store.update_child``)."""
    from api.subagents import store as sub_store

    child = sub_store.get_child(db, child_id)
    if not child:
        return
    spawn = store.latest("child_spawn", child_id=child_id) or {}
    batch = sub_store.get_batch(db, child.batch_id)
    base = dict(user_id=batch.user_id if batch else None, session_id=child.session_id,
                parent_session_id=batch.parent_session_id if batch else None,
                project_path=spawn.get("project_path") or "", playbook_id=spawn.get("playbook_id") or "",
                tier=spawn.get("tier") or "", role=spawn.get("role") or "", profile_id=child.profile_id,
                agent=child.agent, model=child.model, effort=child.effort, child_id=child.id,
                batch_id=child.batch_id)
    if started:
        store.append("child_round_start", **base)
    if finished:
        began = _sqlite_epoch(child.started_at)
        duration = max(0.0, time.time() - began) if began else None
        detail = {"error": (child.error or "")[:ERROR_SNIPPET]} if child.error else {}
        store.append("child_round_end", status=child.status, duration_s=duration, detail=detail,
                     turn_id=child.query_id, **base)


def _schedule_meta(op: Dict[str, Any]) -> Dict[str, Any]:
    cfg = op.get("payload") or {}
    return dict(session_id=int(op["actor"]), parent_session_id=int(op["target"]), agent=cfg.get("agent") or "",
                model=cfg.get("model") or "", effort=cfg.get("effort") or "", batch_id=str(op["id"]))


@_safe
def supervision_check(schedule_id: str, outcome: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """One check of a supervision schedule (``chat_ops.store.record_check``)."""
    from api.chat_ops import store as ops_store

    op = ops_store.find(schedule_id)
    if not op:
        return
    d = detail or {}
    store.append("sup_check", status=outcome, detail={k: d.get(k) for k in (
        "state", "trigger", "reason", "violations", "stalled_min", "entry") if d.get(k) is not None},
        **_schedule_meta(op))


@_safe
def supervision_schedule(action: str, op: Dict[str, Any], **detail: Any) -> None:
    """Schedule lifecycle: start / edit / pause / resume / cancel."""
    store.append("sup_schedule", status=action, detail=detail, **_schedule_meta(op))


@_safe
def supervisor_action(kind: str, actor: int, target: int, **detail: Any) -> None:
    """A supervisor acted on the chat it supervises (send / stop). Other chats' sends are not recorded."""
    from api.chat_ops import store as ops_store

    ops = [o for o in ops_store.supervising({int(target)}) if int(o["actor"]) == int(actor)]
    if not ops:
        return
    store.append(kind, status=str(detail.pop("delivery", "") or ""), detail=detail, **_schedule_meta(ops[0]))
