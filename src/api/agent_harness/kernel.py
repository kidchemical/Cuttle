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


# Agent-visible markers for context fallbacks (CH-000999 F07). Banners carry
# stage wording only — never exception text, which may contain paths, keys,
# or other secrets. Diagnostics carry stage + exception type for the same
# reason (see _context_error).
_CTX_BRIEFING_NOTICE = (
    "[Cuttle context notice: compiled project briefing unavailable, so "
    "project rules/context may be incomplete. Follow the request as "
    "written and do not assume project conventions.]"
)
_CTX_HANDOFF_NOTICE = (
    "[Cuttle context notice: Cuttle could not transfer recent transcript "
    "context; some earlier discussion may be missing. Proceed from the "
    "context actually present.]"
)


def _context_error(stage: str, exc: Exception) -> str:
    """Bounded stage + exception-type tag for context diagnostics.

    The raw message is deliberately excluded: it may carry file paths,
    secret keys, or other sensitive material.
    """
    name = type(exc).__name__ or "Error"
    return f"{stage}: {name}"


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


def _record_context_metrics(
    agent_id: str,
    *,
    sid: Optional[str],
    cwd: str,
    model: Optional[str],
    brain_meta: Dict[str, Any],
    result: Any,
    compacted: bool,
    query_id: Optional[str],
) -> None:
    """One context-metrics row per CLI turn (Context dashboard). Never raises."""
    try:
        from api.agent_context import _usage_context_fill_tokens, resolve_context_limit
        from api.cuttle_brain.metrics import record_turn

        usage = result.usage if isinstance(getattr(result, "usage", None), dict) else {}
        fill, _src = _usage_context_fill_tokens(usage, agent_id=agent_id)
        limit = None
        if fill:
            try:
                limit, _lsrc = resolve_context_limit(agent_id, model)
            except Exception:
                limit = None
        record_turn(
            chat_session_id=sid,
            agent_id=agent_id,
            model=model,
            project_path=cwd,
            mode=brain_meta.get("mode"),
            full_reason=brain_meta.get("full_reason"),
            prompt_chars=brain_meta.get("prompt_chars"),
            envelope_chars=brain_meta.get("envelope_chars"),
            delta_chars=brain_meta.get("delta_chars"),
            handoff_chars=(
                brain_meta.get("handoff_chars")
                if brain_meta.get("handoff_chars") is not None
                else (brain_meta.get("layer_chars") or {}).get("handoff")
            ),
            handoff_messages=brain_meta.get("handoff_messages"),
            layer_chars=brain_meta.get("layer_chars"),
            context_tokens=fill,
            context_limit=limit,
            compacted=compacted,
            success=bool(getattr(result, "success", False)),
            query_id=query_id,
        )
    except Exception as exc:
        print(f"[kernel] context metrics error: {exc}", flush=True)


