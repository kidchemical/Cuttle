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
