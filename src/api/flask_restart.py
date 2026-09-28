"""
Graceful Flask restart protocol (drain-first).

Flask may *request* a restart; the daemon owns stop/start/health.
Durable status + request files survive process replacement so clients can
recover without a second user prompt.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Project root (parent of src/) — same place as cuttle_notify_queue.jsonl
_SRC = Path(__file__).resolve().parent.parent
PROJECT_ROOT = _SRC.parent

STATUS_PATH = PROJECT_ROOT / "cuttle_flask_restart_status.json"
REQUEST_PATH = PROJECT_ROOT / "cuttle_flask_restart_request.json"
EVENTS_PATH = Path.home() / "cuttle_logs" / "flask_restart_events.jsonl"

# Observable delivery states only (no fake client ACKs).
DELIVERY_PERSISTED = "persisted"
DELIVERY_QUEUED = "queued"
DELIVERY_SENT = "sent"  # request handed to daemon / written to request file

STATES = (
    "requested",
    "waiting_for_idle",
    "preparing",
    "acknowledged",
    "stopping_old_flask",
    "starting_new_flask",
    "health_checking",
    "healthy",
    "failed",
    "timed_out",
    "rejected",
    "cancelled",
)

_lock = threading.RLock()
_when_idle_watcher_started = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_logs_dir() -> None:
    try:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def new_restart_id() -> str:
    return uuid.uuid4().hex


def live_flask_generation() -> int:
    """Daemon increments this on each Flask spawn; forms share an id per generation."""
    raw = (os.environ.get("CUTTLE_FLASK_GENERATION") or "").strip()
    if raw.isdigit():
        return int(raw)
    try:
        g = read_status().get("generation")
        return int(g) if g is not None and str(g).strip().isdigit() else 0
    except Exception:
        return 0


def shared_restart_form_id(generation: Optional[int] = None) -> str:
    """Stable action-form id for all Flask-restart cards until the next daemon restart."""
    g = live_flask_generation() if generation is None else int(generation)
    return f"flask-restart-g{max(0, g)}"


def is_flask_restart_controller_spec(spec: Any) -> bool:
    """True when a cuttle_action_form is the daemon-owned Flask restart chooser."""
    if not isinstance(spec, dict):
        return False
    opts = spec.get("options")
    if isinstance(opts, list):
        for opt in opts:
            if not isinstance(opt, dict):
                continue
            action = str(opt.get("action") or "").strip()
            if action in ("flask.restart", "__native_restart__"):
                return True
    title = str(spec.get("title") or "").strip().lower()
    return "restart flask" in title or title.startswith("flask restart")


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def read_status() -> Dict[str, Any]:
    with _lock:
        if not STATUS_PATH.exists():
            return {}
        try:
            with open(STATUS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def write_status(data: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        data = dict(data)
        data["updated_at"] = _utc_now()
        _atomic_write_json(STATUS_PATH, data)
        return data


def append_event(event: Dict[str, Any]) -> None:
    """Structured restart observability (no secrets / full prompts)."""
    try:
        _ensure_logs_dir()
        row = dict(event)
        row.setdefault("ts", _utc_now())
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _bare_session_id(session_id: Any) -> str:
    from api.session_keys import bare_chat_session_id

    return bare_chat_session_id(session_id)


def list_active_work(exclude_session_id: Optional[Any] = None) -> Dict[str, Any]:
    """
    Snapshot of active work for drain decisions.

    ``exclude_session_id`` drops the chat issuing a native /restart control
    command, which is not agent work and must not block its own restart.

    One logical agent turn registers a busy lock *and* an executing job under
    the same ``query_id``; those are collapsed into a single task so counts and
    the rejection list do not double-count it.
    """
    exclude = _bare_session_id(exclude_session_id) if exclude_session_id is not None else None

    busy: List[Dict[str, Any]] = []
    live_procs: List[str] = []
    jobs: List[dict] = []

    try:
        from api import chat_delivery

        busy = [dict(e) for e in (chat_delivery.busy_entries() or [])]
    except Exception:
        pass

    try:
        from api import chat_run_registry as crr

        for entry in busy:
            sid = entry.get("session_id")
            if crr.has_live_process(sid):
                live_procs.append(_bare_session_id(sid) or str(sid))
                if not entry.get("query_id"):
                    qids = crr.live_query_ids(sid)
                    if qids:
                        entry["query_id"] = qids[0]
    except Exception:
        pass

    try:
        from api.active_executions import get_executing_jobs

        jobs = list(get_executing_jobs() or [])
    except Exception:
        pass

    # Drop only the control request's own bookkeeping — both the busy lock and
    # any job record sharing its query_id. A genuine agent subprocess in the
    # same chat still counts as active work.
    if exclude:
        dropped_qids = set()
        kept = []
        for e in busy:
            same = _bare_session_id(e.get("session_id")) == exclude
            bare_sid = _bare_session_id(e.get("session_id")) or str(e.get("session_id") or "")
            if same and bare_sid not in live_procs:
                if e.get("query_id"):
                    dropped_qids.add(str(e["query_id"]))
                continue
            kept.append(e)
        busy = kept
        if dropped_qids:
            jobs = [j for j in jobs if str(j.get("query_id") or "") not in dropped_qids]

    busy_sessions = [str(e.get("session_id")) for e in busy]
    busy_query_ids = {str(e.get("query_id")) for e in busy if e.get("query_id")}

    # A job whose query_id is already represented by a busy chat is the same
    # logical task — keep it for identifiers, but do not count it twice.
    unique_jobs: List[dict] = []
    duplicate_jobs: List[dict] = []
    for job in jobs:
        qid = str(job.get("query_id") or "")
        if qid and qid in busy_query_ids:
            duplicate_jobs.append(job)
        else:
            unique_jobs.append(job)

    tasks: List[Dict[str, Any]] = []
    for entry in busy:
        sid = _bare_session_id(entry.get("session_id")) or str(entry.get("session_id") or "")
        qid = entry.get("query_id")
        pipeline = None
        for job in duplicate_jobs:
            if str(job.get("query_id") or "") == str(qid or ""):
                pipeline = job.get("pipeline_name")
                break
        tasks.append(
            {
                "kind": "chat_run",
                "session_id": sid,
                "query_id": qid,
                "pipeline_name": pipeline,
                "has_live_process": sid in live_procs,
            }
        )
    for job in unique_jobs:
        tasks.append(
            {
                "kind": "job",
                "session_id": None,
                "query_id": job.get("query_id"),
                "pipeline_name": job.get("pipeline_name"),
                "has_live_process": False,
            }
        )

    # Supervised workers register under synthetic session ids and must still
    # block graceful / when-idle restarts — without double-counting the parent chat.
    represented_sids = {
        _bare_session_id(t.get("session_id")) for t in tasks if t.get("session_id")
    }
    represented_sids.discard("")
    represented_pipelines = {
        str(t.get("pipeline_name") or "") for t in tasks if t.get("pipeline_name")
    }
    try:
        from api.agent_router.supervised.orchestrator import supervised_active_work_entries

        for entry in supervised_active_work_entries() or []:
            sid = str(entry.get("session_id") or "")
            pipe = str(entry.get("pipeline_name") or "")
            if sid and sid in represented_sids:
                continue
            if pipe and pipe in represented_pipelines:
                continue
            tasks.append(dict(entry))
            if sid:
                represented_sids.add(sid)
            if pipe:
                represented_pipelines.add(pipe)
    except Exception:
        pass

    # Live subprocesses not already represented (synthetic worker sessions).
    enumeration_failed = False
    try:
        from api import chat_run_registry as crr

        for sid in crr.active_run_session_ids() or []:
            bare = _bare_session_id(sid)
            if not bare or bare in represented_sids:
                continue
            if exclude and bare == exclude:
                continue
            qids = crr.live_query_ids(bare) or []
            tasks.append(
                {
                    "kind": "live_process",
                    "session_id": bare,
                    "query_id": qids[0] if qids else None,
                    "pipeline_name": None,
                    "has_live_process": True,
                }
            )
            represented_sids.add(bare)
    except Exception:
        # Fail closed: a registry error must not look like "idle" and kill Flask
        # while Codex/Cursor is still running (CH-000558 false-idle drain).
        enumeration_failed = True

    return {
        "busy_sessions": busy_sessions,
        "live_process_sessions": live_procs,
        "executing_jobs": jobs,
        "tasks": tasks,
        "deduped_job_count": len(duplicate_jobs),
        "excluded_session_id": exclude,
        "enumeration_failed": enumeration_failed,
        "active_count": len(tasks),
        "is_idle": (not tasks) and not enumeration_failed,
    }


def _terminal(state: str) -> bool:
    return state in ("healthy", "failed", "timed_out", "rejected", "cancelled")


# Non-terminal states left behind after daemon crash / exit_watch must not
# block new restarts forever (e.g. health_checking with an old new_flask_pid).
_STALE_IN_FLIGHT_SEC = 180
_STALE_WAITING_IDLE_SEC = 86400


def _parse_utc(ts: Any) -> Optional[datetime]:
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _status_age_sec(status: Dict[str, Any]) -> Optional[float]:
    dt = _parse_utc(status.get("updated_at"))
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _waiting_idle_orphaned(status: Dict[str, Any]) -> bool:
    """True when this Flask process is not the one that scheduled the wait.

    A full Cuttle/daemon restart mints a new PID (and usually a new generation).
    Keeping ``waiting_for_idle`` for 24h left CH-000545-10 stuck after the
    original Flask was already gone.
    """
    recorded = status.get("flask_pid") or status.get("old_flask_pid")
    try:
        if recorded and int(recorded) != os.getpid():
            return True
    except (TypeError, ValueError):
        pass
    env_gen = (os.environ.get("CUTTLE_FLASK_GENERATION") or "").strip()
    rec_gen = status.get("generation")
    if env_gen.isdigit() and rec_gen is not None and str(rec_gen).strip().isdigit():
        if int(env_gen) != int(rec_gen):
            return True
    return False


def _status_is_stale(status: Dict[str, Any]) -> bool:
    st = str(status.get("state") or "")
    if not st or _terminal(st):
        return False
    age = _status_age_sec(status)
    if st == "waiting_for_idle":
        if _waiting_idle_orphaned(status):
            return True
        return age is not None and age > _STALE_WAITING_IDLE_SEC
    if st in (
        "requested",
        "waiting_for_idle",
        "preparing",
        "acknowledged",
        "stopping_old_flask",
        "starting_new_flask",
        "health_checking",
    ):
        if age is None:
            return st == "health_checking"
        if age > _STALE_IN_FLIGHT_SEC:
            return True
        recorded_pid = status.get("new_flask_pid")
        try:
            if recorded_pid and int(recorded_pid) != os.getpid():
                return True
        except (TypeError, ValueError):
            pass
    return False


def _clear_stale_status(status: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(status.get("restart_id") or "")
    msg = (
        "Stale restart record cleared — Flask is running but the status file "
        "was stuck in a non-terminal state."
    )
    cleared = transition(
        rid or new_restart_id(),
        "failed",
        patch={"superseded": True, "cleared_stale": True},
        error=msg,
    )
    append_event(
        {
            "event": "stale_status_cleared",
            "restart_id": rid,
            "previous_state": status.get("state"),
            "live_flask_pid": os.getpid(),
        }
    )
    return cleared


def _in_flight(status: Dict[str, Any]) -> bool:
    if not status:
        return False
    if _status_is_stale(status):
        _clear_stale_status(status)
        return False
    st = str(status.get("state") or "")
    return bool(st) and not _terminal(st)


def transition(
    restart_id: str,
    state: str,
    *,
    patch: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    with _lock:
        cur = read_status()
        if cur.get("restart_id") and str(cur.get("restart_id")) != str(restart_id):
            # Allow daemon to update only the active id; ignore stale writers.
            if _in_flight(cur) and str(cur.get("restart_id")) != str(restart_id):
                return cur
        data = dict(cur) if cur.get("restart_id") == restart_id else {"restart_id": restart_id}
        data["restart_id"] = restart_id
        data["state"] = state
        if error is not None:
            data["error"] = error
        if patch:
            data.update(patch)
        hist = list(data.get("history") or [])
        hist.append({"state": state, "at": _utc_now()})
        data["history"] = hist[-40:]
        write_status(data)
        append_event(
            {
                "event": "state",
                "restart_id": restart_id,
                "state": state,
                "error": error,
                "old_pid": data.get("old_flask_pid"),
                "new_pid": data.get("new_flask_pid"),
                "generation": data.get("generation"),
                "active_task_count": (data.get("active_work") or {}).get("active_count"),
                "delivery": data.get("delivery"),
            }
        )
        return data


def write_daemon_request(restart_id: str, mode: str, meta: Optional[Dict[str, Any]] = None) -> None:
    req = {
        "restart_id": restart_id,
        "mode": mode,
        "requested_at": _utc_now(),
        "meta": meta or {},
    }
    with _lock:
        _atomic_write_json(REQUEST_PATH, req)
    append_event(
        {
            "event": "daemon_request_written",
            "restart_id": restart_id,
            "mode": mode,
        }
    )


def consume_daemon_request() -> Optional[Dict[str, Any]]:
    """Daemon: read and remove the pending request file."""
    with _lock:
        if not REQUEST_PATH.exists():
            return None
        try:
            with open(REQUEST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            try:
                REQUEST_PATH.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        try:
            REQUEST_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        return data if isinstance(data, dict) else None


def _persist_session_message(
    session_id: Optional[str],
    text: str,
    *,
    restart_id: str,
    kind: str,
) -> bool:
    """Best-effort durable chat visibility before Flask dies."""
    if not session_id or not text:
        return False
    bare = str(session_id).strip()
    if bare.startswith("db_session_"):
        bare = bare[len("db_session_") :]
    try:
        sid_int = int(bare)
    except (TypeError, ValueError):
        return False
    try:
        from api.auth_db import get_auth_db

        db = get_auth_db()
        db.add_message(
            sid_int,
            "assistant",
            text,
            metadata={
                "type": "flask_restart",
                "restart_id": restart_id,
                "kind": kind,
                "delivery": DELIVERY_PERSISTED,
            },
        )
        return True
    except Exception as e:
        append_event(
            {
                "event": "persist_message_failed",
                "restart_id": restart_id,
                "error": str(e)[:200],
            }
        )
        return False


def awaiting_action_session_ids() -> set:
    """Session ids with an in-flight Flask restart (when-idle / restarting)."""
    out: set = set()
    try:
        # Heal stale exit_watch / crash leftovers so history doesn't spin forever.
        st = read_status() or {}
        if not _in_flight(st):
            return out
        st = read_status() or {}
        rsid = _bare_session_id(st.get("session_id"))
        if rsid:
            out.add(rsid)
    except Exception:
        pass
    return out


def status_snapshot(exclude_session_id: Optional[Any] = None) -> Dict[str, Any]:
    """
    Durable restart status for the action-form card / status API.

    Clears stale non-terminal records (daemon crash mid-health-check, leftover
    exit_watch respawns) so the UI does not treat a dead restart as in-flight
    forever and flicker Restarting ↔ Health check.
    """
    status = read_status()
    # Side effect: heal stuck health_checking / pid-mismatch leftovers.
    _in_flight(status)
    status = read_status()
    work = list_active_work(exclude_session_id=exclude_session_id)
    return {
        "success": True,
        "status": status,
        "active_work": work,
        "daemon_request_pending": REQUEST_PATH.exists(),
        "status_path": str(STATUS_PATH),
        "request_path": str(REQUEST_PATH),
    }


def request_restart(
    *,
    mode: str = "graceful",
    session_id: Optional[str] = None,
    user_source: str = "api",
    force_confirm: bool = False,
    triggering_task_id: Optional[str] = None,
    chat_notify: bool = True,
) -> Dict[str, Any]:
    """
    Flask-side entry: drain-first restart request.

    mode: graceful | when-idle | force

    ``chat_notify`` False keeps ack/completion out of the transcript — used by
    the action-form card, which renders progress on the card itself instead of
    dropping restart bubbles into the conversation.
    """
    mode = (mode or "graceful").strip().lower()
    if mode not in ("graceful", "when-idle", "force", "status"):
        return {"success": False, "error": f"Unknown mode `{mode}`", "type": "flask_restart"}

    if mode == "status":
        return {**status_snapshot(exclude_session_id=session_id), "type": "flask_restart"}

    # The initiating native control request is not agent work; excluding it
    # keeps `/restart graceful` from rejecting itself.
    work = list_active_work(exclude_session_id=session_id)
    cur = read_status()
    if _in_flight(cur):
        waiting = str(cur.get("state") or "") == "waiting_for_idle"
        if mode == "force" and force_confirm and waiting:
            old_rid = str(cur.get("restart_id") or "")
            if old_rid:
                transition(
                    old_rid,
                    "cancelled",
                    error="Superseded by force restart",
                    patch={"superseded": True},
                )
            cur = read_status()
        if _in_flight(cur):
            return {
                "success": False,
                "type": "flask_restart",
                "error": "A restart is already in progress.",
                "restart_id": cur.get("restart_id"),
                "state": cur.get("state"),
                "status": cur,
                "active_work": work,
            }

    if mode == "force" and not force_confirm:
        return {
            "success": False,
            "type": "flask_restart",
            "error": (
                "Force restart requires confirmation (`confirm: true` or "
                "`/restart force --yes`). Affected tasks will be interrupted."
            ),
            "active_work": work,
            "needs_confirm": True,
        }

    restart_id = new_restart_id()
    base = {
        "restart_id": restart_id,
        "mode": mode,
        "session_id": str(session_id) if session_id else None,
        "user_source": user_source,
        "triggering_task_id": triggering_task_id,
        "created_at": _utc_now(),
        "active_work": work,
        "chat_notify": bool(chat_notify),
        "delivery": DELIVERY_PERSISTED,
        "history": [],
        "flask_pid": os.getpid(),
        "old_flask_pid": os.getpid(),
        "generation": live_flask_generation(),
        "interrupted_tasks": [],
        "outcome_visible": False,
        "client_notified": False,
    }

    if mode == "graceful" and not work["is_idle"]:
        chat_tasks = [t for t in work["tasks"] if t["kind"] == "chat_run"]
        job_tasks = [t for t in work["tasks"] if t["kind"] == "job"]
        msg = (
            f"**Flask restart postponed** (`{restart_id}`)\n\n"
            f"Active work detected ({work['active_count']} task(s)): "
            f"busy chats={[t['session_id'] for t in chat_tasks] or '[]'}, "
            f"standalone jobs={len(job_tasks)}.\n\n"
            "Use `/restart when-idle` to run after work finishes, "
            "or `/restart force --yes` to interrupt."
        )
        write_status({**base, "state": "rejected", "error": "not_idle"})
        ack_persisted = False
        if chat_notify:
            ack_persisted = _persist_session_message(
                session_id, msg, restart_id=restart_id, kind="rejected"
            )
        append_event(
            {
                "event": "rejected_not_idle",
                "restart_id": restart_id,
                "active_task_count": work["active_count"],
            }
        )
        return {
            "success": False,
            "type": "flask_restart",
            "restart_id": restart_id,
            "state": "rejected",
            "response": msg,
            "active_work": work,
            "suggest": "when-idle",
            "ack_persisted": ack_persisted,
        }

    if mode == "when-idle" and not work["is_idle"]:
        ack = (
            f"**Flask restart scheduled when idle** (`{restart_id}`)\n\n"
            f"Waiting for {work['active_count']} active task(s) to finish. "
            "New work should be avoided; drain is in effect for this restart."
        )
        data = write_status(
            {
                **base,
                "state": "waiting_for_idle",
                "delivery": DELIVERY_QUEUED,
            }
        )
        ack_persisted = False
        if chat_notify:
            ack_persisted = _persist_session_message(
                session_id, ack, restart_id=restart_id, kind="ack_when_idle"
            )
        _ensure_when_idle_watcher()
        append_event(
            {
                "event": "waiting_for_idle",
                "restart_id": restart_id,
                "active_task_count": work["active_count"],
            }
        )
        return {
            "success": True,
            "type": "flask_restart",
            "restart_id": restart_id,
            "state": "waiting_for_idle",
            "response": ack,
            "status": data,
            "active_work": work,
            "ack_persisted": ack_persisted,
        }

    # Idle graceful, when-idle that is already idle, or force
    return _begin_handoff(restart_id, mode, base, work, session_id)


def _begin_handoff(
    restart_id: str,
    mode: str,
    base: Dict[str, Any],
    work: Dict[str, Any],
    session_id: Optional[str],
) -> Dict[str, Any]:
    interrupted: List[dict] = []
    if mode == "force" and not work["is_idle"]:
        interrupted = _mark_interrupted(work)

    ack = (
        f"**Flask restart acknowledged** (`{restart_id}`)\n\n"
        f"Mode: `{mode}`. Daemon will replace Flask after this message is persisted. "
        "Reconnect automatically; you should see the result without sending another message."
    )
    if interrupted:
        ack += f"\n\nInterrupted tasks: `{json.dumps(interrupted)[:500]}`"

    data = write_status(
        {
            **base,
            "state": "acknowledged",
            "delivery": DELIVERY_PERSISTED,
            "interrupted_tasks": interrupted,
            "active_work": work,
        }
    )
    persisted = False
    if base.get("chat_notify", True):
        persisted = _persist_session_message(
            session_id, ack, restart_id=restart_id, kind="ack"
        )
    data = transition(
        restart_id,
        "preparing",
        patch={
            "ack_persisted": persisted,
            "delivery": DELIVERY_SENT,
            "interrupted_tasks": interrupted,
        },
    )
    write_daemon_request(
        restart_id,
        mode,
        meta={
            "session_id": session_id,
            "user_source": base.get("user_source"),
            "old_flask_pid": os.getpid(),
        },
    )
    # Small delay so the HTTP response can flush before the daemon kills us.
    # The caller (API) should return the response; daemon waits ~2s after seeing the file.
    return {
        "success": True,
        "type": "flask_restart",
        "restart_id": restart_id,
        "state": data.get("state"),
        "response": ack,
        "status": data,
        "active_work": work,
        "restart_scheduled": True,
        "ack_persisted": persisted,
    }


def _mark_interrupted(work: Dict[str, Any]) -> List[dict]:
    """Record interrupted sessions; do not pretend they completed."""
    out: List[dict] = []
    # `tasks` is already deduplicated (a busy chat and its executing job are one
    # task) and excludes the native control request that asked for the restart.
    for task in work.get("tasks") or []:
        out.append(
            {
                "kind": task.get("kind"),
                "session_id": task.get("session_id"),
                "query_id": task.get("query_id"),
                "pipeline": task.get("pipeline_name"),
                "status": "interrupted",
            }
        )
    append_event(
        {
            "event": "force_interrupt_marked",
            "tasks": out,
        }
    )
    return out


def maybe_fire_when_idle() -> Optional[Dict[str, Any]]:
    """If a when-idle restart is pending and work is done, hand off to daemon."""
    with _lock:
        cur = read_status()
        if str(cur.get("state")) != "waiting_for_idle":
            return None
        restart_id = str(cur.get("restart_id") or "")
        if not restart_id:
            return None
        # The chat that scheduled the restart must not keep the system
        # permanently non-idle on its own behalf.
        work = list_active_work(exclude_session_id=cur.get("session_id"))
        if not work["is_idle"]:
            return None
        # Avoid double-fire if request already written
        if REQUEST_PATH.exists():
            return cur
        base = dict(cur)
        base["active_work"] = work
        return _begin_handoff(
            restart_id,
            "when-idle",
            base,
            work,
            cur.get("session_id"),
        )


def _ensure_when_idle_watcher() -> None:
    global _when_idle_watcher_started
    with _lock:
        if _when_idle_watcher_started:
            return
        _when_idle_watcher_started = True

    def _loop():
        while True:
            try:
                maybe_fire_when_idle()
            except Exception:
                pass
            time.sleep(2.0)

    t = threading.Thread(target=_loop, name="flask-restart-when-idle", daemon=True)
    t.start()


def mark_outcome_visible(restart_id: str) -> None:
    """Client fetched post-restart status (dedupe by restart_id)."""
    with _lock:
        cur = read_status()
        if str(cur.get("restart_id")) != str(restart_id):
            return
        if cur.get("client_notified"):
            return
        cur["client_notified"] = True
        cur["outcome_visible"] = True
        write_status(cur)
        append_event({"event": "client_notified", "restart_id": restart_id})


def build_completion_message(status: Dict[str, Any]) -> str:
    rid = status.get("restart_id") or "?"
    state = status.get("state") or "?"
    if state == "healthy":
        return (
            f"**Flask restart complete** (`{rid}`)\n\n"
            f"New PID `{status.get('new_flask_pid')}`, generation `{status.get('generation')}`. "
            f"Health check passed"
            + (
                f" in {status.get('health_ms')} ms."
                if status.get("health_ms") is not None
                else "."
            )
        )
    if state in ("failed", "timed_out"):
        return (
            f"**Flask restart {state}** (`{rid}`)\n\n"
            f"{status.get('error') or 'See daemon logs.'}"
        )
    return f"**Flask restart status:** `{state}` (`{rid}`)"


def strip_sticky_agent_prefix(message: str) -> str:
    """Drop a leading sticky agent prefix (`/cursor `, `/codex `, …).

    Older clients prepend the sticky chip to every outgoing message, which would
    otherwise turn a native control command into an agent prompt.
    """
    raw = (message or "").strip()
    for _ in range(2):
        try:
            from api.starred_slash import sticky_prefix_from_text
        except Exception:
            return raw
        prefix = sticky_prefix_from_text(raw)
        if not prefix:
            return raw
        stripped = raw[len(prefix.strip()) :].lstrip()
        if not stripped.startswith("/"):
            return raw
        raw = stripped
    return raw


_RESTART_FORM_OPTIONS: List[Dict[str, Any]] = [
    {
        "id": "status",
        "label": "Status — show restart state + active work",
        "action": "flask.restart",
        "params": {"mode": "status"},
    },
    {
        "id": "graceful",
        "label": "Graceful — restart now (rejects if busy)",
        "action": "flask.restart",
        "params": {"mode": "graceful"},
    },
    {
        "id": "when-idle",
        "label": "When idle — wait for active jobs to finish",
        "action": "flask.restart",
        "params": {"mode": "when-idle"},
    },
    {
        "id": "force",
        "label": "Force — interrupt active work",
        "action": "flask.restart",
        "params": {"mode": "force"},
    },
    {
        "id": "health",
        "label": "Health check only (no restart)",
        "action": "flask.health",
        "params": {},
    },
]

_RESTART_TERMINAL_STATES = frozenset(
    {"healthy", "failed", "timed_out", "rejected", "cancelled"}
)


def _blocking_chat_handles(active_work: Optional[Dict[str, Any]]) -> List[str]:
    """CH- handles for drain-blocking chats (card toast / force interrupt list)."""
    handles: List[str] = []
    seen = set()
    for task in (active_work or {}).get("tasks") or []:
        if not isinstance(task, dict):
            continue
        bare = _bare_session_id(task.get("session_id"))
        if not bare or bare in seen:
            continue
        seen.add(bare)
        if bare.isdigit():
            handles.append(f"CH-{int(bare):06d}")
        else:
            handles.append(bare)
    return handles


def _restart_card_toast(
    *,
    mode: str,
    state: Optional[str] = None,
    active_work: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> str:
    if error:
        return str(error).strip()
    st = str(state or "").strip()
    work = active_work or {}
    n = int(work.get("active_count") or 0)
    named = _blocking_chat_handles(work)
    named_s = ", ".join(named[:8])
    extra = f" ({named_s})" if named_s else ""
    if st == "rejected":
        return (
            f"Postponed — {n} active task(s) still running{extra}"
            if n
            else "Restart postponed — other work is still running"
        )
    if st == "waiting_for_idle":
        return (
            f"Waiting for {n} active task(s) to finish{extra}…"
            if n
            else "Waiting for active work to finish…"
        )
    if st in ("acknowledged", "preparing"):
        return "Restarting Flask…"
    if mode == "force":
        return "Force restart — interrupting active work…"
    return "Restarting Flask…"


def build_restart_action_form(
    *,
    restart_id: Optional[str],
    session_id: Optional[str],
    mode: str,
    state: Optional[str] = None,
    active_work: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    success: bool = True,
) -> str:
    """Locked restart card — progress on the card, no ack/completion bubbles."""
    mode_key = (mode or "graceful").strip().lower()
    if mode_key in ("when_idle", "idle"):
        mode_key = "when-idle"
    selected = mode_key if mode_key in ("status", "graceful", "when-idle", "force") else "graceful"
    st = str(state or "").strip()
    spec: Dict[str, Any] = {
        "mode": "choice",
        "title": "Restart Flask (daemon-owned)",
        "lock": "form",
        "locked": True,
        "silent": True,
        "selected": [selected],
        "toast": _restart_card_toast(
            mode=mode_key,
            state=st or None,
            active_work=active_work,
            error=error,
        ),
        "options": list(_RESTART_FORM_OPTIONS),
    }
    if session_id is not None:
        spec["session_id"] = str(session_id)
    if restart_id:
        spec["restartId"] = str(restart_id)
    try:
        spec["restartFormGroup"] = shared_restart_form_id()
        spec["id"] = spec["restartFormGroup"]
    except Exception:
        pass
    if st and st not in _RESTART_TERMINAL_STATES and success:
        spec["pending"] = True
    body = json.dumps(spec, ensure_ascii=False)
    return f"<cuttle_action_form>\n{body}\n</cuttle_action_form>"


def parse_restart_slash(message: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Parse `/restart …` → (mode, opts) or None.
    """
    raw = strip_sticky_agent_prefix(message)
    low = raw.lower()
    if not (low == "/restart" or low.startswith("/restart ")):
        return None
    parts = raw.split()
    if len(parts) == 1:
        return "status", {}
    sub = parts[1].lower().strip()
    if sub in ("status", "graceful", "when-idle", "when_idle", "idle", "force"):
        if sub in ("when_idle", "idle"):
            sub = "when-idle"
        opts: Dict[str, Any] = {}
        rest = " ".join(parts[2:]).lower()
        if "--yes" in rest or "-y" in rest or "confirm" in rest:
            opts["force_confirm"] = True
        return sub, opts
    return "status", {}


