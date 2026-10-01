"""Application-level chat-turn workflow owner (Phase 5 P5-B).

Owns the per-lane turn orchestration that used to repeat inline in the
``/api/chat`` route: busy-guard → persist user → run → post-turn
(save/notify) → release, plus the stream completion tail and the
pipeline response-body shape. The Flask ingress keeps
authenticate/validate/resolve/call/serialize and all SSE transport;
this module never sees a Flask request object.

Single source of truth for turn state: the injected ``delivery`` object
(``api.chat_delivery`` in production) — busy slots, turn tokens,
cancel flags, and the parked-result store. SQLite assistant rows are
written through the injected ``persist_user`` / ``save_assistant``
callables, so lifetimes stay with the existing stores.

Executor errors are NOT contained here: the lanes let ``run()`` raise
(the route's 500 handler answers), while ``release`` still runs.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple


def busy_response_body(session_id: Any) -> Dict[str, Any]:
    """JSON body when a second send hits a chat that is still generating."""
    return {
        'success': False,
        'error': 'busy',
        'busy': True,
        'session_id': session_id,
        'response': (
            '⏳ Still working on your previous message in this chat. '
            'Wait for it to finish, or stop it first.'
        ),
    }


def begin_sync_turn(delivery: Any, session_id: Any) -> Optional[Any]:
    """Claim the busy slot; return the turn token, or None when busy."""
    if session_id is not None and not delivery.try_begin(session_id):
        return None
    return delivery.current_turn(session_id)


def release_sync_turn(delivery: Any, session_id: Any, token: Any) -> None:
    """Release the slot by token so a stale worker never frees a newer turn."""
    try:
        delivery.end(session_id, turn=token)
    except Exception:
        pass


def is_turn_superseded(delivery: Any, session_id: Any, token: Any) -> bool:
    """True when the turn was replaced (Stop → re-send) or cancelled."""
    return bool(
        delivery.is_stale_turn(session_id, token)
        or delivery.is_turn_cancelled(session_id)
    )


def run_agent_sync_turn(
    session_id: Any,
    *,
    delivery: Any,
    persist_user: Callable[[], None],
    run: Callable[[], Dict[str, Any]],
    after_run: Callable[[Dict[str, Any]], None],
) -> Tuple[Dict[str, Any], int]:
    """Harness-lane / router-family-lane sync workflow (order is the contract).

    persist → run → after_run (session stamp + mobile notify + saver).
    Returns ``(body, status)``; the ingress serializes. ``run()`` errors
    propagate after release — matching the pre-extraction lanes.
    """
    token = begin_sync_turn(delivery, session_id)
    if token is None and session_id is not None:
        return busy_response_body(session_id), 409
    try:
        persist_user()
        body = run()
        if isinstance(body, dict):
            after_run(body)
        return body, 200
    finally:
        release_sync_turn(delivery, session_id, token)


def run_pipeline_sync_turn(
    session_id: Any,
    *,
    delivery: Any,
    persist_user: Callable[[], None],
    run: Callable[[], Dict[str, Any]],
    save_assistant: Callable[[Dict[str, Any]], Dict[str, Any]],
    build_body: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Tuple[Dict[str, Any], int]:
    """Leftover-pipeline sync workflow: stale/cancelled and failed turns are
    returned to the caller but never persisted into the newer turn's history.
    """
    token = begin_sync_turn(delivery, session_id)
    if token is None and session_id is not None:
        return busy_response_body(session_id), 409
    try:
        persist_user()
        res = run()
        if isinstance(res, dict) and res.get('success'):
            if not is_turn_superseded(delivery, session_id, token):
                res = save_assistant(res) or res
        return build_body(res), 200
    finally:
        release_sync_turn(delivery, session_id, token)


def build_pipeline_body(
    res: Dict[str, Any],
    session_id: Any,
    *,
    usage_meta_fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Response body for the leftover-pipeline sync lane (verbatim shape)."""
    res = res if isinstance(res, dict) else {}
    body = {
        'success': bool(res.get('success')),
        'response': res.get('response', '') or '',
        'session_id': session_id,
        'type': res.get('type', 'pipeline_execution'),
    }
    if res.get('query_id'):
        body['query_id'] = res['query_id']
    if res.get('report_url'):
        body['report_url'] = res['report_url']
    if res.get('cursor_run'):
        body['cursor_run'] = res['cursor_run']
    try:
        usage = usage_meta_fn(res)
        if usage:
            body['usage'] = usage
    except Exception:
        pass
    return body