def _compile_agent_prompt(
    manifest: AgentManifest,
    prompt: str,
    *,
    cwd: str,
    chat_session_id: Optional[str],
    has_resume: bool,
) -> tuple:
    """Build the outbound prompt via Context Compiler + hot-swap handoff + context delta.

    Returns ``(prompt_text, brain_meta, receipt)`` where ``receipt`` is the
    exact ``ContextSnapshot`` the outbound text describes (None when nothing
    new was sent). The receipt is private delivery bookkeeping: callers must
    acknowledge it only after the prompt is successfully delivered — never
    at compile time and never recomputed — so failed, cancelled, or
    interrupted turns keep their notices pending and mid-turn edits are not
    swallowed. Keep it out of query-log metadata and sent payloads.
    """
    from api.cuttle_brain.context_compiler import _USER_REQUEST_HEADER

    handoff = None
    handoff_error: Optional[str] = None
    try:
        from api.cuttle_brain.handoff import build_handoff

        # Agents without native resume remember nothing between turns, so
        # they get the recent conversation every turn, not just on a switch.
        handoff = build_handoff(
            chat_session_id,
            to_agent=manifest.id,
            current_prompt=prompt,
            full_history=not manifest.resume,
        )
    except Exception as exc:
        # A failed handoff means the agent misses unseen conversation, not
        # that it is caught up — keep stage + type for diagnostics and mark
        # it undelivered below. The raw message is never kept.
        handoff = None
        handoff_error = _context_error("handoff", exc)

    # Operator diagnostics for this turn (stage + type only). Snapshot
    # bookkeeping failures land here too: on their own they mean the full
    # envelope was still sent, so they never imply missing context.
    context_errors: List[str] = []
    if handoff_error is not None:
        context_errors.append(handoff_error)
        print(f"[kernel] context {handoff_error}", flush=True)

    def _with_diagnostics(
        text: str,
        brain: Dict[str, Any],
        receipt: Any,
        *,
        briefing_missing: bool = False,
        handoff_delivered: bool = True,
    ) -> tuple:
        """Attach notices + error signal without changing availability.

        ``handoff_delivered`` is True when the handoff text rides in the
        sent prompt (or nothing was unseen); a failed handoff build always
        counts as undelivered. Callers use the returned flag to hold the
        handoff seen-cursor so missed conversation is retried, not skipped.
        """
        notices = []
        if briefing_missing:
            notices.append(_CTX_BRIEFING_NOTICE)
        if handoff_error is not None or not handoff_delivered:
            notices.append(_CTX_HANDOFF_NOTICE)
        final_brain = dict(brain)
        if context_errors:
            final_brain["context_errors"] = list(context_errors)
        if notices:
            final_brain["degraded"] = True
            text = "\n\n".join(notices) + "\n\n" + text
        # prompt_chars always describes the exact bytes handed to the CLI,
        # including any banners above.
        final_brain["prompt_chars"] = len(text)
        final_brain["handoff_delivered"] = bool(
            handoff_delivered and handoff_error is None
        )
        return text, final_brain, receipt

    def _full_envelope(reason=None):
        """Fresh full briefing plus the exact snapshot it sends (ack after delivery).

        The snapshot is captured before and after compilation: a context
        file can change between the reads, and only a stable state proves
        the receipt represents the compiled bytes. When preparation raced
        an edit, the envelope is still sent but the receipt is None, so a
        successful turn acknowledges nothing and the notice stays pending.
        """
        inject_caps = _should_inject_capabilities(manifest, has_resume=False)
        # Receipt bookkeeping is optional and isolated: a snapshot I/O
        # failure must never prevent an otherwise successful compilation.
        try:
            from api.cuttle_brain.context_delta import compute_snapshot

            stable_before = compute_snapshot(cwd)
        except Exception as exc:
            # Bookkeeping only: the envelope below still carries the full
            # briefing, so this alone is an operator diagnostic, never a
            # missing-context notice.
            stable_before = None
            entry = _context_error("snapshot_before", exc)
            context_errors.append(entry)
            print(f"[kernel] context {entry}", flush=True)
        try:
            from api.cuttle_brain.context_compiler import compile_context

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
                chat_session_id=chat_session_id,
            )
        except Exception as exc:
            entry = _context_error("compile", exc)
            context_errors.append(entry)
            print(f"[kernel] context {entry}", flush=True)
            if handoff and handoff.text.strip():
                text = f"{handoff.text.strip()}\n\n{_USER_REQUEST_HEADER}\n{prompt}"
                # Handoff text rides along: conversation is delivered even
                # though the governing briefing is missing.
                return _with_diagnostics(
                    text,
                    {"mode": "fallback_handoff", "prompt_chars": len(text)},
                    None,
                    briefing_missing=True,
                    handoff_delivered=True,
                )
            try:
                from api.cuttle_ui_capabilities import with_cuttle_ui_capabilities

                text = with_cuttle_ui_capabilities(prompt, inject=True)
                return _with_diagnostics(
                    text,
                    {"mode": "fallback_caps", "prompt_chars": len(text)},
                    None,
                    briefing_missing=True,
                    handoff_delivered=(handoff is None),
                )
            except Exception as caps_exc:
                caps_entry = _context_error("capabilities", caps_exc)
                context_errors.append(caps_entry)
                print(f"[kernel] context {caps_entry}", flush=True)
                return _with_diagnostics(
                    prompt,
                    {
                        "mode": "fallback_bare",
                        "prompt_chars": len(prompt or ""),
                    },
                    None,
                    briefing_missing=True,
                    handoff_delivered=(handoff is None),
                )
        try:
            from api.cuttle_brain.context_delta import compute_snapshot

            prepared = compute_snapshot(cwd)
            if stable_before is None or prepared != stable_before:
                prepared = None
        except Exception as exc:
            prepared = None
            entry = _context_error("snapshot_after", exc)
            context_errors.append(entry)
            print(f"[kernel] context {entry}", flush=True)
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
            "handoff_messages": handoff.message_count if handoff else 0,
            "layer_chars": dict(meta.get("layer_chars") or {}),
        }
        ranked_meta = meta.get("ranked_context")
        if isinstance(ranked_meta, dict):
            # Visibility into the existing selection only: bounded choice,
            # confidence, injected IDs, excerpt and skip/error status. No
            # bodies, receipts, snapshots, or prompt duplication.
            err = ranked_meta.get("error")
            raw_choice = ranked_meta.get("choice")
            brain["ranked"] = {
                "choice": (str(raw_choice)[:120] if raw_choice else None),
                "confidence": ranked_meta.get("confidence"),
                "injected": [str(i)[:120] for i in (ranked_meta.get("injected") or [])][:8],
                "excerpt": bool(ranked_meta.get("excerpt")),
                "skipped": bool(ranked_meta.get("skipped", True)),
                "error": (str(err)[:200] if err else None),
            }
        if reason:
            brain["full_reason"] = reason
        # Full briefing sent: nothing briefing-side is missing. A failed
        # handoff still gets its own notice via _with_diagnostics.
        return _with_diagnostics(
            compiled.prompt, brain, prepared, handoff_delivered=True
        )

    if not has_resume:
        return _full_envelope()

    # Resumed session: only a known acknowledged briefing earns the delta
    # path. A missing or unreadable snapshot means the agent never
    # confirmed this context — send the full briefing, never a bare prompt
    # that assumes it knows the rules. No receipt is fabricated here; the
    # full path prepares its own stability-checked snapshot.
    snapshot_read_error: Optional[str] = None
    try:
        from api.cuttle_brain.context_delta import load_injected_snapshot

        acknowledged = load_injected_snapshot(chat_session_id, manifest.id, cwd)
    except Exception as exc:
        # An unreadable snapshot is not a first briefing: the agent may have
        # confirmed context before, but this turn cannot prove it.
        acknowledged = None
        snapshot_read_error = _context_error("snapshot_read", exc)
    if acknowledged is None:
        if snapshot_read_error is not None:
            context_errors.append(snapshot_read_error)
            print(f"[kernel] context {snapshot_read_error}", flush=True)
            return _full_envelope(reason="snapshot_read_failed")
        return _full_envelope(reason="missing_snapshot")

    # Delta + optional handoff, never a full re-inject. Preparation never
    # acknowledges. A preparation failure falls back to the full briefing
    # (which prepares its own receipt) rather than a bare resume.
    prepare_failed = False
    prepare_error: Optional[str] = None
    plan = None
    try:
        from api.cuttle_brain.context_delta import prepare_resume_delta

        plan = prepare_resume_delta(chat_session_id, manifest.id, cwd)
    except Exception as exc:
        prepare_failed = True
        prepare_error = _context_error("delta_prepare", exc)
    if prepare_failed:
        if prepare_error is not None:
            context_errors.append(prepare_error)
            print(f"[kernel] context {prepare_error}", flush=True)
        return _full_envelope(reason="delta_prepare_failed")
    if plan is not None and plan.truncated:
        # A truncated delta withholds instructions: never acknowledge it as
        # delivered — fall back to the full briefing (ack its snapshot).
        return _full_envelope(reason="truncated_delta")
    parts: List[str] = []
    delta_text = plan.text if plan is not None else ""
    if delta_text:
        parts.append(delta_text)
    if handoff and handoff.text.strip():
        parts.append(handoff.text.strip())
    if parts:
        body = "\n\n".join(parts)
        text = f"{body}\n\n{_USER_REQUEST_HEADER}\n{prompt}"
        # Handoff/delta text rides along when present, so conversation is
        # delivered; a failed handoff build still earns its own notice.
        return _with_diagnostics(
            text,
            {
                "mode": "resume_delta" if delta_text else "resume_handoff",
                "prompt_chars": len(text),
                "delta_chars": len(delta_text),
                "handoff_chars": len(handoff.text.strip()) if handoff else 0,
                "handoff_messages": handoff.message_count if handoff else 0,
                "handoff_from": getattr(handoff, "from_agent", None)
                if handoff
                else None,
            },
            (plan.snapshot if plan is not None else None),
            handoff_delivered=True,
        )
    # Bare resume: nothing new to deliver, so no receipt — but a failed
    # handoff still means unseen conversation never arrived.
    return _with_diagnostics(
        prompt,
        {"mode": "resume", "prompt_chars": len(prompt or "")},
        None,
        handoff_delivered=(handoff is None),
    )


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
                from api.chat_metadata import execution_badge_metadata
                status_queue.put(
                    (
                        "query_started",
                        {
                            "query_id": query_id,
                            "report_url": f"/query_log.html?id={query_id}",
                            "slash_command": execution_badge_metadata(
                                manifest.id, label, sid, model=model_override,
                                effort=(execute_kwargs or {}).get("reasoning_effort"),
                            ),
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

        # BYO-CLI (Phase 6 P6-A): Cuttle discovers, validates, invokes,
        # and documents vendor CLIs — it never installs them. A missing CLI
        # answers with its manifest guidance below.
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
        brain_receipt = None
        brain_meta: Dict[str, Any] = {}
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
                # Private delivery receipt (3rd element when the compiler
                # provides one; older stubs return 2-tuples). Never logged.
                brain_receipt = compiled[2] if len(compiled) > 2 else None
            else:
                agent_prompt = compiled
                brain_meta = {}
                brain_receipt = None
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
                            "degraded": bool((brain_meta or {}).get("degraded")),
                            "context_errors": list(
                                (brain_meta or {}).get("context_errors") or []
                            ),
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
            line_baseline = None
            line_event = None
            try:
                from api.edit_attribution.recorder import snapshot_for_attribution

                edit_baseline = snapshot_for_attribution(cwd)
                line_baseline = None
                try:
                    from api.agent_events.snapshots import begin
                    line_baseline = begin(cwd, query_id)
                except Exception as exc:
                    print(f"[agent_events] snapshot start unavailable: {exc}", flush=True)
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
                    if line_baseline:
                        try:
                            from api.agent_events.snapshots import finish
                            from api.agent_events.writer import record
                            line_event = finish(line_baseline)
                            record("event", query_id, {"kind": "edit", **line_event})
                        except Exception as exc:
                            print(f"[agent_events] snapshot finish unavailable: {exc}", flush=True)
                    from api.edit_attribution.recorder import record_run_deltas

                    record_run_deltas(
                        edit_baseline,
                        line_snapshot=line_event,
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
        compacted = bool(
            isinstance(result, AgentResult)
            and isinstance(result.meta, dict)
            and result.meta.get("context_compacted")
        )
        if result.success and not handled_by_meta:
            # Settings/meta answers never reached the CLI: they must not move
            # this agent's seen cursor or claim the chat's last agent. The
            # same holds for undelivered handoffs: the missed conversation
            # must be retried next turn, never skipped by an advanced cursor.
            # File-snapshot receipts are unaffected (they cover the briefing,
            # not conversation).
            if brain_meta.get("handoff_delivered", True):
                try:
                    from api.cuttle_brain.handoff import record_last_agent

                    record_last_agent(sid, manifest.id)
                except Exception:
                    pass
            else:
                print(
                    "[kernel] context handoff cursor held (undelivered)",
                    flush=True,
                )
        if compacted and not handled_by_meta:
            # The CLI summarized its context this turn; the briefing may be
            # gone. Drop the acknowledgement so the next turn re-sends it.
            brain_receipt = None
            try:
                from api.cuttle_brain.context_delta import clear_injected_snapshot

                clear_injected_snapshot(sid, manifest.id)
            except Exception:
                pass
        if result.success:
            # Delivery evidence is intentionally conservative: generic
            # adapters expose no common delivery acknowledgement, so a
            # successful non-meta result is the delivery signal, while
            # failed turns keep their notices pending and repeat them.
            # A success that raced a cancellation acknowledges nothing, and
            # meta answers (never sent to a CLI) never acknowledge.
            if (
                not handled_by_meta
                and brain_receipt is not None
                and not _run_was_cancelled(chat_session_id)
                and not _run_was_cancelled(sid)
            ):
                # Acknowledge exactly the snapshot prepared for the prompt
                # that was sent — never recompute here, so context files
                # edited mid-turn stay pending for the next delta.
                try:
                    from api.cuttle_brain.context_delta import record_snapshot

                    record_snapshot(sid, manifest.id, cwd, brain_receipt)
                except Exception as exc:
                    # Logging only: the notice stays pending and repeats
                    # next turn; availability and receipts are unchanged.
                    entry = _context_error("snapshot_record", exc)
                    print(f"[kernel] context {entry}", flush=True)

        if (
            result.success
            and not handled_by_meta
            and not _run_was_cancelled(chat_session_id)
            and not _run_was_cancelled(sid)
        ):
            # Opt-in (Settings → Agents → Git): commit only this turn's files.
            try:
                from api.git_autocommit import schedule_after_turn

                schedule_after_turn(
                    cwd=cwd, query_id=query_id, chat_session_id=sid, prompt=prompt
                )
            except Exception as exc:
                print(f"[kernel] auto-commit hook error: {exc}", flush=True)

        if not handled_by_meta and brain_meta:
            _record_context_metrics(
                manifest.id,
                sid=sid,
                cwd=cwd,
                model=model or getattr(result, "model", None),
                brain_meta=brain_meta,
                result=result,
                compacted=compacted,
                query_id=query_id,
            )

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
        if tracker and tracker.execution_data and callable(getattr(tracker, "set_harness", None)):
            tracker.set_harness({**tracker.execution_data.get("harness", {}), "model": model_name})
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
