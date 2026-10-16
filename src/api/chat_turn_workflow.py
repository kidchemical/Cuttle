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
        if isinstance(body, dict):
            from api.response_postprocessing import process_result
            process_result(body, cancelled=lambda: is_turn_superseded(delivery, session_id, token))
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
        from api.response_postprocessing import process_result
        process_result(
            result, cancelled=lambda: is_turn_superseded(delivery, session_id, token),
            status=lambda message: events.publish('status', message),
        )
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


# ---------------------------------------------------------------------------
# /help text lane (Phase 6B slice 4)
# ---------------------------------------------------------------------------

_HELP_TEXT = """**🦑 Cuttle Web Chat Commands**

**Agent CLI commands:**
• `/claude "prompt"` - Claude Code CLI
• `/opencode "prompt"` - OpenCode CLI (`opencode run`, resumes per chat)
• `/antigravity "prompt"` - Google Antigravity CLI (`agy`, auto-installs, resumes per chat)
• `/cursor "prompt"` - Cursor Agent CLI (`agent -p`)
• `/codex "prompt"` - OpenAI Codex CLI (`codex exec`, resumes per chat)
• `/muse "prompt"` - Meta Muse Code CLI (`muse exec`, resumes per chat; native Windows / macOS / Linux)
• `/muse model` - list Muse models; `/muse model muse-spark-1.3` pins one for this chat
• `/muse effort` - list reasoning-effort levels; `/muse effort high` pins one for this chat
• `/muse /usage` - Muse account + local session usage meters
• `/codex /usage` - ChatGPT Codex plan windows (5-hour / weekly)
• `/hermes /usage` - Hermes local insights (tokens, tools, models)
• `/opencode /usage` - OpenCode local stats (cost, tools, models)
• `/claude /usage` - Claude Code local session usage (tokens, models)
• `/hermes "prompt"` - Hermes Agent using its configured provider
• `/deepseek "prompt"` - DeepSeek Harness CLI (`dsh --profile headless`; Flash by default)

**Agent router** (when no sticky agent is selected):
• `/router status` - show mode, provider, default/escalation/fallbacks
• `/router mode off|api|local|agent` - enable/disable routing brain
• `/router evaluate <prompt>` - routing decision only (no agent run)
• `/router evaluate batch [suite] --yes` - batch suite (routing brain only)
• `/route <agent> <model> <prompt>` - one-task override
• `/retry frontier` / `/retry fallback` - retry last failed routed turn

**Flask restart** (daemon-owned; do not taskkill Flask yourself):
• `/restart status` - daemon/Flask health + active tasks + pending restart
• `/restart graceful` - restart only if idle
• `/restart when-idle` - schedule after active work finishes
• `/restart force --yes` - interrupt active work (requires confirmation)


**Project commands** (from the chat project's ``.cuttle/commands/*.md``):
• `/build`, `/deploy`, … — names come from each project
• `/cmd <name>` — same, explicit form

**Tips:**
• Results show directly in chat; executions are logged in Query Reports"""


def help_response(session_id: Any) -> Dict[str, Any]:
    """``/help`` terminal body (moved verbatim from the ``/api/chat`` ingress).

    Pure data: the route keeps request capture and ``jsonify``.
    """
    return {
        'success': True,
        'response': _HELP_TEXT,
        'session_id': session_id,
        'type': 'help',
    }


# ---------------------------------------------------------------------------
# Router-config lane (Phase 6B slice 5) and route/retry lane (slice 6)
# ---------------------------------------------------------------------------