def handle_restart_slash(
    message: str,
    *,
    session_id: Optional[str] = None,
    user_source: str = "chat",
) -> Optional[Dict[str, Any]]:
    parsed = parse_restart_slash(message)
    if not parsed:
        return None
    mode, opts = parsed
    if mode != "status":
        try:
            from flask import has_request_context

            if has_request_context():
                from api.http_authz import require_owner as _require_owner

                _user, err = _require_owner()
                if err:
                    return {
                        "success": False,
                        "type": "flask_restart",
                        "error": "Owner privileges required.",
                        "response": "Owner privileges required to restart Flask.",
                    }
        except Exception:
            pass
    if mode == "status":
        snap = status_snapshot(exclude_session_id=session_id)
        st = snap.get("status") or {}
        work = snap.get("active_work") or {}
        lines = [
            f"**Flask restart status**",
            f"- state: `{st.get('state') or 'none'}`",
            f"- restart_id: `{st.get('restart_id') or '—'}`",
            f"- mode: `{st.get('mode') or '—'}`",
            f"- delivery: `{st.get('delivery') or '—'}`",
            f"- active tasks: `{work.get('active_count', 0)}`",
            f"- busy sessions: `{work.get('busy_sessions') or []}`",
            f"- daemon request pending: `{snap.get('daemon_request_pending')}`",
        ]
        if work.get("deduped_job_count"):
            lines.append(
                f"- (deduped `{work.get('deduped_job_count')}` job record(s) "
                "already counted as busy chats)"
            )
        if st.get("error"):
            lines.append(f"- error: {st.get('error')}")
        return {
            "success": True,
            "type": "flask_restart",
            "response": "\n".join(lines),
            **snap,
        }
    result = request_restart(
        mode=mode,
        session_id=session_id,
        user_source=user_source,
        force_confirm=bool(opts.get("force_confirm")),
        chat_notify=False,
    )
    rid = result.get("restart_id")
    state = str(result.get("state") or "").strip() or None
    toast_error = None
    if not result.get("success") and not rid:
        toast_error = str(result.get("error") or result.get("response") or "Restart failed")
    result["response"] = build_restart_action_form(
        restart_id=str(rid) if rid else None,
        session_id=session_id,
        mode=mode,
        state=state,
        active_work=result.get("active_work"),
        error=toast_error,
        success=bool(result.get("success")),
    )
    return result
