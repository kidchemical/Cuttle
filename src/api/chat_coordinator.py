"""Shared transport-neutral application coordinator (Phase 5 P5-D).

The single application entry for agent turns: every surface submits the
same normalized ``PreparedAgentTurn`` and gets the same selection +
execution lifecycle. HTTP ingress keeps authenticate / validate /
resolve / call / serialize (plus SSE framing); this module owns the
call: unified agent-lane selection, claimed sync execution for the
harness and router arms, conditional post-turn persistence, and mobile
notification.

Surfaces and their (intentionally different) entry semantics:

- ``/api/chat`` route lanes: ``claim=True`` (busy slots), session
  stamping + example texts in ``format_shortcut``, enriched pipeline
  bodies. The pipeline arm is NOT executed here — the route runs its
  claiming ``run_pipeline_sync_turn`` itself.
- ``process_message_with_bot`` (local-mode prompts, ``/api/sessions/send``):
  ``claim=False`` (no delivery interaction, exactly as before),
  no-op persistence, plain shortcut bodies, naked pipeline fallback.

Non-executed arms (mode blocks, empty prompts, pipeline entry) return
control to the caller with the selection attached — no surface
formatting or pipeline policy is smuggled in here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class PreparedAgentTurn:
    """Normalized agent turn. Plain data only — safe to build anywhere."""

    message: str
    session_id: Any
    inference_mode: str = "auto"
    wants_stream: bool = True
    request_data: Mapping[str, Any] = field(default_factory=dict)
    project_path: str = ""
    user: Optional[dict] = None
    is_owner: bool = False
    session_kind: str = "web_anon"
    routing_key: str = ""
    history_text: str = ""
    user_meta: Optional[dict] = None
    identity: Optional[dict] = None
    run_kwargs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSelection:
    """Which arm handles the turn (single decision tree, all surfaces)."""

    kind: str  # router_family | plain_router | harness | mode_blocked | harness_empty_prompt | pipeline
    agent_id: Optional[str] = None
    prompt: Optional[str] = None
    block_message: Optional[str] = None


def default_match_harness(message: str, project_path: Optional[str] = None):
    """Catalog harness match with the entry's fail-closed guard."""
    try:
        from api.agent_harness.catalog import match_slash_command

        return match_slash_command((message or "").strip(), project_path=project_path)
    except Exception:
        return None


def default_cloud_blocked(message: str, inference_mode: str) -> Optional[str]:
    """Local-mode block message for cloud CLI slash commands, else None."""
    from api.inference_mode import (
        is_cloud_cli_slash_command,
        cloud_cli_slash_blocked_message,
    )

    if is_cloud_cli_slash_command(message):
        return cloud_cli_slash_blocked_message(inference_mode)
    return None


def select_agent_turn(
    message: str,
    *,
    inference_mode: str,
    is_router_family: Callable[[str], bool],
    match_harness: Callable[..., Optional[Tuple[str, str]]] = default_match_harness,
    cloud_blocked: Callable[[str, str], Optional[str]] = default_cloud_blocked,
    project_path: Optional[str] = None,
) -> AgentSelection:
    """One decision tree for every surface: router-family → harness
    (mode block wins over execution, empty prompt wins over run) →
    plain-router → pipeline fallback."""
    if is_router_family(message):
        return AgentSelection(kind="router_family")
    matched = match_harness(message, project_path)
    if matched:
        agent_id, prompt = matched
        blocked = cloud_blocked(message, inference_mode)
        if blocked:
            return AgentSelection(
                kind="mode_blocked", agent_id=agent_id, block_message=blocked
            )
        if not prompt:
            return AgentSelection(kind="harness_empty_prompt", agent_id=agent_id)
        return AgentSelection(kind="harness", agent_id=agent_id, prompt=prompt)
    # Plain-router attempt for the legacy surfaces; a None result means
    # abstain and the caller runs its own pipeline entry.
    if (message or "").strip():
        return AgentSelection(kind="plain_router")
    return AgentSelection(kind="pipeline")


@dataclass(frozen=True)
class AgentTurnIO:
    """One surface's arm implementations. All members are required —
    no silent defaults. ``run_router`` returning None means abstain
    (caller runs its pipeline entry)."""

    run_harness: Callable[..., Dict[str, Any]]
    run_router: Callable[..., Optional[Dict[str, Any]]]
    persist_user: Callable[[], None]
    make_saver: Callable[[], Optional[Callable[[Dict[str, Any]], None]]]
    should_save: Callable[[Dict[str, Any]], bool]
    notify_mobile: Optional[Callable[[Dict[str, Any]], None]]
    format_shortcut: Callable[[str, AgentSelection], Dict[str, Any]]


@dataclass(frozen=True)
class AgentTurnResult:
    """``body`` is None only when the caller must run its own entry:
    the pipeline arm, or a plain-router abstain."""

    body: Optional[Dict[str, Any]]
    status: int
    selection: AgentSelection


@dataclass(frozen=True)
class StreamTurnIO:
    """One surface's stream arm implementations. Narrow by design: the
    stream save policy lives in ``finalize_stream_result`` (kept-rule),
    so there is deliberately no ``should_save`` member."""

    run_harness: Callable[..., Dict[str, Any]]
    run_router: Callable[..., Optional[Dict[str, Any]]]
    persist_user: Callable[[], None]
    make_saver: Callable[[], Optional[Callable[[Dict[str, Any]], None]]]
    notify_mobile: Optional[Callable[[Dict[str, Any]], None]]
    format_shortcut: Callable[[str, AgentSelection], Dict[str, Any]]