def classify_router_family(
    message_content: str,
    *,
    data: Any,
    session_id: Any,
    resolve_project_path: Callable[[Any], Any],
) -> Dict[str, Any]:
    """Classify router-family commands (moved verbatim from the ingress).

    Returns ``{router_cfg, route_or_retry, project_path, fallthrough_body}``.
    ``fallthrough_body`` is set only for the recognized-but-unreturned
    supervised control guard (terminal): the route jsonifies it and never
    falls through to router/agent dispatch. Parse failures fall back to
    all-false / ``project_path=None``, exactly as the inline block did.
    """
    router_cfg = False
    route_or_retry = False
    project_path = None
    fallthrough_body = None
    try:
        from api.agent_router.commands import (
            parse_retry_command,
            parse_route_command,
            parse_router_command,
        )
        from api.agent_router.supervised.commands import (
            parse_coordinate_command,
            parse_coordinator_command,
        )
        from api.agent_router.supervised.control import (
            is_coordinate_control,
            is_supervised_control_message,
        )

        project_path = resolve_project_path({
            **(data or {}),
            'session_id': (
                session_id
                if session_id is not None
                else (data or {}).get('session_id')
            ),
        })
        router_cfg = parse_router_command(message_content) is not None
        route_or_retry = (
            parse_route_command(message_content) is not None
            or parse_retry_command(message_content) is not None
        )
        # Control lane already handled earlier; keep classification for safety.
        # Fallthrough of recognized control commands is forbidden (double-apply).
        if is_supervised_control_message(message_content):
            return {
                'router_cfg': router_cfg,
                'route_or_retry': route_or_retry,
                'project_path': project_path,
                'fallthrough_body': {
                    'success': True,
                    'response': (
                        '❌ Supervised control command was recognized but the early '
                        'control lane did not return. Refusing fallthrough to prevent '
                        'duplicate side effects.'
                    ),
                    'type': 'supervised_control_error',
                    'control_lane': True,
                    'control_handled': True,
                    'session_id': session_id,
                },
            }
        if parse_coordinator_command(message_content) is not None:
            router_cfg = True
        coord_args = parse_coordinate_command(message_content)
        if coord_args is not None:
            if is_coordinate_control(coord_args):
                router_cfg = True
            else:
                route_or_retry = True
        if is_supervised_control_message(message_content):
            router_cfg = True
            route_or_retry = False
    except Exception as _ar_err:
        print(f"[CHAT] agent router parse failed: {_ar_err}", flush=True)
        router_cfg = False
        route_or_retry = False
        project_path = None
    return {
        'router_cfg': router_cfg,
        'route_or_retry': route_or_retry,
        'project_path': project_path,
        'fallthrough_body': fallthrough_body,
    }


def run_router_config_lane(
    message_content: str,
    *,
    session_id: Any,
    project_path: Any,
    auth_user: Any,
    persist_user_turn: Callable[[Any], None],
    get_auth_db: Callable[[], Any],
) -> Dict[str, Any]:
    """Router-config lane (``/router``, ``/coordinator``): handle + persist.

    Moved verbatim from the ``/api/chat`` ingress. Returns a terminal body;
    the route jsonifies.
    """
    try:
        from api.agent_router.integration import handle_router_family_command
        body = handle_router_family_command(
            message_content,
            session_id=session_id,
            project_path=project_path,
        ) or {}
    except Exception as _ar_err:
        body = {
            'success': True,
            'response': f'❌ **Agent router:** {_ar_err}',
            'type': 'router_error',
        }
    body = dict(body)
    body['session_id'] = session_id
    if auth_user and session_id is not None:
        try:
            persist_user_turn(session_id)
            get_auth_db().add_message(
                session_id,
                'assistant',
                body.get('response') or '',
                metadata={'origin': 'agent_router'},
            )
        except Exception as _pe:
            print(f"[CHAT] persist router reply failed: {_pe}")
    return body


def build_route_retry_stream_turn(
    message_content: str,
    *,
    session_id: Any,
    data: Any,
    project_path: Any,
    wants_stream: bool,
    auth_user: Any,
    persist_user_turn: Callable[[Any], None],
    make_saver: Callable[..., Any],
    notify_mobile: Callable[[Any, Dict[str, Any]], None],
) -> Tuple[Any, Any]:
    """Build the route/retry stream turn (LANE 6 stream arm).

    Returns ``(PreparedAgentTurn, StreamTurnIO)``; the route frames SSE
    through its transport helper. Moved verbatim from the ingress.
    """
    from api.chat_coordinator import PreparedAgentTurn, StreamTurnIO

    def _unreachable(*_a, **_k):
        raise RuntimeError('unreachable arm in router stream lane')

    def _run_router(status_queue=None):
        from api.agent_router.integration import handle_router_family_command
        return dict(handle_router_family_command(
            message_content,
            session_id=session_id,
            project_path=project_path,
            status_queue=status_queue,
        ) or {})

    io = StreamTurnIO(
        run_harness=_unreachable,
        run_router=_run_router,
        persist_user=lambda: (persist_user_turn(session_id) if auth_user else None),
        make_saver=lambda: make_saver(
            session_id if auth_user else None, project_path=project_path
        ),
        notify_mobile=lambda _b: notify_mobile(session_id, _b),
        format_shortcut=_unreachable,
    )
    prepared = PreparedAgentTurn(
        message=message_content, session_id=session_id,
        wants_stream=wants_stream,
        request_data=data or {}, project_path=project_path or '',
    )
    return prepared, io


