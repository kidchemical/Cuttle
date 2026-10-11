"""OpenCode palette HTTP — models, per-chat model pin, per-chat effort pin.

Same contract as ``/api/codex/models|model|effort`` so the chat page treats
every badge-gated harness alike. Owned by the OpenCode agent slice (not the
monolith); registered by ``web_chat_api`` as ``opencode_bp``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import require_session_actor as _require_session_actor

opencode_bp = Blueprint("opencode_agent", __name__, url_prefix="/api/opencode")


@opencode_bp.route("/models", methods=["GET"])
def api_opencode_models():
    """OpenCode models for the chat slash palette (live catalog + this chat's pick)."""
    try:
        from api.agent_harness.agents.opencode.adapter import (
            list_opencode_palette_models,
            opencode_model_label,
        )
        from api.agent_harness.agents.opencode.session_store import load_opencode_model

        from api.agent_harness.agent_defaults import get_starred_model as _starred_o

        session = (request.args.get('session') or '').strip()
        refresh = (request.args.get('refresh') or '').strip().lower() in (
            '1', 'true', 'yes', 'refresh',
        )
        provider = (request.args.get('provider') or '').strip() or None
        q = (request.args.get('q') or '').strip() or None
        try:
            limit_raw = request.args.get('limit')
            limit = int(limit_raw) if limit_raw not in (None, '') else None
        except (TypeError, ValueError):
            limit = None

        catalog = list_opencode_palette_models(
            refresh=refresh, provider=provider, q=q, limit=limit
        )
        if refresh:
            try:
                from api.model_pricing import refresh_models_dev_pricing
                refresh_models_dev_pricing(force=True)
            except Exception as _price_exc:
                print(f"[opencode] pricing refresh failed: {_price_exc}", flush=True)
        _sess_o = (load_opencode_model(session) if session else None)
        _star_o = _starred_o('opencode')
        preferred = _sess_o or _star_o or ''
        preferred_source = 'session' if _sess_o else ('starred' if _star_o else 'cli_default')
        pref_l = (preferred or '').lower()
        models = [
            {**m, 'current': str(m.get('id') or '').lower() == pref_l}
            for m in (catalog.get('models') or [])
            if isinstance(m, dict)
        ]
        if preferred and not any(m.get('current') for m in models):
            models.insert(0, {
                'id': preferred,
                'label': opencode_model_label(preferred),
                'description': 'Custom model id pinned for this chat',
                'current': True,
            })
        return jsonify({
            'success': True,
            'models': models,
            'preferredModel': preferred,
            'preferredSource': preferred_source,
            'starredModel': _star_o,
            'sessionModel': _sess_o,
            'defaultModel': '',
            'source': catalog.get('source'),
            'count': catalog.get('count'),
            'returned': catalog.get('returned'),
            'fetchedAt': catalog.get('fetched_at'),
            'error': catalog.get('error'),
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'models': [],
            'preferredModel': 'openrouter/z-ai/glm-5.3-flash',
        }), 500


@opencode_bp.route("/model", methods=["POST"])
def api_set_opencode_model():
    """Body: { session, model } — pin the OpenCode model for one chat ('' resets)."""
    try:
        from api.agent_harness.agents.opencode.adapter import (
            DEFAULT_OPENCODE_MODEL,
            opencode_model_label,
        )
        from api.agent_harness.agents.opencode.session_store import save_opencode_model

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
        from api.agent_harness.agent_defaults import get_starred_model as _starred_o2

        saved = save_opencode_model(session, model) or _starred_o2('opencode') or ''
        return jsonify({
            'success': True,
            'preferredModel': saved,
            'label': opencode_model_label(saved),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@opencode_bp.route("/effort", methods=["GET"])
def api_opencode_effort():
    """OpenCode reasoning-effort levels for the chat slash palette, plus this chat's pin."""
    try:
        from api.agent_harness.agents.opencode.adapter import OPENCODE_REASONING_EFFORTS
        from api.agent_harness.agents.opencode.session_store import load_opencode_effort

        from api.agent_harness.agent_defaults import get_starred_effort as _starred_oe

        session = (request.args.get('session') or '').strip()
        _sess_oe = (load_opencode_effort(session) if session else None) or ''
        _star_oe = _starred_oe('opencode') or ''
        preferred = _sess_oe or _star_oe
        levels = [
            {'id': str(e), 'current': str(e) == preferred}
            for e in OPENCODE_REASONING_EFFORTS
        ]
        return jsonify({
            'success': True,
            'levels': levels,
            'preferredEffort': preferred,
            'preferredSource': 'session' if _sess_oe else ('starred' if _star_oe else 'none'),
            'starredEffort': _star_oe or None,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'levels': []}), 500


@opencode_bp.route("/effort", methods=["POST"])
def api_set_opencode_effort():
    """Body: { session, effort } — pin OpenCode ``--variant`` for one chat ('' resets)."""
    try:
        from api.agent_harness.agents.opencode.adapter import OPENCODE_REASONING_EFFORTS
        from api.agent_harness.agents.opencode.session_store import save_opencode_effort

        data = request.get_json(silent=True) or {}
        session = str(data.get('session') or data.get('session_id') or '').strip()
        if not session:
            return jsonify({'success': False, 'error': 'session is required'}), 400
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        effort = str(data.get('effort') or '').strip().lower()
        known = [str(e) for e in OPENCODE_REASONING_EFFORTS]
        if effort.lower() in ('default', 'reset', 'clear'):
            effort = ''
        if effort and effort not in known:
            return jsonify({
                'success': False,
                'error': f"Unknown effort `{effort}`. Pick one of: {', '.join(known)}.",
            }), 400
        saved = save_opencode_effort(session, effort) or ''
        return jsonify({'success': True, 'preferredEffort': saved})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
