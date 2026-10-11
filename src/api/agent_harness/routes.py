"""Agent harness catalog, defaults, and context HTTP routes."""
from flask import Blueprint, jsonify, request
from api.http_authz import authenticated_required, owner_required, require_session_actor

agent_harness_bp = Blueprint("agent_harness_api", __name__)
_resolve_path = lambda data: ""

def configure_context_routes(resolve_path, require_actor):
    global _resolve_path, _require_actor
    _resolve_path, _require_actor = resolve_path, require_actor

_require_actor = require_session_actor
@agent_harness_bp.route('/api/agent-context', methods=['GET'])
@authenticated_required
def api_agent_context_status():
    """Context-window fill estimate for Cursor / Muse / OpenCode composer radial."""
    try:
        from api.agent_context import get_agent_context_status, normalize_agent_id

        agent = normalize_agent_id(request.args.get('agent') or '')
        if not agent:
            return jsonify({
                'success': False,
                'supported': False,
                'error': 'agent query required (cursor|muse|opencode)',
            }), 400
        session = (request.args.get('session') or '').strip()
        path = (request.args.get('path') or '').strip()
        if not path:
            try:
                path = _resolve_path({
                    'session_id': session,
                    'project_path': request.args.get('project_path'),
                    'project_id': request.args.get('project_id'),
                    'project_name': request.args.get('project_name'),
                }) or ''
            except Exception:
                path = ''
        status = get_agent_context_status(
            chat_session_id=session or None,
            agent_id=agent,
            cwd=path or None,
            live=str(request.args.get('live') or '').strip().lower() in (
                '1', 'true', 'yes',
            ),
        )
        code = 200 if status.get('success') else 400
        return jsonify(status), code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'supported': False}), 500


@agent_harness_bp.route('/api/agent-context/compact', methods=['POST'])
def api_agent_context_compact():
    """Compact / summarize the sticky agent CLI session (Cursor, Muse, OpenCode)."""
    try:
        from api.agent_context import compact_agent_context, normalize_agent_id

        data = request.get_json(silent=True) or {}
        agent = normalize_agent_id(
            data.get('agent') or request.args.get('agent') or ''
        )
        if not agent:
            return jsonify({
                'success': False,
                'error': 'agent required (cursor|muse|opencode)',
            }), 400
        session = (
            data.get('session')
            or data.get('session_id')
            or data.get('chat_session_id')
            or request.args.get('session')
            or ''
        )
        session = str(session).strip()
        _user, _sid, err = _require_actor(session)
        if err:
            return err
        path = (data.get('path') or data.get('project_path') or '').strip()
        if not path:
            try:
                path = _resolve_path(data if isinstance(data, dict) else {}) or ''
            except Exception:
                path = ''
        result = compact_agent_context(
            chat_session_id=session or None,
            agent_id=agent,
            cwd=path or None,
        )
        code = 200 if result.get('success') else 400
        return jsonify(result), code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_harness_bp.route('/api/agents', methods=['GET'])
