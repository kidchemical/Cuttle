"""Pause, stop and resume a whole orchestration (the chat's *hold*).

An orchestrator keeps itself going: every job or sub-agent it starts registers a
wake-up, so stopping one piece just wakes it to start the next. The hold is the
one switch over all of it, kept on the chat's binding:

- ``paused``: the running turn and the sub-agents are stopped, supervision of
  the chat is paused, and wake-ups and queued messages wait. Jobs already
  running finish; their results are delivered on resume.
- ``stopped``: the same, and running watched jobs are stopped and the waiting
  wake-ups and messages are dropped.

While held, the chat-ops pump delivers nothing to the chat
(``api.chat_ops.worker.dispatch``), sub-agent spawns from it are refused
(``api.subagents.service.spawn``) and its brief says so. A message the user
types into the chat still runs: the user is the one in charge.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from api.orchestrator import store
from api.orchestrator.service import (
    OrchestratorError, _db, _handle, _session_owner, get_binding, parse_session,
)

PAUSED, STOPPED = "paused", "stopped"
HOLD_NOTE = {
    PAUSED: ("**This orchestration is PAUSED by the user.** Do not start or continue jobs, builds or "
             "sub-agents and do not carry on with the plan. Answer what the user asks and wait for Resume."),
    STOPPED: ("**This orchestration was STOPPED by the user.** Its jobs and sub-agents were ended on purpose: "
              "do not restart them and do not carry on with the plan. Answer what the user asks and wait "
              "for Resume."),
}


def held(session_id: Any) -> str:
    """The chat's hold (``''`` when running or not orchestrating). Never raises."""
    try:
        row = store.binding(parse_session(session_id))
        return str((row or {}).get("hold") or "")
    except Exception:
        return ""


def hold_note(session_id: Any) -> str:
    """The line a held chat's brief starts with (``''`` when running)."""
    return HOLD_NOTE.get(held(session_id), "")


def _owned(session: Any, user_id: Optional[int], db) -> int:
    sid = parse_session(session)
    owner = _session_owner(sid, db)
    if user_id is not None and int(user_id) != owner:
        raise OrchestratorError("that chat belongs to another account")
    if not store.binding(sid):
        raise OrchestratorError(f"{_handle(sid)} is not orchestrating; start it with /orchestrate")
    return sid


def _step(out: Dict[str, Any], name: str, fn) -> None:
    """One stop step; a failed step is reported, the others still run."""
    try:
        out[name] = fn()
    except Exception as exc:
        out.setdefault("errors", []).append(f"{name}: {exc}"[:300])


def _pause_supervision(sid: int) -> List[str]:
    from api.chat_ops import store as ops

    paused = []
    for op in ops.supervising([sid]):
        if ops.set_paused(op["id"], op["actor"], True):
            paused.append(op["id"])
    return paused


def _cancel_children(sid: int, db) -> int:
    from api.subagents import service as subagents
    from api.subagents import store as sub_store

    batches = sub_store.list_running_for_parent(db, sid)
    for batch in batches:
        subagents.cancel_batch(batch.id, db=db)
    return len(batches)


def _stop_turn(sid: int, db, request) -> bool:
    from api.chat_ops import service as chat_ops
    from api.chat_ops import transport

    _db_, _actor, row = chat_ops.context(sid, sid, db=db)
    out = (request or transport.request)(row["user_id"], row["id"], "POST", "/api/chat-cancel") or {}
    return bool(out.get("success", True))


def _stop_jobs(sid: int) -> List[str]:
    from api import job_watch

    stopped = []
    for record in job_watch.unit_watch_records(sid):
        if job_watch.cancel_job(record["id"]).get("ok"):
            stopped.append(record["id"])
    return stopped


def _drop_deliveries(sid: int) -> int:
    from api.chat_ops import store as ops

    return len(ops.cancel_sends_to(sid, kinds=("send", "notice")))


def hold(session: Any, *, stop: bool = False, user_id: Optional[int] = None, source: str = "ui",
         db=None, request=None) -> Dict[str, Any]:
    """Pause the orchestration (``stop=True``: also end its jobs and drop its wake-ups)."""
    db = _db(db)
    sid = _owned(session, user_id, db)
    state = STOPPED if stop else PAUSED
    previous = store.binding(sid) or {}
    try:
        kept = [str(o) for o in json.loads(previous.get("hold_ops") or "[]")]
    except (TypeError, ValueError):
        kept = []
    # The mark goes first: from here nothing is delivered to the chat and it
    # cannot spawn, so the steps below cannot be undone by a wake-up.
    store.set_hold(sid, state, ops=kept, source=source)
    out: Dict[str, Any] = {}
    _step(out, "supervision_paused", lambda: _pause_supervision(sid))
    store.set_hold(sid, state, ops=sorted(set(kept) | set(out.get("supervision_paused") or [])), source=source)
    _step(out, "agents_stopped", lambda: _cancel_children(sid, db))
    if stop:
        _step(out, "jobs_stopped", lambda: _stop_jobs(sid))
        _step(out, "deliveries_dropped", lambda: _drop_deliveries(sid))
    _step(out, "turn_stopped", lambda: _stop_turn(sid, db, request))
    return {**get_binding(sid, db=db), "actions": out}


def resume(session: Any, *, user_id: Optional[int] = None, source: str = "ui", db=None) -> Dict[str, Any]:
    """Lift the hold: supervision it paused resumes, waiting wake-ups are delivered."""
    db = _db(db)
    sid = _owned(session, user_id, db)
    row = store.binding(sid) or {}
    if not row.get("hold"):
        return {**get_binding(sid, db=db), "actions": {}}
    try:
        op_ids = [str(o) for o in json.loads(row.get("hold_ops") or "[]")]
    except (TypeError, ValueError):
        op_ids = []
    store.set_hold(sid, "", source=source)
    out: Dict[str, Any] = {}

    def _resume_supervision() -> List[str]:
        from api.chat_ops import store as ops

        resumed = []
        for oid in op_ids:
            op = ops.find(oid)
            if op and op.get("state") == "paused" and ops.set_paused(oid, op["actor"], False):
                resumed.append(oid)
        return resumed

    def _release() -> int:
        from api.chat_ops import store as ops

        return ops.release_held(sid)

    _step(out, "supervision_resumed", _resume_supervision)
    _step(out, "deliveries_released", _release)
    return {**get_binding(sid, db=db), "actions": out}
