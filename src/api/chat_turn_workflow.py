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


def result_kept_for_history(result: Any) -> bool:
    """Coarse eligibility pre-gate shared by the sync lanes and the
    stream finalizer (which calls this helper directly).

    A result with success or response text reaches the saver, which
    applies the exact skip policy (supervised-owned, empty failures,
    ``[CANCELLED]``, ``ui == 'system'``, cancelled turns). Never
    duplicated per lane.
    """
    if not isinstance(result, dict):
        return False
    return bool(result.get('success') or result.get('response'))


def run_agent_sync_turn(
    session_id: Any,
    *,
    delivery: Any,
    persist_user: Callable[[], None],
    run: Callable[[], Dict[str, Any]],
    after_run: Callable[[Dict[str, Any]], None],
) -> Tuple[Dict[str, Any], int]:
    """Harness-lane / router-family-lane sync workflow (order is the contract).

    persist → run → after_run (session stamp + mobile notify + saver),
    skipped when the captured turn token was superseded or cancelled
    while ``run()`` executed. Returns ``(body, status)``; the ingress
    serializes. ``run()`` errors propagate after release — matching the
    pre-extraction lanes.
    """
    token = begin_sync_turn(delivery, session_id)
    if token is None and session_id is not None:
        return busy_response_body(session_id), 409
    try:
        persist_user()
        body = run()
        if isinstance(body, dict) and not is_turn_superseded(
            delivery, session_id, token
        ):
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
    """Leftover-pipeline sync workflow: stale/cancelled turns are returned
    to the caller but never persisted into the newer turn's history.
    Useful failed text is retained; the shared saver applies the exact
    skip policy (cancelled markers, system rows, empty failures).
    """
    token = begin_sync_turn(delivery, session_id)
    if token is None and session_id is not None:
        return busy_response_body(session_id), 409
    try:
        persist_user()
        res = run()
        if isinstance(res, dict) and result_kept_for_history(res):
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
    if res.get('routing_badge'):
        body['routing_badge'] = res['routing_badge']
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
    kept = result_kept_for_history(result)
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
    the pump ``finally`` after receiving done. Both ends are token-guarded no-ops once the
    turn is over (``delivery.end`` skips a slot owned by a newer turn),
    so logically the slot is released once while a stale worker can never
    free a newer turn — two guarded ``end`` calls, one release. Closing a
    subscriber before done leaves the started worker's busy slot intact.

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
    subscriber can use for completion diagnostics: when the terminal event is
    yielded, ``completion["stale"]`` / ``completion["cancelled"]`` are
    set from this turn's own token (same point-in-time the old pump read
    them). Live publication and cleanup happen at the producer, guarded
    by this token, regardless of whether a subscriber drains the events.
    """
    import threading as _threads
    from api import chat_live_status
    from api.chat_status import TurnStatusQueue, turn_status_scope

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

    def _publish_live(kind, payload):
        if kind == "query_started":
            query = payload or {}
            chat_live_status.set_live_status(
                session_id, query_id=query.get("query_id"),
                report_url=query.get("report_url"),
                slash_command=query.get("slash_command"),
                turn=token,
                is_cancelled=lambda sid: is_turn_superseded(delivery, sid, token),
            )
        else:
            chat_live_status.set_live_status(
                session_id, payload, turn=token,
                is_cancelled=lambda sid: is_turn_superseded(delivery, sid, token),
            )

    def _clear_live():
        if not delivery.is_stale_turn(session_id, token):
            chat_live_status.clear_live_status(session_id, turn=token)
        elif delivery.is_turn_cancelled(session_id):
            chat_live_status.clear_live_status(session_id, turn=token)

    events = TurnStatusQueue(
        is_superseded=lambda: is_turn_superseded(delivery, session_id, token),
        publish_live=_publish_live, clear_live=_clear_live,
    )
    worker_started = False
    terminal_received = False

    def _worker() -> None:
        try:
            with turn_status_scope(session_id, events):
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
        events.finish()
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
        # Publish before the worker starts, after claim + persist. The wire
        # marker preserves event order; live status belongs to this worker.
        events.publish("status", "Connecting...")
        yield ("connecting", None)
        _thread = _threads.Thread(target=_worker, daemon=True)
        _thread.start()
        worker_started = True
        while True:
            kind, payload = events.get()
            if kind != "done" and is_turn_superseded(delivery, session_id, token):
                continue
            if kind == "done":
                terminal_received = True
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
        # A disconnected subscriber must not release a still-running turn.
        # The worker owns finalization; an unstarted worker has no owner.
        if not worker_started:
            events.finish()
            release_sync_turn(delivery, session_id, token)
        elif terminal_received:
            release_sync_turn(delivery, session_id, token)


# ---------------------------------------------------------------------------
# Supervised control lane (Phase 6B slice 1)
# ---------------------------------------------------------------------------


def _resolve_control_id(data: Any, control_id_header: Optional[str]) -> str:
    """Resolve the idempotency/control id from the request body then header."""
    raw = (
        (data or {}).get('control_request_id')
        or (data or {}).get('idempotency_key')
        or control_id_header
        or ''
    )
    return str(raw or '').strip()


def _supervised_control_error_body(session_id: Any, exc: Any) -> Dict[str, Any]:
    """Terminal body when a recognized control command fails post-recognition."""
    return {
        'success': True,
        'response': (
            f"❌ Supervised control command failed after recognition: {exc}. "
            "No further router/agent dispatch will run for this request."
        ),
        'type': 'supervised_control_error',
        'control_lane': True,
        'control_handled': True,
        'session_id': session_id,
    }


def run_supervised_control_lane(
    message_content: str,
    *,
    session_id: Any,
    data: Any,
    control_id_header: Optional[str],
    resolve_auth_chat_session: Callable[[Any], Any],
    resolve_project_path: Callable[[Any], str],
    get_auth_db: Callable[[], Any],
) -> Optional[Dict[str, Any]]:
    """Supervised control lane (status/cancel/followup/coordinator config).

    Moved verbatim from the ``/api/chat`` ingress: classify → authorize and
    resolve session → mutate once → persist control id → return a terminal
    body. The Flask ingress keeps request capture, SSE framing and
    ``jsonify``; this owner never reads a Flask request object.

    Exactly-once contract: once a control message is recognized this function
    always returns a body (never ``None``), so the route can never fall
    through into router/agent dispatch after a control was recognized — even
    when post-mutation persistence/formatting fails. Returns ``None`` only
    when the message is not a supervised control command.
    """
    recognized = False
    chat_session_id = session_id
    try:
        from api.agent_router.supervised.control import (
            classify_supervised_message,
            is_supervised_control_message,
            strip_sticky_agent_prefix as _strip_sup,
        )
        from api.agent_router.supervised.commands import (
            handle_coordinate_command as _h_coord_task,
            handle_coordinator_command as _h_coord_cfg,
        )

        _sup_msg = _strip_sup(message_content)
        if not is_supervised_control_message(_sup_msg):
            return None

        recognized = True
        message_content = _sup_msg
        # Authorize / resolve session BEFORE mutation or _auth_user use.
        _auth_user, chat_session_id, _created_new_session = resolve_auth_chat_session(
            chat_session_id
        )
        _kind, _args = classify_supervised_message(message_content)
        _router_proj = resolve_project_path({
            **(data or {}),
            'session_id': chat_session_id if chat_session_id is not None else data.get('session_id'),
        })
        _control_id = _resolve_control_id(data, control_id_header)
        # For /coordinate <prompt> starts: persist the user row first so the
        # canonical coordinator bubble is created after it (correct order).
        _pre_user_id = None
        _is_coord_start = (
            _kind == "coordinate_start"
            and _auth_user
            and chat_session_id is not None
            and bool((_args or "").strip())
        )
        if _is_coord_start:
            try:
                _pre_user_id = get_auth_db().add_message(
                    chat_session_id,
                    "user",
                    message_content,
                    metadata={
                        "origin": "supervised_control",
                        "control_request_id": _control_id or None,
                        "control_lane": True,
                    },
                )
            except Exception as _upe:
                print(f"[CHAT] pre-persist supervised user failed: {_upe}")
        if _kind == "coordinator_control":
            _sr = _h_coord_cfg(_args or "", session_id=chat_session_id) or {}
        else:
            _sr = _h_coord_task(
                _args or "",
                session_id=chat_session_id,
                project_path=_router_proj,
                control_request_id=_control_id or None,
            ) or {}
        _sr = dict(_sr)
        if _pre_user_id is not None:
            _sr["parent_user_message_id"] = _pre_user_id
            _sr["user_message_id"] = _pre_user_id
            if _sr.get("task_id"):
                try:
                    from api.agent_router.supervised.bubble import bind_canonical_bubble
                    from api.agent_router.supervised.orchestrator import (
                        ensure_canonical_response,
                        update_canonical_response,
                    )
                    from api.agent_router.supervised.store import load_task as _lt0

                    _t0 = _lt0(_sr["task_id"])
                    if _t0:
                        bind_canonical_bubble(
                            _t0, parent_user_message_id=_pre_user_id
                        )
                        ensure_canonical_response(_t0)
                        update_canonical_response(_t0, _sr.get("response"))
                        _sr["coordinator_response_message_id"] = (
                            _t0.coordinator_response_message_id
                        )
                        _sr["assistant_message_id"] = (
                            _t0.coordinator_response_message_id
                        )
                except Exception as _be:
                    print(f"[CHAT] early bind supervised bubble failed: {_be}")
        _sr["session_id"] = chat_session_id
        _sr["control_lane"] = True
        _sr["control_handled"] = True
        if _control_id:
            _sr["control_request_id"] = _control_id
        # Persist is best-effort AFTER mutation; failures must still return.
        # Exactly-once history: never append a second user/assistant pair for
        # an idempotent control_request_id or when delivery already owns the row.
        _skip_persist = bool(
            _sr.get("skip_history_persist")
            or _sr.get("reconcile_only")
            or _sr.get("idempotent")
            or _sr.get("delivery_handled")
        )
        if (
            (not _skip_persist)
            and _auth_user
            and chat_session_id is not None
            and _sr.get("response")
        ):
            try:
                _user_meta = {
                    "origin": "supervised_control",
                    "control_request_id": _control_id or None,
                    "control_lane": True,
                }
                _uid = get_auth_db().add_message(
                    chat_session_id,
                    "user",
                    message_content,
                    metadata=_user_meta,
                )
                _asst_meta = {
                    "origin": "supervised_control",
                    "control_request_id": _control_id or None,
                    "control_lane": True,
                    "supervised_task_id": _sr.get("task_id"),
                    "supervised_delivery_event_id": _sr.get(
                        "supervised_delivery_event_id"
                    ),
                }
                _aid = get_auth_db().add_message(
                    chat_session_id,
                    "assistant",
                    _sr.get("response") or "",
                    metadata=_asst_meta,
                )
                _sr["user_message_id"] = _uid
                _sr["assistant_message_id"] = _aid
                # Stamp control event with history ids when possible.
                if _control_id and _sr.get("task_id"):
                    try:
                        from api.agent_router.supervised.store import (
                            load_task as _lt,
                            record_control_event as _rce,
                        )

                        _t = _lt(_sr["task_id"])
                        if _t:
                            _rce(
                                _t,
                                control_id=_control_id,
                                kind=str(_sr.get("type") or "control"),
                                result=_sr,
                                user_message_id=_uid,
                                assistant_message_id=_aid,
                            )
                    except Exception:
                        pass
            except Exception as _pe:
                print(f"[CHAT] persist supervised control failed: {_pe}")
                _sr["persist_error"] = str(_pe)[:200]
        elif _skip_persist:
            _sr["history_reconcile_only"] = True
            # Canonical supervised bubble: persist the user turn once and
            # refresh the existing coordinator assistant row (never append).
            if (
                _auth_user
                and chat_session_id is not None
                and _sr.get("type") in (
                    "supervised_started",
                    "supervised_complete",
                    "supervised_paid_blocked",
                )
                and _sr.get("task_id")
            ):
                try:
                    from api.agent_router.supervised.bubble import bind_canonical_bubble
                    from api.agent_router.supervised.orchestrator import (
                        update_canonical_response,
                    )
                    from api.agent_router.supervised.store import load_task as _lt2

                    _uid = None
                    if not _sr.get("parent_user_message_id"):
                        _uid = get_auth_db().add_message(
                            chat_session_id,
                            "user",
                            message_content,
                            metadata={
                                "origin": "supervised_control",
                                "control_request_id": _control_id or None,
                                "control_lane": True,
                                "supervised_task_id": _sr.get("task_id"),
                            },
                        )
                        _sr["parent_user_message_id"] = _uid
                        _sr["user_message_id"] = _uid
                    _t = _lt2(_sr["task_id"])
                    if _t:
                        bind_canonical_bubble(
                            _t,
                            parent_user_message_id=_sr.get("parent_user_message_id"),
                            coordinator_response_message_id=_sr.get(
                                "coordinator_response_message_id"
                            ),
                        )
                        update_canonical_response(_t, _sr.get("response"))
                        _sr["coordinator_response_message_id"] = (
                            _t.coordinator_response_message_id
                        )
                        _sr["assistant_message_id"] = (
                            _t.coordinator_response_message_id
                        )
                except Exception as _pe2:
                    print(f"[CHAT] bind supervised canonical bubble failed: {_pe2}")
                    _sr["persist_error"] = str(_pe2)[:200]
        return _sr
    except Exception as _sc_err:
        print(f"[CHAT] supervised control handler failed: {_sc_err}", flush=True)
        if recognized:
            # Recognized control must NEVER fall through (prevents double apply).
            return _supervised_control_error_body(chat_session_id, _sc_err)
        return None


# ---------------------------------------------------------------------------
# Launch-gate lane (Phase 6B slice 2)
# ---------------------------------------------------------------------------


def _persist_launch_gate_reply(
    db: Any,
    chat_session_id: Any,
    user_message: str,
    launch_reply: Dict[str, Any],
    *,
    button_click_history: Callable[[str], Any],
) -> None:
    """Save button-click + launch-gate assistant reply for authenticated web chat.

    Moved verbatim from the Flask ingress; ``button_click_history`` is the
    route's history shaping helper (still used by the not-yet-moved pipeline
    lane), injected so this owner never imports the entry module.
    """
    if not db or chat_session_id is None or not launch_reply:
        return
    hist_user, user_meta = button_click_history(user_message)
    asst_meta = {'type': launch_reply.get('type')}
    try:
        db.add_message(chat_session_id, 'user', hist_user, metadata=user_meta)
        db.add_message(
            chat_session_id,
            'assistant',
            launch_reply.get('response', '') or '',
            metadata=asst_meta if asst_meta.get('type') else None,
        )
    except Exception as e:
        print(f"[CHAT] persist launch-gate reply failed: {e}")


def run_launch_gate_lane(
    message_content: str,
    *,
    session_id: Any,
    auth_user: Any,
    get_auth_db: Callable[[], Any],
    button_click_history: Callable[[str], Any],
) -> Optional[Dict[str, Any]]:
    """No-LLM launch gates: project confirm buttons and dynamic action forms.

    Moved verbatim from the ``/api/chat`` ingress: a user message that is a
    project-action button (``[button:project-action-…]``) or an action-form
    interaction (``[action-form:…]``) is handled by its existing owner
    (``api.project_actions`` / ``api.action_forms``), the reply is persisted,
    and a terminal body is returned. Returns ``None`` when the message is not
    a launch-gate message, so the route continues its dispatch chain. The
    Flask ingress keeps request capture, auth, SSE framing and ``jsonify``.
    """
    launch_gate_sid = (
        f"db_session_{session_id}" if auth_user and session_id is not None
        else (str(session_id) if session_id is not None else None)
    )
    mc_stripped = (message_content or '').strip()

    # Project confirm actions (no LLM) — e.g. Discord post after <cuttle_confirm>.
    if launch_gate_sid and mc_stripped.startswith('[button:project-action-'):
        try:
            from api.project_actions import handle_project_action_button
            action_reply = handle_project_action_button(
                mc_stripped,
                session_id=str(launch_gate_sid),
            )
        except Exception as pa_err:
            print(f"[CHAT] project action button failed: {pa_err}", flush=True)
            action_reply = {
                'success': False,
                'response': f'Action failed: {pa_err}',
                'type': 'project_action',
            }
        if action_reply is not None:
            body = dict(action_reply)
            body['session_id'] = session_id
            if auth_user and session_id is not None:
                try:
                    _persist_launch_gate_reply(
                        get_auth_db(), session_id, mc_stripped, action_reply,
                        button_click_history=button_click_history,
                    )
                except Exception as pe:
                    print(f"[CHAT] persist project action reply failed: {pe}", flush=True)
            return body

    # Dynamic action forms (no LLM) — choice / multi / form → allowlisted actions.
    if launch_gate_sid and mc_stripped.startswith('[action-form:'):
        try:
            from api.action_forms import handle_action_form_chat_message
            form_reply = handle_action_form_chat_message(
                mc_stripped,
                session_id=str(launch_gate_sid),
            )
        except Exception as af_err:
            print(f"[CHAT] action form failed: {af_err}", flush=True)
            form_reply = {
                'success': False,
                'response': f'Action form failed: {af_err}',
                'type': 'action_form',
                'silent': False,
            }
        if form_reply is not None:
            body = dict(form_reply)
            body['session_id'] = session_id
            # Silent forms: toast + card lock only — no "Selected: …" user bubble.
            if auth_user and session_id is not None and not body.get('silent'):
                try:
                    _persist_launch_gate_reply(
                        get_auth_db(), session_id, mc_stripped, form_reply,
                        button_click_history=button_click_history,
                    )
                except Exception as pe:
                    print(f"[CHAT] persist action form reply failed: {pe}", flush=True)
            elif (
                auth_user
                and session_id is not None
                and body.get('silent')
                and body.get('success')
                and not body.get('reusable')
                and (body.get('lock') or 'form') == 'form'
                and body.get('form_id')
            ):
                try:
                    from api.action_forms import mark_action_form_consumed_in_history
                    mark_action_form_consumed_in_history(
                        session_id=f'db_session_{session_id}',
                        form_id=str(body.get('form_id')),
                        selected=list(body.get('selected') or []),
                        toast=str(body.get('toast') or ''),
                    )
                except Exception as pe:
                    print(f"[CHAT] action form lock persist failed: {pe}", flush=True)
            return body
    return None


# ---------------------------------------------------------------------------
# Shell action expansion lane (Phase 6B slice 3)
# ---------------------------------------------------------------------------


def run_shell_action_lane(
    message_content: str,
    *,
    session_id: Any,
    auth_user: Any,
    data: Any,
    resolve_project_path: Callable[[Any], Any],
    persist_user_turn: Callable[[Any], None],
    make_saver: Callable[[Any], Optional[Callable[[Dict[str, Any]], None]]],
    rewrite_actions: Callable[..., Optional[Dict[str, Any]]],
) -> Tuple[Optional[Dict[str, Any]], str, bool]:
    """Expand project-local ``.cuttle/commands/*.md`` (shell action or prompt).

    Moved verbatim from the ``/api/chat`` ingress: a project command either
    runs as a no-LLM shell action (terminal) or expands into an agent prompt
    (falls through). Returns ``(terminal_body, message_content, busy)``:

    * shell action → ``(reply_body, message_content, False)``; the route
      jsonifies. When the chat is busy first, ``(None, message_content, True)``
      and the transport-owning route frames its own busy SSE/409 response.
    * prompt action → ``(None, expanded_message, False)``; the route continues
      with the expanded prompt (never terminal).
    * anything else → ``(None, message_content, False)`` (unchanged).
    """
    try:
        from api.project_commands import (
            try_expand_message_project_command,
            run_project_command_shell,
            format_project_command_shell_reply,
        )
        proj_path = resolve_project_path({
            **(data or {}),
            'session_id': (
                session_id
                if session_id is not None
                else (data or {}).get('session_id')
            ),
        })
        expanded = try_expand_message_project_command(message_content, proj_path)
        if expanded and expanded.get('action') == 'shell':
            cmd = expanded.get('command') or {}
            # Busy gate is transport-owned; signal it for the route to frame.
            from api import chat_delivery as _delivery
            if session_id is not None and _delivery.is_busy(session_id):
                return None, message_content, True
            result = run_project_command_shell(
                cmd,
                expanded.get('args') or '',
                session_id=session_id,
            )
            body = format_project_command_shell_reply(cmd, result)
            reply = {
                'success': bool(result.get('success')),
                'response': body,
                'session_id': session_id,
                'type': 'project_command',
            }
            if session_id is not None:
                reply = rewrite_actions(
                    reply,
                    f'db_session_{session_id}',
                    proj_path or '',
                ) or reply
            if auth_user and session_id is not None:
                try:
                    persist_user_turn(session_id)
                    saver = make_saver(session_id)
                    if saver:
                        saver({
                            'success': bool(reply.get('success')),
                            'response': reply.get('response') or body,
                            'type': 'project_command',
                        })
                except Exception as pe:
                    print(f"[CHAT] project command persist failed: {pe}", flush=True)
            return reply, message_content, False
        if (
            expanded
            and expanded.get('action') == 'prompt'
            and expanded.get('message')
        ):
            print(
                f"[CHAT] project command /{(expanded.get('command') or {}).get('name')} "
                f"expanded (sticky={expanded.get('sticky')!r}) cwd={proj_path!r}",
                flush=True,
            )
            return None, expanded['message'], False
    except Exception as pc_err:
        print(f"[CHAT] project command expand failed: {pc_err}", flush=True)
    return None, message_content, False