def run_route_retry_sync_turn(
    message_content: str,
    *,
    session_id: Any,
    data: Any,
    project_path: Any,
    wants_stream: bool,
    auth_user: Any,
    persist_user_turn: Callable[[Any], None],
    make_saver: Callable[..., Any],
    delivery: Any,
) -> Tuple[Optional[Dict[str, Any]], int]:
    """Run the route/retry sync turn through the shared coordinator.

    Returns ``(body, status)``; the route jsonifies. Moved verbatim from
    the ingress (including the guarded-saver log policy).
    """
    from api.chat_coordinator import (
        AgentSelection,
        AgentTurnIO,
        PreparedAgentTurn,
        submit_agent_turn,
    )

    def _unreachable(*_a, **_k):
        raise RuntimeError('unreachable arm in router sync lane')

    def _guarded_saver():
        saver = make_saver(session_id)
        if saver is None:
            return None

        def _guarded(body):
            try:
                saver(body)
            except Exception as _se:
                print(f"[CHAT] router persist failed: {_se}")

        return _guarded

    def _run_router(status_queue=None):
        from api.agent_router.integration import handle_router_family_command
        return dict(handle_router_family_command(
            message_content,
            session_id=session_id,
            project_path=project_path,
            status_queue=status_queue,
        ) or {})

    io = AgentTurnIO(
        run_harness=_unreachable,
        run_router=_run_router,
        persist_user=lambda: (persist_user_turn(session_id) if auth_user else None),
        make_saver=_guarded_saver,
        should_save=lambda _b: bool(auth_user and result_kept_for_history(_b)),
        notify_mobile=None,
        format_shortcut=_unreachable,
    )
    out = submit_agent_turn(
        PreparedAgentTurn(
            message=message_content, session_id=session_id,
            wants_stream=wants_stream,
            request_data=data or {}, project_path=project_path or '',
        ),
        io=io, delivery=delivery, claim=True,
        selection=AgentSelection(kind='router_family'),
    )
    body = out.body
    if isinstance(body, dict):
        body.setdefault('session_id', session_id)
    return body, out.status


# ---------------------------------------------------------------------------
# Harness lane (Phase 6B slice 7)
# ---------------------------------------------------------------------------


def harness_empty_prompt_body(agent_id: Any, session_id: Any) -> Dict[str, Any]:
    """Terminal body when a harness slash command has no prompt.

    Moved verbatim from the ``/api/chat`` ingress: the route's early return
    and the stream lane's ``format_shortcut`` both used this shape. Pure
    data — the route keeps request capture and ``jsonify``.
    """
    _eaid = agent_id
    return {
        'success': True,
        'response': (
            f'❌ Please provide a prompt after /{_eaid}. '
            f'Example: `/{_eaid} "summarize this repo"`'
        ),
        'session_id': session_id,
        'type': f'{_eaid}_error',
    }


def build_harness_stream_turn(
    message_content: str,
    *,
    session_id: Any,
    data: Any,
    project_path: Any,
    wants_stream: bool,
    agent_id: str,
    prompt: str,
    auth_user: Any,
    persist_user_turn: Callable[[Any], None],
    make_saver: Callable[..., Any],
    notify_mobile: Callable[[Any, Dict[str, Any]], None],
    run_harness: Callable[..., Dict[str, Any]],
) -> Tuple[Any, Any]:
    """Build the harness stream turn (LANE 7 stream arm).

    Returns ``(PreparedAgentTurn, StreamTurnIO)``; the route frames SSE
    through its transport helper. Moved verbatim from the ingress. The
    runner (with its identity kwargs) is injected so this owner never
    imports the entry module.
    """
    from api.chat_coordinator import PreparedAgentTurn, StreamTurnIO

    def _format_shortcut(kind, sel):
        if kind == "harness_empty_prompt":
            return harness_empty_prompt_body(sel.agent_id or agent_id, session_id)
        raise RuntimeError(f'unexpected shortcut arm: {kind}')

    def _unreachable(*_a, **_k):
        raise RuntimeError('unreachable arm in harness stream lane')

    io = StreamTurnIO(
        run_harness=run_harness,
        run_router=_unreachable,
        persist_user=lambda: (persist_user_turn(session_id) if auth_user else None),
        make_saver=lambda: make_saver(
            session_id if auth_user else None, project_path=project_path
        ),
        notify_mobile=lambda b: notify_mobile(session_id, b),
        format_shortcut=_format_shortcut,
    )
    prepared = PreparedAgentTurn(
        message=message_content, session_id=session_id,
        wants_stream=wants_stream,
        request_data=data or {}, project_path=project_path or '',
    )
    return prepared, io