def finalize_stream_result(
    delivery: Any,
    session_id: Any,
    token: Any,
    result: Dict[str, Any],
    *,
    on_result: Optional[Callable[[Dict[str, Any]], None]] = None,
    notify_mobile: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> bool:
    """Stream completion tail: persist + park + release in exact order.

    A superseded/cancelled turn saves and parks nothing, but the busy slot
    is still released by token. Returns True when the result was kept.
    """
    result = result if isinstance(result, dict) else {}
    superseded = delivery.is_stale_turn(session_id, token)
    try:
        superseded = superseded or delivery.is_turn_cancelled(session_id)
    except Exception:
        pass
    if superseded:
        print(
            f"[CHAT] discarding late reply for session {session_id}: "
            f"turn {token} was superseded",
            flush=True,
        )
    kept = bool(result.get('success') or result.get('response'))
    if not superseded and on_result is not None and kept:
        try:
            on_result(result)
        except Exception as save_err:
            print(f"[CHAT] on_result persistence failed: {save_err}")
    if not superseded and kept and notify_mobile is not None:
        try:
            notify_mobile(result)
        except Exception:
            pass
    if not superseded:
        try:
            delivery.store_result(session_id, result)
        except Exception:
            pass
    try:
        delivery.end(session_id, turn=token)
    except Exception:
        pass
    return bool(not superseded and kept)


def pipeline_fallback_result() -> Dict[str, Any]:
    """Normalized no-LLM compatibility outcome (P5-F).

    The pipeline is not a graph engine anymore (visual graphs retired):
    an empty message or a plain-router abstain ends here. Pure data —
    surfaces persist/frame it through their own transport adapters, and
    the coordinator arms return it instead of None so no surface
    reimplements the fallback. Moved verbatim from the route's
    ``_no_pipeline_chat_result``.
    """
    return {
        'success': True,
        'type': 'no_pipeline',
        'error': 'no_pipeline',
        'response': (
            'No graph is running — Cuttle chat and Discord use slash agents '
            '(`/cursor`, `/codex`, …) or the agent router. Pick an agent chip, or send a plain message for the router.'
        ),
    }


def rewrite_assistant_response_actions(
    res: Optional[Dict[str, Any]], session_id, project_path: str = ""
) -> Optional[Dict[str, Any]]:
    """Register <cuttle_confirm> blocks and rewrite them into Confirm/Cancel buttons.

    Moved verbatim from the Flask ingress (P5-E): response post-processing
    is application workflow, so the coordinator stream worker applies it
    before finalize — the same transform the pump applied to the SSE copy.
    """
    if not isinstance(res, dict) or not session_id:
        return res
    text = res.get('response')
    if not isinstance(text, str):
        return res
    lower = text.lower()
    if (
        '<cuttle_confirm' not in lower
        and '<cuttle_action_form' not in lower
        and '<cuttle_widget' not in lower
    ):
        return res
    try:
        from api.project_actions import prepare_assistant_text_for_actions
        rewritten = prepare_assistant_text_for_actions(
            text,
            session_id=str(session_id),
            project_path=project_path or '',
        )
    except Exception as e:
        print(f"[CHAT] cuttle_confirm rewrite failed: {e}", flush=True)
        rewritten = text
    if '<cuttle_widget' in rewritten.lower():
        try:
            from api.chat_widgets import rewrite_assistant_text_widgets
            rewritten = rewrite_assistant_text_widgets(
                rewritten,
                session_id=session_id,
                project_path=project_path or '',
            )
        except Exception as e:
            print(f"[CHAT] cuttle_widget rewrite failed: {e}", flush=True)
    if rewritten == text:
        return res
    out = dict(res)
    out['response'] = rewritten
    return out


def run_agent_stream_turn(
    session_id: Any,
    *,
    delivery: Any,
    persist_user: Callable[[], None],
    run: Callable[..., Dict[str, Any]],
    make_saver: Callable[[], Optional[Callable[[Dict[str, Any]], None]]],
    notify_mobile: Optional[Callable[[Dict[str, Any]], None]],
    project_path: str = "",
    rewrite_result: bool = True,
    completion: Optional[Dict[str, Any]] = None,
) -> Any:
    """Stream lifecycle skeleton (order is the contract).

    Yields ``("status", message)`` / ``("query_started", payload)``
    progress and exactly one terminal ``("done", result)``. Callers that
    already know the turn is unclaimable never enter: this skeleton always
    claims (all stream ingress is claimed; the unclaimed compat entry is
    sync-only).

    begin (busy yields ``("busy", body)``) → persist → run in a worker
    thread → stale/cancel-filtered progress → rewrite → shared finalize
    (save/park/notify/end-by-token) → belt-and-suspenders end-by-token in
    the pump ``finally``. Both ends are token-guarded no-ops once the
    turn is over (``delivery.end`` skips a slot owned by a newer turn),
    so logically the slot is released once while a stale worker can never
    free a newer turn — two guarded ``end`` calls, one release.

    Distinctions from the sync skeleton, preserved deliberately: executor
    errors become the standard error result instead of propagating (the
    route's 500 handler never sees stream failures); save policy is
    ``finalize_stream_result``'s kept-rule, not ``should_save``.

    ``rewrite_result=False`` is the pipeline arm: the worker result is
    finalized raw and the surface transport adapter applies the response
    rewrite (its own saver rewrites before persisting, the wire rewrite
    is a belt-and-suspenders re-application) — exactly the old pipeline
    stream order. The harness/router arms rewrite in the worker.

    ``completion`` is an optional out-dict carrying the turn identity the
    transport needs for the done-clear: when the terminal event is
    yielded, ``completion["stale"]`` / ``completion["cancelled"]`` are
    set from this turn's own token (same point-in-time the old pump read
    them). Transport must never infer freshness from the busy boolean —
    a free slot does not mean this turn is current. Harness/router lanes
    pass nothing (they do no done-clear).
    """
    import queue as _queue_mod
    import threading as _threads

    token = begin_sync_turn(delivery, session_id)
    if token is None and session_id is not None:
        yield ("busy", busy_response_body(session_id))
        return
    # Saver factory runs HERE (request thread on first next()): saver
    # construction captures request data once, which a worker thread
    # must never re-read.
    try:
        saver = make_saver()
    except Exception:
        saver = None
    try:
        persist_user()
    except Exception as exc:
        release_sync_turn(delivery, session_id, token)
        yield (
            "done",
            {
                "success": False,
                "response": f"Failed to save your message: {exc}",
                "session_id": session_id,
            },
        )
        return
    events: Any = _queue_mod.Queue()

    def _worker() -> None:
        try:
            result = run(events)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            result = {
                'success': False,
                'error': str(exc),
                'response': f'I encountered an error: {str(exc)}. Please try again.'
            }
        if not isinstance(result, dict):
            result = {}
        if not rewrite_result:
            display = result
        else:
            try:
                display = rewrite_assistant_response_actions(
                    result, session_id, project_path
                )
            except Exception:
                display = result
        finalize_stream_result(
            delivery,
            session_id,
            token,
            result,
            on_result=saver,
            notify_mobile=notify_mobile,
        )
        events.put(("done", display if isinstance(display, dict) else result))

    try:
        # Liveness marker BEFORE the worker starts: the transport
        # publishes Connecting/progress only after claim + persist, and a
        # stale turn must never repaint a newer turn's status. The old
        # pump ordered claim → persist → Connecting → worker; the marker
        # restores that order (framing ignores it). Abandoning the
        # generator here still releases via the finally below.
        yield ("connecting", None)
        _thread = _threads.Thread(target=_worker, daemon=True)
        _thread.start()
        while True:
            kind, payload = events.get()
            if kind != "done" and is_turn_superseded(delivery, session_id, token):
                continue
            if kind == "done":
                if completion is not None:
                    completion["stale"] = bool(
                        delivery.is_stale_turn(session_id, token)
                    )
                    try:
                        completion["cancelled"] = bool(
                            delivery.is_turn_cancelled(session_id)
                        )
                    except Exception:
                        completion["cancelled"] = False
                yield ("done", payload)
                break
            yield (kind, payload)
    finally:
        release_sync_turn(delivery, session_id, token)
