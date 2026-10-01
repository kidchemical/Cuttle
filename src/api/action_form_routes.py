"""Action-form HTTP surface — the single owner for action-form transport.

Ownership contract (do not split across the monolith again):
- Transport only: request parsing, auth gating, response shaping, and the
  thin orchestration already present in these handlers (restart-status
  attach, one-shot lock persist, resume inject). Domain logic lives in
  ``api.action_forms`` (+ ``api.project_actions``, ``api.flask_restart``).
  Never add form semantics here.
- Authorization: each handler enforces its own checks inline, frozen below:
  ``run`` / ``dismiss`` / ``followup-message`` require chat-session access
  (or plain authentication when no session is given to ``run``);
  ``watch-state`` requires authentication only. Restart-controller dismiss
  short-circuits pre-auth by design (blind client retries must not poison
  shared controllers).
- Imports from ``api.http_authz`` / ``api.action_forms`` /
  ``api.project_actions`` / ``api.flask_restart`` / ``api.auth_db`` /
  ``api.auth_session`` stay function-lazy (as in the monolith) so tests
  patching those modules keep working.
- This module must never import ``api.web_chat_api`` (Phase 2 rule:
  new subsystems do not reach back into the monolith for globals).

Route contract is frozen: same paths, methods, status codes and payload
shapes as when these handlers lived on the monolith app object.
"""

from __future__ import annotations

import json
import re

from flask import Blueprint, jsonify, request

action_forms_bp = Blueprint("action_forms", __name__, url_prefix="/api")


