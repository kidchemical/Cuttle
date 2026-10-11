"""Cursor Agent CLI model palette HTTP — models for the chat slash palette.

The route name and URL (``/api/cursor-agent/models``) are unchanged from the
monolith. Owned by the Cursor agent slice (not the monolith); registered by
``web_chat_api`` as ``cursor_bp``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

cursor_bp = Blueprint("cursor_agent", __name__, url_prefix="/api/cursor-agent")


@cursor_bp.route("/models", methods=["GET"])
def api_cursor_agent_models():
    """List Cursor Agent CLI models for the chat slash palette (/model).

    Optional query: path + session → preferred model / last reported / recent runs.
    Optional ``refresh=1`` forces a fresh ``agent models`` CLI run.
    """
    try:
        from api.cursor_agent_commands import (
            list_cursor_agent_models,
            refresh_cursor_agent_models,
        )
        from scripts.utilities.cursor_cli_session_store import (
            load_cursor_agent_options,
            load_cursor_agent_options_for_session,
        )

        refresh = (request.args.get('refresh') or '').strip().lower() in (
            '1', 'true', 'yes', 'refresh',
        )
        if refresh:
            refreshed = refresh_cursor_agent_models()
            models = refreshed.get('models') or []
            catalog_source = refreshed.get('source')
            catalog_error = refreshed.get('error')
            catalog_count = int(refreshed.get('count') or len(models))
        else:
            models = list_cursor_agent_models()
            catalog_source = 'cache_or_cli'
            catalog_error = None
            catalog_count = len(models or [])
        path = (request.args.get('path') or '').strip()
        session = (request.args.get('session') or '').strip()
        preferred = None
        last_reported = None
        recent = []
        # Session alone is enough — path is optional (avoids Auto when project
        # chip hasn't hydrated yet).
        if session:
            if path:
                opts = load_cursor_agent_options(path, session) or {}
            else:
                opts = load_cursor_agent_options_for_session(session) or {}
            preferred = opts.get('model')
            last_reported = opts.get('last_reported_model')
            recent = list(opts.get('recent_runs') or [])[-8:]
            if not last_reported and recent:
                last_reported = recent[-1].get('reported_model')
        # Policy order: session pin → starred default → CLI default (auto).
        session_pin = (preferred or '').strip()
        starred = ''
        if not session_pin:
            try:
                from api.agent_harness.agent_defaults import get_starred_model
                starred = get_starred_model('cursor') or ''
            except Exception:
                starred = ''
        preferred_id = session_pin or starred or 'auto'
        preferred_source = 'session' if session_pin else ('starred' if starred else 'cli_default')
        # `agent models` marks Cursor CLI's global current — rewrite to this
        # chat's preferred model so the slash palette "(current)" is correct.
        pref_l = preferred_id.lower()
        annotated = []
        for m in models or []:
            mid = str(m.get('id') or '').strip()
            annotated.append({
                **m,
                'current': bool(mid) and mid.lower() == pref_l,
            })
        return jsonify({
            'success': True,
            'models': annotated,
            'preferredModel': preferred_id,
            'preferredSource': preferred_source,
            'starredModel': starred or None,
            'lastReportedModel': last_reported,
            'recentRuns': recent,
            'source': catalog_source,
            'count': catalog_count,
            'error': catalog_error,
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'models': [],
            'preferredModel': 'auto',
            'lastReportedModel': None,
            'recentRuns': [],
        }), 500