def get_agents_catalog_api():
    """Harness agent catalog (manifests + availability). Frontend drives palette from this."""
    try:
        from api.agent_harness.catalog import discovery_roots, public_catalog

        project_path = (
            request.args.get('path')
            or request.args.get('project_path')
            or ''
        ).strip() or None
        agents = public_catalog(project_path)
        # Settings asks for `?live_models=1` so its dropdowns read the same live
        # CLI/API catalog as the chat palette instead of the manifest snapshot.
        # The chat page's plain `/api/agents` stays cheap (no CLI calls).
        live = (request.args.get('live_models') or '').strip().lower() in (
            '1', 'true', 'yes', 'live',
        )
        if live:
            try:
                from api.agent_harness.agent_models import enrich_agent_catalog

                enrich_agent_catalog(agents)
            except Exception as exc:  # noqa: BLE001 — keep the manifest snapshot
                print(f"[api/agents] live model enrichment failed: {exc}", flush=True)
        return jsonify({
            'success': True,
            'agents': agents,
            'count': len(agents),
            'live_models': live,
            'roots': discovery_roots(project_path),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_harness_bp.route('/api/agent-defaults/<agent_id>', methods=['GET'])
def get_agent_defaults_api(agent_id):
    """Unified starred model/effort defaults for one agent (palette stars).

    Query: ?session=<chat id>. Returns session pin, starred default, and the
    effective preferred value in policy order (session → starred → CLI).
    """
    try:
        from api.agent_harness.agent_defaults import (
            get_starred_effort,
            get_starred_model,
            normalize_agent_id,
        )

        aid = normalize_agent_id(agent_id)
        session = (request.args.get('session') or '').strip()
        sess_m = sess_e = None
        try:
            if aid == 'muse' and session:
                from scripts.utilities.muse_cli_session_store import (
                    load_muse_effort,
                    load_muse_model,
                )

                sess_m, sess_e = load_muse_model(session), load_muse_effort(session)
            elif aid == 'hermes' and session:
                from scripts.utilities.hermes_cli_session_store import (
                    load_hermes_effort,
                    load_hermes_model,
                )

                sess_m, sess_e = load_hermes_model(session), load_hermes_effort(session)
            elif aid == 'opencode' and session:
                from api.agent_harness.agents.opencode.session_store import (
                    load_opencode_effort,
                    load_opencode_model,
                )

                sess_m, sess_e = load_opencode_model(session), load_opencode_effort(session)
            elif aid == 'codex' and session:
                from scripts.utilities.codex_cli_session_store import (
                    load_codex_effort,
                    load_codex_model,
                )

                sess_m, sess_e = load_codex_model(session), load_codex_effort(session)
            elif aid == 'cursor' and session:
                from scripts.utilities.cursor_cli_session_store import load_cursor_agent_options

                opts = load_cursor_agent_options('', session) or {}
                sess_m = opts.get('model')
        except Exception:
            pass
        star_m, star_e = get_starred_model(aid), get_starred_effort(aid)
        return jsonify({
            'success': True,
            'agent': aid,
            'sessionModel': sess_m,
            'sessionEffort': sess_e,
            'starredModel': star_m,
            'starredEffort': star_e,
            'preferredModel': sess_m or star_m or '',
            'preferredModelSource': 'session' if sess_m else ('starred' if star_m else 'cli_default'),
            'preferredEffort': (sess_e or star_e or ''),
            'preferredEffortSource': 'session' if sess_e else ('starred' if star_e else 'none'),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_harness_bp.route('/api/agent-defaults/<agent_id>', methods=['POST'])
@owner_required
def set_agent_defaults_api(agent_id):
    """Star (or clear) the global model/effort default for one agent.

    Body: { starred_model?, starred_effort? } — empty string clears.
    Cursor has no separate effort (baked into the model id); posting
    starred_effort for cursor returns 400.
    """
    try:
        from api.agent_harness.agent_defaults import (
            get_starred_effort,
            get_starred_model,
            normalize_agent_id,
            set_starred_effort,
            set_starred_model,
        )

        aid = normalize_agent_id(agent_id)
        if not aid:
            return jsonify({'success': False, 'error': 'unknown agent'}), 404
        from api.agent_harness.agent_defaults import agent_capabilities

        caps = agent_capabilities(aid)
        data = request.get_json(silent=True) or {}
        if aid in ('codex', 'claude'):
            model_key = 'starred_model' if 'starred_model' in data else 'starredModel'
            effort_key = 'starred_effort' if 'starred_effort' in data else 'starredEffort'
            has_model = 'starred_model' in data or 'starredModel' in data
            has_effort = 'starred_effort' in data or 'starredEffort' in data
            target_model = (
                str(data.get(model_key) or '').strip()
                if has_model
                else (get_starred_model(aid) or '')
            )
            target_effort = (
                str(data.get(effort_key) or '').strip().lower()
                if has_effort
                else (get_starred_effort(aid) or '')
            )
            if target_effort in ('default', 'reset', 'clear', 'none'):
                target_effort = ''
            if target_effort:
                if aid == 'claude':
                    from api.agent_harness.agents.claude.model_catalog import (
                        claude_efforts_for_model as _efforts_for_model,
                    )
                    agent_label, default_note = 'Claude Code', ' for the CLI default model'
                else:
                    from api.agent_harness.agents.codex.model_catalog import (
                        codex_efforts_for_model as _efforts_for_model,
                    )
                    agent_label, default_note = 'Codex', ' across all Codex models'

                supported = _efforts_for_model(target_model or None)
                if target_effort not in supported:
                    model_note = (
                        f" for `{target_model}`"
                        if target_model
                        else default_note
                    )
                    values = ', '.join(supported) or 'none verified'
                    return jsonify({
                        'success': False,
                        'error': (
                            f"{agent_label} effort `{target_effort}` is not supported{model_note}. "
                            f"Supported levels: {values}."
                        ),
                    }), 400
        out = {'success': True, 'agent': aid}
        if 'starred_model' in data or 'starredModel' in data:
            if not caps.get('supports_model_pin', True):
                return jsonify({
                    'success': False,
                    'error': f'{aid} does not support pinning a model.',
                }), 400
            raw = data.get('starred_model', data.get('starredModel'))
            out['starredModel'] = set_starred_model(aid, raw)
        if 'starred_effort' in data or 'starredEffort' in data:
            if not caps.get('supports_effort', True):
                return jsonify({
                    'success': False,
                    'error': f'{aid} bakes effort into the model id — star a model instead.',
                }), 400
            raw = data.get('starred_effort', data.get('starredEffort'))
            out['starredEffort'] = set_starred_effort(aid, raw)
        return jsonify(out)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

