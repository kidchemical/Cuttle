"""Codex palette HTTP — models, per-chat model pin, per-chat effort pin.

Same contract as ``/api/claude/models|model|effort`` so the chat page treats
every badge-gated harness alike. Owned by the Codex agent slice (not the
monolith); registered by ``web_chat_api`` as ``codex_bp``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import require_session_actor as _require_session_actor

codex_bp = Blueprint("codex_agent", __name__, url_prefix="/api/codex")


@codex_bp.route("/models", methods=["GET"])
def api_codex_models():
    """Codex models for the chat slash palette (``codex debug models`` + this chat's pick)."""
    try:
        from api.agent_harness.agents.codex.model_catalog import (
            codex_common_reasoning_efforts,
            codex_model_label,
            list_codex_palette_models,
        )
        from scripts.utilities.codex_cli_session_store import load_codex_model

        from api.agent_harness.agent_defaults import get_starred_model as _starred_c

        session = (request.args.get('session') or '').strip()
        refresh = (request.args.get('refresh') or '').strip().lower() in (
            '1', 'true', 'yes', 'refresh',
        )
        q = (request.args.get('q') or '').strip() or None
        try:
            limit_raw = request.args.get('limit')
            limit = int(limit_raw) if limit_raw not in (None, '') else None
        except (TypeError, ValueError):
            limit = None

        catalog = list_codex_palette_models(refresh=refresh, q=q, limit=limit)
        _sess_c = (load_codex_model(session) if session else None)
        _star_c = _starred_c('codex')
        preferred = _sess_c or _star_c or ''
        preferred_source = 'session' if _sess_c else ('starred' if _star_c else 'cli_default')
        pref_l = (preferred or '').lower()
        models = [
            {**m, 'current': str(m.get('id') or '').lower() == pref_l}
            for m in (catalog.get('models') or [])
            if isinstance(m, dict)
        ]
        if preferred and not any(m.get('current') for m in models):
            models.insert(0, {
                'id': preferred,
                'label': codex_model_label(preferred),
                'description': 'Custom model id pinned for this chat',
                'current': True,
            })
        return jsonify({
            'success': True,
            'models': models,
            'preferredModel': preferred,
            'preferredSource': preferred_source,
            'starredModel': _star_c,
            'sessionModel': _sess_c,
            'defaultModel': '',
            'source': catalog.get('source'),
            'count': catalog.get('count'),
            'returned': catalog.get('returned'),
            'commonEfforts': codex_common_reasoning_efforts(),
            'fetchedAt': catalog.get('fetched_at'),
            'error': catalog.get('error'),
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'models': [],
            'preferredModel': '',
        }), 500


@codex_bp.route("/model", methods=["POST"])
def api_set_codex_model():
    """Body: { session, model } — pin the Codex model for one chat ('' resets)."""
    try:
        from api.agent_harness.agents.codex.model_catalog import codex_model_label
        from scripts.utilities.codex_cli_session_store import save_codex_model

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
        from api.agent_harness.agent_defaults import get_starred_model as _starred_c2

        saved = save_codex_model(session, model) or _starred_c2('codex') or ''
        return jsonify({
            'success': True,
            'preferredModel': saved,
            'label': codex_model_label(saved) if saved else '',
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@codex_bp.route("/effort", methods=["GET"])
def api_codex_effort():
    """Codex reasoning-effort levels for the chat slash palette, plus this chat's pin."""
    try:
        from api.agent_harness.agents.codex.model_catalog import codex_efforts_for_model
        from scripts.utilities.codex_cli_session_store import (
            load_codex_effort,
            load_codex_model,
        )

        from api.agent_harness.agent_defaults import (
            get_starred_effort as _starred_ce,
            get_starred_model as _starred_cm,
        )

        session = (request.args.get('session') or '').strip()
        model = (
            (load_codex_model(session) if session else None)
            or _starred_cm('codex')
            or (request.args.get('model') or '').strip()
        )
        _sess_ce = (load_codex_effort(session) if session else None) or ''
        _star_ce = _starred_ce('codex') or ''
        preferred = _sess_ce or _star_ce
        levels = [
            {'id': str(e), 'current': str(e) == preferred}
            for e in codex_efforts_for_model(model or None)
        ]
        return jsonify({
            'success': True,
            'levels': levels,
            'model': model or '',
            'preferredEffort': preferred,
            'preferredSource': 'session' if _sess_ce else ('starred' if _star_ce else 'none'),
            'starredEffort': _star_ce or None,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'levels': []}), 500


@codex_bp.route("/effort", methods=["POST"])
def api_set_codex_effort():
    """Body: { session, effort } — pin Codex ``model_reasoning_effort`` ('' resets)."""
    try:
        from api.agent_harness.agents.codex.model_catalog import codex_efforts_for_model
        from scripts.utilities.codex_cli_session_store import (
            load_codex_model,
            save_codex_effort,
        )
        from api.agent_harness.agent_defaults import get_starred_model as _starred_cm

        data = request.get_json(silent=True) or {}
        session = str(data.get('session') or data.get('session_id') or '').strip()
        if not session:
            return jsonify({'success': False, 'error': 'session is required'}), 400
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        effort = str(data.get('effort') or '').strip().lower()
        if effort.lower() in ('default', 'reset', 'clear', 'none'):
            effort = ''
        model = (
            str(data.get('model') or '').strip()
            or load_codex_model(session)
            or _starred_cm('codex')
        )
        known = codex_efforts_for_model(model or None)
        if effort and effort not in known:
            detail = (
                f"Supported for `{model}`: {', '.join(known)}."
                if model and known
                else f"Common supported levels: {', '.join(known)}."
                if known
                else "No verified effort levels are available for the current model."
            )
            return jsonify({
                'success': False,
                'error': (
                    f"Effort `{effort}` is not supported for the current Codex model. "
                    f"{detail}"
                ),
                'model': model or '',
                'levels': known,
            }), 400
        saved = save_codex_effort(session, effort) or ''
        return jsonify({
            'success': True,
            'preferredEffort': saved,
            'model': model or '',
            'levels': known,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