def _after_run(
    body: Dict[str, Any],
    *,
    make_saver: Callable[[], Optional[Callable[[Dict[str, Any]], None]]],
    should_save: Callable[[Dict[str, Any]], bool],
    notify_mobile: Optional[Callable[[Dict[str, Any]], None]],
) -> None:
    if notify_mobile is not None:
        notify_mobile(body)
    if should_save(body):
        saver = make_saver()
        if saver is not None:
            saver(body)


def submit_agent_turn(
    prepared: PreparedAgentTurn,
    *,
    io: AgentTurnIO,
    delivery: Any,
    claim: bool = True,
    is_router_family: Callable[[str], bool] = lambda message: False,
    selection: Optional[AgentSelection] = None,
) -> AgentTurnResult:
    """Execute the harness / router arms through the shared lifecycle.

    Claimed (route lanes): busy → persist → run → conditional save →
    release via the owned sync skeleton. Unclaimed (legacy surfaces):
    the same order with no delivery interaction. Shortcut arms are
    formatted by the surface; pipeline/plain-router-abstain returns
    ``body=None`` for the caller's own entry. Callers that already
    matched (route lanes) pass ``selection`` to skip re-selection.
    """
    from api.chat_turn_workflow import run_agent_sync_turn as _run_lane

    message = prepared.message or ""
    sel = selection or select_agent_turn(
        message,
        inference_mode=prepared.inference_mode,
        is_router_family=is_router_family,
        project_path=prepared.project_path or None,
    )

    if sel.kind in ("mode_blocked", "harness_empty_prompt"):
        return AgentTurnResult(
            body=io.format_shortcut(sel.kind, sel), status=200, selection=sel
        )

    def _after(body):
        _after_run(
            body,
            make_saver=io.make_saver,
            should_save=io.should_save,
            notify_mobile=io.notify_mobile,
        )

    if sel.kind == "harness":
        def _run():
            return io.run_harness(
                sel.agent_id, sel.prompt,
                status_queue=None, **dict(prepared.run_kwargs or {}),
            )

        if not claim:
            io.persist_user()
            body = _run()
            if isinstance(body, dict):
                _after(body)
            return AgentTurnResult(body=body, status=200, selection=sel)
        body, status = _run_lane(
            prepared.session_id,
            delivery=delivery,
            persist_user=io.persist_user,
            run=_run,
            after_run=_after,
        )
        return AgentTurnResult(body=body, status=status, selection=sel)

    if sel.kind in ("router_family", "plain_router"):
        def _run_router():
            return io.run_router(status_queue=None)

        if not claim:
            io.persist_user()
            body = _run_router()
            if body is None:
                return AgentTurnResult(body=None, status=200, selection=sel)
            if isinstance(body, dict):
                _after(body)
            return AgentTurnResult(body=body, status=200, selection=sel)
        body, status = _run_lane(
            prepared.session_id,
            delivery=delivery,
            persist_user=io.persist_user,
            run=_run_router,
            after_run=_after,
        )
        if body is None:
            return AgentTurnResult(body=None, status=status, selection=sel)
        return AgentTurnResult(body=body, status=status, selection=sel)

    return AgentTurnResult(body=None, status=200, selection=sel)


def submit_agent_stream_turn(
    prepared: PreparedAgentTurn,
    *,
    io: StreamTurnIO,
    delivery: Any,
    is_router_family: Callable[[str], bool] = lambda message: False,
    selection: Optional[AgentSelection] = None,
):
    """Execute the harness / router arms through the shared stream lifecycle.

    The stream twin of ``submit_agent_turn``: same normalized selection
    (single decision tree, caller-supplied or derived), same normalized
    turn context and execution request — but lifecycle events instead of
    a body. Yields ``("status", message)`` / ``("query_started", payload)``
    progress, exactly one terminal event: ``("shortcut", body)`` for
    non-executed arms, ``("busy", body)`` when claimed elsewhere, or
    ``("done", result)``. Transport (SSE framing, pump loop) stays with
    the caller; busy/turn-token ownership, persist ordering, rewrite,
    finalize, and exactly-once release live in the owned skeleton.

    Always claims: every stream ingress is claimed (the unclaimed compat
    entry is sync-only by transport).
    """
    from api.chat_turn_workflow import run_agent_stream_turn as _run_stream

    message = prepared.message or ""
    sel = selection or select_agent_turn(
        message,
        inference_mode=prepared.inference_mode,
        is_router_family=is_router_family,
        project_path=prepared.project_path or None,
    )

    if sel.kind in ("mode_blocked", "harness_empty_prompt"):
        yield ("shortcut", io.format_shortcut(sel.kind, sel))
        return

    if sel.kind == "harness":
        def _run(queue):
            return io.run_harness(
                sel.agent_id, sel.prompt,
                status_queue=queue, **dict(prepared.run_kwargs or {}),
            )
    elif sel.kind in ("router_family", "plain_router"):
        def _run(queue):
            return io.run_router(status_queue=queue)
    else:  # pragma: no cover - unreachable from route stream branches
        yield (
            "done",
            {
                "success": False,
                "error": "unsupported_arm",
                "response": "This turn type cannot stream.",
                "type": "stream_unsupported",
            },
        )
        return

    yield from _run_stream(
        prepared.session_id,
        delivery=delivery,
        persist_user=io.persist_user,
        run=_run,
        make_saver=io.make_saver,
        notify_mobile=io.notify_mobile,
        project_path=prepared.project_path or "",
    )
