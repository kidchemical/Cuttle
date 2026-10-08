"""Run one turn in a child chat session (harness CLI, persist both sides)."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

import api.subagents.store as store
from api.subagents.types import (
    ChildRecord,
    ChildSpec,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    chat_handle,
)

TurnRunner = Callable[..., Dict[str, Any]]


class ChildStatusSink:
    """``status_queue`` stand-in for a child turn running outside Flask.

    ``api.subagents`` executes child turns in the CLI process, so the
    process-local live-status store in Flask never sees them — the only channel
    that crosses the process boundary is SQLite. This sink captures coalesced
    live statuses and pins the harness's ``query_started`` id onto the child row,
    which lets parent cards show progress and child panes open the live log. Without a
    status_queue the kernel skips that emit entirely (kernel.py gates it on
    ``if status_queue``), which is why sub-agent panes had no log to open.
    """

    def __init__(self, db, child_id: str):
        self._db = db
        self._child_id = child_id
        self._inner = None
        self._lock = threading.Lock()
        self.query_id = ""
        self.last_status = ""
        child = store.get_child(db, child_id)
        self._turn_id = child.live_turn_id if child else ""
        self._last_write = 0.0
        self._timer = None
        self._closed = False

    def put(self, item: Any, *args: Any, **kwargs: Any) -> Any:
        self._ingest(item)
        if self._inner is None:
            return None
        return self._inner.put(item, *args, **kwargs)

    def put_nowait(self, item: Any) -> Any:
        self._ingest(item)
        if self._inner is None:
            return None
        put_nowait = getattr(self._inner, "put_nowait", None)
        if callable(put_nowait):
            return put_nowait(item)
        return self._inner.put(item)

    def _ingest(self, item: Any) -> None:
        try:
            if not (isinstance(item, tuple) and len(item) >= 2):
                return
            kind, payload = item[0], item[1]
            if kind == "query_started":
                qid = str((payload or {}).get("query_id") or "").strip()
                if not qid:
                    return
                with self._lock:
                    if self._closed:
                        return
                    self.query_id = qid
                    store.update_child(self._db, self._child_id, query_id=qid,
                                       live_turn_id=self._turn_id)
            elif kind == "status":
                with self._lock:
                    if self._closed:
                        return
                    self.last_status = " ".join(str(payload or "").split())[:600]
                    # Leading write + trailing coalesced snapshot: noisy harness
                    # events cause at most four SQLite writes per second.
                    delay = 0.25 - (time.monotonic() - self._last_write)
                    if delay <= 0:
                        self._write_status()
                    elif self._timer is None:
                        self._timer = threading.Timer(delay, self._flush_status)
                        self._timer.daemon = True
                        self._timer.start()
        except Exception:
            pass

    def _write_status(self) -> None:
        store.update_child_live_status(self._db, self._child_id, self._turn_id, self.last_status)
        self._last_write = time.monotonic()

    def _flush_status(self) -> None:
        with self._lock:
            self._timer = None
            if not self._closed:
                try:
                    self._write_status()
                except Exception:
                    pass  # Observability must never fail a child turn.

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def default_runner(
    *,
    agent: str,
    prompt: str,
    session_id: int,
    project_path: str = "",
    model_override: str = "",
    execute_kwargs: Optional[Dict[str, Any]] = None,
    status_queue: Any = None,
) -> Dict[str, Any]:
    from api.agent_harness.kernel import run_agent_web_command

    kw: Dict[str, Any] = {}
    if model_override:
        kw["model_override"] = model_override
    if execute_kwargs:
        kw["execute_kwargs"] = execute_kwargs
    if status_queue is not None:
        kw["status_queue"] = status_queue
    return run_agent_web_command(
        agent,
        prompt,
        session_id,
        project_path=project_path or None,
        **kw,
    )


def _parent_speaker_meta(db, child: ChildRecord, spec: ChildSpec) -> Dict[str, Any]:
    parent_sid = None
    parent_label = "Parent chat"
    batch = store.get_batch(db, child.batch_id)
    if batch:
        parent_sid = int(batch.parent_session_id)
        prow = db.get_chat_session_by_id(parent_sid) or {}
        parent_label = str(prow.get("session_name") or "").strip() or chat_handle(parent_sid)
    handle = chat_handle(parent_sid) if parent_sid is not None else ""
    from api.subagents.identity import parent_slash_command

    meta: Dict[str, Any] = {
        "origin": "subagent",
        "speaker": "Cuttle",
        "speaker_kind": "parent",
        "parent_session_id": parent_sid,
        "parent_handle": handle,
        "parent_label": parent_label,
        "avatar": "cuttle",
        "slash_command": parent_slash_command(db, parent_sid),
    }
    return meta


def _assistant_identity_meta(
    child: ChildRecord,
    spec: Optional[ChildSpec],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"origin": "subagent", "speaker_kind": "subagent"}
    name = ""
    avatar = ""
    profile_id = ""
    if spec is not None:
        name = (spec.display_name or spec.title or "").strip()
        avatar = (spec.avatar or "").strip()
        profile_id = (spec.profile_id or "").strip()
        from api.subagents.identity import annotate_runner_result, child_slash_command

        result = annotate_runner_result(result, spec, session_id=child.session_id if child else None)
        # Child bubble = the child harness, never Auto inferred from a missing
        # model_override / session pin (that is what painted Grok Riddler as Auto).
        # With no spec model/effort the chip falls back to what the harness
        # actually ran (result agent_model/agent_effort), so agent-only spawns
        # still badge the full `Agent - model · effort` chip on the full page.
        meta["slash_command"] = child_slash_command(spec, child, result=result)
        try:
            from api.chat_metadata import usage_meta_from_assistant_result

            _res = result if isinstance(result, dict) else {}
            _cursor_run = _res.get("cursor_run")
            if isinstance(_cursor_run, dict) and _cursor_run and not meta.get("cursor_run"):
                meta["cursor_run"] = _cursor_run
            _usage = usage_meta_from_assistant_result(_res)
            if _usage:
                meta["usage"] = _usage
            _eff = str(_res.get("agent_effort") or "").strip()
            if _eff and not meta.get("agent_effort"):
                meta["agent_effort"] = _eff
        except Exception:
            pass
    name = name or (child.display_name or child.label or "").strip()
    avatar = avatar or (child.avatar or "").strip()
    profile_id = profile_id or (child.profile_id or "").strip()
    if name:
        meta["speaker"] = name
    if avatar:
        meta["avatar"] = avatar
    if profile_id:
        meta["profile_id"] = profile_id
    if result.get("query_id"):
        meta["query_id"] = result.get("query_id")
    if result.get("report_url"):
        meta["report_url"] = result.get("report_url")
    cursor_run = result.get("cursor_run")
    if isinstance(cursor_run, dict) and cursor_run:
        meta["cursor_run"] = cursor_run
    return meta


def _persist_assistant(
    db,
    session_id: int,
    result: Dict[str, Any],
    *,
    child: Optional[ChildRecord] = None,
    spec: Optional[ChildSpec] = None,
) -> None:
    text = str(result.get("response") or result.get("output") or "").strip()
    if not text or text.startswith("[CANCELLED]"):
        return
    if child is not None:
        meta = _assistant_identity_meta(child, spec, result)
    else:
        meta = {"origin": "subagent"}
        if result.get("query_id"):
            meta["query_id"] = result.get("query_id")
        if result.get("report_url"):
            meta["report_url"] = result.get("report_url")
        cursor_run = result.get("cursor_run")
        if isinstance(cursor_run, dict) and cursor_run:
            meta["cursor_run"] = cursor_run
    db.add_message(int(session_id), "assistant", text, metadata=meta)


def _cancelled(db, child_id: str) -> bool:
    row = store.get_child(db, child_id)
    return bool(row and row.status == STATUS_CANCELLED)


def run_child_turn(
    db,
    child: ChildRecord,
    spec: Optional[ChildSpec] = None,
    *,
    project_path: str = "",
    runner: Optional[TurnRunner] = None,
    extra_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist the user turn, run the harness, persist the assistant reply."""
    if _cancelled(db, child.id):
        return {"success": False, "cancelled": True, "response": ""}

    if spec is None:
        spec = ChildSpec(
            title=child.label,
            message=extra_message or child.prompt,
            agent=child.agent,
            model=child.model,
            effort=child.effort,
            profile_id=child.profile_id,
            display_name=child.display_name,
            avatar=child.avatar,
        )
    if extra_message:
        spec = replace(spec, message=extra_message)

    from api.subagents.identity import pin_child_session, resolve_child_model_id

    user_text = (spec.message or "").strip()
    run_prompt = user_text
    if runner is None:
        pin_child_session(child.session_id, spec, project_path=project_path)
    db.add_message(
        int(child.session_id),
        "user",
        user_text,
        metadata=_parent_speaker_meta(db, child, spec),
    )
    if _cancelled(db, child.id):
        return {"success": False, "cancelled": True, "response": ""}
    store.update_child(db, child.id, status=STATUS_RUNNING, started=True)
    # owner_pid names the process responsible for this turn. If that process
    # dies (SIGKILL runs no cleanup) reconcile_orphan_children flips the row
    # terminal instead of leaving the child pane spinning forever.
    store.update_child(db, child.id, owner_pid=os.getpid())
    # Sink (not a real queue): captures the harness query_started emit so a
    # child pane can open the live query log while the sub-agent works.
    sink = ChildStatusSink(db, child.id)

    try:
        from api import chat_delivery

        chat_delivery.try_begin(child.session_id)
        turn = chat_delivery.current_turn(child.session_id)
    except Exception:
        turn = None

    if _cancelled(db, child.id):
        try:
            from api import chat_delivery

            chat_delivery.end(child.session_id, turn=turn)
        except Exception:
            pass
        return {"success": False, "cancelled": True, "response": ""}

    model_id = resolve_child_model_id(spec.agent, spec.model, spec.effort)
    exec_kw = None
    effort = str(spec.effort or "").strip()
    if effort and str(spec.agent or "").strip().lower() != "cursor":
        exec_kw = {"reasoning_effort": effort}

    run = runner or (
        lambda **kw: default_runner(
            agent=kw.get("agent") or spec.agent,
            prompt=kw.get("prompt") or run_prompt,
            session_id=int(kw.get("session_id") or child.session_id),
            project_path=kw.get("project_path") or project_path,
            model_override=kw.get("model_override") or model_id,
            execute_kwargs=kw.get("execute_kwargs") or exec_kw,
            status_queue=sink,
        )
    )
    try:
        result = run(
            agent=spec.agent,
            prompt=run_prompt,
            session_id=int(child.session_id),
            project_path=project_path,
            spec=spec,
            child=child,
            model_override=model_id,
            execute_kwargs=exec_kw,
        ) or {}
    except Exception as exc:
        result = {
            "success": False,
            "error": str(exc),
            "response": f"Subagent failed: {exc}",
        }
    finally:
        sink.close()
        try:
            from api import chat_delivery

            chat_delivery.end(child.session_id, turn=turn)
        except Exception:
            pass

    if _cancelled(db, child.id):
        return {"success": False, "cancelled": True, "response": ""}

    if not isinstance(result, dict):
        result = {"success": True, "response": str(result)}

    # The kernel reports query_id on the returned body; fall back to the sink
    # for turns whose reply text came from somewhere other than that result.
    if sink.query_id and not result.get("query_id"):
        result["query_id"] = sink.query_id
        result["report_url"] = f"/query_log.html?id={sink.query_id}"

    # Backfill what the turn actually ran onto the child row (the fleet
    # source of truth) when the spawn named none — agent-only children used
    # to keep NULL model/effort forever, so fleet tips stayed bare too.
    try:
        from api.subagents.identity import executed_harness

        exec_model, exec_effort = executed_harness(result, child.agent)
        row_patch: Dict[str, str] = {}
        if not (child.model or "").strip() and exec_model:
            row_patch["model"] = exec_model
        if not (child.effort or "").strip() and exec_effort:
            row_patch["effort"] = exec_effort
        if row_patch:
            store.update_child(db, child.id, **row_patch)
    except Exception:
        pass

    text = str(result.get("response") or result.get("output") or "").strip()
    already = store.latest_assistant_text(db, child.session_id)
    if text and text != already:
        try:
            _persist_assistant(db, child.session_id, result, child=child, spec=spec)
        except Exception:
            pass
    if not text:
        text = store.latest_assistant_text(db, child.session_id)

    if result.get("cancelled") or str(text).startswith("[CANCELLED]"):
        store.update_child(
            db, child.id, status=STATUS_CANCELLED, result=text, finished=True
        )
        return result

    ok = result.get("success", True) and not result.get("error")
    store.update_child(
        db,
        child.id,
        status=STATUS_DONE if ok else STATUS_FAILED,
        result=text,
        error=str(result.get("error") or "") if not ok else "",
        finished=True,
    )
    return result


def run_child_turn_async(
    db,
    child: ChildRecord,
    spec: Optional[ChildSpec] = None,
    **kwargs: Any,
) -> threading.Thread:
    def _target():
        try:
            run_child_turn(db, child, spec, **kwargs)
        except Exception as exc:
            try:
                store.update_child(
                    db,
                    child.id,
                    status=STATUS_FAILED,
                    error=str(exc),
                    finished=True,
                )
            except Exception:
                pass

    thread = threading.Thread(target=_target, daemon=True, name=f"subagent-{child.id[:8]}")
    thread.start()
    return thread
