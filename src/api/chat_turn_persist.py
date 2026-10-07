"""Turn persistence owner (Phase 5 P5-C).

Owns the assistant-saver construction, the user-turn persist, and the
auth user-message write that used to close over the Flask request and
the ``get_auth_db`` singleton inside ``web_chat_api``. Everything the
persistence needs from HTTP/transport arrives as explicit plain data:

- ``db`` — the session store (``AuthDatabase`` in production).
- ``request_data`` — the already-parsed JSON body, captured once at the
  ingress boundary (replaces ``_current_request_data()`` reads).
- ``resolve_project`` / ``assistant_meta_fn`` / ``project_merge_fn`` /
  ``schedule_autoname`` / ``badge_fn`` — the entry's project, metadata,
  titler, and badge shapers, passed in — never imported back.

Saver skip semantics are verbatim: supervised-owned rows, empty
failures, ``[CANCELLED]`` status lines, ``ui == 'system'`` rows, and
cancelled turns are never persisted.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def persist_auth_user_message(
    db: Any,
    chat_session_id: Any,
    message_text: str,
    *,
    metadata: Optional[dict] = None,
    project_merge_fn: Callable[..., Optional[dict]],
    request_data: Optional[dict] = None,
) -> None:
    """Save a user turn for authenticated slash-command chats."""
    if chat_session_id is None or not message_text:
        return
    try:
        metadata = project_merge_fn(metadata, request_data, chat_session_id)
        db.add_message(chat_session_id, 'user', message_text, metadata=metadata or None)
    except Exception as e:
        print(f"[CHAT] persist user message failed: {e}")


def persist_user_turn(
    db: Any,
    session_id: Any,
    *,
    message_text: str,
    history_text: str,
    base_meta: Optional[dict] = None,
    identity: Optional[dict] = None,
    badge_fn: Callable[..., Optional[dict]],
    project_merge_fn: Callable[..., Optional[dict]],
    request_data: Optional[dict] = None,
) -> None:
    """Persist the user turn as history sees it (no digest, keeps thumbnails)."""
    if session_id is None or not history_text:
        return
    turn_meta = dict(base_meta) if isinstance(base_meta, dict) else {}
    try:
        badge = badge_fn(message_text, session_id, identity=identity)
        if badge and 'slash_command' not in turn_meta:
            turn_meta['slash_command'] = badge['slash_command']
    except Exception:
        pass
    try:
        metadata = project_merge_fn(turn_meta or None, request_data, session_id)
        db.add_message(session_id, 'user', history_text, metadata=metadata or None)
    except Exception as e:
        print(f"[CHAT] persist user turn failed: {e}")


def make_assistant_saver(
    *,
    chat_session_id: Any,
    project_path: Optional[str] = None,
    db: Any,
    request_data: Optional[dict] = None,
    resolve_project: Callable[[dict], str],
    assistant_meta_fn: Callable[[dict], Optional[dict]],
    project_merge_fn: Callable[..., Optional[dict]],
    schedule_autoname: Callable[..., None],
    project_root: Any,
) -> Optional[Callable[[dict], None]]:
    """Return the on_result callback that persists assistant replies."""
    if chat_session_id is None:
        return None

    def on_save(res):
        if not res:
            return
        # Supervised orchestration owns and updates its canonical assistant row.
        if res.get('skip_history_persist') or res.get('coordinator_response_message_id'):
            return
        nonlocal_res = res
        try:
            from api.project_actions import prepare_assistant_text_for_actions
            from api.cuttle_ui_capabilities import strip_cuttle_ui_capabilities
            sid = f"db_session_{chat_session_id}"
            text = nonlocal_res.get('response') or ''
            if isinstance(text, str):
                stripped = strip_cuttle_ui_capabilities(text)
                if stripped != text:
                    nonlocal_res = dict(nonlocal_res)
                    nonlocal_res['response'] = stripped
                    if isinstance(res, dict):
                        res['response'] = stripped
                    text = stripped
            if isinstance(text, str) and (
                '<cuttle_confirm' in text.lower()
                or '<cuttle_action_form' in text.lower()
                or '<cuttle_widget' in text.lower()
            ):
                proj = project_path or ''
                if not proj:
                    try:
                        proj = resolve_project({'session_id': chat_session_id}) or ''
                    except Exception:
                        proj = ''
                rewritten = text
                if '<cuttle_confirm' in text.lower() or '<cuttle_action_form' in text.lower():
                    rewritten = prepare_assistant_text_for_actions(
                        rewritten, session_id=sid, project_path=proj
                    )
                if '<cuttle_widget' in rewritten.lower():
                    try:
                        from api.chat_widgets import rewrite_assistant_text_widgets
                        rewritten = rewrite_assistant_text_widgets(
                            rewritten,
                            session_id=chat_session_id,
                            project_path=proj,
                        )
                    except Exception as _we:
                        print(f"[CHAT] cuttle_widget rewrite on save failed: {_we}", flush=True)
                if rewritten != text:
                    nonlocal_res = dict(nonlocal_res)
                    nonlocal_res['response'] = rewritten
                    # Mutate original so streaming callers also see rewritten text
                    if isinstance(res, dict):
                        res['response'] = rewritten
                    text = rewritten
            # Stage local ![…](E:\…) / <media src="…"> into /output/shared/
            if isinstance(text, str) and ('![' in text or '<media' in text.lower()):
                try:
                    from api.shared_media import rewrite_local_media_refs

                    staged_text, n_staged = rewrite_local_media_refs(
                        text, project_root=project_root
                    )
                    if n_staged and staged_text != text:
                        nonlocal_res = dict(nonlocal_res)
                        nonlocal_res['response'] = staged_text
                        if isinstance(res, dict):
                            res['response'] = staged_text
                        print(
                            f"[CHAT] staged {n_staged} local media ref(s) → /output/shared/",
                            flush=True,
                        )
                except Exception as _me:
                    print(f"[CHAT] shared media rewrite on save failed: {_me}", flush=True)
        except Exception as e:
            print(f"[CHAT] cuttle_confirm rewrite on save failed: {e}", flush=True)

        text = (nonlocal_res.get('response') or '').strip()
        if not nonlocal_res.get('success') and not text:
            return
        # Status-line events — not assistant bubbles (Stop, /model set, …).
        if text.startswith('[CANCELLED]'):
            return
        try:
            from api.chat_delivery import is_turn_cancelled

            if is_turn_cancelled(chat_session_id):
                return
        except Exception:
            pass
        if str(nonlocal_res.get('ui') or '').strip().lower() == 'system':
            return
        asst_meta = assistant_meta_fn(nonlocal_res)
        if isinstance(nonlocal_res.get('routing_badge'), dict):
            asst_meta = dict(asst_meta or {}, routing_badge=nonlocal_res['routing_badge'])
        asst_meta = project_merge_fn(
            asst_meta,
            request_data,
            chat_session_id,
            nonlocal_res.get('cursor_run') if isinstance(nonlocal_res.get('cursor_run'), dict) else None,
        )
        try:
            from api.subagents.service import attach_batches_to_assistant_meta

            asst_meta = attach_batches_to_assistant_meta(
                chat_session_id, asst_meta or {}
            ) or asst_meta
        except Exception as _sa:
            print(f"[CHAT] subagent attach meta failed: {_sa}", flush=True)
        try:
            mid = db.add_message(
                chat_session_id,
                'assistant',
                nonlocal_res.get('response', '') or '',
                metadata=asst_meta or None,
            )
            try:
                from api.subagents.store import bind_unattached_batches

                if mid:
                    bind_unattached_batches(db, chat_session_id, int(mid))
            except Exception as _sb:
                print(f"[CHAT] subagent bind failed: {_sb}", flush=True)
        except Exception as e:
            print(f"[CHAT] persist assistant message failed: {e}")
            return
        try:
            schedule_autoname(chat_session_id)
        except Exception as _te:
            print(f"[TITLER] hook failed: {_te}")
        # Experimental (opt-in, no-op when the flag is off): reward progress for
        # the completed turn. Fire-and-forget — never delay reply delivery.
        try:
            from api import achievements as _achievements

            _achievements.on_turn_saved(chat_session_id, nonlocal_res)
        except Exception:
            pass

    return on_save