@action_forms_bp.route('/action-form/followup-message', methods=['POST'])
def api_action_form_followup_message():
    """Persist a programmatic follow-up action form (e.g. Discord confirm after Steam)."""
    try:
        data = request.get_json(silent=True) or {}
        spec = data.get('spec')
        if not isinstance(spec, dict):
            return jsonify({'success': False, 'error': 'missing spec'}), 400
        preface = str(data.get('preface') or 'Steam upload succeeded.').strip()
        session_id = data.get('session_id') or data.get('session')
        if session_id is not None:
            session_id = str(session_id).strip()
            if session_id.isdigit():
                session_id = f'db_session_{session_id}'

        from api.http_authz import require_chat_session_access
        from api.project_actions import prepare_assistant_text_for_actions
        from api.action_forms import resolve_session_project_path
        from api.auth_db import get_auth_db

        _user, nid, err = require_chat_session_access(session_id)
        if err:
            return err
        session_id = f'db_session_{nid}'

        project_path = (data.get('project_path') or '').strip() or resolve_session_project_path(session_id) or ''
        blob = json.dumps(spec, ensure_ascii=False)
        text = f"{preface}\n\n<cuttle_action_form>\n{blob}\n</cuttle_action_form>"
        rewritten = prepare_assistant_text_for_actions(
            text,
            session_id=str(session_id),
            project_path=str(project_path or ''),
        )
        get_auth_db().add_message(nid, 'assistant', rewritten)
        return jsonify({'success': True, 'response': rewritten, 'session_id': nid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@action_forms_bp.route('/action-form/dismiss', methods=['POST'])
def api_action_form_dismiss():
    """
    Lock/collapse open action forms the user ignored by sending a follow-up
    instead of clicking. No action side effects — history patch only.
    """
    try:
        data = request.get_json(silent=True) or {}
        form_ids = data.get('form_ids') if isinstance(data.get('form_ids'), list) else []
        form_id_one = str(data.get('form_id') or '').strip()
        if form_id_one and form_id_one not in form_ids:
            form_ids = list(form_ids) + [form_id_one]
        form_ids = [str(x).strip() for x in form_ids if str(x).strip()]
        # Soft follow-up dismiss must never poison shared Flask restart controllers
        # (id flask-restart-g{N} is reused until a real restart bumps generation).
        form_ids = [
            fid
            for fid in form_ids
            if not re.match(r"^flask-restart-g\d+$", fid, re.I)
        ]
        session_id = data.get('session_id') or data.get('session')
        if session_id is not None:
            session_id = str(session_id).strip()
            if session_id.isdigit():
                session_id = f'db_session_{session_id}'
        toast = str(data.get('toast') or 'Ignored').strip() or 'Ignored'
        if not session_id:
            return jsonify({'success': False, 'error': 'missing session_id'}), 400
        if not form_ids:
            # All ids were flask-restart controllers (or empty) — nothing to lock.
            return jsonify({'success': True, 'locked': [], 'toast': toast, 'skipped': True})

        from api.http_authz import require_chat_session_access

        _user, nid, err = require_chat_session_access(session_id)
        if err:
            return err
        session_id = f'db_session_{nid}'

        from api.action_forms import mark_action_form_consumed_in_history

        locked = []
        for fid in form_ids[:40]:
            ok = mark_action_form_consumed_in_history(
                session_id=session_id,
                form_id=fid,
                selected=[],
                toast=toast,
            )
            if ok:
                locked.append(fid)
        return jsonify({'success': True, 'locked': locked, 'toast': toast})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@action_forms_bp.route('/action-form/run', methods=['POST'])
def api_action_form_run():
    """Run a cuttle_action_form submission (no LLM). Prefers silent toast UX."""
    try:
        data = request.get_json(silent=True) or {}
        token = (data.get('token') or data.get('form_token') or data.get('fallback') or '').strip()
        selection = data.get('selection') if isinstance(data.get('selection'), dict) else {}
        form_id_hint = (data.get('form_id') or '').strip() or None
        spec_override = data.get('spec') if isinstance(data.get('spec'), dict) else None
        session_id = data.get('session_id') or data.get('session')
        if session_id is not None:
            session_id = str(session_id).strip()
            if session_id.isdigit():
                session_id = f'db_session_{session_id}'
            elif not session_id.startswith('db_session_') and session_id:
                pass
        if not token and not spec_override and not form_id_hint:
            return jsonify({'success': False, 'toast': 'Missing form token.', 'type': 'action_form'}), 400

        from api.http_authz import require_chat_session_access, require_authenticated

        if session_id:
            auth_user, _nid, err = require_chat_session_access(session_id)
            if err:
                toast_err = err[0].get_json() if hasattr(err[0], 'get_json') else None
                msg = (toast_err or {}).get('error') or 'Not authenticated.'
                code = err[1]
                return jsonify({'success': False, 'toast': msg, 'type': 'action_form'}), code
        else:
            auth_user, err = require_authenticated()
            if err:
                return jsonify({'success': False, 'toast': 'Not authenticated.', 'type': 'action_form'}), 401

        from api.action_forms import (
            execute_action_form_submission,
            mark_action_form_consumed_in_history,
            resolve_session_project_path,
        )

        # Prefer live project chip from the client, then the session's saved project.
        project_override = (data.get('project_path') or '').strip() or resolve_session_project_path(session_id)

        result = execute_action_form_submission(
            form_token=token,
            selection=selection,
            session_id=session_id,
            project_path_override=project_override or None,
            form_id_hint=form_id_hint,
            owner_user_id=int(auth_user['id']),
            spec_override=spec_override,
        )
        form_id = result.get('form_id') or form_id_hint
        result['form_id'] = form_id
        # The card's own chat owns the side effects, not whatever session the
        # client thought was open.
        session_id = result.get('session_id') or session_id

        # A restart requested from a card reports progress on the card, so the
        # client needs the id to follow it across the Flask replacement.
        spec_patch = {}
        scheduled_restart = any(
            str(r.get('action') or '') == 'flask.restart'
            and str((r.get('params') or {}).get('mode') or 'graceful').lower() != 'status'
            and r.get('success')
            for r in (result.get('results') or [])
        )
        if scheduled_restart:
            try:
                from api.flask_restart import read_status

                st = read_status() or {}
                rid = str(st.get('restart_id') or '')
                if rid:
                    result['flask_restart'] = {
                        'restart_id': rid,
                        'state': st.get('state'),
                        'mode': st.get('mode'),
                    }
                    if str(st.get('state')) not in ('rejected', 'cancelled'):
                        spec_patch['restartId'] = rid
            except Exception as _re:
                print(f"[CHAT] restart status attach failed: {_re}", flush=True)

        # Persist one-shot lock + selection label in history (survives refresh).
        reusable = bool(result.get('reusable'))
        lock = (result.get('lock') or 'form').lower()
        should_persist_lock = (
            result.get('success')
            and not reusable
            and lock == 'form'
            and not result.get('already_locked')
        )
        if should_persist_lock and session_id and form_id:
            try:
                mark_action_form_consumed_in_history(
                    session_id=session_id,
                    form_id=str(form_id),
                    selected=list(result.get('selected') or []),
                    toast=str(result.get('toast') or ''),
                    spec_patch=spec_patch or None,
                )
            except Exception as _me:
                print(f"[CHAT] action form lock persist failed: {_me}", flush=True)

        # Opt-in resume: Q&A form with "resume": true hands the client the answer
        # text to send as a normal turn. /api/chat persists that turn, so this
        # route must not also write it (that showed every answer twice).
        # Side-effect / watch / flask forms never set resume, so they stay silent.
        if result.get('success') and result.get('resume') and session_id:
            try:
                sid_str = str(session_id)
                numeric = sid_str[11:] if sid_str.startswith('db_session_') else sid_str
                if numeric.isdigit():
                    selected = list(result.get('selected') or [])
                    label = str(result.get('selected_label') or (selected[0] if selected else '')).strip()
                    oid = str(selected[0] if selected else '').strip()
                    oid_l = oid.lower()
                    # Short, agent-friendly resumes for common plan-bridge picks.
                    if result.get('answer_text'):
                        user_text = str(result['answer_text'])
                    elif oid_l == 'go':
                        user_text = 'go'
                    elif oid_l == 'revise':
                        user_text = 'I want changes to the plan.'
                    else:
                        user_text = (
                            f"[form-selection] {label} ({oid})"
                            if oid and label != oid
                            else f"[form-selection] {oid or label}"
                        )
                    if len(user_text) > 2000:
                        user_text = user_text[:2000]
                    result['injected_user_message'] = user_text
                    result['should_resume'] = True
            except Exception as _re2:
                print(f"[CHAT] form resume inject failed: {_re2}", flush=True)

        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'toast': str(e),
            'type': 'action_form',
            'silent': True,
        }), 500


