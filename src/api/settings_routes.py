"""Settings HTTP surface — the single owner for settings concerns.

Ownership contract (do not split across the monolith again):
- Validation: per-family ``validate_*`` functions in THIS module, or the
  dedicated backend module named in SETTING_FAMILIES (config setters,
  ``starred_*``, ``video_playlists``). Never inline new rules in handlers.
- Persistence: ``managers.settings_manager`` for app settings,
  ``core.config`` for bot/model settings. No other stores.
- Defaults: ``settings_manager`` (app) and ``core.config`` (bot).
- Authorization: reads are ``authenticated_required`` (bot config and
  channel security stay owner-only); every write is ``owner_required``,
  enforced SOLELY by the route decorator — never repeat an inline
  ``require_owner()`` check inside a handler.
- To add a setting: add the key + validator below, persist via one of the
  two stores, gate write owner-only. No changes to ``web_chat_api.py``.

Route contract is frozen: same paths, methods, status codes and payload
shapes as when these handlers lived on the monolith app object.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.http_authz import authenticated_required, owner_required

settings_bp = Blueprint("settings", __name__, url_prefix="/api")

# Pairing (channel-level security) and bot config are optional imports,
# mirroring the monolith: routes answer 503 when the backend is missing.
PAIRING_AVAILABLE = False
try:
    from api.pairing_manager import get_pairing_manager  # noqa: F401
    from managers.settings_manager import get_settings_manager
    PAIRING_AVAILABLE = True
except ImportError:
    get_settings_manager = None  # type: ignore[assignment]

try:
    from core.config import get_config
except ImportError:
    get_config = None


# Family registry: the one place that names every settings group, its
# backend, its validator, and its auth. Add rows here, not new modules.
# backend: "bot-config" | "settings-manager" | named helper module.
SETTING_FAMILIES = (
    {"name": "bot", "routes": ["GET/POST /settings"],
     "backend": "bot-config (core.config setters validate)",
     "validator": "config.set_*", "read": "owner", "write": "owner"},
    {"name": "lan-access", "routes": ["GET/POST /settings/lan-access"],
     "backend": "settings-manager key 'discovery' + api.lan_access (live)",
     "validator": "validate_lan_access_update", "read": "owner", "write": "owner"},
    {"name": "channels", "routes": ["GET/POST /settings/channels"],
     "backend": "settings_manager channel config (pairing-gated)",
     "validator": "validate_channel_update", "read": "owner", "write": "owner"},
    {"name": "starred-slash", "routes": ["GET/POST /settings/starred-slash"],
     "backend": "api.starred_slash", "validator": "set_starred_prefixes",
     "read": "authenticated", "write": "owner"},
    {"name": "starred-project", "routes": ["GET/POST /settings/starred-project"],
     "backend": "api.starred_project", "validator": "set_starred_project",
     "read": "authenticated", "write": "owner"},
    {"name": "ui-layout", "routes": ["GET/POST /settings/ui-layout"],
     "backend": "settings-manager generic key 'ui_layout'",
     "validator": "validate_ui_layout_update", "read": "authenticated", "write": "owner"},
    {"name": "video-background", "routes": ["GET/POST /settings/video-background"],
     "backend": "api.video_playlists + settings-manager generic key",
     "validator": "apply_video_background_update", "read": "authenticated", "write": "owner"},
    {"name": "video-metadata", "routes": ["GET /settings/video-metadata"],
     "backend": "api.video_metadata (YouTube oEmbed proxy, in-process cache)",
     "validator": "resolve_video_metadata (no writes; read-only lookup)",
     "read": "authenticated", "write": "n/a"},
    {"name": "completion-providers", "routes": ["GET/POST /settings/completion-providers"],
     "backend": "api.completion_providers (provider registry + settings keys)",
     "validator": "set_preferred_provider / set_provider_model",
     "read": "authenticated", "write": "owner"},
    {"name": "github-app", "routes": ["GET/POST/DELETE /settings/github-app", "POST /settings/github-app/test"],
     "backend": "api.github_app + settings-manager key 'github_app' (+ server-local key file)",
     "validator": "validate_github_app_update", "read": "authenticated", "write": "owner"},
    {"name": "releases", "routes": ["GET /settings/releases"],
     "backend": "api.releases (read-only, process cache)",
     "validator": "normalize_release", "read": "authenticated", "write": "n/a"},
    {"name": "agent-adapters", "routes": ["GET/POST /settings/agent-adapters"],
     "backend": "api.agent_harness.steer (key 'agent_steer') + catalog (key 'agent_harness'); "
                "model/effort defaults read here, written via POST /agent-defaults/<id>",
     "validator": "validate_agent_adapters_update", "read": "authenticated", "write": "owner"},
    {"name": "app-settings", "routes": ["GET /app-settings"],
     "backend": "settings_manager.get_all_settings() (read-only aggregate)",
     "validator": "none (read-only)", "read": "open", "write": "n/a"},
)


# --- validators (owned here) ---------------------------------------------

def validate_channel_update(data: dict) -> tuple[bool, str]:
    """Channel writes accept only the live channels."""
    if data.get('channel') != 'webchat':
        return False, 'Invalid channel'
    return True, ''


def validate_ui_layout_update(data: dict) -> dict:
    """Merge rail keys; drop retired side-panel keys. Returns the patch."""
    patch: dict = {}
    for key in ('rail_items', 'rail_footer', 'rail_hidden'):
        if key in data and data[key] is not None:
            patch[key] = [str(x) for x in data[key]]
    if isinstance(data.get('layout_version'), int):
        patch['layout_version'] = data['layout_version']
    return patch


def validate_lan_access_update(data: dict) -> tuple[bool, str]:
    """LAN writes require the master flag; mdns is optional."""
    if 'lan_access_enabled' not in data:
        return False, 'lan_access_enabled required'
    return True, ''


def validate_completion_providers_update(data: dict) -> tuple[bool, str, dict]:
    """Normalize a cheap-completion provider/model write.

    Models are free-form on purpose: a hardcoded allowlist goes stale the day
    a provider ships a model, and a silently-rejected save is worse than a
    provider reporting "unknown model" at call time. Only shape is validated
    here; provider ids are checked against the registry.
    """
    from api.completion_providers import get_provider

    patch: dict = {}
    if 'provider' in data:
        wanted = str(data.get('provider') or '').strip().lower()
        if wanted in ('', 'auto', 'none'):
            patch['provider'] = ''
        elif get_provider(wanted) is None:
            return False, f'Unknown completion provider: {wanted}', {}
        else:
            patch['provider'] = wanted

    models = data.get('models')
    if isinstance(models, dict):
        clean: dict = {}
        for key, value in models.items():
            provider_id = str(key or '').strip().lower()
            model_id = str(value or '').strip()
            if get_provider(provider_id) is None:
                return False, f'Unknown completion provider: {provider_id}', {}
            if model_id:
                clean[provider_id] = model_id
        patch['models'] = clean

    if not patch:
        return False, 'provider or models required', {}
    return True, '', patch


# --- bot/model settings (backend: core.config) ----------------------------

def validate_agent_adapters_update(data: dict) -> tuple[bool, str, dict]:
    """Body: ``{steer?: {agent: bool}, allow_project_adapters?: bool}``."""
    from api.agent_harness.steer import STEERABLE_AGENTS

    if not isinstance(data, dict):
        return False, 'Body must be a JSON object', {}
    clean: dict = {}
    steer = data.get('steer')
    if steer is not None:
        if not isinstance(steer, dict) or not steer:
            return False, 'steer must be an object of agent: true/false', {}
        for agent, value in steer.items():
            if agent not in STEERABLE_AGENTS:
                return False, f'{agent} does not support mid-turn steering', {}
            if not isinstance(value, bool):
                return False, f'steer.{agent} must be true or false', {}
        clean['steer'] = dict(steer)
    if 'allow_project_adapters' in data:
        if not isinstance(data['allow_project_adapters'], bool):
            return False, 'allow_project_adapters must be true or false', {}
        clean['allow_project_adapters'] = data['allow_project_adapters']
    if not clean:
        return False, 'Nothing to update', {}
    return True, '', clean


@settings_bp.route('/settings', methods=['GET'])
@owner_required
def get_settings():
    """Get current bot settings"""
    try:
        if get_config is None:
            return jsonify({'success': False, 'error': 'Bot config not available'}), 503
        config = get_config()
        return jsonify({
            'success': True,
            'settings': {
                'agent_stage_mode': config.get_agent_stage_mode(),
                'preferred_llm_model': config.get_preferred_llm_model(),
                'preferred_ollama_model': config.get_preferred_ollama_model(),
                'preferred_tools_llm_model': config.get_preferred_tools_llm_model(),
                'preferred_tools_ollama_model': config.get_preferred_tools_ollama_model(),
                'agent_name': config.get_agent_name(),
                'llm_fallback_enabled': config.is_llm_fallback_enabled(),
                'mode': config.get_mode(),
                'thinking_response': config.should_show_thinking(),
                'debug_mode': config.is_debug_mode(),
                'cursor_agent_method': config.get_cursor_agent_method(),
                'system_prompt_mode': config.get_system_prompt_mode(),
                'custom_system_prompt': config.get_custom_system_prompt()
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@settings_bp.route('/settings', methods=['POST'])
@owner_required
def update_settings():
    """Update bot settings"""
    try:
        if get_config is None:
            return jsonify({'success': False, 'error': 'Bot config not available'}), 503
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'No data provided'
            }), 400

        config = get_config()
        updated_settings = []

        # Update agent stage mode
        if 'agent_stage_mode' in data:
            if config.set_agent_stage_mode(data['agent_stage_mode']):
                updated_settings.append('agent_stage_mode')
            else:
                return jsonify({
                    'success': False,
                    'error': f"Invalid agent stage mode: {data['agent_stage_mode']}"
                }), 400

        # Update preferred LLM model
        if 'preferred_llm_model' in data:
            if config.set_preferred_llm_model(data['preferred_llm_model']):
                updated_settings.append('preferred_llm_model')
            else:
                return jsonify({
                    'success': False,
                    'error': f"Invalid LLM model: {data['preferred_llm_model']}"
                }), 400

        # Update preferred Ollama (local) model
        if 'preferred_ollama_model' in data:
            if config.set_preferred_ollama_model(data['preferred_ollama_model']):
                updated_settings.append('preferred_ollama_model')
            else:
                return jsonify({
                    'success': False,
                    'error': "Preferred Ollama model must be a non-empty string"
                }), 400

        # Update preferred tool-calling LLM model (cloud)
        if 'preferred_tools_llm_model' in data:
            if config.set_preferred_tools_llm_model(data['preferred_tools_llm_model']):
                updated_settings.append('preferred_tools_llm_model')
            else:
                return jsonify({
                    'success': False,
                    'error': f"Invalid tool-calling LLM model: {data['preferred_tools_llm_model']}"
                }), 400

        # Update preferred tool-calling Ollama (local) model
        if 'preferred_tools_ollama_model' in data:
            if config.set_preferred_tools_ollama_model(data['preferred_tools_ollama_model']):
                updated_settings.append('preferred_tools_ollama_model')
            else:
                return jsonify({
                    'success': False,
                    'error': "Preferred tool-calling Ollama model must be a non-empty string"
                }), 400

        # Update agent name
        if 'agent_name' in data:
            if config.set_agent_name(data['agent_name']):
                updated_settings.append('agent_name')
            else:
                return jsonify({
                    'success': False,
                    'error': f"Invalid agent name: {data['agent_name']}"
                }), 400

        # Update LLM fallback setting
        if 'llm_fallback_enabled' in data:
            config.set_llm_fallback_enabled(data['llm_fallback_enabled'])
            updated_settings.append('llm_fallback_enabled')

        # Update other settings if provided
        if 'mode' in data:
            if config.set_mode(data['mode']):
                updated_settings.append('mode')

        if 'thinking_response' in data:
            config.set('thinking_response', data['thinking_response'])
            updated_settings.append('thinking_response')

        if 'debug_mode' in data:
            config.set('debug_mode', data['debug_mode'])
            updated_settings.append('debug_mode')

        if 'cursor_agent_method' in data:
            if config.set_cursor_agent_method(data['cursor_agent_method']):
                updated_settings.append('cursor_agent_method')

        # Update system prompt mode
        if 'system_prompt_mode' in data:
            if config.set_system_prompt_mode(data['system_prompt_mode']):
                updated_settings.append('system_prompt_mode')
            else:
                return jsonify({
                    'success': False,
                    'error': f"Invalid system prompt mode: {data['system_prompt_mode']}"
                }), 400

        # Update custom system prompt
        if 'custom_system_prompt' in data:
            config.set_custom_system_prompt(data['custom_system_prompt'])
            updated_settings.append('custom_system_prompt')

        return jsonify({
            'success': True,
            'message': f'Updated settings: {", ".join(updated_settings)}',
            'updated_settings': updated_settings
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# --- LAN access (backend: settings-manager 'discovery' + api.lan_access) ---

@settings_bp.route('/settings/lan-access', methods=['GET', 'POST'])
@owner_required
def api_settings_lan_access():
    """Read/update discovery.lan_access_enabled (phone/LAN portal). Restart Flask after changes."""
    try:
        from managers.settings_manager import get_settings_manager
        from api.lan_access import (
            is_lan_access_enabled,
            get_lan_ipv4,
            lan_phone_portal_url,
            lan_phone_http_fallback_url,
            get_phone_https_port,
            get_http_fallback_port,
        )

        sm = get_settings_manager()
        discovery = dict(sm.get_setting('discovery') or {})

        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            ok, err = validate_lan_access_update(data)
            if not ok:
                return jsonify({'success': False, 'error': err}), 400
            patch = {'lan_access_enabled': bool(data['lan_access_enabled'])}
            if 'mdns_enabled' in data:
                patch['mdns_enabled'] = bool(data['mdns_enabled'])
            if not sm.update_setting('discovery', lambda current: {**(current or {}), **patch}):
                return jsonify({'success': False, 'error': 'Failed to save settings'}), 500
            discovery = sm.get_setting('discovery')

        enabled = bool(discovery.get('lan_access_enabled'))
        # Live process may still reflect env override / pre-restart bind.
        live = is_lan_access_enabled()
        lan_ip = get_lan_ipv4() if live else None
        return jsonify({
            'success': True,
            'lan_access_enabled': enabled,
            'live_enabled': live,
            'mdns_enabled': bool(discovery.get('mdns_enabled')),
            'lan_ip': lan_ip,
            'portal_url_phone': lan_phone_portal_url(get_phone_https_port(), lan_ip) if live else None,
            'portal_url_http_fallback': lan_phone_http_fallback_url(lan_ip) if live else None,
            'http_port': get_http_fallback_port(),
            'https_port': get_phone_https_port(),
            'restart_required': enabled != live or (
                enabled and live and request.method == 'POST'
            ),
            'notes': (
                'Turning LAN on/off requires a Flask restart so the server rebinds. '
                'Use the tray Restart Flask, or approve a remote restart.'
            ),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- channels (backend: settings_manager, pairing-gated) -------------------

@settings_bp.route('/settings/channels', methods=['GET'])
@owner_required
def get_channel_settings():
    """Get channel security config (dmPolicy, allowFrom) for Web Chat."""
    if not PAIRING_AVAILABLE:
        return jsonify({'success': False, 'error': 'Pairing not available'}), 503
    try:
        settings = get_settings_manager()
        channels = {}
        for ch in ('webchat',):
            channels[ch] = settings.get_channel_config(ch)
        return jsonify({'success': True, 'channels': channels})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@settings_bp.route('/settings/channels', methods=['POST'])
@owner_required
def update_channel_settings():
    """Update channel security config. Body: { channel, dmPolicy?, allowFrom? }."""
    if not PAIRING_AVAILABLE:
        return jsonify({'success': False, 'error': 'Pairing not available'}), 503
    try:
        data = request.get_json() or {}
        ok, err = validate_channel_update(data)
        if not ok:
            return jsonify({'success': False, 'error': err}), 400
        channel = data.get('channel')
        settings = get_settings_manager()
        dm_policy = data.get('dmPolicy')
        allow_from = data.get('allowFrom')
        if dm_policy is not None or allow_from is not None:
            settings.set_channel_config(channel, dm_policy=dm_policy, allow_from=allow_from)
        return jsonify({'success': True, 'channels': {channel: settings.get_channel_config(channel)}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- starred defaults (backends: api.starred_slash / api.starred_project) ---

@settings_bp.route('/settings/starred-slash', methods=['GET'])
@authenticated_required
def get_starred_slash_api():
    """Starred sticky agent for new Cuttle chats."""
    try:
        from api.starred_slash import get_starred_prefixes
        return jsonify({'success': True, 'prefixes': get_starred_prefixes()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'prefixes': []}), 500


@settings_bp.route('/settings/starred-slash', methods=['POST'])
@owner_required
def update_starred_slash_api():
    """Body: { prefixes: ["/cursor "] } — empty list clears the default."""
    try:
        from api.starred_slash import set_starred_prefixes
        data = request.get_json() or {}
        prefixes = data.get('prefixes')
        if prefixes is None:
            prefixes = data.get('prefix')
        saved = set_starred_prefixes(prefixes if prefixes is not None else [])
        return jsonify({'success': True, 'prefixes': saved})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/settings/starred-project', methods=['GET'])
@authenticated_required
def get_starred_project_api():
    """Exclusive starred default project for new Cuttle chats."""
    try:
        from api.starred_project import get_starred_project
        return jsonify({'success': True, 'project': get_starred_project()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'project': None}), 500


@settings_bp.route('/settings/starred-project', methods=['POST'])
@owner_required
def update_starred_project_api():
    """Body: { project: {id, path, name} } or { project: null } to clear.

    Only one project can be starred; posting a new project replaces the previous.
    """
    try:
        from api.starred_project import set_starred_project
        data = request.get_json() or {}
        payload = data.get('project')
        if payload is None and 'path' in data:
            payload = data
        saved = set_starred_project(payload)
        return jsonify({'success': True, 'project': saved})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- ui-layout (backend: settings-manager generic key) ----------------------

@settings_bp.route('/settings/ui-layout', methods=['GET'])
@authenticated_required
def get_ui_layout():
    """Get persisted UI layout (rail item / footer order)."""
    try:
        settings = get_settings_manager()
        layout = settings.get_setting('ui_layout')
        if layout is None:
            layout = {}
        return jsonify({'success': True, 'ui_layout': layout})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@settings_bp.route('/settings/ui-layout', methods=['POST'])
@owner_required
def update_ui_layout():
    """Save UI layout. Body: { rail_items?, rail_footer?, rail_hidden?, layout_version? }.

    ``rail_hidden`` lists apps stashed in the Apps page instead of the blade bar.
    Legacy panel_* keys are ignored.
    """
    try:
        data = request.get_json() or {}
        settings = get_settings_manager()
        patch = validate_ui_layout_update(data)

        def apply(current):
            layout = {**(current or {}), **patch}
            for legacy in ('panel_sections', 'section_status_items', 'section_nav_items', 'nav_rows_hidden'):
                layout.pop(legacy, None)
            return layout

        if not settings.update_setting('ui_layout', apply):
            return jsonify({'success': False, 'error': 'Failed to save settings'}), 500
        layout = settings.get_setting('ui_layout')
        return jsonify({'success': True, 'ui_layout': layout})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- video-background (backend: api.video_playlists + generic key) ----------

@settings_bp.route('/settings/video-background', methods=['GET'])
@authenticated_required
def get_video_background_setting():
    """Persisted wallpaper playlists (mirrors localStorage; restores after data clear)."""
    try:
        from api.video_playlists import normalize_video_background
        settings = get_settings_manager()
        vb = normalize_video_background(settings.get_setting('video_background'))
        return jsonify({'success': True, 'video_background': vb})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/settings/video-background', methods=['POST'])
@owner_required
def update_video_background_setting():
    """Save wallpaper prefs to settings.json. Body: { playlists?, active_playlist?, urls?, duration?, opacity?, enabled? }.

    Merges with existing; ``urls`` alone edits the active playlist.
    """
    try:
        from api.video_playlists import apply_video_background_update
        data = request.get_json() or {}
        settings = get_settings_manager()
        if not settings.update_setting('video_background', lambda current: apply_video_background_update(current, data)):
            return jsonify({'success': False, 'error': 'Failed to save settings'}), 500
        vb = settings.get_setting('video_background')
        return jsonify({'success': True, 'video_background': vb})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- git auto-commit (backend: api.git_autocommit) -------------------------

@settings_bp.route('/settings/git-auto-commit', methods=['GET'])
@authenticated_required
def get_git_auto_commit_setting():
    """Whether successful agent turns commit the files they changed (default off)."""
    from api.git_autocommit import is_enabled
    return jsonify({'success': True, 'git_auto_commit': is_enabled()})


@settings_bp.route('/settings/git-auto-commit', methods=['POST'])
@owner_required
def update_git_auto_commit_setting():
    """Body: { git_auto_commit: bool }."""
    try:
        from api.git_autocommit import is_enabled, set_enabled
        data = request.get_json() or {}
        value = data.get('git_auto_commit')
        if not isinstance(value, bool):
            return jsonify({'success': False, 'error': 'git_auto_commit must be true or false'}), 400
        if not set_enabled(value):
            return jsonify({'success': False, 'error': 'Failed to save settings'}), 500
        return jsonify({'success': True, 'git_auto_commit': is_enabled()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- agent adapters (backends: agent_harness.steer / catalog / agent_defaults)

def _agent_adapters_payload() -> dict:
    from api.agent_harness.agent_defaults import get_starred_effort, get_starred_model
    from api.agent_harness.catalog import (
        list_agents,
        project_adapters_env_allowed,
        project_adapters_setting,
    )
    from api.agent_harness.steer import steer_env_disabled, steer_settings

    return {
        'success': True,
        'defaults': {
            aid: {'model': get_starred_model(aid) or '', 'effort': get_starred_effort(aid) or ''}
            for aid in list_agents()
        },
        'steer': steer_settings(),
        'steer_env_disabled': steer_env_disabled(),
        'allow_project_adapters': project_adapters_setting(),
        'allow_project_adapters_env': project_adapters_env_allowed(),
    }


@settings_bp.route('/settings/agent-adapters', methods=['GET'])
@authenticated_required
def get_agent_adapters_setting():
    """Per-agent adapter config: starred model/effort, steering, drop-in opt-in."""
    try:
        return jsonify(_agent_adapters_payload())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/settings/agent-adapters', methods=['POST'])
@owner_required
def update_agent_adapters_setting():
    """Body: ``{steer?: {agent: bool}, allow_project_adapters?: bool}``."""
    try:
        ok, message, clean = validate_agent_adapters_update(request.get_json(silent=True))
        if not ok:
            return jsonify({'success': False, 'error': message}), 400
        from api.agent_harness.catalog import set_project_adapters_allowed
        from api.agent_harness.steer import set_steer_enabled

        for agent, value in (clean.get('steer') or {}).items():
            if not set_steer_enabled(agent, value):
                return jsonify({'success': False, 'error': 'Failed to save settings'}), 500
        if 'allow_project_adapters' in clean:
            if not set_project_adapters_allowed(clean['allow_project_adapters']):
                return jsonify({'success': False, 'error': 'Failed to save settings'}), 500
        return jsonify(_agent_adapters_payload())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- completion providers (backend: api.completion_providers) --------------

@settings_bp.route('/settings/completion-providers', methods=['GET'])
@authenticated_required
def get_completion_providers_setting():
    """Which provider/model serves cheap completions (titles, commits, enhance).

    Read-only view of the registry plus what Settings currently pins, so the
    page never has to reimplement resolution order.
    """
    try:
        from api.completion_providers import describe

        return jsonify({'success': True, **describe()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/settings/completion-providers', methods=['POST'])
@owner_required
def update_completion_providers_setting():
    """Pin a provider and/or per-provider model ids. Body: { provider?, models? }.

    Replaces the two dead "cheap completion model" dropdowns: these writes are
    read by api.llm_complete on the next title/commit/enhance call.
    """
    try:
        from api.completion_providers import (
            describe,
            set_preferred_provider,
            set_provider_model,
        )

        data = request.get_json() or {}
        ok, error, patch = validate_completion_providers_update(data)
        if not ok:
            return jsonify({'success': False, 'message': error}), 400

        if 'provider' in patch:
            set_preferred_provider(patch['provider'])
        for provider_id, model_id in (patch.get('models') or {}).items():
            set_provider_model(provider_id, model_id)
        return jsonify({'success': True, **describe()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- github-app (backend: api.github_app + settings-manager key) -----------

def validate_github_app_update(data: dict, has_existing_key: bool = False) -> tuple[bool, str, dict]:
    """Validate a GitHub App save through its crypto/config owner."""
    from api.github_app import validate_save

    return validate_save(data, has_existing_key)


@settings_bp.route('/settings/github-app', methods=['GET'])
@authenticated_required
def get_github_app_setting():
    """Public (non-secret) GitHub App config: ids plus key presence only."""
    try:
        from api.github_app import get_config

        settings = get_settings_manager()
        return jsonify({'success': True, 'github_app': get_config(settings)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/settings/github-app', methods=['POST'])
@owner_required
def update_github_app_setting():
    """Save App ID / installation ID / private key (.pem contents).

    An empty ``private_key`` keeps the stored key; the response never
    contains key material.
    """
    try:
        from api.github_app import get_config, save_config

        data = request.get_json() or {}
        settings = get_settings_manager()
        ok, error, clean = validate_github_app_update(
            data, get_config(settings)['key_configured']
        )
        if not ok:
            return jsonify({'success': False, 'error': error}), 400
        saved = save_config(
            settings,
            clean['app_id'],
            clean['installation_id'],
            clean.get('private_key'),
        )
        return jsonify({'success': True, 'github_app': saved})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/settings/github-app', methods=['DELETE'])
@owner_required
def delete_github_app_setting():
    """Disconnect the app: drop stored ids and delete the key file."""
    try:
        from api.github_app import remove_config

        settings = get_settings_manager()
        return jsonify({'success': True, 'github_app': remove_config(settings)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/settings/github-app/test', methods=['POST'])
@owner_required
def test_github_app_setting():
    """Mint an installation token and prove the installation exists.

    Reachable GitHub failures answer 200 with ``success: false`` (this is a
    probe, not a crash); the token itself is never returned.
    """
    try:
        from api.github_app import test_connection

        settings = get_settings_manager()
        try:
            result = test_connection(settings)
        except RuntimeError as e:
            return jsonify({'success': False, 'error': str(e)}), 200
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- video-metadata (backend: api.video_metadata; read-only lookup) ---------

@settings_bp.route('/settings/video-metadata', methods=['GET'])
@authenticated_required
def get_video_metadata_setting():
    """Title/author/thumbnail for one wallpaper URL (``?url=``).

    The page cannot ask YouTube directly (oEmbed sends no CORS headers), so the
    server proxies it and caches per video id. Non-YouTube URLs answer
    ``supported: false`` and the page falls back to the filename.
    """
    try:
        from api.video_metadata import resolve_video_metadata
        meta = resolve_video_metadata(request.args.get('url'))
        return jsonify({'success': True, 'metadata': meta})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- read-only aggregate -----------------------------------------------------

@settings_bp.route('/app-settings', methods=['GET'])
def get_all_app_settings():
    """Get all application settings"""
    try:
        from managers.settings_manager import get_settings_manager
        settings_mgr = get_settings_manager()

        snapshot = settings_mgr.get_all_settings()
        # Legacy installs may still hold a shared bearer token until migration.
        if isinstance(snapshot.get('device_workers'), dict):
            snapshot['device_workers'].pop('token', None)
        return jsonify({'success': True, 'settings': snapshot})
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@settings_bp.route("/settings/releases", methods=["GET"])
@authenticated_required
def get_published_releases():
    from api.releases import check_releases
    return jsonify(check_releases(force=request.args.get('force') == '1'))