def run_harness_sync_turn(
    message_content: str,
    *,
    session_id: Any,
    data: Any,
    project_path: Any,
    wants_stream: bool,
    agent_id: str,
    prompt: str,
    auth_user: Any,
    persist_user_turn: Callable[[Any], None],
    make_saver: Callable[..., Any],
    notify_mobile: Callable[[Any, Dict[str, Any]], None],
    delivery: Any,
    run_harness: Callable[..., Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], int]:
    """Run the harness sync turn through the shared coordinator.

    Returns ``(body, status)``; the route jsonifies. Moved verbatim from
    the ingress (including the guarded-saver log policy). The runner (with
    its identity kwargs) is injected so this owner never imports the entry
    module.
    """
    from api.chat_coordinator import (
        AgentSelection,
        AgentTurnIO,
        PreparedAgentTurn,
        submit_agent_turn,
    )

    def _guarded_saver():
        saver = make_saver(session_id, project_path=project_path)
        if saver is None:
            return None

        def _guarded(body):
            try:
                saver(body)
            except Exception as _se:
                print(f'[CHAT] harness persist failed: {_se}')

        return _guarded

    def _unreachable(*_a, **_k):
        raise RuntimeError('unreachable arm in harness sync lane')

    io = AgentTurnIO(
        run_harness=run_harness,
        run_router=_unreachable,
        persist_user=lambda: (persist_user_turn(session_id) if auth_user else None),
        make_saver=_guarded_saver,
        should_save=lambda b: bool(auth_user and result_kept_for_history(b)),
        notify_mobile=lambda b: notify_mobile(session_id, b),
        format_shortcut=_unreachable,
    )
    out = submit_agent_turn(
        PreparedAgentTurn(
            message=message_content, session_id=session_id,
            wants_stream=wants_stream,
            request_data=data or {}, project_path=project_path or '',
        ),
        io=io, delivery=delivery, claim=True,
        selection=AgentSelection(kind='harness', agent_id=agent_id, prompt=prompt),
    )
    body = out.body
    if isinstance(body, dict) and session_id is not None:
        body.setdefault('session_id', session_id)
    return body, out.status


# ---------------------------------------------------------------------------
# Pipeline lane (Phase 6B slice 8)
# ---------------------------------------------------------------------------


def build_pipeline_stream_turn(
    message_content: str,
    *,
    session_id: Any,
    data: Any,
    project_path: Any,
    wants_stream: bool,
    persist_user: Callable[[], None],
    make_saver: Callable[[], Any],
    notify_mobile: Callable[[Dict[str, Any]], None],
    run_pipeline: Callable[..., Dict[str, Any]],
) -> Tuple[Any, Any]:
    """Build the pipeline stream turn (LANE 8 stream arm).

    Returns ``(PreparedAgentTurn, StreamTurnIO)``; the route frames SSE
    through ``_stream_pipeline_turn_response``. Moved verbatim from the
    ingress: the pipeline arm runs the injected ``run_pipeline`` (the
    compat ``process_message_with_bot`` entry) and is finalized raw (its
    saver rewrites before persisting, the wire rewrite is re-applied by
    the transport adapter). ``project_path`` is unused for this arm
    (explicit pipeline selection, worker not rewritten).
    """
    from api.chat_coordinator import PreparedAgentTurn, StreamTurnIO

    def _unreachable(*_a, **_k):
        raise RuntimeError('unreachable arm in pipeline stream lane')

    io = StreamTurnIO(
        run_harness=_unreachable,
        run_router=_unreachable,
        persist_user=persist_user,
        make_saver=make_saver,
        notify_mobile=notify_mobile,
        format_shortcut=_unreachable,
        run_pipeline=run_pipeline,
    )
    prepared = PreparedAgentTurn(
        message=message_content, session_id=session_id,
        wants_stream=wants_stream,
        request_data=data or {},
        # Unused: selection is explicit pipeline (no catalog
        # match) and the pipeline worker is not rewritten.
        project_path='',
    )
    return prepared, io


def run_pipeline_sync_lane(
    session_id: Any,
    *,
    delivery: Any,
    persist_user: Callable[[], None],
    run: Callable[[], Dict[str, Any]],
    make_saver: Callable[[], Any],
    usage_meta_fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
) -> Tuple[Dict[str, Any], int]:
    """Run the leftover-pipeline sync lane through the shared skeleton.

    Builds the pipeline saver from the injected ``make_saver`` factory
    (the shared ``make_assistant_saver`` is the sole eligibility policy)
    and delegates to ``run_pipeline_sync_turn``. The route keeps request
    capture, the busy 409 transport and ``jsonify``. Moved verbatim from
    the ingress; the saver's return value flows into the body builder
    exactly as the old inline wrapper did.
    """
    def _save_assistant(res):
        saver = make_saver()
        if saver is None:
            return res
        saver(res)
        return res

    return run_pipeline_sync_turn(
        session_id,
        delivery=delivery,
        persist_user=persist_user,
        run=run,
        save_assistant=_save_assistant,
        build_body=lambda res: build_pipeline_body(
            res, session_id, usage_meta_fn=usage_meta_fn,
        ),
    )
