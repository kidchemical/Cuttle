"""Shared web/Discord runner — one implementation for every harness agent."""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Dict, List, Optional

from api.agent_harness.catalog import get_agent
from api.agent_harness.cwd import constrain_to_project, resolve_harness_cwd
from api.agent_harness.types import AgentManifest, AgentResult, normalize_chat_session_id


_CLEAR_TOKENS = frozenset(
    {"new", "new session", "reset", "clear", "clear session"}
)

# CH-000513: Stop killed the parent CLI but the session store still had an
# active writer (often an orphaned helper). Resuming that thread fails until
# the orphan is gone — wait and retry the *same* resume; never mint a new session.
_STALE_SESSION_WRITER_MARKERS = (
    "active writer",
    "thread-store conflict",
    "thread/resume failed",
    "already has an active writer",
)


def _looks_like_stale_session_writer(result: Optional[AgentResult]) -> bool:
    if result is None or result.success:
        return False
    blob = f"{result.output or ''}\n{result.error or ''}".lower()
    return any(m in blob for m in _STALE_SESSION_WRITER_MARKERS)


def _put_status(status_queue: Any, message: str) -> None:
    if status_queue is None:
        return
    try:
        status_queue.put(("status", message))
    except Exception:
        pass


def _adapter_execute_kwargs(adapter: Any, **kwargs: Any) -> Dict[str, Any]:
    """Drop kwargs the adapter ``execute()`` does not accept (except ``**kwargs``)."""
    try:
        sig = inspect.signature(adapter.execute)
    except (TypeError, ValueError):
        return dict(kwargs)
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in params}


def _should_inject_capabilities(manifest: AgentManifest, *, has_resume: bool) -> bool:
    mode = (manifest.capabilities_inject or "once_per_resume").strip().lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    # once_per_resume (default): skip when the same CLI session already has the briefing.
    return not has_resume


def _fallback_chat_cwd() -> str:
    """Registered Cuttle project when the request/session has no chip."""
    try:
        from managers.project_manager import (
            REPO_ROOT,
            default_chat_cwd,
            project_manager as _pm,
        )

        return str(default_chat_cwd(_pm, REPO_ROOT) or "").strip()
    except Exception:
        return ""


def _run_was_cancelled(chat_session_id: Any) -> bool:
    if not chat_session_id:
        return False
    try:
        from api.chat_run_registry import is_run_cancelled

        if is_run_cancelled(chat_session_id):
            return True
    except Exception:
        pass
    try:
        from api.chat_delivery import is_turn_cancelled

        return is_turn_cancelled(chat_session_id)
    except Exception:
        return False


def _cancelled_web_result(
    chat_session_id: Any,
    *,
    query_id: Optional[str],
    type_err: str,
    agent_id: str,
    label: str,
) -> Dict[str, Any]:
    report_url = f"/query_log.html?id={query_id}" if query_id else None
    out: Dict[str, Any] = {
        "success": True,
        "response": f"[CANCELLED] {label} run was cancelled (chat deleted or stopped).",
        "session_id": chat_session_id,
        "type": type_err,
        "agent_id": agent_id,
    }
    if query_id:
        out["query_id"] = query_id
    if report_url:
        out["report_url"] = report_url
    return out


def _resolve_agent_cwd(adapter, project_path: Optional[str]) -> str:
    """Chip path wins for every agent. Adapters may only refine within that project.

    Cursor used to pin ``--workspace`` to whichever folder last saved a
    ``--resume`` UUID for the chat, so an Escape Purgatory chip still launched
    inside Cuttle (CH-000164). Future adapters that fall back to process cwd
    hit the same trap — constrain here so they cannot.
    """
    base = resolve_harness_cwd(
        project_path,
        fallback=None if (project_path or "").strip() else _fallback_chat_cwd(),
    )
    refine = getattr(adapter, "resolve_cwd", None)
    if not callable(refine):
        return base
    try:
        candidate = refine(str(base))
    except Exception:
        return base
    return constrain_to_project(base, str(candidate or base))