@action_forms_bp.route('/action-form/watch-state', methods=['POST'])
def api_action_form_watch_state():
    """Persist a watch card's own job snapshot so a later run cannot paint over it."""
    try:
        data = request.get_json(silent=True) or {}
        form_id = str(data.get('form_id') or '').strip()
        session_id = data.get('session_id') or data.get('session')
        if session_id is not None:
            session_id = str(session_id).strip()
            if session_id.isdigit():
                session_id = f'db_session_{session_id}'
        if not form_id or not session_id:
            return jsonify({'success': False, 'error': 'missing form_id or session_id'}), 400

        from api.auth_session import get_request_session_token
        from api.auth_db import get_auth_db

        auth_user = None
        try:
            st = get_request_session_token()
            if st:
                auth_user = get_auth_db().verify_auth_session(st)
        except Exception:
            auth_user = None
        if not auth_user:
            return jsonify({'success': False, 'error': 'Not authenticated.'}), 401

        from api.action_forms import patch_action_form_watch_in_history

        snapshot = data.get('snapshot') if isinstance(data.get('snapshot'), dict) else {}
        terminal = bool(data.get('terminal'))
        toast = str(data.get('toast') or snapshot.get('label') or '').strip()
        lock = bool(data.get('lock'))
        ok = patch_action_form_watch_in_history(
            session_id=session_id,
            form_id=form_id,
            snapshot=snapshot,
            terminal=terminal,
            toast=toast,
            lock=lock,
        )
        return jsonify({'success': bool(ok)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
