"""Hermes palette HTTP — models, per-chat model pin, per-chat effort pin.

Same contract as ``/api/codex/models|model|effort`` so the chat page treats
every badge-gated harness alike. Owned by the Hermes agent slice (not the
monolith); registered by ``web_chat_api`` as ``hermes_bp``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import require_session_actor as _require_session_actor

hermes_bp = Blueprint("hermes_agent", __name__, url_prefix="/api/hermes")


@hermes_bp.route("/models", methods=["GET"])
def api_hermes_models():
    """Hermes models for the chat slash palette, plus this chat's pick."""
    try:
        from scripts.utilities.hermes_cli_tool import (
            hermes_model_label,
            list_hermes_known_models,
            resolve_hermes_default_model,
        )
        from scripts.utilities.hermes_cli_session_store import load_hermes_model

        from api.agent_harness.agent_defaults import get_starred_model as _starred_h

        session = (request.args.get('session') or '').strip()
        _sess_h = (load_hermes_model(session) if session else None)
        _star_h = _starred_h('hermes')
        preferred = _sess_h or _star_h or resolve_hermes_default_model()
        preferred_source = 'session' if _sess_h else ('starred' if _star_h else 'cli_default')
        pref_l = (preferred or '').lower()
        models = [
            {**m, 'current': m['id'].lower() == pref_l}
            for m in list_hermes_known_models()
        ]
        if not any(m['current'] for m in models):
            models.append({
                'id': preferred,
                'label': hermes_model_label(preferred),
                'description': 'Custom model id pinned for this chat',
                'current': True,
            })
        return jsonify({
            'success': True,
            'models': models,
            'preferredModel': preferred,
            'preferredSource': preferred_source,
            'starredModel': _star_h,
            'sessionModel': _sess_h,
            'defaultModel': resolve_hermes_default_model(),
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'models': [],
            'preferredModel': 'qwen3-coder',
        }), 500


@hermes_bp.route("/model", methods=["POST"])
def api_set_hermes_model():
    """Body: { session, model } — pin the Hermes model for one chat ('' resets)."""
    try:
        from scripts.utilities.hermes_cli_tool import (
            hermes_model_label,
            resolve_hermes_default_model,
        )
        from scripts.utilities.hermes_cli_session_store import save_hermes_model

        data = request.get_json(silent=True) or {}
        session = str(data.get('session') or data.get('session_id') or '').strip()
        if not session:
            return jsonify({'success': False, 'error': 'session is required'}), 400
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        model = str(data.get('model') or '').strip()
        if model.lower() in ('default', 'reset', 'clear'):
            model = ''
        from api.agent_harness.agent_defaults import get_starred_model as _starred_h2

        saved = save_hermes_model(session, model) or _starred_h2('hermes') or resolve_hermes_default_model()
        return jsonify({
            'success': True,
            'preferredModel': saved,
            'label': hermes_model_label(saved),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@hermes_bp.route("/effort", methods=["GET"])
def api_hermes_effort():
    """Hermes reasoning-effort levels for the chat slash palette, plus this chat's pin."""
    try:
        from scripts.utilities.hermes_cli_tool import HERMES_REASONING_EFFORTS
        from scripts.utilities.hermes_cli_session_store import load_hermes_effort

        from api.agent_harness.agent_defaults import get_starred_effort as _starred_he

        session = (request.args.get('session') or '').strip()
        _sess_he = (load_hermes_effort(session) if session else None) or ''
        _star_he = _starred_he('hermes') or ''
        preferred = _sess_he or _star_he
        levels = [
            {'id': str(e), 'current': str(e) == preferred}
            for e in HERMES_REASONING_EFFORTS
        ]
        return jsonify({
            'success': True,
            'levels': levels,
            'preferredEffort': preferred,
            'preferredSource': 'session' if _sess_he else ('starred' if _star_he else 'none'),
            'starredEffort': _star_he or None,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'levels': []}), 500


@hermes_bp.route("/effort", methods=["POST"])
def api_set_hermes_effort():
    """Body: { session, effort } — pin Hermes reasoning effort for one chat ('' resets)."""
    try:
        from scripts.utilities.hermes_cli_tool import HERMES_REASONING_EFFORTS
        from scripts.utilities.hermes_cli_session_store import save_hermes_effort

        data = request.get_json(silent=True) or {}
        session = str(data.get('session') or data.get('session_id') or '').strip()
        if not session:
            return jsonify({'success': False, 'error': 'session is required'}), 400
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        effort = str(data.get('effort') or '').strip().lower()
        known = [str(e) for e in HERMES_REASONING_EFFORTS]
        if effort.lower() in ('default', 'reset', 'clear'):
            effort = ''
        if effort and effort not in known:
            return jsonify({
                'success': False,
                'error': f"Unknown effort `{effort}`. Pick one of: {', '.join(known)}.",
            }), 400
        saved = save_hermes_effort(session, effort) or ''
        return jsonify({'success': True, 'preferredEffort': saved})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