def _usage_from_result(usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize adapter usage for query logs, web responses, and router outcomes."""
    u = usage or {}
    pt = int(
        u.get("prompt_tokens")
        or u.get("input_tokens")
        or u.get("inputTokens")
        or 0
    )
    ct = int(
        u.get("completion_tokens")
        or u.get("output_tokens")
        or u.get("outputTokens")
        or 0
    )
    total = int(u.get("total_tokens") or u.get("totalTokens") or 0)
    if total <= 0 and (pt or ct):
        total = pt + ct
    cost = u.get("cost")
    try:
        cost_f = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost_f = None
    payload: Dict[str, Any] = {}
    if total > 0 or pt or ct:
        payload = {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": total,
        }
    # Preserve cache + context peaks (Cursor/Codex/OpenCode/…).
    for src_keys, dst in (
        (
            ("cache_read_tokens", "cacheReadTokens", "cached_input_tokens", "cached_tokens"),
            "cache_read_tokens",
        ),
        (
            ("cache_write_tokens", "cacheWriteTokens", "cache_write_input_tokens"),
            "cache_write_tokens",
        ),
        (("context_tokens", "contextTokens"), "context_tokens"),
        (("peak_context_tokens",), "peak_context_tokens"),
    ):
        for key in src_keys:
            if u.get(key) is None:
                continue
            try:
                n = int(u.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if n > 0:
                payload[dst] = n
            break
    if cost_f is not None and cost_f >= 0:
        payload["cost"] = cost_f
    return payload


def _attach_usage_to_web_response(out: Dict[str, Any], usage: Optional[Dict[str, Any]]) -> None:
    payload = _usage_from_result(usage)
    if not payload:
        return
    out["usage"] = payload
    if payload.get("cost") is not None:
        out["cost"] = payload["cost"]


def _compile_agent_prompt(
    manifest: AgentManifest,
    prompt: str,
    *,
    cwd: str,
    chat_session_id: Optional[str],
    has_resume: bool,
) -> tuple:
    """Build the outbound prompt via Context Compiler + hot-swap handoff + context delta.

    Returns ``(prompt_text, brain_meta)``.
    """
    from api.cuttle_brain.context_compiler import _USER_REQUEST_HEADER

    handoff = None
    try:
        from api.cuttle_brain.handoff import build_handoff

        handoff = build_handoff(chat_session_id, to_agent=manifest.id)
    except Exception:
        handoff = None

    full_layers = not has_resume

    if full_layers:
        inject_caps = _should_inject_capabilities(manifest, has_resume=False)
        try:
            from api.cuttle_brain.context_compiler import compile_context
            from api.cuttle_brain.context_delta import record_injected_snapshot

            compiled = compile_context(
                prompt,
                project_path=cwd,
                profile="standard",
                inject_capabilities=inject_caps,
                include_rules=True,
                include_profile=True,
                include_inventory=True,
                handoff=handoff,
                include_chat_store_hint=True,
                wsl=(manifest.env_profile or "").strip().lower() == "wsl",
                chat_session_id=chat_session_id,
            )
            record_injected_snapshot(chat_session_id, manifest.id, cwd)
            meta = dict(compiled.meta or {})
            inv = meta.get("inventory") if isinstance(meta.get("inventory"), dict) else {}
            brain = {
                "mode": "full",
                "layers": list(compiled.layers_used or []),
                "envelope_chars": len(compiled.envelope or ""),
                "prompt_chars": len(compiled.prompt or ""),
                "rules_count": meta.get("rules_count"),
                "global_rules_count": meta.get("global_rules_count"),
                "inventory": {
                    "commands": list(inv.get("commands") or [])[:40],
                    "docs": list(inv.get("docs") or [])[:40],
                    "actions": list(inv.get("actions") or [])[:40],
                    "rules": list(inv.get("rules") or [])[:40],
                },
                "handoff_from": meta.get("handoff_from"),
                "handoff_to": meta.get("handoff_to"),
            }
            return compiled.prompt, brain
        except Exception:
            if handoff and handoff.text.strip():
                text = f"{handoff.text.strip()}\n\n{_USER_REQUEST_HEADER}\n{prompt}"
                return text, {"mode": "fallback_handoff", "prompt_chars": len(text)}
            try:
                from api.cuttle_ui_capabilities import with_cuttle_ui_capabilities

                text = with_cuttle_ui_capabilities(prompt, inject=True)
                return text, {"mode": "fallback_caps", "prompt_chars": len(text)}
            except Exception:
                return prompt, {"mode": "fallback_bare", "prompt_chars": len(prompt or "")}

    # Resumed session — delta + optional handoff, never full re-inject.
    parts: List[str] = []
    delta_text = ""
    try:
        from api.cuttle_brain.context_delta import build_resume_delta

        delta = build_resume_delta(chat_session_id, manifest.id, cwd)
        if delta:
            parts.append(delta)
            delta_text = delta
    except Exception:
        pass
    if handoff and handoff.text.strip():
        parts.append(handoff.text.strip())
    if parts:
        body = "\n\n".join(parts)
        text = f"{body}\n\n{_USER_REQUEST_HEADER}\n{prompt}"
        return text, {
            "mode": "resume_delta" if delta_text else "resume_handoff",
            "prompt_chars": len(text),
            "delta_chars": len(delta_text),
            "handoff_from": getattr(handoff, "from_agent", None) if handoff else None,
        }
    return prompt, {"mode": "resume", "prompt_chars": len(prompt or "")}


def run_agent_web_command(
    agent_id: str,
    prompt: str,
    chat_session_id: Any,
    *,
    status_queue=None,
    project_path: Optional[str] = None,
    model_override: Optional[str] = None,
    timeout: float = 3600.0,
    execute_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Shared kernel used by ``/api/chat``, Discord, and the agent router.

    Returns the same shape as legacy ``_run_*_web_command`` helpers so callers
    can ``jsonify`` without special-casing.

    ``timeout`` is the turn budget. Streaming adapters (e.g. OpenCode) treat it
    as an **idle** budget — the deadline resets whenever the CLI emits output,
    with an absolute runaway cap on top — so long productive turns are not
    killed. Buffer-everything adapters keep the absolute wall-clock semantics.

    ``execute_kwargs`` are forwarded to ``adapter.execute`` (e.g. Codex
    ``reasoning_effort`` from supervised profiles).
    """
    pair = get_agent(agent_id, project_path=project_path)
    if not pair:
        return {
            "success": True,
            "response": f"❌ Unknown harness agent `{agent_id}`.",
            "session_id": chat_session_id,
            "type": "harness_error",
        }

    manifest, adapter = pair
    slash = manifest.slash_prefix().rstrip()
    type_ok = f"{manifest.id}_command"
    type_err = f"{manifest.id}_error"
    label = manifest.label or manifest.id
    # Coerce once at the harness boundary (DB ints → opaque str). Adapters see Optional[str].
    sid = normalize_chat_session_id(chat_session_id)

    from api.query_tracker import (
        finish_query_tracking,
        get_query_tracker,
        start_query_tracking,
    )
    from api.active_executions import register_execution, unregister_execution

    query_id = None
    cancel_event = None
    qid_token = None
    try:
        from api.query_events import QueryStatusTee, bind_query_id, reset_query_id

        user_ctx = {"web_ui": True, "slash_command": slash}
        _put_status(status_queue, f"Starting {label}…")
        query_id = start_query_tracking(f"{slash} {prompt}", user_ctx)
        qid_token = bind_query_id(query_id)
        if status_queue is not None:
            status_queue = QueryStatusTee(status_queue)
        try:
            from api.chat_run_registry import begin_run

            cancel_event = begin_run(chat_session_id, query_id)
        except Exception:
            cancel_event = None
        if _run_was_cancelled(chat_session_id):
            try:
                finish_query_tracking(success=False, error_message="cancelled")
            except Exception:
                pass
            return _cancelled_web_result(
                chat_session_id,
                query_id=query_id,
                type_err=type_err,
                agent_id=manifest.id,
                label=label,
            )
        register_execution(query_id, label)
        if status_queue:
            try:
                status_queue.put(
                    (
                        "query_started",
                        {
                            "query_id": query_id,
                            "report_url": f"/query_log.html?id={query_id}",
                        },
                    )
                )
            except Exception:
                pass

        # ``/cost`` is a pricing lookup, not a CLI turn: answer it for every
        # harness before install/availability gates or the adapter's meta hook.
        from api.agent_cost import handle_agent_cost_slash, parse_cost_slash

        if parse_cost_slash(prompt) is not None:
            cost_md = handle_agent_cost_slash(
                manifest,
                prompt,
                chat_session_id=sid,
                cwd=_resolve_agent_cwd(adapter, project_path),
                model_override=(str(model_override).strip() if model_override else None),
            )
            finish_query_tracking(success=True)
            return {
                "success": True,
                "response": cost_md or f"❌ **{label}:** pricing unavailable.",
                "session_id": chat_session_id,
                "type": type_ok,
                "query_id": query_id,
                "report_url": f"/query_log.html?id={query_id}",
                "agent_id": manifest.id,
                "meta_command": True,
            }

        if not adapter.available() and manifest.auto_install:
            if _run_was_cancelled(chat_session_id):
                try:
                    finish_query_tracking(success=False, error_message="cancelled")
                except Exception:
                    pass
                return _cancelled_web_result(
                    chat_session_id,
                    query_id=query_id,
                    type_err=type_err,
                    agent_id=manifest.id,
                    label=label,
                )
            _put_status(status_queue, f"Installing {label} CLI…")
            from api.agent_harness.installer import install_agent_cli

            install = install_agent_cli(
                manifest.id,
                project_path=project_path,
                automatic=True,
            )
            if not install.get("success"):
                message = str(install.get("message") or "CLI installation failed.")
                finish_query_tracking(success=False, error_message=message[:500])
                return {
                    "success": True,
                    "response": f"❌ **{label} setup failed.** {message}",
                    "session_id": chat_session_id,
                    "type": type_err,
                    "query_id": query_id,
                    "report_url": f"/query_log.html?id={query_id}",
                    "agent_id": manifest.id,
                    "setup_status": install.get("status"),
                }
            _put_status(status_queue, f"{label} installed; checking authentication…")

        if not adapter.available():
            hint = (
                manifest.missing_cli_hint
                or manifest.install_hint
                or f"{label} CLI not found on PATH."
            )
            finish_query_tracking(success=False, error_message="CLI not found")
            return {
                "success": True,
                "response": f"❌ **{label} not found.** {hint}",
                "session_id": chat_session_id,
                "type": type_err,
                "query_id": query_id,
                "report_url": f"/query_log.html?id={query_id}",
                "agent_id": manifest.id,
            }

        cwd = _resolve_agent_cwd(adapter, project_path)

        low_p = (prompt or "").strip().lower()
        # Policy (CH-000419): the kernel never invents a model. Adapters own
        # the full chain — per-chat pin → explicit override → starred default
        # → CLI default — so an unpinned turn omits the flag and the CLI wins.
        # ``manifest.default_model`` is documentation/fallback only.
        model = (str(model_override).strip() if model_override else "") or None
        if manifest.resume and low_p in _CLEAR_TOKENS:
            adapter.clear_resume(cwd, sid)
            try:
                from api.cuttle_brain.context_delta import clear_injected_snapshot

                clear_injected_snapshot(sid, manifest.id, cwd)
            except Exception:
                pass
            finish_query_tracking(success=True)
            return {
                "success": True,
                "response": (
                    f"**{label}:** Session cleared. "
                    f"Next `{slash}` message will start a fresh conversation."
                ),
                "session_id": chat_session_id,
                "type": type_ok,
                "query_id": query_id,
                "report_url": f"/query_log.html?id={query_id}",
                "agent_id": manifest.id,
                "agent_model": model or manifest.id,
                "meta_command": True,
            }

        t0 = time.time()
        # Optional native meta-command hook runs on the raw prompt. This keeps
        # `/muse model …` / Cursor `/model` (and future agent controls) out of
        # Context Compiler. Pass cwd when the handler accepts it (Cursor prefs).
        meta_handler = getattr(adapter, "handle_meta", None)
        result: Optional[AgentResult] = None
        resume = None
        if callable(meta_handler):
            try:
                result = meta_handler(
                    prompt, chat_session_id=sid, model=model, cwd=cwd
                )
            except TypeError:
                result = meta_handler(prompt, chat_session_id=sid, model=model)
        handled_by_meta = result is not None

        if result is None:
            # Prefer this agent's own native resume (even after a hot-swap). Handoff
            # delta is layered by the Context Compiler when the sticky agent changed.
            resume = adapter.load_resume(cwd, sid) if manifest.resume else None
            compiled = _compile_agent_prompt(
                manifest,
                prompt,
                cwd=cwd,
                chat_session_id=sid,
                has_resume=bool(resume),
            )
            if isinstance(compiled, tuple):
                agent_prompt = compiled[0]
                brain_meta = compiled[1] if len(compiled) > 1 and isinstance(compiled[1], dict) else {}
            else:
                agent_prompt = compiled
                brain_meta = {}
            try:
                tr = get_query_tracker(query_id)
                if tr and tr.query_id == query_id:
                    tr.set_harness(
                        {
                            "agent_id": manifest.id,
                            "label": label,
                            "slash": slash,
                            "cwd": cwd,
                            "resume": resume,
                            "model": model,
                            "chat_session_id": sid,
                        }
                    )
                    tr.set_brain(brain_meta or {})
                    tr.add_event(
                        "brain",
                        {
                            "mode": (brain_meta or {}).get("mode"),
                            "layers": (brain_meta or {}).get("layers") or [],
                            "prompt_chars": (brain_meta or {}).get("prompt_chars"),
                            "delta_chars": (brain_meta or {}).get("delta_chars"),
                        },
                    )
                    tr.set_sent(agent_prompt, resume=bool(resume))
            except Exception:
                pass
            if _run_was_cancelled(chat_session_id) or _run_was_cancelled(sid):
                try:
                    finish_query_tracking(success=False, error_message="cancelled")
                except Exception:
                    pass
                return _cancelled_web_result(
                    chat_session_id,
                    query_id=query_id,
                    type_err=type_err,
                    agent_id=manifest.id,
                    label=label,
                )
            # Observed edit attribution: snapshot before the CLI run so only
            # digest deltas during this turn are journaled (no session guessing).
            edit_baseline = None
            try:
                from api.edit_attribution.recorder import snapshot_for_attribution

                edit_baseline = snapshot_for_attribution(cwd)
            except Exception:
                edit_baseline = None
            def _run_execute(prompt_text: str, resume_id: Optional[str]) -> AgentResult:
                return asyncio.run(
                    adapter.execute(
                        prompt_text,
                        **_adapter_execute_kwargs(
                            adapter,
                            cwd=cwd,
                            resume=resume_id,
                            model=model,
                            status_queue=status_queue,
                            chat_session_id=sid,
                            timeout=timeout,
                            cancel_event=cancel_event,
                            **(execute_kwargs or {}),
                        ),
                    )
                )

            try:
                result = _run_execute(agent_prompt, resume)
                # Stop→followup race (CH-000513): prior thread still has a writer.
                # Drain orphans, wait, retry the *same* resume — never start a new
                # CLI session (secret/context must survive Stop).
                if (
                    manifest.resume
                    and resume
                    and isinstance(result, AgentResult)
                    and _looks_like_stale_session_writer(result)
                    and not _run_was_cancelled(chat_session_id)
                    and not _run_was_cancelled(sid)
                ):
                    from api.chat_run_registry import ensure_session_procs_dead

                    for attempt in range(6):
                        _put_status(
                            status_queue,
                            f"{label}: waiting for prior session to release "
                            f"(attempt {attempt + 1}/6)…",
                        )
                        ensure_session_procs_dead(chat_session_id, timeout=5.0)
                        time.sleep(0.4 * (attempt + 1))
                        result = _run_execute(agent_prompt, resume)
                        if not _looks_like_stale_session_writer(result):
                            break
            finally:
                try:
                    from api.edit_attribution.recorder import record_run_deltas

                    record_run_deltas(
                        edit_baseline,
                        cwd=cwd,
                        agent_id=manifest.id,
                        agent_model=model,
                        result_model=(
                            result.model if isinstance(result, AgentResult) else None
                        ),
                        result_meta=(
                            result.meta
                            if isinstance(result, AgentResult)
                            and isinstance(result.meta, dict)
                            else None
                        ),
                        query_id=query_id,
                        chat_session_id=sid,
                    )
                except Exception as _attr_exc:
                    print(
                        f"[edit_attribution] kernel record error: {_attr_exc}",
                        flush=True,
                    )

        # Persist resume whenever the CLI minted a session id — including
        # timeout/cancel — so the next turn can continue that transcript.
        if manifest.resume and result.session_id:
            adapter.save_resume(cwd, sid, result.session_id)
        if result.success:
            try:
                from api.cuttle_brain.handoff import record_last_agent

                record_last_agent(sid, manifest.id)
            except Exception:
                pass

        tracker = get_query_tracker(query_id)
        usage_payload = _usage_from_result(result.usage)
        pt = int(usage_payload.get("prompt_tokens") or 0)
        ct = int(usage_payload.get("completion_tokens") or 0)
        total_tok = int(usage_payload.get("total_tokens") or 0)
        tok_payload = (
            {
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": total_tok,
            }
            if total_tok > 0
            else {}
        )
        if tok_payload:
            for key in (
                "cache_read_tokens",
                "cache_write_tokens",
                "context_tokens",
                "peak_context_tokens",
            ):
                if usage_payload.get(key):
                    tok_payload[key] = int(usage_payload[key])
        call_cost = float(usage_payload.get("cost") or 0) if usage_payload.get("cost") is not None else 0.0
        _result_meta = result.meta if isinstance(result.meta, dict) else {}
        if result.model:
            model_name = result.model
        elif _result_meta.get("model_source") == "unknown":
            # The adapter could not determine the executed model — record
            # unknown honestly instead of the harness id (e.g. "opencode").
            model_name = "unknown"
        else:
            model_name = model or manifest.id
        preview = (result.output or "")[:8000]
        report_url = f"/query_log.html?id={query_id}"
        tool_params = {
            "prompt_preview": (prompt or "")[:2000],
            "cwd": cwd,
            "resume_id": resume,
            "model": model_name,
            "agent_id": manifest.id,
        }

        if result.success:
            if tracker and tracker.query_id == query_id and tracker.execution_data:
                tracker.add_tool_call(
                    label,
                    tool_params,
                    t0,
                    time.time(),
                    success=True,
                    result_preview=preview,
                    model=model_name if total_tok > 0 else None,
                    tokens=tok_payload if total_tok > 0 else None,
                    cost=call_cost,
                )
            finish_query_tracking(success=True)
            out = {
                "success": True,
                "response": result.output or "Done.",
                "session_id": chat_session_id,
                "type": type_ok,
                "query_id": query_id,
                "report_url": report_url,
                "agent_id": manifest.id,
                "agent_model": model_name,
            }
            if handled_by_meta:
                out["meta_command"] = True
            _attach_usage_to_web_response(out, result.usage)
            # Adapters may attach UI extras (Cursor /model preferred_model, Muse pin, …).
            if isinstance(result.meta, dict):
                for key, val in result.meta.items():
                    if key and val is not None and key not in out:
                        out[key] = val
            # Drift guard: an explicit per-turn override that the run did not
            # honor is a badge-lie — log it canonically and flag the response.
            try:
                if model and result.model and result.model.strip() != model.strip():
                    from api.chat_warnings import record_model_drift

                    entry = record_model_drift(
                        sid or chat_session_id,
                        agent_id=manifest.id,
                        expected=model,
                        actual=result.model,
                        source=(result.meta or {}).get("model_source")
                        if isinstance(result.meta, dict) else None,
                    )
                    if entry:
                        out["drift_warning"] = entry["message"]
            except Exception:
                pass
            return out

        err = (result.output or "").strip() or (result.error or "").strip() or "Unknown error"
        if tracker and tracker.query_id == query_id and tracker.execution_data:
            tracker.add_tool_call(
                label,
                tool_params,
                t0,
                time.time(),
                success=False,
                result_preview=preview or str(err)[:2000],
                model=None,
                tokens=None,
                cost=0.0,
            )
        finish_query_tracking(
            success=False,
            error_message=str(result.error or err)[:500],
        )
        out = {
            "success": True,
            "response": str(err) or "Unknown error",
            "session_id": chat_session_id,
            "type": type_err,
            "query_id": query_id,
            "report_url": report_url,
            "agent_id": manifest.id,
            "agent_model": model_name,
        }
        if handled_by_meta:
            out["meta_command"] = True
        _attach_usage_to_web_response(out, result.usage)
        return out
    except Exception as exc:
        try:
            if query_id:
                finish_query_tracking(success=False, error_message=str(exc)[:500])
        except Exception:
            pass
        report_url = f"/query_log.html?id={query_id}" if query_id else None
        return {
            "success": True,
            "response": f"❌ **{label}:** {exc}",
            "session_id": chat_session_id,
            "type": type_err,
            "agent_id": manifest.id,
            **({"query_id": query_id, "report_url": report_url} if query_id else {}),
        }
    finally:
        if chat_session_id:
            try:
                from api.chat_run_registry import end_run

                end_run(chat_session_id, query_id)
            except Exception:
                pass
        if query_id:
            try:
                unregister_execution(query_id)
            except Exception:
                pass
        if qid_token is not None:
            try:
                from api.query_events import reset_query_id

                reset_query_id(qid_token)
            except Exception:
                pass
