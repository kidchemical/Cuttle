"""Run one turn in a child chat session (harness CLI, persist both sides)."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

from api.subagents import store
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


def default_runner(
    *,
    agent: str,
    prompt: str,
    session_id: int,
    project_path: str = "",
    model_override: str = "",
    execute_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from api.agent_harness.kernel import run_agent_web_command

    kw: Dict[str, Any] = {}
    if model_override:
        kw["model_override"] = model_override
    if execute_kwargs:
        kw["execute_kwargs"] = execute_kwargs
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
        meta["slash_command"] = child_slash_command(spec, child)
        try:
            from api.chat_metadata import usage_meta_from_assistant_result

            _res = result if isinstance(result, dict) else {}
            _cursor_run = _res.get("cursor_run")
            if isinstance(_cursor_run, dict) and _cursor_run and not meta.get("cursor_run"):
                meta["cursor_run"] = _cursor_run
            _usage = usage_meta_from_assistant_result(_res)
            if _usage:
                meta["usage"] = _usage
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
        try:
            from api import chat_delivery

            chat_delivery.end(child.session_id, turn=turn)
        except Exception:
            pass

    if _cancelled(db, child.id):
        return {"success": False, "cancelled": True, "response": ""}

    if not isinstance(result, dict):
        result = {"success": True, "response": str(result)}

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
