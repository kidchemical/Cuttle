#!/usr/bin/env python3
"""
Web Chat API Server for Cuttle Landing Page
Connects the landing page to the local bot functionality
"""

import json
import asyncio
import threading
import time
import re
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context, make_response, redirect
from flask_cors import CORS
import sys
import ntpath
import os
import uuid
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

# Imports for SSL certificate generation
import ssl
from datetime import datetime

# Add project root to path (parent of api directory, which is 'src')
script_dir = Path(__file__).parent
project_root = script_dir.parent  # This is 'src' directory (needed for web file serving)
actual_project_root = project_root.parent  # repo root (parent of src/)


def generate_self_signed_cert(lan_ip: Optional[str] = None, force_regenerate: bool = False):
    """Host TLS cert (owner: ``api.tls_cert``; keeps the key so Client pins survive)."""
    from api.tls_cert import ensure_certificate
    return ensure_certificate(lan_ip=lan_ip, force_regenerate=force_regenerate)

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# State lives in the per-user Cuttle home, never the install tree; refuse to
# open fresh stores while an older checkout's state awaits the daemon's move.
from core.runtime_data import refuse_unmigrated as _refuse_unmigrated

_refuse_unmigrated(actual_project_root)

# Load <home>/.env so API keys are available whether started by daemon or directly
from core.runtime_paths import env_file as _env_file

_env_path = _env_file()
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=False)
    except ImportError:
        # Manual parse fallback if python-dotenv not installed
        with open(_env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _v = _line.split('=', 1)
                    os.environ.setdefault(_k.strip(), _v.strip())

# Paths for self-signed certificate
from core.runtime_paths import notify_queue_path as _notify_queue_path
from core.runtime_paths import output_dir as _output_dir
from core.runtime_paths import logs_dir as _logs_dir
from core.runtime_paths import query_logs_dir as _query_logs_dir


from api.chat_status_phases import (
    PHASE_LLM,
    PHASE_ROUTE,
    PHASE_TOOL,
    emit_pipeline_status,
)

# Cuttle-as-MCP-server is retired (do not spawn run_cuttle_mcp.py). Guest
# harnesses own their MCP; agents use ``python -m api.*`` (see agent-ops-cli.md).

# Change to actual project root if we're in the api subdirectory
if os.path.basename(os.getcwd()) == 'api':
    os.chdir(actual_project_root)

# Import loopback HTTP helpers (LLM fallback). Visual pipeline graphs are
# gone. Only the module presence is probed here — all four helper names
# previously imported were uncalled (P4-3 removed the dead in-process
# POST and its entry-module import).
try:
    from api import internal_http  # noqa: F401
    PIPELINE_AVAILABLE = True  # chat backend is up (legacy flag name)
except ImportError as e:
    print(f"Warning: Internal HTTP helpers not available: {e}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Python path: {sys.path}")
    PIPELINE_AVAILABLE = False

# Import authentication
try:
    from api.auth_api import auth_bp, pairing_bp
    from api.auth_db import get_auth_db
    from api.auth_session import get_request_session_token
except ImportError as e:
    print(f"Error: Could not import authentication modules: {e}")
    sys.exit(1)

# Import project management
try:
    from managers.project_manager import project_manager
except ImportError as e:
    print(f"Warning: Project manager not available: {e}")
    project_manager = None

# Import pairing (channel-level security)
PAIRING_AVAILABLE = False
try:
    from api.pairing_manager import get_pairing_manager
    from managers.settings_manager import get_settings_manager
    PAIRING_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Pairing modules not available: {e}")

app = Flask(__name__)
# Restrict CORS to known-safe origins (+ LAN IP when lan_access_enabled).
try:
    from api.lan_access import build_cors_origins
    _CORS_ORIGINS = build_cors_origins()
except ImportError:
    # Early-import fallback only (lan_access not importable yet). Loopback
    # origins still derive from the port owner when it imports; only a fully
    # unimportable api package keeps the static defaults. A PortConfigError
    # from api.server_ports must propagate: malformed config fails boot
    # instead of serving stale origins.
    try:
        from api.server_ports import resolve_with_env_file as _resolve_ports_fallback
        _fb_ports = _resolve_ports_fallback()
        _fb_loopback = [
            f"http://localhost:{_fb_ports.https}",
            f"http://127.0.0.1:{_fb_ports.https}",
            f"https://localhost:{_fb_ports.https}",
            f"https://127.0.0.1:{_fb_ports.https}",
            f"http://localhost:{_fb_ports.http}",
            f"http://127.0.0.1:{_fb_ports.http}",
        ]
    except ImportError:
        _fb_loopback = [
            'http://localhost:8080',
            'http://127.0.0.1:8080',
            'https://localhost:8080',
            'https://127.0.0.1:8080',
            'http://localhost:8000',
            'http://127.0.0.1:8000',
        ]
    _CORS_ORIGINS = _fb_loopback + ['app://cuttle']
CORS(app, origins=_CORS_ORIGINS, supports_credentials=True)


from api import asset_versions  # noqa: E402


@app.after_request
def _no_cache_ui_assets(response):
    """Cache policy for UI assets.

    HTML stays no-store so Electron/phone WebViews pick up markup changes.
    Versioned JS/CSS (`?v=…`) may be cached — forcing no-store on every
    stylesheet made phone LAN cold-starts re-fetch the whole CSS set over
    Werkzeug HTTPS, which often dropped links and painted an unstyled UI
    (video still worked via inline styles). HTML stamps each `?v=` with the
    file's fingerprint (`api.asset_versions`), so only current URLs are cached
    long-term and a forgotten manual bump can no longer serve stale JS.
    """
    path = (request.path or '').lower()
    is_html = path.endswith('.html') or (response.mimetype == 'text/html' and response.status_code == 200)
    is_asset = (
        path.endswith('.js')
        or path.endswith('.css')
        or path.startswith('/js/')
        or path.startswith('/css/')
    )
    web_dir = project_root / 'web'
    if is_html:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        if response.status_code == 200:
            try:
                response.direct_passthrough = False
                html = response.get_data(as_text=True)
                stamped = asset_versions.stamp_html(html, web_dir)
                if stamped != html:
                    response.set_data(stamped)
                    response.headers.pop('ETag', None)
                    response.headers.pop('Last-Modified', None)
            except Exception:
                pass
    elif is_asset:
        v = request.args.get('v') or ''
        if v and asset_versions.is_current(web_dir, request.path, v):
            # Fingerprint matches the file on disk → safe to cache.
            response.headers['Cache-Control'] = 'public, max-age=604800, immutable'
            response.headers.pop('Pragma', None)
            response.headers.pop('Expires', None)
        else:
            # Unstamped/stale URL: always revalidate (ETag → cheap 304).
            response.headers['Cache-Control'] = 'no-cache, must-revalidate, max-age=0'
            response.headers.pop('Pragma', None)
            response.headers.pop('Expires', None)
    return response


# Rate limiter — shared instance from limiter.py (auth_api.py imports from there too,
# avoiding the circular-import that would occur if it imported from web_chat_api).
from api.limiter import limiter
limiter.init_app(app)

# Live-status store — owned service (chat_live_status.py). Delivery, auth,
# run-registry, and supervised orchestration import the service directly;
# this module keeps thin wrappers so existing callers/tests are untouched.
from api import chat_live_status as _live_status_svc
# Status-queue registry + emit fanout — owned service (chat_status.py).
# Turn producers import the service directly; this module keeps a thin
# cancel-injecting wrapper so existing call sites are untouched.
from api import chat_status as _status_svc
# Message/badge metadata shapers — owned module (chat_metadata.py).
# Subagent builders import it directly; aliases below preserve the
# existing internal call sites.
from api import chat_metadata as _metadata_svc

from api.http_authz import (
    authenticated_required,
    current_user,
    is_owner_user,
    loopback_or_authenticated,
    loopback_or_owner,
    loopback_required,
    owner_required,
    request_is_loopback,
    require_authenticated,
    require_chat_session_access,
    require_owner,
    require_session_actor,
)


_require_session_actor = require_session_actor

# Register authentication blueprint
app.register_blueprint(auth_bp)
try:
    app.register_blueprint(pairing_bp)
except Exception as _pairing_routes_err:
    print(f"[PAIRING] Failed to register routes: {_pairing_routes_err}")

from api.usage_live import usage_live_bp
app.register_blueprint(usage_live_bp)

# Device workers mesh (host-first coordinator queue)
try:
    from api.device_workers.routes import workers_bp
    app.register_blueprint(workers_bp)
except Exception as _workers_err:
    print(f"[DEVICE_WORKERS] Failed to register routes: {_workers_err}")

# Shared media HTTP (file operations and route behavior owned by api.shared_media)
try:
    from api.shared_media import shared_media_bp
    app.register_blueprint(shared_media_bp)
except Exception as _shared_media_err:
    print(f"[SHARED MEDIA] Failed to register routes: {_shared_media_err}")

# Unified Jobs surface (read model + control in api.jobs; bootstrap only here)
try:
    from api.jobs.routes import jobs_bp
    app.register_blueprint(jobs_bp)
except Exception as _jobs_err:
    print(f"[JOBS] Failed to register routes: {_jobs_err}")

try:
    from api.dashboards.routes import dashboards_bp
    app.register_blueprint(dashboards_bp)
except Exception as _dash_err:
    print(f"[DASHBOARDS] Failed to register routes: {_dash_err}")

# Settings (validation/persistence/defaults/authorization owned by api.settings_routes)
try:
    from api.settings_routes import settings_bp
    app.register_blueprint(settings_bp)
except Exception as _settings_err:
    print(f"[SETTINGS] Failed to register routes: {_settings_err}")

# Experimental feature flags (generic toggle surface) + experimental Apps.
# App packages own their behavior; this module only composes their blueprints.
try:
    from api.experimental.routes import experimental_bp
    app.register_blueprint(experimental_bp)
except Exception as _experimental_err:
    print(f"[EXPERIMENTAL] Failed to register routes: {_experimental_err}")

try:
    from api.achievements.routes import achievements_bp, achievements_pages_bp
    app.register_blueprint(achievements_bp)
    app.register_blueprint(achievements_pages_bp)
except Exception as _achievements_err:
    print(f"[ACHIEVEMENTS] Failed to register routes: {_achievements_err}")

try:
    from api.test_lab.routes import test_lab_bp, test_lab_pages_bp
    app.register_blueprint(test_lab_bp)
    app.register_blueprint(test_lab_pages_bp)
except Exception as _test_lab_err:
    print(f"[TEST_LAB] Failed to register routes: {_test_lab_err}")

try:
    from api.agent_events.routes import bp as agent_events_bp
    app.register_blueprint(agent_events_bp)
except Exception as exc:
    print(f"[agent_events] routes unavailable: {exc}", flush=True)

try:
    from api.gizmos.routes import gizmos_bp, gizmos_pages_bp
    app.register_blueprint(gizmos_bp)
    app.register_blueprint(gizmos_pages_bp)
except Exception as _gizmos_err:
    print(f"[GIZMOS] Failed to register routes: {_gizmos_err}")

try:
    from api.chat_vfx.routes import chat_vfx_bp
    app.register_blueprint(chat_vfx_bp)
except Exception as _vfx_err:
    print(f"[CHAT VFX] Failed to register routes: {_vfx_err}")

try:
    from api.voice_narrator.routes import voice_narrator_bp
    app.register_blueprint(voice_narrator_bp)
except Exception as _narrator_err:
    print(f"[VOICE NARRATOR] Failed to register routes: {_narrator_err}")

try:
    from api.voice_stt.routes import voice_stt_bp
    app.register_blueprint(voice_stt_bp)
except Exception as _voice_stt_err:
    print(f"[VOICE STT] Failed to register routes: {_voice_stt_err}")

# Projects (transport owned by api.project_routes; logic in managers.project_manager)
try:
    from api.project_routes import projects_bp, projects_pages_bp
    app.register_blueprint(projects_bp)
    app.register_blueprint(projects_pages_bp)
except Exception as _projects_err:
    print(f"[PROJECTS] Failed to register routes: {_projects_err}")

# Worktrees (transport owned by api.worktrees.routes; decisions in api.worktrees)
try:
    from api.worktrees.routes import worktrees_bp
    app.register_blueprint(worktrees_bp)
except Exception as _worktrees_err:
    print(f"[WORKTREES] Failed to register routes: {_worktrees_err}")

# Action forms (transport owned by api.action_form_routes; logic in api.action_forms)
try:
    from api.action_form_routes import action_forms_bp
    app.register_blueprint(action_forms_bp)
except Exception as _action_forms_err:
    print(f"[ACTION_FORMS] Failed to register routes: {_action_forms_err}")

# Git (transport owned by api.git_routes; behavior in api.git_service +
# scripts.utilities git helpers)
try:
    from api.git_routes import git_bp
    app.register_blueprint(git_bp)
except Exception as _git_err:
    print(f"[GIT] Failed to register routes: {_git_err}")

# Phase 6A route families
try:
    from api.agent_router.routes import agent_router_bp
    app.register_blueprint(agent_router_bp)
except Exception as _router_err:
    print(f"[ROUTER] Failed to register routes: {_router_err}")
try:
    from api.agent_harness.routes import agent_harness_bp, configure_context_routes
    configure_context_routes(lambda data: _resolve_request_project_path(data), lambda session: _require_session_actor(session))
    app.register_blueprint(agent_harness_bp)
except Exception as _harness_routes_err:
    print(f"[AGENT HARNESS] Failed to register routes: {_harness_routes_err}")
try:
    from api.settings_routes import provider_keys_bp
    app.register_blueprint(provider_keys_bp)
except Exception as _provider_keys_err:
    print(f"[PROVIDER KEYS] Failed to register routes: {_provider_keys_err}")

# Claude Code palette pins (owned by the Claude agent slice)
try:
    from api.agent_harness.agents.claude.routes import claude_bp
    app.register_blueprint(claude_bp)
except Exception as _claude_err:
    print(f"[CLAUDE] Failed to register routes: {_claude_err}")

# Router editor decision-only tools (transport in api.agent_router.routes)
try:
    from api.agent_router.routes import router_editor_bp
    app.register_blueprint(router_editor_bp)
except Exception as _router_editor_err:
    print(f"[ROUTER] Failed to register editor routes: {_router_editor_err}")

# Supervised task HTTP transport (task policy and storage stay in the owner).
try:
    from api.agent_router.supervised.routes import supervised_bp
    app.register_blueprint(supervised_bp)
except Exception as _supervised_routes_err:
    print(f"[SUPERVISED] Failed to register routes: {_supervised_routes_err}")

# Chat activity push stream (transport in api.activity_stream)
try:
    from api.activity_stream import activity_bp
    app.register_blueprint(activity_bp)
except Exception as _activity_err:
    print(f"[ACTIVITY] Failed to register routes: {_activity_err}")

try:
    from api.mobile_companion import mobile_companion_bp
    app.register_blueprint(mobile_companion_bp)
except Exception as _mobile_companion_err:
    print(f"[MOBILE] Failed to register companion routes: {_mobile_companion_err}")

try:
    from api.flask_restart import flask_restart_bp
    app.register_blueprint(flask_restart_bp)
except Exception as _flask_restart_err:
    print(f"[FLASK RESTART] Failed to register routes: {_flask_restart_err}")

try:
    from api.jev.watch import ensure_started as _jev_watch_start

    _jev_watch_start()
except Exception as _jev_watch_err:
    print(f"[JEV] regress watcher not started: {_jev_watch_err}")

# Chat bubble TTS (OpenAI summarize-then-speak, on-demand play button)
try:
    from api.chat_tts import register_chat_tts_routes
    register_chat_tts_routes(app)
except Exception as _chat_tts_err:
    print(f"[CHAT_TTS] Failed to register routes: {_chat_tts_err}")

# Web terminal (localhost-only PTY sessions)
try:
    from api.web_terminal import register_terminal_routes
    register_terminal_routes(app)
except Exception as _terminal_err:
    print(f"[TERMINAL] Failed to register web terminal: {_terminal_err}")

# Electron desktop client (LAN thin-client + shell updates)
try:
    from api.desktop_electron import register_desktop_electron_routes
    register_desktop_electron_routes(app)
except Exception as _desktop_err:
    print(f"[DESKTOP] Failed to register desktop electron routes: {_desktop_err}")

# Host-served git bundle (Scenario B: LAN Client follows the Host working tree)
try:
    from api.desktop_git import register_desktop_git_routes
    register_desktop_git_routes(app)
except Exception as _desktop_git_err:
    print(f"[DESKTOP] Failed to register desktop git routes: {_desktop_git_err}")

# Android Capacitor shell updates (LAN APK, same idea as Electron app.asar)
try:
    from api.mobile_android_update import register_mobile_android_update_routes
    register_mobile_android_update_routes(app)
except Exception as _mobile_apk_err:
    print(f"[MOBILE] Failed to register Android update routes: {_mobile_apk_err}")

# Global state (for backwards compatibility, new users use database)
chat_sessions = {}
session_counter = 0



class ChatSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.messages = []
        self.created_at = time.time()
        self.last_activity = time.time()
    
    def add_message(self, role, content):
        self.messages.append({
            'role': role,
            'content': content,
            'timestamp': time.time()
        })
        self.last_activity = time.time()
    
    def get_recent_messages(self, limit=10):
        return self.messages[-limit:]

def get_or_create_session(session_id=None):
    """Get existing session or create new one"""
    global session_counter
    
    if not session_id:
        session_counter += 1
        session_id = f"web_session_{session_counter}"
    
    if session_id not in chat_sessions:
        chat_sessions[session_id] = ChatSession(session_id)
    
    return chat_sessions[session_id]


# Session-scoped status-queue registry lives in the owned service
# (api.chat_status — process lifetime, same semantics as before).
# This alias preserves the existing module attribute (transports and
# tests poke the single shared registry, never a replica).
_chat_status_queues = _status_svc._QUEUES

# Latest in-flight status store + lock live in the owned service
# (api.chat_live_status — process lifetime, same semantics as before).
# These aliases preserve the existing module attributes (tests and
# external readers poke the single shared store, never a replica).
_chat_live_status = _live_status_svc._STORE
_chat_live_status_lock = _live_status_svc._LOCK
_CHAT_LIVE_STATUS_TTL = _live_status_svc.TTL_SECONDS
_CHAT_LIVE_STATUS_CONNECTING_TTL = _live_status_svc.CONNECTING_TTL_SECONDS


def _live_status_ttl_seconds(entry: dict) -> float:
    return _live_status_svc._ttl_seconds(entry)

# Serialize local LLM (Ollama) requests: Ollama processes one request at a time per model.
# When multiple pipelines call Ollama, later requests wait; we emit "Waiting for local LLM..." for the UI.


def _live_status_keys(session_id) -> list:
    # Alias to the owned service (single key-mapping implementation).
    return _live_status_svc.live_status_keys(session_id)


def _chat_turn_cancelled(session_id) -> bool:
    """True between Stop and the next send — live-status must stay idle."""
    try:
        from api.chat_delivery import is_turn_cancelled

        return bool(is_turn_cancelled(session_id))
    except Exception:
        return False


def set_chat_live_status(
    session_id,
    message: str = None,
    *,
    active: bool = True,
    report_url: str = None,
    query_id: str = None,
) -> None:
    """Publish (or refresh) live generation status for all devices watching this session."""
    # Owned service; the cancel predicate is injected here at the
    # composition root (service never imports delivery/monolith state).
    # Stop already cleared this chat. A dying Codex/Cursor worker must not
    # republish active=True — refresh polls this and resurrects the spinner
    # (CH-000522: activity flashed multiple times after Stop + reload).
    return _live_status_svc.set_live_status(
        session_id,
        message,
        active=active,
        report_url=report_url,
        query_id=query_id,
        is_cancelled=_chat_turn_cancelled,
    )


def clear_chat_live_status(session_id) -> None:
    # Owned service (composition root delegates; no route/logic change).
    return _live_status_svc.clear_live_status(session_id)


def get_chat_live_status(session_id) -> dict:
    # Owned service (composition root delegates; no route/logic change).
    return _live_status_svc.get_live_status(session_id)


def _public_live_generating(session_id, live=None):
    """Flags the refresh client reads. Cancelled chats stay idle even if a leftover row lingers."""
    from api import chat_delivery

    cancelled = _chat_turn_cancelled(session_id)
    if live is None:
        live = get_chat_live_status(session_id)
    if cancelled:
        try:
            clear_chat_live_status(session_id)
        except Exception:
            pass
        return False, False, True, {
            'active': False,
            'status': None,
            'updated_at': None,
            'report_url': None,
            'query_id': None,
        }
    live_active = bool((live or {}).get('active'))
    generating = chat_delivery.is_busy(session_id) or live_active
    try:
        from api.subagents.service import child_live_status

        overlay = child_live_status(session_id)
        if overlay and overlay.get('generating'):
            generating = True
            live = dict(live or {})
            live['active'] = True
            if not (live.get('status') or '').strip():
                live['status'] = overlay.get('status') or 'Subagent working…'
            live_active = True
            # A sub-agent turn runs in the CLI process, so its query log never
            # touches this process's live-status store. Carry the id the child
            # row captured at query_started, else the child pane's query-log
            # button stays inert for the whole turn.
            if not live.get('report_url') and overlay.get('report_url'):
                live['report_url'] = overlay['report_url']
            if not live.get('query_id') and overlay.get('query_id'):
                live['query_id'] = overlay['query_id']
    except Exception:
        pass
    return generating, live_active, False, live or {}


def _live_parent_subagents(session_id) -> list:
    try:
        from api.subagents.service import public_parent_subagents

        return public_parent_subagents(session_id) or []
    except Exception:
        return []


def _clear_live_status_on_stream_done(session_id, *, stale: bool, cancelled: bool) -> None:
    """Drop live-status when this turn ends, unless a newer send owns it.

    Stop used to skip the clear (`if not stale`) so a late Codex status row
    survived refresh and kept the activity spinner up.
    """
    if not stale or cancelled:
        try:
            clear_chat_live_status(session_id)
        except Exception:
            pass


def active_live_session_ids() -> list:
    """Bare session ids with an active live-status entry (for history spinners)."""
    # Owned service (composition root delegates; no route/logic change).
    return _live_status_svc.active_live_session_ids()


def emit_chat_status(session_id: str, message: str) -> None:
    """Emit a status update to the streaming chat UI if this session has a status queue registered."""
    # Owned service; cancel policy + live publish injected here at the
    # composition root (service defines no silent defaults for active
    # writes — every producer supplies both explicitly).
    return _status_svc.emit_status(
        session_id,
        message,
        is_cancelled=_chat_turn_cancelled,
        publish_live=lambda text: set_chat_live_status(session_id, text, active=True),
    )


def _emit_chat_complete_mobile(session_id: str, result: dict) -> None:
    try:
        from api.mobile_companion import chat_complete_payload, emit_mobile_event

        emit_mobile_event("chat_complete", chat_complete_payload(session_id, result))
    except Exception as exc:
        print(f"[MOBILE] chat_complete emit failed: {exc}", flush=True)


_CUTTLE_BUTTON_LABELS = {
    'project-action-confirm': 'Confirm',
    'project-action-cancel': 'Cancel',
}


def _button_click_history(message_content: str):
    """Map ``[button:<id>]`` clicks to friendly history text + metadata."""
    stripped = (message_content or '').strip()
    m = re.match(r'^\[button:([^\]]+)\](?:\s+(.*))?$', stripped)
    if not m:
        return message_content, None
    bid = m.group(1).strip()
    payload = (m.group(2) or '').strip()
    label = _CUTTLE_BUTTON_LABELS.get(bid) or bid.replace('-', ' ').replace('_', ' ').title()
    if bid == 'project-action-confirm':
        label = 'Confirm action'
    elif bid == 'project-action-cancel':
        label = 'Cancel action'
    meta = {'button_click': {'id': bid, 'label': label, 'raw': stripped}}
    if payload:
        meta['button_click']['payload'] = payload
    return f'Selected: {label}', meta


def _rewrite_assistant_response_actions(res: Optional[dict], session_id, project_path: str = '') -> Optional[dict]:
    """Register <cuttle_confirm> blocks and rewrite them into Confirm/Cancel buttons.

    Delegates to the owned workflow implementation (P5-E); the body moved
    to ``api.chat_turn_workflow`` so the coordinator stream worker applies
    the identical transform. All existing callers keep working.
    """
    from api.chat_turn_workflow import (
        rewrite_assistant_response_actions as _owned_rewrite,
    )
    return _owned_rewrite(res, session_id, project_path or '')


def process_message_with_bot(
    message_content,
    session_id,
    session_kind=None,
    routing_key=None,
    is_owner=False,
    status_queue=None,
):
    """Process a compatibility chat turn through the shared coordinator.

    Compatibility entry for non-lane surfaces (`/api/sessions/send`
    cross-session sends): it submits the turn to
    the shared application entry (`api.chat_coordinator`) unclaimed,
    with plain shortcut bodies and a naked pipeline fallback — the
    same contract as before, not a second execution path. Authed web
    turns go through the route lanes instead; do not add new surfaces
    here.
    """
    if isinstance(message_content, str):
        from api.chat_turn import strip_invisible_leading as _strip_leading

        message_content = _strip_leading(message_content)
    if not PIPELINE_AVAILABLE:
        return {
            'success': False,
            'error': 'Chat backend not available',
            'response': "I'm sorry, but the chat backend is not available. Please check that all dependencies are installed."
        }

    if status_queue:
        _status_svc.register_status_queue(session_id, status_queue)
        emit_pipeline_status(
            session_id,
            PHASE_ROUTE,
            "Routing to top-level agent...",
            emit_fn=emit_chat_status,
        )

    try:
        # Ensure the session row exists (return value unused since P5-F
        # removed the naked-fallback context build; kept for the side
        # effect sessions/send relies on).
        _session = get_or_create_session(session_id)

        # Native Cuttle control command — must win over sticky agent prefixes.
        try:
            from api.flask_restart import handle_restart_slash, parse_restart_slash

            if parse_restart_slash(message_content) is not None:
                _rr = handle_restart_slash(
                    message_content,
                    session_id=session_id,
                    user_source=str(session_kind or 'chat'),
                ) or {}
                _rr = dict(_rr)
                _rr.setdefault("native_command", "/restart")
                if status_queue is None:
                    _emit_chat_complete_mobile(session_id, _rr)
                return _rr
        except Exception as _rs_err:
            print(f"[PIPELINE] /restart handler failed: {_rs_err}", flush=True)

        # Native /branch control command — must win over sticky agent prefixes.
        try:
            from api.branch_slash import handle_branch_slash, parse_branch_slash

            if parse_branch_slash(message_content) is not None:
                try:
                    _branch_proj = _resolve_request_project_path({'session_id': session_id})
                except Exception:
                    _branch_proj = None
                _br = dict(
                    handle_branch_slash(
                        message_content,
                        session_id=session_id,
                        project_path=_branch_proj,
                    )
                    or {}
                )
                _br.setdefault("native_command", "/branch")
                if status_queue is None:
                    _emit_chat_complete_mobile(session_id, _br)
                return _br
        except Exception as _bs_err:
            print(f"[PIPELINE] /branch handler failed: {_bs_err}", flush=True)

        # Explicit agent CLIs must not fall through to a missing graph.
        # Bundled agents go through the harness only.
        # Selection + execution owned by the shared application entry
        # (api.chat_coordinator); this surface stays unclaimed with plain
        # shortcut bodies and a naked pipeline fallback, exactly as before.
        from api.chat_coordinator import (
            AgentTurnIO as _AgentTurnIO,
            PreparedAgentTurn as _PreparedAgentTurn,
            submit_agent_turn as _submit_turn,
        )
        from api.chat_turn import split_db_session_id as _split_db_sid
        from api import chat_delivery as _chat_delivery

        _legacy_proj = _resolve_request_project_path({'session_id': session_id})
        _ident_kw = _harness_identity_run_kwargs(
            _freeze_send_identity(session_id, message_content, None)
        )

        # The sync coordinator arms pass status_queue=None; this entry owns
        # the caller's queue, so harness/router progress must still reach it
        # (otherwise the bubble sits on "Routing to top-level agent...").
        _outer_status_queue = status_queue

        def _legacy_run_harness(_aid, _prompt, status_queue=None):
            return _run_pinned_harness_turn(
                _aid, _prompt, session_id,
                status_queue=status_queue or _outer_status_queue,
                project_path=_legacy_proj,
                **_ident_kw,
            )

        def _legacy_run_router(status_queue=None):
            try:
                from api.agent_router.integration import maybe_route_plain_message

                _db_sid = _split_db_sid(session_id)
                return maybe_route_plain_message(
                    message_content,
                    session_id=_db_sid if _db_sid is not None else session_id,
                    project_path=_legacy_proj,
                    status_queue=status_queue or _outer_status_queue,
                )
            except Exception as _ar_disp:
                print(f"[AGENT-ROUTER] plain-message dispatch failed: {_ar_disp}", flush=True)
                return None

        def _legacy_shortcut(_kind, _sel):
            return {
                'success': True,
                'response': f'❌ Please provide a prompt after /{_sel.agent_id}.',
                'type': f'{_sel.agent_id}_error',
            }

        _legacy_io = _AgentTurnIO(
            run_harness=_legacy_run_harness,
            run_router=_legacy_run_router,
            persist_user=lambda: None,
            make_saver=lambda: None,
            should_save=lambda _b: False,
            notify_mobile=(
                None if status_queue is not None
                else lambda _b: _emit_chat_complete_mobile(session_id, _b)
            ),
            format_shortcut=_legacy_shortcut,
        )
        _turn_out = _submit_turn(
            _PreparedAgentTurn(
                message=message_content, session_id=session_id,
                session_kind=session_kind or 'web_anon',
                routing_key=routing_key or f'web_anon_{session_id}',
                is_owner=is_owner, project_path=_legacy_proj or '',
                run_kwargs=_ident_kw,
            ),
            io=_legacy_io, delivery=_chat_delivery,
            claim=False,
        )
        # The shared entry never returns None: executed arms return their
        # result, and the pipeline arm / plain-router abstain return the
        # owned no-LLM fallback — no naked tail to reimplement here.
        return _turn_out.body

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in process_message_with_bot: {e}")
        print(f"Traceback: {error_details}")
        
        return {
            'success': False,
            'error': str(e),
            'response': f"I encountered an error processing your request: {str(e)}. Please try again or rephrase your request."
        }
    finally:
        if status_queue:
            _status_svc.unregister_status_queue(session_id)


def _resolve_request_project_path(data: Optional[dict] = None) -> str:
    """Resolve chat working directory from the request body.

    Preference order:
    1. ``project_id`` looked up in ProjectManager (authoritative — ignores stale paths)
    2. Match ``project_name`` against known projects
    3. Non-empty ``project_path`` (only when id/name did not resolve)
    4. Auth chat session's stored project (survives reload / missing client fields)
    5. Registered Cuttle project / current project / git root

    Client chips can show the right *name* while localStorage still has a stale
    Cuttle ``project_path``; id/name must win over that path.
    """
    from managers.project_locations import require_project_path, ProjectUnavailable
    from flask import g, has_request_context
    if has_request_context() and hasattr(g, 'project_turn_path'):
        return g.project_turn_path
    data = data or {}

    # A bound worktree checkout is the turn's working directory; the project
    # identity stays in the registry (api.worktrees.binding).
    try:
        from api.worktrees import binding as _wt, is_worktrees_enabled as _wt_on
        _bound = _wt.resolve_request_checkout_path(data) if _wt_on() else ''
    except Exception:
        _bound = ''
    if _bound:
        return _bound

    pid = data.get('project_id')
    if pid is not None and str(pid).strip() != '' and project_manager is not None:
        try:
            proj = project_manager.get_project(int(pid))
        except (TypeError, ValueError):
            proj = None
        if proj:
            return require_project_path(proj)
        if proj is None and str(pid).isdigit():
            raise ProjectUnavailable('This project is no longer registered. Select a project in Apps → Projects.')

    pname = data.get('project_name') or data.get('projectName')
    if isinstance(pname, str) and pname.strip() and project_manager is not None:
        needle = pname.strip().lower()
        try:
            for p in project_manager.get_projects() or []:
                if str(p.get('name') or '').strip().lower() == needle:
                    return require_project_path(p)
        except ProjectUnavailable:
            raise
        except Exception:
            pass

    raw = data.get('project_path')
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    # Fall back to the auth session row (project chip is client-side; this is durable).
    sid_raw = data.get('session_id') or data.get('chat_session_id')
    if sid_raw is not None and str(sid_raw).strip() != '':
        try:
            sid_s = str(sid_raw).strip()
            if sid_s.startswith('db_session_'):
                sid_s = sid_s[len('db_session_'):]
            if sid_s.upper().startswith('CH-'):
                # CH-000087 → 87
                bare = sid_s.split('-', 1)[-1]
                sid_int = int(bare.lstrip('0') or '0')
            else:
                sid_int = int(sid_s)
            from api.auth_db import get_auth_db
            stored = get_auth_db().get_session_project(sid_int)
            if stored:
                spid = stored.get('project_id')
                if spid is not None and project_manager is not None:
                    try:
                        proj = project_manager.get_project(int(spid))
                    except (TypeError, ValueError):
                        proj = None
                    if proj:
                        return require_project_path(proj)
                    if proj is None:
                        raise ProjectUnavailable('This chat’s project is no longer registered. Select another project.')
                spath = stored.get('project_path')
                if isinstance(spath, str) and spath.strip():
                    return spath.strip()
                sname = stored.get('project_name')
                if isinstance(sname, str) and sname.strip() and project_manager is not None:
                    needle = sname.strip().lower()
                    for p in project_manager.get_projects() or []:
                        if str(p.get('name') or '').strip().lower() == needle:
                            return require_project_path(p)
        except ProjectUnavailable:
            raise
        except Exception as e:
            print(f"[CHAT] session project lookup failed: {e}")

    return _default_chat_cwd()


def _default_chat_cwd() -> str:
    # Owned helper (managers.project_manager); explicit inputs preserve the
    # import-failed fallback to this module's repo root exactly.
    from managers.project_manager import default_chat_cwd

    return default_chat_cwd(project_manager, actual_project_root)


def _project_record_for_path(path: str) -> Optional[Dict[str, Any]]:
    """Best-matching registered project for a filesystem path."""
    raw = str(path or '').strip()
    if not raw or project_manager is None:
        return None

    # abspath() on a foreign-OS path (``G:\...`` on Linux) joins it under cwd,
    # making every unmapped Windows project look nested inside the checkout.
    # Normalize each path in its own flavor and only compare like with like.
    def _norm(p: str):
        if os.path.isabs(p):
            return os.sep, os.path.normcase(os.path.abspath(p))
        if ntpath.isabs(p):
            return '\\', ntpath.normcase(ntpath.normpath(p))
        return None, ''

    sep, want = _norm(raw)
    if not sep:
        return None
    best = None
    best_len = -1
    try:
        for p in project_manager.get_projects() or []:
            pp = str(p.get('path') or '').strip()
            if not pp:
                continue
            have_sep, have = _norm(pp)
            if have_sep != sep:
                continue
            if want == have or want.startswith(have + sep) or have.startswith(want + sep):
                if len(have) > best_len:
                    best = p
                    best_len = len(have)
    except Exception:
        return None
    return best


def _project_fields_from_mapping(data: Optional[dict]) -> Dict[str, Any]:
    data = data or {}
    out: Dict[str, Any] = {}
    pid = data.get('project_id', data.get('projectId'))
    if pid is not None and str(pid).strip() != '':
        try:
            out['project_id'] = int(pid)
        except (TypeError, ValueError):
            pass
    pname = data.get('project_name') or data.get('projectName')
    if isinstance(pname, str) and pname.strip():
        out['project_name'] = pname.strip()
    ppath = data.get('project_path') or data.get('projectPath')
    if isinstance(ppath, str) and ppath.strip():
        out['project_path'] = ppath.strip()
    cuid = data.get('checkout_uid') or data.get('checkoutUid')
    if isinstance(cuid, str) and cuid.strip():
        out['checkout_uid'] = cuid.strip()
    return out


def _project_snapshot_for_turn(
    data: Optional[dict] = None,
    session_id=None,
    cursor_run: Optional[dict] = None,
) -> Dict[str, Any]:
    """Project actually used for this turn (request → session → Cursor cwd)."""
    snap = _project_fields_from_mapping(data)
    cwd = ''
    if isinstance(cursor_run, dict):
        cwd = str(cursor_run.get('cwd') or '').strip()
    has_turn_ctx = bool(snap) or session_id is not None
    if data:
        has_turn_ctx = has_turn_ctx or any(
            data.get(k) not in (None, '')
            for k in ('session_id', 'chat_session_id', 'project_id', 'project_path', 'project_name')
        )
    if not snap.get('project_path') and has_turn_ctx:
        try:
            resolved = _resolve_request_project_path(data or {})
        except Exception:
            resolved = ''
        if resolved:
            snap['project_path'] = resolved
    if cwd and not snap.get('project_path'):
        snap['project_path'] = cwd
    path = str(snap.get('project_path') or cwd or '').strip()
    rec = _project_record_for_path(path) if path else None
    if rec:
        snap.setdefault('project_id', rec.get('id'))
        snap.setdefault('project_name', rec.get('name'))
        if rec.get('path'):
            snap['project_path'] = rec.get('path')
    elif path and not snap.get('project_name'):
        snap['project_name'] = Path(path).name
    if (not snap.get('project_name') and not snap.get('project_path')
            and session_id is not None):
        try:
            stored = get_auth_db().get_session_project(int(session_id)) or {}
        except Exception:
            stored = {}
        for key in ('project_id', 'project_name', 'project_path', 'checkout_uid'):
            val = stored.get(key)
            if val not in (None, ''):
                snap[key] = val
    return {k: v for k, v in snap.items() if v not in (None, '')}


def _merge_project_into_meta(
    meta: Optional[dict],
    data: Optional[dict] = None,
    session_id=None,
    cursor_run: Optional[dict] = None,
) -> Optional[dict]:
    snap = _project_snapshot_for_turn(data, session_id, cursor_run)
    if not snap:
        return meta
    base = dict(meta or {})
    for key, val in snap.items():
        if val in (None, ''):
            continue
        if key not in base or base.get(key) in (None, ''):
            base[key] = val
    return base


def _stamp_auth_session_project(user, session_id, data) -> None:
    """Persist the client's project/checkout chip (owner: api.chat_session_project)."""
    from api.chat_session_project import stamp_auth_session_project

    stamp_auth_session_project(user, session_id, data, db=get_auth_db())


def _match_harness_slash(message: str, project_path=None):
    """Return ``(agent_id, prompt)`` for harness agents, else None."""
    try:
        raw = (message or "").strip()
        from api.agent_harness.catalog import match_slash_command

        return match_slash_command(raw, project_path=project_path)
    except Exception as exc:
        print(f"[CHAT] harness match failed: {exc}", flush=True)
        return None


def _run_harness_web_command(
    agent_id: str,
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
    execute_kwargs=None,
) -> dict:
    """Shared entry for harness agents — owned by api.agent_harness.runners."""
    from api.agent_harness.runners import run_harness_web_command as _owned

    return _owned(
        agent_id,
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
        execute_kwargs=execute_kwargs,
    )


def _run_pinned_harness_turn(agent_id: str, prompt: str, chat_session_id, **kwargs) -> dict:
    """Pinned/starred slash-agent turn, recorded for My Cuttle Performance.

    Router turns record their own attempts in ``agent_router.dispatch``; only
    call this from paths that bypass the router.
    """
    from api.agent_harness.runners import run_pinned_harness_turn as _owned

    return _owned(agent_id, prompt, chat_session_id, **kwargs)


def _handle_muse_model_command(prompt, chat_session_id, active_model) -> Optional[dict]:
    """Handle `/muse model …` natively (palette parity for typed / Discord use).

    Returns a reply dict, or None when the prompt is a normal Muse task.
    """
    from scripts.utilities.muse_cli_tool import (
        MUSE_KNOWN_MODELS,
        muse_model_label,
        resolve_muse_default_model,
    )
    from scripts.utilities.muse_cli_session_store import save_muse_model

    raw = (prompt or '').strip()
    m = re.match(r'^(/?)models?\b[\s:=]*(\S*)\s*$', raw, flags=re.I)
    if not m:
        return None
    requested = (m.group(2) or '').strip().strip('"').strip("'")
    # Bare `model foo` without a slash is ambiguous with a real prompt
    # ("model the login flow"), so only accept recognizable arguments.
    if not m.group(1) and requested and not re.match(
        r'^(muse[-_]|default$|reset$|clear$)', requested, flags=re.I
    ):
        return None

    if not requested:
        lines = ['**Muse Code models**', '']
        for known in MUSE_KNOWN_MODELS:
            mark = ' ✅ current' if known['id'].lower() == str(active_model).lower() else ''
            lines.append(f"- `{known['id']}` — {known['description']}{mark}")
        lines.append('')
        lines.append('Set one with `/muse model muse-spark-1.3`, or pick it from the `/` palette.')
        return {
            'success': True,
            'response': '\n'.join(lines),
            'type': 'muse_command',
            'agent_model': active_model,
        }

    if requested.lower() in ('default', 'reset', 'clear'):
        save_muse_model(chat_session_id, None)
        default = resolve_muse_default_model()
        return {
            'success': True,
            'response': f'**Muse Code:** Model reset to default `{default}`.',
            'type': 'muse_command',
            'agent_model': default,
            'model_source': 'cli_default',
        }

    # Unknown ids are allowed — Meta ships new checkpoints faster than this list.
    saved = save_muse_model(chat_session_id, requested) or requested
    return {
        'success': True,
        'response': f'**Muse Code:** Model set to `{saved}` ({muse_model_label(saved)}) for this chat.',
        'type': 'muse_command',
        'agent_model': saved,
        'model_source': 'session',
    }


# Word triggers ("chats", "history", …) plus explicit session / message handles
# ("CH-000147", "CH-000147-9"). A bare CH- id is the common ask ("summarize
# CH-000147") and must unlock the cuttle_auth.db pointer — otherwise Muse greps
# the tree / opens the wrong .db. Message refs share the same CH- prefix match.
_CHAT_LOOKUP_RE = re.compile(
    r"(?:\b(chats?|conversations?|transcripts?|messages?|panes?|sessions?|history)\b"
    r"|\bCH-[A-Z0-9]+(?:-\d+)?\b)",
    re.IGNORECASE,
)


def _with_muse_chat_context(prompt: str, chat_session_id=None) -> str:
    """Prepend pane map + chat-store location to a Muse prompt.

    Transcripts are gitignored SQLite, so give the native CLI the runbook
    and database location instead of relying on working-tree searches.

    ``chat_session_id`` scopes the read recipe to the chat the turn belongs to;
    without it an agent asked about "this chat" has no id to use.
    """
    from api.cuttle_ui_capabilities import cuttle_chat_store_addon

    blocks = []
    try:
        pane_addon = _build_shell_pane_prompt_addon(prompt)
        if pane_addon:
            blocks.append(pane_addon)
    except Exception as pane_err:
        print(f"[Muse] Pane context skipped: {pane_err}")

    if _CHAT_LOOKUP_RE.search(prompt or ""):
        try:
            store_addon = cuttle_chat_store_addon(
                current_session_id=chat_session_id,
            )
            if store_addon:
                blocks.append(store_addon)
        except Exception as store_err:
            print(f"[Muse] Chat store context skipped: {store_err}")

    if not blocks:
        return prompt
    return "\n\n".join(blocks) + "\n\n---\n\n" + prompt


def _generate_chat_stream(process_fn, session_id_for_status, on_result=None, on_claimed=None):
    """Run process_fn() in a thread and yield SSE events for status updates + final response.

    Transport adapter over the owned pipeline stream entry (P5-F): the
    claim → persist → worker → finalize → release lifecycle lives in
    ``api.chat_coordinator.submit_agent_stream_turn`` /
    ``api.chat_turn_workflow.run_agent_stream_turn``; this function only
    submits (explicit pipeline selection) and frames. Same observable
    contract as before:

    on_result(result) persists the reply (e.g. save to db). It runs on the
    worker thread, not in the SSE loop, so closing the tab mid-reply no
    longer discards a finished answer; the reply is also parked in
    chat_delivery for the client to collect when it reconnects.
    (No clear_result on done: writing the SSE chunk is not proof the
    browser parsed it — clients often detach the fetch body right as the
    final event is written. Client take_result (or TTL) clears pending
    after a real delivery.)

    on_claimed() runs only after this chat's busy slot is acquired — persist
    the user turn here so a second send cannot land in history before the
    lock. A busy slot yields the status/busy/done triple (no session
    event: historical callers prefix it themselves).
    """
    from api import chat_delivery
    from api.chat_coordinator import (
        AgentSelection as _AgentSelection,
        PreparedAgentTurn as _PreparedAgentTurn,
        StreamTurnIO as _StreamTurnIO,
        submit_agent_stream_turn as _submit_stream_turn,
    )

    def _unreachable(*_a, **_k):
        raise RuntimeError("unreachable arm in pipeline compat adapter")

    _io = _StreamTurnIO(
        run_harness=_unreachable,
        run_router=_unreachable,
        persist_user=(on_claimed if on_claimed is not None else (lambda: None)),
        make_saver=(lambda: on_result),
        notify_mobile=(
            lambda _r: _emit_chat_complete_mobile(session_id_for_status, _r)
        ),
        format_shortcut=_unreachable,
        run_pipeline=(
            lambda status_queue=None: process_fn(status_queue=status_queue)
        ),
    )
    _completion: dict = {}
    _events = _submit_stream_turn(
        _PreparedAgentTurn(message="", session_id=session_id_for_status),
        io=_io,
        delivery=chat_delivery,
        is_router_family=_is_router_family_message,
        selection=_AgentSelection(kind="pipeline"),
        completion=_completion,
    )
    _first = next(_events, None)
    # Pipeline selection never yields shortcut arms; busy keeps the
    # historical bare triple (no session event).
    if _first is not None and _first[0] == "busy":
        yield from _pipeline_busy_chunks(session_id_for_status)
        return
    yield from _pipeline_progress_chunks(
        _first, _events, session_id_for_status, _completion
    )



@app.route('/phone')
def serve_phone_landing():
    """Short LAN setup page — open from phone browser."""
    try:
        from api.lan_access import (
            lan_phone_portal_url, lan_phone_http_fallback_url, get_lan_ipv4,
            get_phone_https_port, get_http_fallback_port, get_primary_https_port,
        )
        ip = get_lan_ipv4() or request.host.split(':')[0]
        base = lan_phone_portal_url(get_phone_https_port(), ip) or f'https://{ip}:{get_phone_https_port()}'
        http_fb = lan_phone_http_fallback_url(ip) or f'http://{ip}:{get_http_fallback_port()}'
        pc_url = f'https://127.0.0.1:{get_primary_https_port()}'
        phone_port_label = str(get_phone_https_port())
        http_port_label = str(get_http_fallback_port())
    except Exception:
        base = request.url_root.rstrip('/')
        http_fb = base
        pc_url = base
        ip = request.host.split(':')[0]
        _req_port = request.host.split(':')[1] if ':' in request.host else 'configured port'
        phone_port_label = http_port_label = _req_port
    chat = f'{base}/chat_page.html'
    shell = f'{base}/app_shell.html'
    ping = f'{base}/api/lan-ping'
    qr_url = f'https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={base}/phone'
    from_pc = request.remote_addr in ('127.0.0.1', '::1')
    qr_block = f'''
<h2>Scan with phone</h2>
<img src="{qr_url}" alt="QR code" width="220" height="220" style="display:block;margin:1rem auto;background:#fff;padding:8px;border-radius:8px">
<p style="text-align:center;font-size:1.2rem;word-break:break-all"><strong>{base}</strong></p>
''' if from_pc else ''
    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cuttle on your phone</title>
<style>body{{font-family:system-ui,sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}}
a{{display:block;margin:1rem 0;padding:1rem;background:#2563eb;color:#fff;text-decoration:none;border-radius:8px;text-align:center;font-size:1.1rem}}
p,li{{line-height:1.5;color:#aaa}} .ok{{color:#4ade80}} .warn{{color:#fbbf24}}</style></head><body>
<h1>Cuttle — phone access</h1>
<p class="warn">Android Chrome uses <strong>HTTPS</strong> on port <strong>{phone_port_label}</strong>. Accept the certificate warning once.</p>
{qr_block}
<a href="{ping}">Test connection (lan-ping)</a>
<a href="{shell}">Open Cuttle</a>
<a href="{chat}">Open Chat</a>
<h2>Certificate warning (Android Chrome)</h2>
<ol>
<li>Tap <strong>Advanced</strong></li>
<li>Tap <strong>Proceed to {ip}</strong> (unsafe)</li>
</ol>
<p>Or use plain HTTP fallback: <a href="{http_fb}/api/lan-ping" style="display:inline;background:none;padding:0;color:#60a5fa">{http_fb}/api/lan-ping</a></p>
<h2>If it still does not load</h2>
<ul>
<li>Phone on <strong>same Wi‑Fi</strong> as this PC (mobile data off)</li>
<li>Try <strong>{http_fb}/api/lan-ping</strong> (port {http_port_label}, plain HTTP)</li>
<li>Run <strong>Enable Cuttle LAN Firewall</strong> as Administrator on PC</li>
</ul>
<p>PC only: <a href="{pc_url}" style="display:inline;background:none;padding:0;color:#60a5fa">{pc_url}</a></p>
</body></html>'''


@app.route('/')
def serve_app_shell():
    """Serve the app shell (main Electron entry point)"""
    return send_from_directory(project_root / 'web', 'app_shell.html')



@app.route('/app_shell.html')
def serve_app_shell_direct():
    """Serve app shell directly"""
    return send_from_directory(project_root / 'web', 'app_shell.html')

@app.route('/query_log.html')
def serve_query_log():
    """In-pane / pop-out query inspector (JSON-backed)."""
    return send_from_directory(project_root / 'web', 'query_log.html')


@app.route('/router_editor.html')
def serve_router_editor():
    """Serve the Router editor page (replaces the retired pipeline node editor)"""
    return send_from_directory(project_root / 'web', 'router_editor.html')



@app.route('/jobs_page.html')
def serve_jobs_page():
    """Serve the unified executions ledger (agent turns + mesh jobs)."""
    return send_from_directory(project_root / 'web', 'jobs_page.html')


@app.route('/mesh_page.html')
def serve_mesh_page():
    """Serve the device mesh admin (workers, pairing, mesh job queue)."""
    return send_from_directory(project_root / 'web', 'mesh_page.html')


@app.route('/dashboards_page.html')
def serve_dashboards_page():
    """Serve the Dashboards hub (Model Benchmarks and future tiles)."""
    return send_from_directory(project_root / 'web', 'dashboards_page.html')


@app.route('/apps_page.html')
def serve_apps_page():
    """Serve the Apps launcher (Cuttle web apps grid; pin/unpin to the blade bar)."""
    return send_from_directory(project_root / 'web', 'apps_page.html')


@app.route('/settings_page.html')
def serve_settings_page():
    """Serve the settings page"""
    return send_from_directory(project_root / 'web', 'settings_page.html')

@app.route('/achievements_preview.html')
@owner_required
def serve_achievements_preview():
    """Achievement celebration preview (toast + confetti + sfx).

    Fires CuttleCelebrate directly with sample payloads, so it needs neither the
    achievements flag nor any API route. Owner-only: it is a dev/demo surface.
    """
    return send_from_directory(project_root / 'web', 'achievements_preview.html')


@app.route('/api/status', methods=['GET'])
def api_status():
    """Liveness ping for the daemon watchdog and the app shell status panel.

    Keep this cheap: no subprocesses, no outbound probes. Diagnostic checks
    belong on GET /api/health (claude CLI, WSL, local LLM).
    """
    return jsonify({
        'status': 'ok',
        'flask': True,
    })


@app.route('/api/chat-cancel', methods=['POST'])
def chat_cancel():
    """Cancel in-flight generation for a chat session (Stop button / delete chat)."""
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    if sid is None or str(sid).strip() == '':
        return jsonify({'success': False, 'error': 'session_id required'}), 400

    from api.cuttle_ui_capabilities import numeric_chat_session_id

    db_sid = numeric_chat_session_id(sid)

    if db_sid is not None:
        _user, nid, err = require_chat_session_access(db_sid)
        if err:
            return err
        db_sid = nid
    else:
        _user, err = require_authenticated()
        if err:
            return err

    cancel_sid = db_sid if db_sid is not None else sid
    try:
        from api.chat_run_registry import cancel_session_runs

        info = cancel_session_runs(cancel_sid) or {}
    except Exception as e:
        print(f"[CHAT] cancel_session_runs failed: {e}")
        info = {'cancelled': False, 'error': str(e)}
    try:
        from api.subagents.service import on_session_cancelled

        on_session_cancelled(cancel_sid)
    except Exception as _sc_err:
        print(f"[CHAT] subagent cancel hook: {_sc_err}", flush=True)

    job_ids = []
    for raw in data.get('job_ids') or []:
        ident = str(raw or '').strip()
        if ident:
            job_ids.append(ident)
    if data.get('cancel_jobs'):
        try:
            from api.chat_run_registry import session_job_ids
            for ident in session_job_ids(cancel_sid):
                if ident not in job_ids:
                    job_ids.append(ident)
        except Exception:
            pass
    killed_jobs = 0
    for ident in job_ids:
        try:
            from api.job_watch import cancel_job
            jinfo = cancel_job(ident) or {}
            if jinfo.get('killed') or jinfo.get('ok'):
                killed_jobs += int(jinfo.get('killed') or 0) or (1 if jinfo.get('ok') else 0)
            try:
                from api.chat_run_registry import clear_session_jobs
                clear_session_jobs(cancel_sid, ident)
            except Exception:
                pass
        except Exception as e:
            print(f"[CHAT] cancel_job {ident!r} failed: {e}")

    try:
        clear_chat_live_status(cancel_sid)
    except Exception:
        pass

    return jsonify({
        'success': True,
        'cancelled': bool(info.get('cancelled')) or killed_jobs > 0 or bool(job_ids),
        'killed_procs': int(info.get('killed_procs') or 0),
        'killed_jobs': killed_jobs,
        'query_ids': info.get('query_ids') or [],
    })


@app.route('/api/chat-steer', methods=['POST'])
def chat_steer():
    """Inject a follow-up into the chat's running agent turn (Codex / Muse Code / Claude Code).

    ``steered: false`` means nothing steerable is live (or the harness rejected
    it) — the client keeps the message on its follow-up queue instead.
    """
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    message = str(data.get('message') or '').strip()
    if sid is None or str(sid).strip() == '':
        return jsonify({'success': False, 'error': 'session_id required'}), 400
    if not message:
        return jsonify({'success': False, 'error': 'message required'}), 400

    from api.cuttle_ui_capabilities import numeric_chat_session_id

    db_sid = numeric_chat_session_id(sid)
    if db_sid is not None:
        _user, nid, err = require_chat_session_access(db_sid)
        if err:
            return err
        db_sid = nid
    else:
        _user, err = require_authenticated()
        if err:
            return err

    from api.agent_harness.steer import steer as steer_live_turn

    info = steer_live_turn(db_sid if db_sid is not None else sid, message)
    if info.get('steered') and db_sid is not None:
        _persist_auth_user_message(
            db_sid,
            message,
            {'steered': True, 'steered_agent': info.get('agent')},
        )
    return jsonify({'success': True, **info})


@app.route('/api/chat-live-status', methods=['GET'])
def chat_live_status():
    """Latest in-flight chat status for cross-device viewers (poll while a reply is generating)."""
    sid = (request.args.get('session_id') or '').strip()
    if not sid:
        return jsonify({'success': False, 'error': 'session_id required'}), 400

    _user, _actor, err = _require_session_actor(sid)
    if err:
        return err

    bare = sid[len('db_session_'):] if sid.startswith('db_session_') else sid
    try:
        db_sid = int(bare)
    except (TypeError, ValueError):
        db_sid = None

    live = get_chat_live_status(sid)
    generating, live_active, cancelled, live = _public_live_generating(sid, live)
    supervised = None
    try:
        from api.agent_router.supervised.delivery import public_indicator, reconcile_session_delivery
        from api.agent_router.supervised.store import get_active_task

        # Reconcile terminal delivery on live-status polls (covers refresh/reconnect).
        reconcile_session_delivery(bare if db_sid is not None else sid)
        task = get_active_task(bare if db_sid is not None else sid)
        if task is None and db_sid is not None:
            task = get_active_task(db_sid)
        if task is None:
            task = get_active_task(sid)
        supervised = public_indicator(task)
        # Hide terminal archived indicator after a short window is client's job;
        # still return terminal=true so UI can archive.
    except Exception as _sup_err:
        print(f"[CHAT] supervised indicator: {_sup_err}", flush=True)
    widgets_revision = 0
    try:
        if db_sid is not None:
            token = get_request_session_token()
            if token:
                db = get_auth_db()
                user = db.verify_auth_session(token)
                if user:
                    sess = db.get_chat_session(db_sid, user['id'])
                    proj = (sess or {}).get('project_path') or ''
                    widgets_revision = db.widgets_revision_for(
                        user_id=user['id'],
                        session_id=db_sid,
                        project_path=proj,
                    )
    except Exception:
        widgets_revision = 0
    return jsonify({
        'success': True,
        'active': live_active,
        'status': live.get('status'),
        'updated_at': live.get('updated_at'),
        'report_url': live.get('report_url'),
        'query_id': live.get('query_id'),
        'slash_command': live.get('slash_command'),
        # Busy lock OR live status — stream can die while the worker is still going.
        # Deliberately excludes supervised background work (separate field).
        # Cancelled (Stop) wins: refresh must not resurrect the spinner.
        'generating': generating,
        'cancelled': cancelled,
        'supervised_task': supervised,
        'widgets_revision': widgets_revision,
        'subagents': _live_parent_subagents(sid),
    })


@app.route('/api/chat-live-status-batch', methods=['GET'])
def chat_live_status_batch():
    """One round-trip live-status for many chat sessions (split panes share one poll).

    Query: session_ids=1,2,db_session_3  (comma-separated, max 12)
    """
    raw = (request.args.get('session_ids') or request.args.get('ids') or '').strip()
    if not raw:
        return jsonify({'success': False, 'error': 'session_ids required'}), 400
    parts = [p.strip() for p in raw.split(',') if p.strip()]
    if len(parts) > 12:
        parts = parts[:12]

    user, err = require_authenticated()
    if err:
        return err
    db = get_auth_db()

    out = {}
    for sid in parts:
        bare = sid[len('db_session_'):] if sid.startswith('db_session_') else sid
        try:
            db_sid = int(bare)
        except (TypeError, ValueError):
            db_sid = None
        if db_sid is not None and user is not None and db is not None:
            try:
                if not db.get_chat_session(db_sid, user['id']):
                    continue
            except Exception:
                continue
        live = get_chat_live_status(sid)
        generating, live_active, cancelled, live = _public_live_generating(sid, live)
        supervised = None
        try:
            from api.agent_router.supervised.delivery import public_indicator
            from api.agent_router.supervised.store import get_active_task

            lookup = bare if db_sid is not None else sid
            task = get_active_task(lookup) or get_active_task(sid)
            if task is None and db_sid is not None:
                task = get_active_task(db_sid)
            supervised = public_indicator(task)
        except Exception:
            supervised = None
        widgets_revision = 0
        if db_sid is not None and user is not None and db is not None:
            try:
                sess = db.get_chat_session(db_sid, user['id'])
                proj = (sess or {}).get('project_path') or ''
                widgets_revision = db.widgets_revision_for(
                    user_id=user['id'],
                    session_id=db_sid,
                    project_path=proj,
                )
            except Exception:
                widgets_revision = 0
        out[str(sid)] = {
            'active': live_active,
            'status': live.get('status'),
            'updated_at': live.get('updated_at'),
            'report_url': live.get('report_url'),
            'query_id': live.get('query_id'),
            'slash_command': live.get('slash_command'),
            'generating': generating,
            'cancelled': cancelled,
            'supervised_task': supervised,
            # Electron / app-shell poll this batch instead of per-pane live-status;
            # without widgets_revision the Tasks strip never refreshes until reload.
            'widgets_revision': widgets_revision,
            'subagents': _live_parent_subagents(sid),
        }
        # Also index under bare / db_session_ aliases for easy client lookup.
        if db_sid is not None:
            out[str(db_sid)] = out[str(sid)]
            out[f'db_session_{db_sid}'] = out[str(sid)]

    return jsonify({'success': True, 'statuses': out})


@app.route('/api/chat-pending-result', methods=['GET'])
def chat_pending_result():
    """Collect a reply that finished while the browser was disconnected.

    The chat page polls this after a dropped SSE stream (tab closed, refresh,
    network blip) so a completed agent run is never lost.

    Query:
      session_id (required)
      consume=0|1 — default 0 (peek, multi-device safe). Pass 1 to pop after the
        client has successfully rendered the reply.
    """
    sid = (request.args.get('session_id') or '').strip()
    if not sid:
        return jsonify({'success': False, 'error': 'session_id required'}), 400

    consume_raw = (request.args.get('consume') or '0').strip().lower()
    consume = consume_raw in ('1', 'true', 'yes', 'on')

    _user, _actor, err = _require_session_actor(sid)
    if err:
        return err

    bare = sid[len('db_session_'):] if sid.startswith('db_session_') else sid
    try:
        db_sid = int(bare)
    except (TypeError, ValueError):
        db_sid = None

    from api import chat_delivery
    # Never hand over a parked reply while this chat is still generating —
    # leftovers from a prior turn would make the UI chirp and drop the typing
    # indicator mid-run. (take_result would also destroy the stale entry; we
    # only discard when starting a new turn via try_begin.)
    if chat_delivery.is_turn_cancelled(sid):
        return jsonify({
            'success': True,
            'pending': False,
            'generating': False,
            'cancelled': True,
        })
    if chat_delivery.is_busy(sid):
        return jsonify({
            'success': True,
            'pending': False,
            'generating': True,
        })
    result = (
        chat_delivery.take_result(sid)
        if consume
        else chat_delivery.peek_result(sid)
    )
    if not result:
        return jsonify({
            'success': True,
            'pending': False,
            'generating': False,
        })
    return jsonify({
        'success': True,
        'pending': True,
        'generating': False,
        'result': {
            'success': bool(result.get('success')),
            'response': result.get('response', '') or '',
            'session_id': sid,
            'type': result.get('type', 'unknown'),
            'query_id': result.get('query_id'),
            'report_url': result.get('report_url'),
        },
    })


@app.route('/api/query-live-status', methods=['GET'])
@authenticated_required
def query_live_status():
    """Live execution snapshot for in-progress query reports (poll from placeholder HTML or UI)."""
    qid = (request.args.get('query_id') or '').strip()
    if not qid or len(qid) > 36:
        return jsonify({'error': 'query_id required'}), 400
    try:
        from api.active_executions import is_query_executing
        from api.query_tracker import get_query_tracker, get_shared_live_snapshot
        executing = is_query_executing(qid)
        tracker = get_query_tracker(qid)
        stages = []
        llm_calls = []
        tool_calls = []
        d = None
        if tracker and tracker.query_id == qid and tracker.execution_data:
            d = tracker.execution_data
        else:
            snap = None
            if tracker:
                snap = tracker.get_live_snapshot(qid)
            if not snap:
                snap = get_shared_live_snapshot(qid)
            if snap:
                d = snap
        if d:
            for s in (d.get('execution_stages') or [])[-25:]:
                det = s.get('details')
                if isinstance(det, dict):
                    det_preview = det.get('details') or det.get('source') or ''
                else:
                    det_preview = str(det) if det else ''
                stages.append({
                    'name': s.get('name'),
                    'type': s.get('type'),
                    'success': s.get('success'),
                    'duration': s.get('duration'),
                    'details': (det_preview or '')[:400],
                })
            for c in (d.get('llm_calls') or [])[-8:]:
                notes = (c.get('notes') or '')[:200]
                if c.get('success') is False:
                    err = (c.get('response_preview') or c.get('error') or '')[:400]
                    if err:
                        notes = (notes + ' — ' if notes else '') + err
                llm_calls.append({
                    'model': c.get('model'),
                    'success': c.get('success'),
                    'total_tokens': c.get('total_tokens', 0),
                    'notes': notes[:500],
                })
            for t in (d.get('tool_calls') or [])[-8:]:
                prev = t.get('input_preview') or t.get('result_preview') or ''
                if not prev and t.get('parameters') is not None:
                    try:
                        prev = json.dumps(t.get('parameters'), ensure_ascii=False)[:500]
                    except Exception:
                        prev = str(t.get('parameters'))[:500]
                tool_calls.append({
                    'tool_name': t.get('tool_name') or t.get('name'),
                    'success': t.get('success'),
                    'input_preview': (prev or '')[:400],
                })
        return jsonify({
            'query_id': qid,
            'executing': executing,
            'stages': stages,
            'llm_calls': llm_calls,
            'tool_calls': tool_calls,
            'events': (d.get('events') or [])[-80:] if d else [],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/query-log/<query_id>', methods=['GET'])
@authenticated_required
def api_query_log(query_id):
    """Structured harness turn log for the in-pane inspector."""
    from api.query_events import build_query_log_response

    payload, err, code = build_query_log_response(query_id)
    if err:
        return jsonify({'error': err}), code
    return jsonify(payload)


# Notify queue path for tray notifications (daemon watches this file).
# Kept for compatibility; the live writer is api.ui_notify.notify_tray.
NOTIFY_QUEUE_PATH = _notify_queue_path()

# App-shell split panes (reading order: left→right or top→bottom). Client POSTs on layout change; agents read via GET.
# columns: [{ "page": "/chat_page.html?chat=42", "flex": "..." }, ...]
# orientation: "horizontal" | "vertical"
_shell_pane_layout = {'version': 1, 'orientation': 'horizontal', 'columns': [], 'updated_at': None}


def _parse_chat_id_from_page(page: str):
    """Extract ?chat= / ?session= from a shell column page URL. Returns str or None."""
    if not page or not isinstance(page, str):
        return None
    try:
        from urllib.parse import urlparse, parse_qs
        raw = page.strip()
        if not raw.startswith('http'):
            raw = 'https://local' + (raw if raw.startswith('/') else '/' + raw)
        q = parse_qs(urlparse(raw).query)
        for key in ('chat', 'session'):
            vals = q.get(key) or []
            if vals and str(vals[0]).strip():
                return str(vals[0]).strip()
    except Exception:
        pass
    return None


_SHELL_WORKSPACE_MAX = 20
_SHELL_WORKSPACE_COLUMNS_MAX = 12
_SHELL_PAGE_SAFE_RE = re.compile(r'^/[A-Za-z0-9_./-]+\.html(?:[?#][^\s]*)?$')


def _normalize_shell_orientation(raw) -> str:
    return 'vertical' if str(raw or '').strip().lower() == 'vertical' else 'horizontal'


def _normalize_shell_workspace_columns(columns):
    """Validate split-pane snapshots for save/load workspace."""
    if not isinstance(columns, list):
        return []
    cleaned = []
    for col in columns[:_SHELL_WORKSPACE_COLUMNS_MAX]:
        if not isinstance(col, dict):
            continue
        page = str(col.get('page') or '/chat_page.html').strip()[:500]
        if not page.startswith('/') or '://' in page or '\\' in page:
            page = '/chat_page.html'
        if not _SHELL_PAGE_SAFE_RE.match(page.split('#', 1)[0]):
            # Allow query strings the regex already includes; reject junk paths.
            path_only = page.split('?', 1)[0]
            if not path_only.startswith('/') or not path_only.endswith('.html'):
                page = '/chat_page.html'
        flex = str(col.get('flex') or '')[:80]
        chat = str(col.get('chat') or '').strip()[:64]
        terminal = str(col.get('terminal') or '').strip()[:64]
        entry = {'page': page, 'flex': flex}
        if chat:
            entry['chat'] = chat
        if terminal:
            entry['terminal'] = terminal
        cleaned.append(entry)
    return cleaned or [{'page': '/chat_page.html', 'flex': ''}]


def _workspace_column_summary(col: dict) -> str:
    page = str((col or {}).get('page') or '')
    sid = _parse_chat_id_from_page(page) or (col or {}).get('chat') or (col or {}).get('terminal')
    kind = 'Pane'
    try:
        from urllib.parse import urlparse
        raw = page if page.startswith('http') else 'https://local' + (page if page.startswith('/') else '/' + page)
        path = urlparse(raw).path or ''
        if 'chat_page' in path:
            kind = 'Chat'
        elif 'terminal_page' in path:
            kind = 'Terminal'
        elif path:
            kind = Path(path).stem.replace('_', ' ').replace('-', ' ').title() or 'Pane'
    except Exception:
        pass
    if sid:
        raw_id = str(sid).strip()
        if raw_id.isdigit():
            return f"{kind} CH-{int(raw_id):06d}"
        return f"{kind} {raw_id}"
    return kind


def _public_shell_workspace(item: dict) -> dict:
    columns = item.get('columns') or []
    out = {
        'id': item.get('id'),
        'name': item.get('name') or 'Workspace',
        'updated_at': item.get('updated_at'),
        'orientation': _normalize_shell_orientation(item.get('orientation')),
        'columns': columns,
        'pane_count': len(columns),
        'summary': ' · '.join(_workspace_column_summary(c) for c in columns) or '1 pane',
    }
    root = item.get('root')
    if isinstance(root, dict):
        out['root'] = root
    return out


def _load_shell_workspaces() -> list:
    from managers.settings_manager import get_settings_manager
    raw = get_settings_manager().get_setting('shell_workspaces') or {}
    items = raw.get('items') if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:_SHELL_WORKSPACE_MAX]:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        columns = _normalize_shell_workspace_columns(item.get('columns'))
        name = str(item.get('name') or 'Workspace').strip()[:80] or 'Workspace'
        entry = {
            'id': str(item.get('id'))[:40],
            'name': name,
            'updated_at': item.get('updated_at'),
            'orientation': _normalize_shell_orientation(item.get('orientation')),
            'columns': columns,
        }
        root = item.get('root')
        if isinstance(root, dict):
            entry['root'] = root
        out.append(entry)
    return out


def _save_shell_workspaces(items: list) -> list:
    from managers.settings_manager import get_settings_manager
    trimmed = items[:_SHELL_WORKSPACE_MAX]
    get_settings_manager().set_setting('shell_workspaces', {
        'version': 1,
        'items': trimmed,
        'updated_at': time.time(),
    })
    return trimmed


def _shell_panes_snapshot():
    """Normalized pane list (0-indexed column → 1-indexed pane number for users)."""
    cols = _shell_pane_layout.get('columns') or []
    panes = []
    for i, col in enumerate(cols):
        page = (col or {}).get('page') or ''
        sid = _parse_chat_id_from_page(page)
        title = None
        kind = 'other'
        try:
            from urllib.parse import urlparse
            raw = page if page.startswith('http') else 'https://local' + (page if page.startswith('/') else '/' + page)
            path = urlparse(raw).path or ''
            if 'chat_page' in path:
                kind = 'chat'
            elif 'terminal_page' in path:
                kind = 'terminal'
        except Exception:
            pass
        if sid and kind == 'chat':
            try:
                db_sid = _parse_auth_db_session_id(sid)
                if db_sid is not None:
                    db = get_auth_db()
                    conn = db._get_connection()
                    cur = conn.cursor()
                    cur.execute(
                        'SELECT session_name FROM chat_sessions WHERE id = ? AND is_active = 1',
                        (int(db_sid),),
                    )
                    row = cur.fetchone()
                    conn.close()
                    if row:
                        title = row['session_name'] if hasattr(row, 'keys') else row[0]
            except Exception:
                pass
        panes.append({
            'pane': i + 1,
            'column': i,
            'page': page,
            'kind': kind,
            'session_id': sid,
            'title': title,
        })
    return panes


def _ordinal_pane_refs(text: str):
    """Return 1-indexed pane numbers mentioned in natural language (e.g. '1st pane', 'pane 2')."""
    if not text or not isinstance(text, str):
        return []
    low = text.lower()
    found = []
    word_map = {
        'first': 1, '1st': 1, 'one': 1, 'leftmost': 1, 'left': 1,
        'second': 2, '2nd': 2, 'two': 2, 'middle': 2,
        'third': 3, '3rd': 3, 'three': 3,
        'fourth': 4, '4th': 4, 'four': 4,
        'fifth': 5, '5th': 5, 'five': 5,
        'rightmost': -1, 'right': -1,
    }
    # "1st pane", "first pane", "pane 2", "panes 1 and 2"
    for m in re.finditer(
        r'\b(?:(\d+)(?:st|nd|rd|th)?|(first|second|third|fourth|fifth|leftmost|rightmost|left|right|middle))\s+panes?\b'
        r'|\bpanes?\s*(?:#|number\s*)?(\d+)\b',
        low,
    ):
        token = (m.group(1) or m.group(2) or m.group(3) or '').strip()
        if not token:
            continue
        if token.isdigit():
            n = int(token)
        else:
            n = word_map.get(token)
        if n is None:
            continue
        if n not in found:
            found.append(n)
    if re.search(r'\ball\s+panes?\b', low) or re.search(r'\bevery\s+pane\b', low):
        return ['all']
    return found


def _format_pane_messages_for_prompt(pane_number: int, limit: int = 40):
    """Load recent messages for a 1-indexed pane. Returns (header, body) or error string."""
    panes = _shell_panes_snapshot()
    if pane_number == -1:
        pane_number = len(panes)
    if pane_number < 1 or pane_number > len(panes):
        return None, f'(Pane {pane_number} is not open — {len(panes)} pane(s) currently.)'
    pane = panes[pane_number - 1]
    sid = pane.get('session_id')
    title = pane.get('title') or '(untitled)'
    header = f'### Pane {pane_number} — {pane.get("kind")} session `{sid}` — {title}'
    if not sid:
        return header, '(No chat session open in this pane.)'
    if pane.get('kind') == 'terminal':
        return header, '(Terminal pane — no chat message history.)'
    db_sid = _parse_auth_db_session_id(sid)
    messages = []
    if db_sid is not None:
        try:
            messages = get_auth_db().get_messages(int(db_sid), limit=limit)
        except Exception as e:
            return header, f'(Failed to load messages: {e})'
    else:
        # Legacy in-memory session
        try:
            sess = chat_sessions.get(sid) or {}
            messages = (sess.get('messages') or [])[-limit:]
        except Exception:
            messages = []
    if not messages:
        return header, '(No messages yet.)'
    lines = []
    for msg in messages:
        role = (msg.get('role') or '?').upper()
        content = (msg.get('content') or '').strip()
        if len(content) > 4000:
            content = content[:4000] + '\n…(truncated)'
        lines.append(f'**{role}:** {content}')
    return header, '\n\n'.join(lines)


def _build_shell_pane_prompt_addon(user_message: str, history_limit: int = 40) -> str:
    """
    Compact pane map always (when 2+ panes). Full history for panes the user named
    (e.g. "read 1st pane", "what's in pane 2").
    """
    panes = _shell_panes_snapshot()
    if len(panes) < 1:
        return ''
    orientation = _normalize_shell_orientation(_shell_pane_layout.get('orientation'))
    axis = 'top → bottom' if orientation == 'vertical' else 'left → right'
    lines = [
        f'## Cuttle UI panes ({axis})',
        'When the user says "1st pane" / "pane 2" / etc., they mean these columns.',
        'API: GET /api/shell/panes  |  GET /api/shell/panes/<n>/messages?limit=40',
    ]
    for p in panes:
        sid = p.get('session_id') or '—'
        title = p.get('title') or ''
        extra = f' "{title}"' if title else ''
        lines.append(f'- **{p["pane"]}{"st" if p["pane"]==1 else "nd" if p["pane"]==2 else "rd" if p["pane"]==3 else "th"} pane**: {p.get("kind")} session `{sid}`{extra}')
    refs = _ordinal_pane_refs(user_message or '')
    want = []
    if refs == ['all'] or (isinstance(refs, list) and refs and refs[0] == 'all'):
        want = list(range(1, len(panes) + 1))
    else:
        for n in refs:
            if n == -1:
                want.append(len(panes))
            elif isinstance(n, int) and n >= 1:
                want.append(n)
    # Dedupe preserve order
    seen = set()
    want = [n for n in want if not (n in seen or seen.add(n))]
    if want:
        lines.append('')
        lines.append('## Requested pane history')
        for n in want:
            hdr, body = _format_pane_messages_for_prompt(n, limit=history_limit)
            if hdr:
                lines.append(hdr)
            lines.append(body or '')
            lines.append('')
    elif len(panes) > 1:
        lines.append('')
        lines.append(
            'To inspect another pane, call the API above or ask the user which pane — '
            'history is auto-attached when they name a pane.'
        )
    if len(panes) < 2 and not want:
        return ''
    return '\n'.join(lines).strip()


@app.route('/api/toast', methods=['POST'])
@authenticated_required
def api_toast():
    """Queue tray + in-app UI toast (Electron/app_shell polls /api/ui-toasts)."""
    try:
        data = request.get_json() or {}
        message = data.get('message', '')[:500]  # limit length
        variant = data.get('variant', 'info')
        if not message:
            return jsonify({'success': False, 'error': 'message required'}), 400
        from api.ui_notify import notify_tray
        notify_tray(message, variant=str(variant or 'info'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ui-toasts', methods=['GET'])
def api_ui_toasts():
    """Pull+clear pending UI toasts for Electron/app_shell."""
    _user, err = loopback_or_authenticated()
    if err:
        return err
    try:
        from api.ui_notify import pull_ui_toasts
        return jsonify({'toasts': pull_ui_toasts()})
    except Exception:
        return jsonify({'toasts': []})


@app.route('/git_graph_page.html')
def serve_git_graph_page():
    """Serve the vertical git topology visualizer."""
    return send_from_directory(project_root / 'web', 'git_graph_page.html')


@app.route('/chat_page.html')
def serve_chat_page():
    """Serve the chat page"""
    return send_from_directory(project_root / 'web', 'chat_page.html')


@app.route('/img/<path:filename>')
def serve_image(filename):
    """Serve images from the img directory"""
    return send_from_directory(project_root / 'img', filename)


@app.route('/css/<path:filename>')
def serve_css(filename):
    """Serve CSS files from the web/css directory"""
    return send_from_directory(project_root / 'web' / 'css', filename)

@app.route('/js/<path:filename>')
def serve_js(filename):
    """Serve JavaScript files from the web/js directory"""
    return send_from_directory(project_root / 'web' / 'js', filename)

@app.route('/sounds/<path:filename>')
def serve_sounds(filename):
    """Serve short UI sound effects (e.g. reply-ready chirp)"""
    return send_from_directory(project_root / 'web' / 'sounds', filename)

@app.route('/output/<path:filename>')
def serve_output(filename):
    """Serve files from the output directory"""
    if filename.endswith('-status.json'):
        job_id = filename[: -len('-status.json')]
        try:
            from api.job_watch import read_status_reconciled, sanitize_job_id

            sid = sanitize_job_id(job_id)
            data = read_status_reconciled(sid, persist=True)
            if data is not None:
                resp = jsonify(data)
                resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
                resp.headers['Pragma'] = 'no-cache'
                return resp
        except ValueError:
            pass
        except Exception as e:
            print(f"[OUTPUT] status reconcile failed for {filename}: {e}")
    # Lazy mid-frame poster for staged videos: /output/shared/foo.poster.jpg
    try:
        norm = str(filename).replace('\\', '/')
        if norm.startswith('shared/') and norm.lower().endswith('.poster.jpg'):
            from api.shared_media import ensure_poster_sidecar_file

            ensure_poster_sidecar_file(Path(norm).name)
    except Exception as e:
        print(f"[OUTPUT] poster ensure failed for {filename}: {e}")
    resp = make_response(send_from_directory(_output_dir(), filename))
    # Phone / LAN download: ?download=1 forces attachment disposition
    as_download = (request.args.get('download') or '').strip().lower() in (
        '1', 'true', 'yes',
    )
    if as_download:
        base = Path(filename).name
        resp.headers['Content-Disposition'] = f'attachment; filename="{base}"'
    return resp


@app.route('/logs/')
def serve_logs_directory():
    """Serve the logs directory listing"""
    return send_from_directory(_query_logs_dir(), 'index.html')

@app.route('/logs/<path:filename>')
def serve_logs(filename):
    """Serve files from the query logs directory (<home>/logs/queries)"""
    if filename.startswith('query_report_') and filename.endswith('.html'):
        stem = filename[len('query_report_'):-5]
        qid = (stem.split('_')[0] if stem else '').strip()
        if qid:
            return redirect(f'/query_log.html?id={qid}', code=302)
    resp = make_response(
        send_from_directory(_query_logs_dir(), filename)
    )
    return resp














def _parse_auth_db_session_id(chat_session_id):
    """Accept bare ints, ``db_session_<id>``, or ``CH-000155`` from clients."""
    from api.cuttle_ui_capabilities import numeric_chat_session_id

    return numeric_chat_session_id(chat_session_id)


def _auth_session_id_is_absent(chat_session_id) -> bool:
    """True when the client is on the welcome splash (mint a new chat)."""
    if chat_session_id is None:
        return True
    s = str(chat_session_id).strip().lower()
    return s in ("", "anon", "null", "undefined", "none")


def _apply_request_agent_pins(session_id, data) -> None:
    """Pin first-turn model/effort from the chat POST before badge + agent run.

    The client cannot POST ``/api/<agent>/effort`` until it knows the session id.
    That mint happens inside this request, and the post-adopt client POST races
    both ``_user_badge_metadata`` and the harness execute — so new-chat picks
    must arrive on the chat body as ``agent_pins``.
    """
    if session_id is None or not isinstance(data, dict):
        return
    pins = data.get('agent_pins')
    if not isinstance(pins, dict) or not pins:
        return

    def _entry(agent: str) -> dict:
        raw = pins.get(agent)
        return raw if isinstance(raw, dict) else {}

    try:
        codex = _entry('codex')
        if codex:
            from scripts.utilities.codex_cli_session_store import (
                save_codex_effort,
                save_codex_model,
            )
            model = str(codex.get('model') or '').strip()
            effort = str(codex.get('effort') or '').strip().lower()
            if model:
                save_codex_model(session_id, model)
            if effort and effort not in ('default', 'reset', 'clear', 'none'):
                save_codex_effort(session_id, effort)
    except Exception as exc:
        print(f"[CHAT] apply codex agent_pins failed: {exc}", flush=True)

    try:
        muse = _entry('muse')
        if muse:
            from scripts.utilities.muse_cli_session_store import (
                save_muse_effort,
                save_muse_model,
            )
            model = str(muse.get('model') or '').strip()
            effort = str(muse.get('effort') or '').strip().lower()
            if model:
                save_muse_model(session_id, model)
            if effort and effort not in ('default', 'reset', 'clear', 'none'):
                save_muse_effort(session_id, effort)
    except Exception as exc:
        print(f"[CHAT] apply muse agent_pins failed: {exc}", flush=True)

    try:
        hermes = _entry('hermes')
        if hermes:
            from scripts.utilities.hermes_cli_session_store import (
                save_hermes_effort,
                save_hermes_model,
            )
            model = str(hermes.get('model') or '').strip()
            effort = str(hermes.get('effort') or '').strip().lower()
            if model:
                save_hermes_model(session_id, model)
            if effort and effort not in ('default', 'reset', 'clear', 'none'):
                save_hermes_effort(session_id, effort)
    except Exception as exc:
        print(f"[CHAT] apply hermes agent_pins failed: {exc}", flush=True)

    try:
        opencode = _entry('opencode')
        if opencode:
            from api.agent_harness.agents.opencode.session_store import (
                save_opencode_effort,
                save_opencode_model,
            )
            model = str(opencode.get('model') or '').strip()
            effort = str(opencode.get('effort') or '').strip().lower()
            if model:
                save_opencode_model(session_id, model)
            if effort and effort not in ('default', 'reset', 'clear', 'none'):
                save_opencode_effort(session_id, effort)
    except Exception as exc:
        print(f"[CHAT] apply opencode agent_pins failed: {exc}", flush=True)

    try:
        claude = _entry('claude')
        if claude:
            from scripts.utilities.claude_cli_session_store import (
                save_claude_effort,
                save_claude_model,
            )
            model = str(claude.get('model') or '').strip()
            effort = str(claude.get('effort') or '').strip().lower()
            if model:
                save_claude_model(session_id, model)
            if effort and effort not in ('default', 'reset', 'clear', 'none'):
                save_claude_effort(session_id, effort)
    except Exception as exc:
        print(f"[CHAT] apply claude agent_pins failed: {exc}", flush=True)


_HARNESS_IDENTITY_AGENTS = frozenset({'muse', 'hermes', 'opencode', 'codex', 'claude'})


def _harness_agent_from_message(message_text: str) -> Optional[str]:
    m = re.match(
        r'^/(muse|hermes|opencode|codex|claude|cursor)\b',
        str(message_text or '').lstrip(),
        re.IGNORECASE,
    )
    return m.group(1).lower() if m else None


def _load_harness_session_model_effort(agent: str, session_id) -> tuple:
    """Return ``(model, effort)`` strings from this chat's pin store."""
    aid = str(agent or '').strip().lower()
    sid = session_id
    model = effort = ''
    if sid is None:
        return model, effort
    try:
        if aid == 'muse':
            from scripts.utilities.muse_cli_session_store import (
                load_muse_effort,
                load_muse_model,
            )
            model = str(load_muse_model(sid) or '').strip()
            effort = str(load_muse_effort(sid) or '').strip()
        elif aid == 'hermes':
            from scripts.utilities.hermes_cli_session_store import (
                load_hermes_effort,
                load_hermes_model,
            )
            model = str(load_hermes_model(sid) or '').strip()
            effort = str(load_hermes_effort(sid) or '').strip()
        elif aid == 'opencode':
            from api.agent_harness.agents.opencode.session_store import (
                load_opencode_effort,
                load_opencode_model,
            )
            model = str(load_opencode_model(sid) or '').strip()
            effort = str(load_opencode_effort(sid) or '').strip()
        elif aid == 'codex':
            from scripts.utilities.codex_cli_session_store import (
                load_codex_effort,
                load_codex_model,
            )
            model = str(load_codex_model(sid) or '').strip()
            effort = str(load_codex_effort(sid) or '').strip()
        elif aid == 'claude':
            from scripts.utilities.claude_cli_session_store import (
                load_claude_effort,
                load_claude_model,
            )
            model = str(load_claude_model(sid) or '').strip()
            effort = str(load_claude_effort(sid) or '').strip()
    except Exception:
        return '', ''
    return model, effort


def _request_agent_pin(data, agent: str) -> tuple:
    """Composer identity from this POST (``agent_pins``), if present."""
    if not isinstance(data, dict):
        return '', ''
    pins = data.get('agent_pins')
    if not isinstance(pins, dict):
        return '', ''
    raw = pins.get(agent)
    if not isinstance(raw, dict):
        return '', ''
    model = str(raw.get('model') or '').strip()
    effort = str(raw.get('effort') or '').strip().lower()
    if effort in ('default', 'reset', 'clear', 'none'):
        effort = ''
    return model, effort


def _freeze_send_identity(session_id, message_text, data=None) -> Optional[dict]:
    """One send-time (agent, model, effort) for the user chip AND the CLI argv.

    Pins on the request body win even if ``_apply_request_agent_pins`` could
    not write the session store yet (adopt race). Session pin then starred.
    """
    agent = _harness_agent_from_message(message_text)
    if agent not in _HARNESS_IDENTITY_AGENTS:
        return None
    from api.agent_harness.agent_defaults import (
        resolve_effective_effort,
        resolve_effective_model,
    )

    pin_model, pin_effort = _request_agent_pin(data, agent)
    sess_model, sess_effort = _load_harness_session_model_effort(agent, session_id)
    # This send's composer pin beats a stale session pin (adopt/POST race).
    model, model_source = resolve_effective_model(
        agent,
        session_model=pin_model or sess_model or None,
        cli_default='',
    )
    effort, effort_source = resolve_effective_effort(
        agent,
        session_effort=pin_effort or sess_effort or None,
        cli_default=None,
    )
    return {
        'agent': agent,
        'model': (model or '').strip(),
        'effort': (effort or '').strip().lower(),
        'model_source': model_source,
        'effort_source': effort_source,
    }


def _harness_identity_run_kwargs(identity: Optional[dict]) -> dict:
    """kwargs for ``_run_harness_web_command`` from a frozen send identity."""
    if not isinstance(identity, dict):
        return {}
    kw: dict = {}
    model = str(identity.get('model') or '').strip()
    effort = str(identity.get('effort') or '').strip()
    if model:
        kw['model_override'] = model
    if effort:
        kw['execute_kwargs'] = {'reasoning_effort': effort}
    return kw


def _resolve_auth_chat_session(chat_session_id):
    """Resolve (or create) a DB chat session for the authenticated cookie user.

    Returns (user_dict_or_None, session_id, created_new). Slash commands run before
    the main auth pipeline path, so callers need a real session id up front for
    cancel/URL sync on the first message of a new chat.

    Only a missing id mints a new chat. A parseable handle that does not exist
    (or an unparseable agent CLI id) must not create a replacement — that
    redirected the phone UI into another thread and parked the turn there.
    """
    session_token = get_request_session_token()
    if not session_token:
        return None, chat_session_id, False
    try:
        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        if not user:
            return None, chat_session_id, False

        if _auth_session_id_is_absent(chat_session_id):
            from api.chat_turn_idempotency import mint_or_reuse

            rid = ""
            try:
                from flask import has_request_context, request as _req

                if has_request_context():
                    payload = _req.get_json(silent=True)
                    if isinstance(payload, dict):
                        rid = str(payload.get("chat_request_id") or "").strip()
            except Exception:
                rid = ""
            sid, created = mint_or_reuse(
                db,
                user_id=user["id"],
                chat_session_id=None,
                chat_request_id=rid,
            )
            return user, sid, created

        _db_sid = _parse_auth_db_session_id(chat_session_id)
        if _db_sid is not None:
            chat_session = db.get_chat_session(_db_sid, user['id'])
            if chat_session:
                return user, _db_sid, False
            return user, _db_sid, False
        return user, chat_session_id, False
    except Exception as e:
        print(f"[CHAT] early session resolve failed: {e}")
        return None, chat_session_id, False


def _sse_session_event(session_id) -> str:
    """First SSE event so the client can update ?chat= before the reply finishes."""
    import json as _json
    return (
        "data: "
        + _json.dumps(
            {'type': 'session', 'session_id': session_id},
            ensure_ascii=False,
            default=str,
        )
        + "\n\n"
    )


def _chat_busy_response(session_id):
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


# Response type prefix -> header chip shown beside the assistant name. Labels must
# match the palette entries in chat_page.js so history agrees with the live turn.
_SLASH_AGENT_CHIPS = {
    'codex': ('Codex', '/codex'),
    'muse': ('Muse Code', '/muse'),
    'hermes': ('Hermes Agent', '/hermes'),
    'opencode': ('OpenCode', '/opencode'),
    'antigravity': ('Antigravity CLI', '/antigravity'),
    'claude': ('Claude Code', '/claude'),
    'deepseek': ('DeepSeek Harness', '/deepseek'),
}


# Harness agents that share unified palette chrome (not generic "command").
_HARNESS_PALETTE_AGENTS = frozenset({
    'muse', 'hermes', 'opencode', 'codex', 'cursor',
    'claude', 'deepseek', 'antigravity',
})

# Badge display names for assistant reply chips (may differ from stock chip
# labels — e.g. Hermes enrichment uses "Hermes", not "Hermes Agent").
_HARNESS_BADGE_NAMES = {
    'muse': 'Muse Code',
    'hermes': 'Hermes',
    'opencode': 'OpenCode',
    'codex': 'Codex',
    'claude': 'Claude Code',
    'deepseek': 'DeepSeek Harness',
    'antigravity': 'Antigravity CLI',
}


def _slash_agent_chip(response_type) -> Optional[dict]:
    """Header chip for a CLI-agent response type (e.g. 'codex_error' -> Codex)."""
    rtype = str(response_type or '')
    if not rtype.endswith(('_command', '_error')):
        return None
    agent = rtype.rsplit('_', 1)[0]
    chip = _SLASH_AGENT_CHIPS.get(agent)
    category = agent if agent in _HARNESS_PALETTE_AGENTS else 'command'
    if chip:
        return {'label': chip[0], 'meta': chip[1], 'category': category}
    try:
        from api.agent_harness.catalog import get_agent

        pair = get_agent(agent)
        if pair:
            manifest = pair[0]
            return {
                'label': manifest.label or agent,
                'meta': manifest.slash_prefix().rstrip(),
                'category': category,
            }
    except Exception:
        pass
    return None


# Owned shaper (api.chat_metadata); alias preserves internal callers.
_muse_model_label = _metadata_svc.muse_model_label
def _harness_pretty_model_label(agent: str, model: str) -> str:
    """Best-effort human model label for a harness agent id."""
    mid = str(model or '').strip()
    if not mid:
        return ''
    aid = str(agent or '').strip().lower()
    try:
        if aid == 'muse':
            return _muse_model_label(mid)
        if aid == 'hermes':
            from scripts.utilities.hermes_cli_tool import hermes_model_label
            return hermes_model_label(mid)
        if aid == 'opencode':
            from api.agent_harness.agents.opencode.adapter import opencode_model_label
            return opencode_model_label(mid)
        if aid == 'codex':
            from api.agent_harness.agents.codex.model_catalog import codex_model_label
            return codex_model_label(mid)
        if aid == 'claude':
            from api.agent_harness.agents.claude.model_catalog import claude_model_label
            return claude_model_label(mid)
    except Exception:
        pass
    return mid


def _harness_session_effort(agent: str, session_id) -> str:
    """Load a pinned effort for agents that store one, else ''."""
    aid = str(agent or '').strip().lower()
    sid = session_id
    try:
        if aid == 'muse':
            from scripts.utilities.muse_cli_session_store import load_muse_effort
            return str(load_muse_effort(sid) or '').strip()
        if aid == 'hermes':
            from scripts.utilities.hermes_cli_session_store import load_hermes_effort
            return str(load_hermes_effort(sid) or '').strip()
        if aid == 'opencode':
            from api.agent_harness.agents.opencode.session_store import load_opencode_effort
            return str(load_opencode_effort(sid) or '').strip()
        if aid == 'codex':
            from scripts.utilities.codex_cli_session_store import load_codex_effort
            return str(load_codex_effort(sid) or '').strip()
        if aid == 'claude':
            from scripts.utilities.claude_cli_session_store import load_claude_effort
            return str(load_claude_effort(sid) or '').strip()
    except Exception:
        pass
    return ''


def _enrich_harness_assistant_chip(chip: dict, res: dict) -> dict:
    """Attach model (+ effort) to a non-Cursor harness reply chip.

    One path for every harness (CH-000497-8): Codex used to fall through with a
    bare "Codex" / category=command chip while Muse/Hermes/OpenCode got
    model·effort. Reads canonical ``agent_model`` / ``agent_effort`` (+ legacy
    per-agent keys).
    """
    if not isinstance(chip, dict) or not isinstance(res, dict):
        return chip
    rtype = str(res.get('type') or '')
    agent = rtype.rsplit('_', 1)[0] if '_' in rtype else str(res.get('agent_id') or '')
    agent = agent.strip().lower()
    if not agent or agent == 'cursor':
        return chip

    legacy_model_key = f'{agent}_model'
    legacy_effort_key = f'{agent}_effort'
    model = str(
        res.get('agent_model')
        or res.get(legacy_model_key)
        or res.get('model')
        or ''
    ).strip()
    # usage.model is often just the agent id ("codex") — skip that placeholder.
    if not model:
        usage = res.get('usage') if isinstance(res.get('usage'), dict) else {}
        um = str((usage or {}).get('model') or '').strip()
        if um and um.lower() not in (agent, 'auto', 'default'):
            model = um
    if not model:
        return chip

    effort = str(
        res.get('agent_effort') or res.get(legacy_effort_key) or ''
    ).strip()
    if not effort:
        effort = _harness_session_effort(agent, res.get('session_id'))

    display = _HARNESS_BADGE_NAMES.get(agent) or str(chip.get('label') or agent).strip()
    label_model = _harness_pretty_model_label(agent, model)
    slash = str(chip.get('meta') or f'/{agent}').split('·', 1)[0].strip() or f'/{agent}'

    out = dict(chip)
    out['category'] = agent if agent in _HARNESS_PALETTE_AGENTS else out.get('category') or 'command'
    out['label'] = f'{display} - {label_model}'
    out['meta'] = f'{slash} · model {model}'
    if effort:
        out['label'] += f' · {effort}'
        out['meta'] += f' · effort {effort}'
    return out


def _assistant_message_metadata(res: Optional[dict], request_data: Optional[dict] = None) -> Optional[dict]:
    """Build chat_messages.metadata for an assistant turn (query link + Cursor badge)."""
    if not isinstance(res, dict):
        return None
    meta: dict = {}
    if res.get('query_id'):
        meta['query_id'] = res['query_id']
    if res.get('report_url'):
        meta['report_url'] = res['report_url']
    cursor_run = res.get('cursor_run')
    if isinstance(cursor_run, dict) and cursor_run:
        meta['cursor_run'] = cursor_run
        # Badge = what we asked for (palette id / label). CLI "reported" strings
        # (e.g. "Sonnet 4.6 200K Medium") often disagree with `agent models`
        # labels ("Sonnet 4.6 1M Thinking") — keep reported in the tooltip only.
        requested = str(
            cursor_run.get('requested_model') or 'auto'
        ).strip() or 'auto'
        reported = str(cursor_run.get('reported_model') or '').strip()
        pretty = requested
        if requested.lower() in ('auto', 'default'):
            pretty = 'Auto'
        else:
            try:
                from api.cursor_agent_commands import list_cursor_agent_models
                for m in list_cursor_agent_models() or []:
                    if m.get('id') == requested and m.get('label'):
                        pretty = str(m['label']).strip() or requested
                        break
            except Exception:
                pass
        badge = pretty
        tip_parts = ['/cursor', f'requested {pretty}']
        if reported and reported.lower() not in (requested.lower(), pretty.lower()):
            tip_parts.append(f'CLI reported {reported}')
            # Unpinned turn that came back as a named model really ran that model
            # (premium usage). Badging it "Auto" hid the spend.
            if (
                requested.lower() in ('auto', 'default')
                and reported.lower() not in ('auto', 'default')
            ):
                badge = reported
        rid = cursor_run.get('request_id')
        if rid:
            tip_parts.append(f"req {str(rid)[:8]}…")
        cwd = cursor_run.get('cwd')
        if cwd:
            tip_parts.append(f'cwd {cwd}')
        meta['slash_command'] = {
            'chips': [{
                'label': f'Cursor - {badge}',
                'meta': ' · '.join(tip_parts),
                'category': 'cursor',
            }]
        }
        resp = str(res.get('response') or '')
        rtype = str(res.get('type') or '')
        if rtype.endswith('_error') or resp.startswith('[FAIL]') or resp.startswith('[CANCELLED]'):
            meta['slash_command_failed'] = True
    else:
        # Non-Cursor CLI agents have no cursor_run to hang a badge off, so the
        # chip (and its failed state) would be lost on history reload.
        chip = _slash_agent_chip(res.get('type'))
        if chip:
            chip = _enrich_harness_assistant_chip(chip, res)
            meta['slash_command'] = {'chips': [chip]}
            if str(res.get('type') or '').endswith('_error'):
                meta['slash_command_failed'] = True
    usage_meta = _usage_meta_from_assistant_result(res)
    if usage_meta:
        meta['usage'] = usage_meta
    req = request_data if request_data is not None else _current_request_data()
    sid = None
    if isinstance(req, dict):
        sid = req.get('session_id') or req.get('chat_session_id')
    cursor_run = res.get('cursor_run') if isinstance(res.get('cursor_run'), dict) else None
    meta = _merge_project_into_meta(meta or None, req, sid, cursor_run) or meta
    return meta or None


# Owned shaper (api.chat_metadata); alias preserves internal callers.
_usage_meta_from_assistant_result = _metadata_svc.usage_meta_from_assistant_result
def _current_request_data() -> dict:
    try:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            return body
    except Exception:
        pass
    return {}


# Owned shaper (api.chat_metadata); alias preserves internal callers.
_user_badge_metadata = _metadata_svc.user_badge_metadata
def _persist_auth_user_message(chat_session_id, message_text: str, metadata=None) -> None:
    """Save a user turn for authenticated slash-command chats (/cursor, /hermes, …)."""
    from api.chat_turn_persist import persist_auth_user_message as _owned

    _owned(
        get_auth_db(),
        chat_session_id,
        message_text,
        metadata=metadata,
        project_merge_fn=_merge_project_into_meta,
        request_data=_current_request_data(),
    )


def _attachment_history_text(message_text: str, note: str) -> str:
    """
    History form of an outbound prompt: drop the pre-analysis digest, keep the
    short ``[Attached: …]`` note the chat UI turns back into thumbnails.
    """
    from api.vision_prepass import DIGEST_HEADER

    text = message_text or ''
    idx = text.find(DIGEST_HEADER)
    if idx >= 0:
        text = text[:idx]
    text = text.rstrip()
    if not note:
        return text
    return f'{text}\n{note}' if text else note


def _run_attachment_prepass(message_content: str, raw_attachments, chat_session_id):
    """
    Analyze chat uploads and fold the result into the outbound prompt.

    Returns ``(agent_message, note, attachment_metadata)``. The digest is
    appended to the prompt (see append_attachment_digest); the note and
    metadata are what history persists so images survive a refresh.
    """
    from api.vision_prepass import append_attachment_digest, resolve_upload_refs

    uploads_root = _output_dir() / 'uploads'
    resolved = resolve_upload_refs(raw_attachments, uploads_root)
    if not resolved:
        print(f"[VISION] no usable attachment refs in {len(raw_attachments)} upload(s)")
        return message_content, '', []

    root_res = uploads_root.resolve()
    att_meta = []
    for r in resolved:
        fname = r.get('filename') or 'file'
        url = ''
        try:
            rel = Path(r['path']).resolve().relative_to(root_res)
            url = '/output/uploads/' + rel.as_posix()
        except Exception:
            # Fall back to client-supplied url when path mapping fails
            for raw in raw_attachments:
                if isinstance(raw, dict) and (
                    raw.get('filename') == fname or raw.get('path') == r.get('path')
                ):
                    url = (raw.get('url') or '').strip()
                    break
        att_meta.append({'filename': fname, 'mime': r.get('mime') or '', 'url': url})

    note = '[Attached: ' + ', '.join(a['filename'] for a in att_meta) + ']'
    status_sid = chat_session_id or 'anon'
    emit_chat_status(status_sid, 'Analyzing attachments...')
    agent_message = append_attachment_digest(
        message_content,
        resolved,
        status_cb=lambda m: emit_chat_status(status_sid, m),
    )
    print(f"[VISION] pre-pass ok for {len(resolved)} file(s)")
    return agent_message, note, att_meta


def _make_auth_assistant_saver(chat_session_id, project_path=None):
    """Return on_result callback that persists assistant replies for auth DB sessions.

    Construction (and its request-data capture) is owned by
    ``api.chat_turn_persist``; this wrapper only injects the entry's
    project/metadata/titler shapers. ``request_data`` is captured here —
    once, in the request thread — so late stream-thread saves see the
    same body the turn started with instead of an empty re-read.
    """
    from api.chat_titler import schedule_session_autoname
    from api.chat_turn_persist import make_assistant_saver as _owned

    _req = _current_request_data()

    def _meta_for_save(_res):
        return _assistant_message_metadata(_res, _req)

    return _owned(
        chat_session_id=chat_session_id,
        project_path=project_path,
        db=get_auth_db(),
        request_data=_req,
        resolve_project=_resolve_request_project_path,
        assistant_meta_fn=_meta_for_save,
        project_merge_fn=_merge_project_into_meta,
        schedule_autoname=schedule_session_autoname,
        project_root=actual_project_root,
    )


def _reject_if_chat_busy(session_id, wants_stream: bool):
    """If this chat already has a generating reply, return a Flask response; else None."""
    if session_id is None:
        return None
    from api import chat_delivery as _chat_delivery
    if not _chat_delivery.is_busy(session_id):
        return None
    body = _chat_busy_response(session_id)
    if wants_stream:
        def stream_busy():
            yield _sse_session_event(session_id)
            yield f"data: {json.dumps({'type': 'status', 'message': 'A reply is already generating…'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'busy', 'session_id': session_id}, ensure_ascii=False, default=str)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return Response(
            stream_with_context(stream_busy()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            },
        )
    return jsonify(body), 409


def _frame_stream_lifecycle_event(kind, payload, session_id_for_status):
    """Frame one owned lifecycle event as SSE chunk(s). Transport only.

    Verbatim framing the pump loop applied: status text plus the flush
    comment, query_started, and the done branch (badge/usage passthrough,
    response + done). Unknown kinds are ignored, exactly as the loop did.
    The coordinator already applied the response rewrite; framing never
    re-rewrites.
    """
    if kind == 'status':
        return [
            f"data: {json.dumps({'type': 'status', 'message': payload}, ensure_ascii=False)}\n\n",
            ": " + (" " * 128) + "\n\n",
        ]
    if kind == 'query_started':
        _pq = payload or {}
        return [
            f"data: {json.dumps({'type': 'query_started', 'query_id': _pq.get('query_id'), 'report_url': _pq.get('report_url'), 'slash_command': _pq.get('slash_command')}, ensure_ascii=False)}\n\n"
        ]
    if kind == 'done':
        result = payload or {}
        resp = {'type': 'response', 'success': result.get('success', False), 'response': result.get('response', ''), 'session_id': session_id_for_status if session_id_for_status is not None else result.get('session_id'), 'response_type': result.get('type', 'unknown')}
        if result.get('query_id'):
            resp['query_id'] = result['query_id']
        if result.get('report_url'):
            resp['report_url'] = result['report_url']
        if result.get('cursor_run'):
            resp['cursor_run'] = result['cursor_run']
        for _passthrough in (
            'routing_badge',
            'agent_id',
            'agent_model',
            'agent_effort',
            'muse_model',
            'muse_effort',
            'hermes_model',
            'hermes_effort',
            'opencode_model',
            'opencode_effort',
            'codex_model',
            'codex_effort',
        ):
            if result.get(_passthrough):
                resp[_passthrough] = result[_passthrough]
        try:
            _usage = _usage_meta_from_assistant_result(result)
            if _usage:
                resp['usage'] = _usage
        except Exception:
            if isinstance(result.get('usage'), dict) and result.get('usage'):
                resp['usage'] = result['usage']
            if result.get('cost') is not None:
                resp['cost'] = result['cost']
        return [
            f"data: {json.dumps(resp, ensure_ascii=False, default=str)}\n\n",
            f"data: {json.dumps({'type': 'done'})}\n\n",
        ]
    return []


def _pipeline_busy_chunks(session_id):
    """Busy triple without the session event (callers prefix it)."""
    _sid = session_id
    yield f"data: {json.dumps({'type': 'status', 'message': 'A reply is already generating…'}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'busy', 'session_id': _sid}, ensure_ascii=False, default=str)}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


def _stream_busy_sse_response(session_id):
    """Transport helper: busy answer without streaming (both stream twins)."""
    def _stream_busy():
        yield _sse_session_event(session_id)
        yield from _pipeline_busy_chunks(session_id)
    return Response(
        stream_with_context(_stream_busy()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


def _stream_connecting_head(session_id):
    """Transport helper: session event + Connecting liveness pair."""
    _sid = session_id
    if _sid is not None:
        yield _sse_session_event(_sid)
    yield f"data: {json.dumps({'type': 'status', 'message': 'Connecting...'}, ensure_ascii=False)}\n\n"
    yield ": " + (" " * 128) + "\n\n"


def _stream_agent_turn_response(prepared, *, io):
    """Submit one stream turn through the shared entry; frame events as SSE.

    Transport only: peek the first owned lifecycle event (shortcut/busy
    answer without streaming, exactly like the pre-P5-E lanes), else emit
    the session event plus the Connecting liveness pair and frame every
    lifecycle event verbatim. Claim/persist/run/rewrite/finalize/release
    all live in the coordinator entry, never here.

    The owned workflow publishes/clears live status from the worker, even
    after the client detaches. This subscriber only frames queued events.
    """
    from api import chat_delivery as _chat_delivery
    from api.chat_coordinator import (
        submit_agent_stream_turn as _submit_stream_turn,
    )

    _completion = {}
    _events = _submit_stream_turn(
        prepared,
        io=io,
        delivery=_chat_delivery,
        is_router_family=_is_router_family_message,
        completion=_completion,
    )
    _first = next(_events, None)
    if _first is not None and _first[0] == "shortcut":
        return jsonify(_first[1])
    if _first is not None and _first[0] == "busy":
        return _stream_busy_sse_response(prepared.session_id)

    def _stream_gen():
        _sid = prepared.session_id
        from contextlib import closing
        with closing(_events):
            yield from _stream_connecting_head(_sid)
            if _first is None:
                _first_event = (
                    "done",
                    {
                        "success": False,
                        "response": "Stream ended unexpectedly.",
                        "type": "stream_error",
                    },
                )
            else:
                _first_event = _first
            import itertools as _it
            for _kind, _payload in _it.chain([_first_event], _events):
                for _chunk in _frame_stream_lifecycle_event(_kind, _payload, _sid):
                    yield _chunk

    return Response(
        stream_with_context(_stream_gen()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


def _pipeline_progress_chunks(first, events, session_id, completion):
    """Connecting head + framed owned pipeline lifecycle events.

    Transport only, shared by the pipeline lane adapter and the
    ``_generate_chat_stream`` compat adapter: framing plus the
    belt-and-suspenders wire rewrite of confirms on done. Live status
    belongs to the owned workflow, not this subscriber. ``first`` is
    the peeked head event; ``events`` is the remaining owned iterator.
    """
    from contextlib import closing
    with closing(events):
        _sid = session_id
        yield from _stream_connecting_head(_sid)
        if first is None:
            _rest = iter([(
                "done",
                {
                    "success": False,
                    "response": "Stream ended unexpectedly.",
                    "type": "stream_error",
                },
            )])
        else:
            import itertools as _it
            _rest = _it.chain([first], events)
        for _kind, _payload in _rest:
            if _kind == "done":
                _result = _payload or {}
                # Belt-and-suspenders: rewrite confirms here too in case
                # on_result was missing/failed — client must never see
                # stuck "Waiting…" cards. Verbatim from the old SSE loop.
                try:
                    _result = _rewrite_assistant_response_actions(
                        _result,
                        _sid,
                        (_result.get("cursor_run") or {}).get("cwd")
                        or _result.get("project_path")
                        or "",
                    ) or _result
                except Exception as _rw_err:
                    print(f"[CHAT] SSE confirm rewrite failed: {_rw_err}", flush=True)
                _payload = _result
            for _chunk in _frame_stream_lifecycle_event(_kind, _payload, _sid):
                yield _chunk


def _stream_pipeline_turn_response(prepared, *, io):
    """Pipeline twin of ``_stream_agent_turn_response`` (transport only).

    The pipeline lane submits the shared entry with an explicit pipeline
    selection; this adapter frames the owned lifecycle events verbatim
    and keeps the lane's surface delivery effects (see
    ``_pipeline_progress_chunks``). Claim/persist/run/finalize/release
    all live in the coordinator entry, never here.
    """
    from api import chat_delivery as _chat_delivery
    from api.chat_coordinator import (
        AgentSelection as _AgentSelection,
        submit_agent_stream_turn as _submit_stream_turn,
    )

    _completion: dict = {}
    _events = _submit_stream_turn(
        prepared,
        io=io,
        delivery=_chat_delivery,
        is_router_family=_is_router_family_message,
        selection=_AgentSelection(kind="pipeline"),
        completion=_completion,
    )
    _first = next(_events, None)
    if _first is not None and _first[0] == "shortcut":
        return jsonify(_first[1])
    if _first is not None and _first[0] == "busy":
        return _stream_busy_sse_response(prepared.session_id)

    def _stream_gen():
        yield from _pipeline_progress_chunks(
            _first, _events, prepared.session_id, _completion
        )

    return Response(
        stream_with_context(_stream_gen()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


@app.route('/api/upload', methods=['POST'])
def upload_attachments():
    """Receive chat file uploads (images/PDFs) for vision pre-pass."""
    try:
        from werkzeug.utils import secure_filename

        _user, err = require_authenticated()
        if err:
            return err

        files = request.files.getlist('files')
        if not files:
            return jsonify({'success': False, 'error': 'No files provided'}), 400

        session_id = (request.form.get('session_id') or 'anon').strip() or 'anon'
        from api.cuttle_ui_capabilities import numeric_chat_session_id
        nid = numeric_chat_session_id(session_id)
        if nid:
            _owner_user, _nid, sess_err = require_chat_session_access(nid)
            if sess_err:
                return sess_err
            session_id = str(_nid)
        # Keep session folder name filesystem-safe
        safe_session = secure_filename(session_id) or 'anon'
        upload_dir = _output_dir() / 'uploads' / safe_session
        upload_dir.mkdir(parents=True, exist_ok=True)

        allowed_ext = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.pdf'}
        max_bytes = 25 * 1024 * 1024
        results = []
        for f in files:
            if not f or not f.filename:
                continue
            safe_name = secure_filename(f.filename)
            if not safe_name:
                continue
            ext = Path(safe_name).suffix.lower()
            if ext not in allowed_ext:
                return jsonify({
                    'success': False,
                    'error': f'Unsupported file type: {ext or "(none)"}. Use images or PDF.',
                }), 400
            # Size check via stream peek
            f.stream.seek(0, os.SEEK_END)
            size = f.stream.tell()
            f.stream.seek(0)
            if size > max_bytes:
                return jsonify({
                    'success': False,
                    'error': f'{safe_name} is too large ({size} bytes; max {max_bytes}).',
                }), 400
            dest = upload_dir / safe_name
            # Avoid overwrite collisions
            if dest.exists():
                stem, suffix = dest.stem, dest.suffix
                dest = upload_dir / f"{stem}_{int(time.time())}{suffix}"
            f.save(dest)
            # Public URL via existing /output/ static serve (<home>/output/...)
            public_url = f'/output/uploads/{safe_session}/{dest.name}'
            results.append({
                'filename': dest.name,
                'path': str(dest.resolve()),
                'mime': f.content_type or '',
                'size': dest.stat().st_size,
                'url': public_url,
            })

        if not results:
            return jsonify({'success': False, 'error': 'No valid files uploaded'}), 400
        return jsonify({'success': True, 'files': results})
    except Exception as e:
        print(f"[UPLOAD] error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/chat', methods=['POST'])
def chat_endpoint():
    """Handle chat messages from the landing page"""
    try:
        data = request.get_json()
        print(f"[API] POST /api/chat: received, has_message={bool(data and data.get('message'))}")

        # Owned normalization (api.chat_turn, P5-A); error strings are the 400s.
        from api.chat_turn import normalize_chat_post as _normalize_chat_post

        _turn_req = _normalize_chat_post(data)
        if _turn_req.error is not None:
            return jsonify({
                'success': False,
                'error': _turn_req.error
            }), 400

        message_content = _turn_req.message
        raw_attachments = _turn_req.attachments
        chat_session_id = _turn_req.session_id
        wants_stream = _turn_req.wants_stream

        _auth_user_early, auth_err = require_authenticated()
        if auth_err:
            return auth_err

        # Authenticate first, then enforce channel admission before any control,
        # shell, vision, router, or harness lane can perform work.
        if not PAIRING_AVAILABLE:
            return jsonify({"success": False, "error": "pairing_unavailable"}), 503
        try:
            channel_cfg = get_settings_manager().get_channel_config("webchat")
            access = get_pairing_manager().check_access(
                "webchat", f"web_user_{_auth_user_early['id']}",
                channel_cfg.get("dmPolicy", "open"),
                channel_cfg.get("allowFrom", ["*"]),
                meta={"session_id": chat_session_id},
            )
        except Exception as e:
            print(f"[PAIRING] Web chat check error: {e}")
            return jsonify({"success": False, "error": "pairing_unavailable"}), 503
        if not access["allowed"]:
            return jsonify({
                "success": False,
                "error": "pairing_required" if access.get("pairing_required") else "allowlist_denied",
                "response": access["message"],
                "pairing_code": access.get("pairing_code"),
                "session_id": chat_session_id,
            }), 403

        # Native Cuttle control command. Handled before sticky/starred agent
        # prefixing, the router, and any agent dispatch so it never becomes a
        # model turn (and never registers as active work against itself).
        try:
            from api.flask_restart import (
                handle_restart_slash,
                parse_restart_slash,
                strip_sticky_agent_prefix,
            )

            if parse_restart_slash(message_content) is not None:
                _owner, owner_err = require_owner()
                if owner_err:
                    return owner_err
                message_content = strip_sticky_agent_prefix(message_content)
                _rr = handle_restart_slash(
                    message_content,
                    session_id=chat_session_id,
                    user_source="chat",
                ) or {}
                _rr = dict(_rr)
                _rr["session_id"] = chat_session_id
                _rr.setdefault("native_command", "/restart")
                if chat_session_id is not None and _rr.get("response"):
                    # Slash /restart returns a locked action card (chat_notify=False).
                    # Only skip the saver when flask_restart already wrote a bubble.
                    already_persisted = bool(_rr.get("ack_persisted"))
                    try:
                        # Runs before the attachment pre-pass; /restart never carries uploads.
                        _persist_auth_user_message(chat_session_id, message_content, metadata=None)
                        if not already_persisted:
                            saver = _make_auth_assistant_saver(
                                chat_session_id
                            )
                            if saver:
                                saver(_rr)
                    except Exception as _rpe:
                        print(f"[CHAT] restart slash persist: {_rpe}", flush=True)
                if wants_stream:
                    def _restart_stream():
                        yield _sse_session_event(chat_session_id)
                        yield f"data: {json.dumps({'type': 'response', **_rr}, ensure_ascii=False, default=str)}\n\n"
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return Response(
                        stream_with_context(_restart_stream()),
                        mimetype="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "X-Accel-Buffering": "no",
                            "Connection": "keep-alive",
                        },
                    )
                return jsonify(_rr)
        except Exception as _rs_err:
            print(f"[CHAT] /restart handler failed: {_rs_err}", flush=True)

        # Native /branch control command — same contract as /restart: handled
        # before sticky/starred prefixing, the router, and agent dispatch.
        try:
            from api.branch_slash import handle_branch_slash, parse_branch_slash
            from api.flask_restart import strip_sticky_agent_prefix as _strip_branch

            if parse_branch_slash(message_content) is not None:
                message_content = _strip_branch(message_content)
                _branch_path_err = None
                try:
                    _branch_proj = _resolve_request_project_path(data)
                except Exception as _bpe:
                    _branch_proj = None
                    _branch_path_err = str(_bpe) or 'Cannot resolve the chat project'
                if _branch_path_err:
                    _br = {
                        "success": False,
                        "type": "git_branch",
                        "native_command": "/branch",
                        "response": f"Cannot switch branch: {_branch_path_err}",
                    }
                else:
                    _br = dict(
                        handle_branch_slash(
                            message_content,
                            session_id=chat_session_id,
                            project_path=_branch_proj,
                        )
                        or {}
                    )
                _br["session_id"] = chat_session_id
                _br.setdefault("native_command", "/branch")
                if chat_session_id is not None and _br.get("response"):
                    try:
                        _persist_auth_user_message(chat_session_id, message_content, metadata=None)
                        saver = _make_auth_assistant_saver(chat_session_id)
                        if saver:
                            saver(_br)
                    except Exception as _bpe:
                        print(f"[CHAT] branch slash persist: {_bpe}", flush=True)
                if wants_stream:
                    def _branch_stream():
                        yield _sse_session_event(chat_session_id)
                        yield f"data: {json.dumps({'type': 'response', **_br}, ensure_ascii=False, default=str)}\n\n"
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return Response(
                        stream_with_context(_branch_stream()),
                        mimetype="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "X-Accel-Buffering": "no",
                            "Connection": "keep-alive",
                        },
                    )
                return jsonify(_br)
        except Exception as _bs_err:
            print(f"[CHAT] /branch handler failed: {_bs_err}", flush=True)

        # Supervised control lane — status/cancel/followup/coordinator config.
        # Must run even while a child worker is active; never waits on chat busy
        # or the pending-prompt queue (frontend also bypasses for these).
        #
        # Exactly-once contract: classify/authorize/mutate/persist/terminal-return
        # owned by api.chat_turn_workflow.run_supervised_control_lane; the route
        # only frames SSE and jsonify. A recognized control always returns a body.
        from api.chat_turn_workflow import (
            run_supervised_control_lane as _run_supervised_control_lane,
        )

        _sup_body = _run_supervised_control_lane(
            message_content,
            session_id=chat_session_id,
            data=data,
            control_id_header=request.headers.get('X-Cuttle-Control-Id'),
            resolve_auth_chat_session=_resolve_auth_chat_session,
            resolve_project_path=_resolve_request_project_path,
            get_auth_db=get_auth_db,
        )
        if _sup_body is not None:
            _sup_sid = _sup_body.get('session_id', chat_session_id)
            if wants_stream:
                def _sup_control_stream():
                    yield _sse_session_event(_sup_sid)
                    yield f"data: {json.dumps({'type': 'response', **_sup_body}, ensure_ascii=False, default=str)}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return Response(
                    stream_with_context(_sup_control_stream()),
                    mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    },
                )
            return jsonify(_sup_body)

        # Check for slash commands
        if message_content.startswith('/help'):
            help_text = """**🦑 Cuttle Web Chat Commands**

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
            
            return jsonify({
                'success': True,
                'response': help_text,
                'session_id': chat_session_id,
                'type': 'help'
            })

        # Authenticated chats: assign a DB session id before remaining slash-command
        # handlers so the first message (including /cursor) can update the URL immediately.
        _auth_user, chat_session_id, _created_new_session = _resolve_auth_chat_session(chat_session_id)
        if _auth_user and chat_session_id is not None:
            try:
                # First-turn dirty picks arrive here — must pin before user-badge
                # metadata and harness execute (post-adopt client POSTs race both).
                _apply_request_agent_pins(chat_session_id, data)
            except Exception as _pin_err:
                print(f"[CHAT] apply agent_pins failed: {_pin_err}", flush=True)
        if _auth_user and _created_new_session:
            try:
                _preview = ' '.join((message_content or '').split())
                if _preview:
                    get_auth_db().set_session_name(
                        chat_session_id,
                        (_preview[:60] + ('…' if len(_preview) > 60 else '')),
                        auto=True,
                    )
            except Exception as _ne:
                print(f"[CHAT] provisional session name failed: {_ne}")

        if _auth_user and chat_session_id is not None:
            try:
                _stamp_auth_session_project(_auth_user, chat_session_id, data)
            except Exception as _se:
                print(f"[CHAT] stamp session project failed: {_se}", flush=True)

        # Resolve host locations once before execution. Owned validation fails closed;
        # pin the chosen cwd for all subsequent helpers in this request.
        from managers.project_locations import ProjectUnavailable
        from flask import g
        try:
            g.project_turn_path = _resolve_request_project_path(data)
        except ProjectUnavailable as exc:
            return jsonify({'success': False, 'error': str(exc), 'response': str(exc),
                            'type': 'project_unavailable', 'session_id': chat_session_id}), 409

        # Starred / session sticky agent, server-side. The chat page normally
        # attaches the chip itself, but a first turn sent without it (LAN client,
        # API caller, dropped chip) must not silently fall through to the agent
        # router — the star is an explicit user selection. Removing the badge is
        # just as explicit, so a client that reports "no agent" wins over both
        # the star and this chat's earlier agent turns.
        try:
            from api.starred_slash import (
                apply_default_sticky_prefix,
                is_no_agent_request,
            )

            _sticky_applied = apply_default_sticky_prefix(
                message_content,
                chat_session_id if isinstance(chat_session_id, int) else None,
                star_on_new_session_only=True,
                no_agent=is_no_agent_request(data),
            )
            if _sticky_applied != message_content:
                print(
                    f"[CHAT] applied sticky agent prefix for session {chat_session_id}",
                    flush=True,
                )
                message_content = _sticky_applied
        except Exception as _ss_err:
            print(f"[CHAT] sticky prefix failed: {_ss_err}", flush=True)

        # Vision / PDF pre-pass. Must run here, above every slash-agent handler:
        # /cursor, /codex, /muse, /hermes, project commands and the router all
        # slice a prompt out of message_content and hand it to a CLI that cannot
        # take image input, so an attachment analyzed later never reaches them.
        _att_note = ''
        _user_msg_meta = None
        if raw_attachments:
            try:
                message_content, _att_note, _att_meta = _run_attachment_prepass(
                    message_content, raw_attachments, chat_session_id
                )
                if _att_meta:
                    _user_msg_meta = {'attachments': _att_meta}
            except Exception as _vp_err:
                print(f"[VISION] pre-pass failed: {_vp_err}", flush=True)

        _send_identity = None
        try:
            _send_identity = _freeze_send_identity(
                chat_session_id, message_content, data
            )
        except Exception as _id_err:
            print(f"[CHAT] freeze send identity failed: {_id_err}", flush=True)
            _send_identity = None
        _ident_run_kw = _harness_identity_run_kwargs(_send_identity)

        def _persist_user_turn(sid):
            """Persist the user turn as history sees it (owned by chat_turn_persist)."""
            from api.chat_turn_persist import persist_user_turn as _owned

            _owned(
                get_auth_db(),
                sid,
                message_text=message_content,
                history_text=_attachment_history_text(message_content, _att_note),
                base_meta=_user_msg_meta,
                identity=_send_identity,
                badge_fn=_user_badge_metadata,
                project_merge_fn=_merge_project_into_meta,
                request_data=_current_request_data(),
            )

        # No-LLM launch gates — project-action confirm buttons and dynamic
        # action forms. Owned by api.chat_turn_workflow.run_launch_gate_lane;
        # the route only frames jsonify. Returns None for non-gate messages.
        from api.chat_turn_workflow import (
            run_launch_gate_lane as _run_launch_gate_lane,
        )

        _launch_gate_body = _run_launch_gate_lane(
            message_content,
            session_id=chat_session_id,
            auth_user=_auth_user,
            get_auth_db=get_auth_db,
            button_click_history=_button_click_history,
        )
        if _launch_gate_body is not None:
            return jsonify(_launch_gate_body)

        # Expand project-local ``.cuttle/commands/*.md`` (e.g. /build, /cmd deploy).
        # Runs before /cursor handlers so ``/cursor /build`` becomes a full agent prompt.
        # Owned by api.chat_turn_workflow.run_shell_action_lane; the route keeps
        # request capture, the busy SSE/409 transport and jsonify.
        from api.chat_turn_workflow import (
            run_shell_action_lane as _run_shell_action_lane,
        )

        _shell_body, message_content, _shell_busy = _run_shell_action_lane(
            message_content,
            session_id=chat_session_id,
            auth_user=_auth_user,
            data=data,
            resolve_project_path=_resolve_request_project_path,
            persist_user_turn=_persist_user_turn,
            make_saver=_make_auth_assistant_saver,
            rewrite_actions=_rewrite_assistant_response_actions,
        )
        if _shell_busy:
            busy_resp = _reject_if_chat_busy(chat_session_id, wants_stream)
            if busy_resp is not None:
                return busy_resp
        if _shell_body is not None:
            return jsonify(_shell_body)

        # Agent router: /router (config), /route, /retry, /coordinator, /coordinate
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
            from api.agent_router.integration import handle_router_family_command

            _router_proj = _resolve_request_project_path({
                **(data or {}),
                'session_id': chat_session_id if chat_session_id is not None else data.get('session_id'),
            })
            _is_router_cfg = parse_router_command(message_content) is not None
            _is_route_or_retry = (
                parse_route_command(message_content) is not None
                or parse_retry_command(message_content) is not None
            )
            # Control lane already handled earlier; keep classification for safety.
            # Fallthrough of recognized control commands is forbidden (double-apply).
            if is_supervised_control_message(message_content):
                return jsonify({
                    'success': True,
                    'response': (
                        '❌ Supervised control command was recognized but the early '
                        'control lane did not return. Refusing fallthrough to prevent '
                        'duplicate side effects.'
                    ),
                    'type': 'supervised_control_error',
                    'control_lane': True,
                    'control_handled': True,
                    'session_id': chat_session_id,
                })
            if parse_coordinator_command(message_content) is not None:
                _is_router_cfg = True
            _coord_args = parse_coordinate_command(message_content)
            if _coord_args is not None:
                if is_coordinate_control(_coord_args):
                    _is_router_cfg = True
                else:
                    _is_route_or_retry = True
            if is_supervised_control_message(message_content):
                _is_router_cfg = True
                _is_route_or_retry = False
        except Exception as _ar_err:
            print(f"[CHAT] agent router parse failed: {_ar_err}", flush=True)
            _is_router_cfg = False
            _is_route_or_retry = False
            _router_proj = None

        if _is_router_cfg:
            try:
                from api.agent_router.integration import handle_router_family_command as _hrf
                body = _hrf(
                    message_content,
                    session_id=chat_session_id,
                    project_path=_router_proj,
                ) or {}
            except Exception as _ar_err:
                body = {
                    'success': True,
                    'response': f'❌ **Agent router:** {_ar_err}',
                    'type': 'router_error',
                }
            body = dict(body)
            body['session_id'] = chat_session_id
            if _auth_user and chat_session_id is not None:
                try:
                    _persist_user_turn(chat_session_id)
                    get_auth_db().add_message(
                        chat_session_id,
                        'assistant',
                        body.get('response') or '',
                        metadata={'origin': 'agent_router'},
                    )
                except Exception as _pe:
                    print(f"[CHAT] persist router reply failed: {_pe}")
            return jsonify(body)

        if _is_route_or_retry:
            busy_resp = _reject_if_chat_busy(chat_session_id, wants_stream)
            if busy_resp is not None:
                return busy_resp
            if wants_stream:
                from api.chat_coordinator import (
                    PreparedAgentTurn as _PreparedAgentTurn,
                    StreamTurnIO as _StreamTurnIO,
                )

                def _stream_unreachable_router(_kind, _sel):
                    raise RuntimeError('unreachable arm in router stream lane')

                def _stream_run_router(status_queue=None):
                    from api.agent_router.integration import handle_router_family_command as _hrf2
                    return dict(_hrf2(
                        message_content,
                        session_id=chat_session_id,
                        project_path=_router_proj,
                        status_queue=status_queue,
                    ) or {})

                _stream_router_io = _StreamTurnIO(
                    run_harness=_stream_unreachable_router,
                    run_router=_stream_run_router,
                    persist_user=lambda: (_persist_user_turn(chat_session_id) if _auth_user else None),
                    make_saver=lambda: _make_auth_assistant_saver(
                        chat_session_id if _auth_user else None,
                        project_path=_router_proj,
                    ),
                    notify_mobile=lambda _b: _emit_chat_complete_mobile(chat_session_id, _b),
                    format_shortcut=_stream_unreachable_router,
                )
                return _stream_agent_turn_response(
                    _PreparedAgentTurn(
                        message=message_content, session_id=chat_session_id,
                        wants_stream=wants_stream,
                        request_data=data or {}, project_path=_router_proj or '',
                    ),
                    io=_stream_router_io,
                )
            from api import chat_delivery as _chat_delivery
            from api.chat_coordinator import (
                AgentSelection as _AgentSelection,
                AgentTurnIO as _AgentTurnIO,
                PreparedAgentTurn as _PreparedAgentTurn,
                submit_agent_turn as _submit_turn,
            )
            from api.chat_turn_workflow import (
                result_kept_for_history as _result_kept_for_history,
            )

            def _guarded_router_saver():
                _saver = _make_auth_assistant_saver(chat_session_id)
                if _saver is None:
                    return None

                def _guarded(_body):
                    try:
                        _saver(_body)
                    except Exception as _se:
                        print(f"[CHAT] router persist failed: {_se}")

                return _guarded

            def _router_unreachable(*_a, **_k):
                raise RuntimeError('unreachable arm in router sync lane')

            def _router_run_family(status_queue=None):
                from api.agent_router.integration import handle_router_family_command as _hrf2
                return dict(_hrf2(
                    message_content,
                    session_id=chat_session_id,
                    project_path=_router_proj,
                    status_queue=status_queue,
                ) or {})

            _router_io = _AgentTurnIO(
                run_harness=_router_unreachable,
                run_router=_router_run_family,
                persist_user=lambda: (_persist_user_turn(chat_session_id) if _auth_user else None),
                make_saver=_guarded_router_saver,
                should_save=lambda _b: bool(
                    _auth_user and _result_kept_for_history(_b)
                ),
                notify_mobile=None,
                format_shortcut=_router_unreachable,
            )
            _turn_out = _submit_turn(
                _PreparedAgentTurn(
                    message=message_content, session_id=chat_session_id,
                    wants_stream=wants_stream,
                    request_data=data or {}, project_path=_router_proj or '',
                ),
                io=_router_io, delivery=_chat_delivery, claim=True,
                selection=_AgentSelection(kind='router_family'),
            )
            if _turn_out.status == 409:
                return jsonify(_turn_out.body), 409
            _lane_body = _turn_out.body
            if isinstance(_lane_body, dict):
                _lane_body.setdefault('session_id', chat_session_id)
            return jsonify(_lane_body)

        # Start a new command-dispatch chain here.
        # Harness agents live under api/agent_harness/agents/<id>/ (folder per agent)
        # plus optional drop-ins under .cuttle_global/agents and {project}/.cuttle/agents.
        project_path = _resolve_request_project_path({
            **(data or {}),
            'session_id': chat_session_id if chat_session_id is not None else data.get('session_id'),
        })
        _harness_match = _match_harness_slash(message_content, project_path=project_path)
        if _harness_match:
            _hid, prompt = _harness_match
            if not prompt:
                return jsonify({
                    'success': True,
                    'response': (
                        f'❌ Please provide a prompt after /{_hid}. '
                        f'Example: `/{_hid} "summarize this repo"`'
                    ),
                    'session_id': chat_session_id,
                    'type': f'{_hid}_error',
                })
            busy_resp = _reject_if_chat_busy(chat_session_id, wants_stream)
            if busy_resp is not None:
                return busy_resp
            if wants_stream:
                from api.chat_coordinator import (
                    PreparedAgentTurn as _PreparedAgentTurn,
                    StreamTurnIO as _StreamTurnIO,
                )

                def _stream_format_shortcut(_kind, _sel):
                    if _kind == "harness_empty_prompt":
                        _eaid = _sel.agent_id or _hid
                        return {
                            'success': True,
                            'response': (
                                f'❌ Please provide a prompt after /{_eaid}. '
                                f'Example: `/{_eaid} "summarize this repo"`'
                            ),
                            'session_id': chat_session_id,
                            'type': f'{_eaid}_error',
                        }
                    raise RuntimeError(f'unexpected shortcut arm: {_kind}')

                def _stream_unreachable_harness(*_a, **_k):
                    raise RuntimeError('unreachable arm in harness stream lane')

                _stream_harness_io = _StreamTurnIO(
                    run_harness=lambda _aid, _prompt, status_queue=None: _run_pinned_harness_turn(
                        _aid, _prompt, chat_session_id, project_path=project_path,
                        status_queue=status_queue, **_ident_run_kw,
                    ),
                    run_router=_stream_unreachable_harness,
                    persist_user=lambda: (_persist_user_turn(chat_session_id) if _auth_user else None),
                    make_saver=lambda: _make_auth_assistant_saver(
                        chat_session_id if _auth_user else None,
                        project_path=project_path,
                    ),
                    notify_mobile=lambda _b: _emit_chat_complete_mobile(chat_session_id, _b),
                    format_shortcut=_stream_format_shortcut,
                )
                return _stream_agent_turn_response(
                    _PreparedAgentTurn(
                        message=message_content, session_id=chat_session_id,
                        wants_stream=wants_stream,
                        request_data=data or {}, project_path=project_path or '',
                    ),
                    io=_stream_harness_io,
                )
            from api import chat_delivery as _chat_delivery
            from api.chat_coordinator import (
                AgentSelection as _AgentSelection,
                AgentTurnIO as _AgentTurnIO,
                PreparedAgentTurn as _PreparedAgentTurn,
                submit_agent_turn as _submit_turn,
            )
            from api.chat_turn_workflow import (
                result_kept_for_history as _result_kept_for_history,
            )

            def _guarded_harness_saver():
                _saver = _make_auth_assistant_saver(
                    chat_session_id,
                    project_path=project_path,
                )
                if _saver is None:
                    return None

                def _guarded(_body):
                    try:
                        _saver(_body)
                    except Exception as _se:
                        print(f'[CHAT] harness persist failed: {_se}')

                return _guarded

            def _harness_unreachable(*_a, **_k):
                raise RuntimeError('unreachable arm in harness sync lane')

            _harness_io = _AgentTurnIO(
                run_harness=lambda _aid, _prompt, status_queue=None: _run_pinned_harness_turn(
                    _aid, _prompt, chat_session_id, project_path=project_path,
                    status_queue=status_queue, **_ident_run_kw,
                ),
                run_router=_harness_unreachable,
                persist_user=lambda: (_persist_user_turn(chat_session_id) if _auth_user else None),
                make_saver=_guarded_harness_saver,
                should_save=lambda _b: bool(
                    _auth_user and _result_kept_for_history(_b)
                ),
                notify_mobile=lambda _b: _emit_chat_complete_mobile(chat_session_id, _b),
                format_shortcut=_harness_unreachable,
            )
            _turn_out = _submit_turn(
                _PreparedAgentTurn(
                    message=message_content, session_id=chat_session_id,
                    wants_stream=wants_stream,
                    request_data=data or {}, project_path=project_path or '',
                ),
                io=_harness_io, delivery=_chat_delivery, claim=True,
                selection=_AgentSelection(kind='harness', agent_id=_hid, prompt=prompt),
            )
            if _turn_out.status == 409:
                return jsonify(_turn_out.body), 409
            _lane_body = _turn_out.body
            if isinstance(_lane_body, dict) and chat_session_id is not None:
                _lane_body.setdefault('session_id', chat_session_id)
            return jsonify(_lane_body)

        # Attachments were already analyzed above (before slash dispatch); history
        # keeps the short note + metadata instead of the full digest.
        history_message = _attachment_history_text(message_content, _att_note)
        if (message_content or '').strip().startswith('[button:'):
            history_message, _btn_meta = _button_click_history(message_content)
            if _btn_meta:
                _user_msg_meta = {**(_user_msg_meta or {}), **_btn_meta}

        # Authenticated user — session was resolved earlier so slash commands share it.
        if _auth_user:
            user = _auth_user
            db = get_auth_db()

            # Re-sending while the previous reply is still generating would
            # queue a duplicate agent run; reject before it reaches history.
            from api import chat_delivery as _chat_delivery
            if _chat_delivery.is_busy(chat_session_id):
                return jsonify({
                    'success': False,
                    'error': 'busy',
                    'busy': True,
                    'session_id': chat_session_id,
                    'response': (
                        '⏳ Still working on your previous message in this chat. '
                        'Wait for it to finish, or stop it first.'
                    ),
                }), 409

            print("[API] POST /api/chat: authenticated user, processing message")

            # Process message with bot (session kind for per-channel pipeline routing)
            from api.http_authz import is_owner_user as _is_owner_user
            _user_is_owner = _is_owner_user(user)
            _session_id = f"db_session_{chat_session_id}"
            if not wants_stream:
                from api.chat_turn_workflow import run_pipeline_sync_turn as _run_lane
                from api.chat_turn_workflow import build_pipeline_body as _build_body

                def _pipeline_save_assistant(_res):
                    # Shared saver (make_assistant_saver) is the sole
                    # eligibility policy: persist-anything lived here
                    # ([ERR-20261001-001]). The ingress-resolved
                    # project_path is supplied as the saver's fallback
                    # so the turn's action context survives a missing
                    # session project; action_forms remains authoritative
                    # for live-session precedence. Return the (possibly
                    # rewritten-in-place) result for the body.
                    _saver = _make_auth_assistant_saver(
                        chat_session_id,
                        project_path=project_path,
                    )
                    if _saver is None:
                        return _res
                    _saver(_res)
                    return _res

                _lane_body, _lane_status = _run_lane(
                    chat_session_id,
                    delivery=_chat_delivery,
                    persist_user=lambda: db.add_message(
                        chat_session_id,
                        'user',
                        history_message,
                        metadata=_user_msg_meta,
                    ),
                    run=lambda: process_message_with_bot(
                        message_content, _session_id,
                        session_kind='web_user', routing_key=f'web_user_{user["id"]}',
                        is_owner=_user_is_owner, status_queue=None,
                        ),
                    save_assistant=_pipeline_save_assistant,
                    build_body=lambda _res: _build_body(
                        _res, chat_session_id,
                        usage_meta_fn=_usage_meta_from_assistant_result,
                    ),
                )
                if _lane_status == 409:
                    return jsonify(_lane_body), 409
                return jsonify(_lane_body)
            from api.chat_coordinator import (
                PreparedAgentTurn as _PreparedAgentTurn,
                StreamTurnIO as _StreamTurnIO,
            )

            def _pipeline_unreachable(*_a, **_k):
                raise RuntimeError('unreachable arm in pipeline stream lane')

            # Shared saver (make_assistant_saver) is the sole eligibility
            # policy: the persist-anything on_save lived here
            # ([ERR-20261001-001], now unified). The factory runs in the
            # request thread on first next(); the ingress-resolved
            # project_path below is the saver's fallback when the
            # session carries no project. Action_forms keeps live-session
            # precedence for the card embed itself.
            _stream_pipeline_io = _StreamTurnIO(
                run_harness=_pipeline_unreachable,
                run_router=_pipeline_unreachable,
                persist_user=lambda: db.add_message(
                    chat_session_id,
                    'user',
                    history_message,
                    metadata=_user_msg_meta,
                ),
                make_saver=lambda: _make_auth_assistant_saver(
                    chat_session_id,
                    project_path=project_path,
                ),
                notify_mobile=lambda _b: _emit_chat_complete_mobile(chat_session_id, _b),
                format_shortcut=_pipeline_unreachable,
                run_pipeline=lambda status_queue=None: process_message_with_bot(
                    message_content, _session_id,
                    session_kind='web_user', routing_key=f'web_user_{user["id"]}',
                    is_owner=_user_is_owner, status_queue=status_queue,
                    ),
            )
            return _stream_pipeline_turn_response(
                _PreparedAgentTurn(
                    message=message_content, session_id=chat_session_id,
                    wants_stream=wants_stream,
                    request_data=data or {},
                    # Unused: selection is explicit pipeline (no catalog
                    # match) and the pipeline worker is not rewritten.
                    project_path='',
                ),
                io=_stream_pipeline_io,
            )
        
        # Cookie was required above; missing resolve must not run a harness turn.
        print("[API] POST /api/chat: authenticated session missing after resolve")
        return jsonify({'success': False, 'error': 'Not authenticated.'}), 401
            
    except Exception as e:
        print(f"Chat endpoint error: {e}")
        import traceback
        traceback.print_exc()
        debug = os.getenv('CUTTLE_CHAT_DEBUG', '').lower() in ('1', 'true', 'yes')
        response_msg = str(e) if debug else 'I encountered an unexpected error. Please try again later.'
        return jsonify({
            'success': False,
            'error': str(e),
            'response': response_msg
        }), 500

def _is_router_family_message(message_content) -> bool:
    """Shared router-family predicate for coordinator selection (P5-E).

    True for every message the route lanes handle as router-family
    (router commands, route/retry/coordinate dispatches, supervised
    control). Mirrors the inline lane classification exactly; the lanes
    keep their own conditions for arm routing, the coordinator uses this
    for the single normalized selection tree.

    Placed after ``chat_endpoint`` on purpose: the restart dispatch-order
    test pins ``from api.flask_restart import (`` textually before any
    ``from api.agent_router.commands import (``, and this predicate holds
    the file's first such import. Referenced at request time only, so
    definition order has no runtime effect.
    """
    try:
        from api.agent_router.commands import (
            parse_retry_command as _parse_retry,
            parse_route_command as _parse_route,
            parse_router_command as _parse_router,
        )
        from api.agent_router.supervised.commands import (
            parse_coordinate_command as _parse_coordinate,
            parse_coordinator_command as _parse_coord,
        )
        from api.agent_router.supervised.control import (
            is_coordinate_control as _is_coord_control,
            is_supervised_control_message as _is_sup,
        )
        _m = message_content
        if _parse_router(_m) is not None:
            return True
        if _parse_route(_m) is not None or _parse_retry(_m) is not None:
            return True
        if _parse_coord(_m) is not None:
            return True
        _coord_args = _parse_coordinate(_m)
        if _coord_args is not None and not _is_coord_control(_coord_args):
            return True
        if _is_sup(_m):
            return True
    except Exception:
        return False
    return False


@app.route('/api/local-llm/status', methods=['GET'])
@authenticated_required
def local_llm_status():
    """Status of the local LLM backend (llama.cpp / Ollama)."""
    from core.local_llm import get_local_backend, get_local_base_url, local_reachable, list_local_models
    running = local_reachable(timeout=1.5)
    return jsonify({
        'success': True,
        'backend': get_local_backend(),
        'base_url': get_local_base_url(),
        'running': running,
        'models': list_local_models() if running else [],
    })


@app.route('/api/health', methods=['GET'])
def health_check():
    """Diagnostic health (Python, cwd, env flags, CLI / local-LLM probes).

    Not a liveness probe. The daemon watchdog uses GET /api/status instead —
    this handler can run `claude --version` (3s) then `wsl which claude` (5s),
    which exceeds the watchdog's 5s HTTP timeout and caused false restarts.
    """
    from core.agent_cli_env import agent_cli_env
    import platform
    import shutil
    import sys

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    cwd = str(actual_project_root.resolve())
    anthropic_set = bool(os.environ.get('ANTHROPIC_API_KEY'))
    openai_set = bool(os.environ.get('OPENAI_API_KEY'))

    claude_cli = False
    try:
        r = subprocess.run(
            ["claude", "--version"],
            env=agent_cli_env(),
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        claude_cli = r.returncode == 0
    except Exception:
        try:
            r = subprocess.run(
                ["wsl", "which", "claude"],
                env=agent_cli_env(),
            capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            claude_cli = r.returncode == 0
        except Exception:
            claude_cli = False

    ollama_reachable = None
    try:
        from core.local_llm import local_reachable, get_local_backend, get_local_label
        ollama_reachable = local_reachable(timeout=0.6)
        local_llm_backend = get_local_backend()
        local_llm_label = get_local_label()
    except Exception:
        ollama_reachable = False
        local_llm_backend = 'ollama'
        local_llm_label = 'Ollama'

    cursor_exe = bool(shutil.which("agent") or shutil.which("cursor-agent"))
    from api.device_workers.capabilities import cuttle_version

    payload = {
        'status': 'healthy',
        'service': 'cuttle',
        'bot_available': PIPELINE_AVAILABLE,
        'active_sessions': len(chat_sessions),
        'hostname': os.environ.get('COMPUTERNAME', platform.node()),
        'version': cuttle_version(),
        'python_version': py_ver,
        'platform': platform.platform(),
        'cwd': cwd,
        'env': {
            'ANTHROPIC_API_KEY_set': anthropic_set,
            'OPENAI_API_KEY_set': openai_set,
        },
        'claude_cli_available': claude_cli,
        'cursor_cli_on_path': cursor_exe,
        'ollama_reachable': ollama_reachable,
        'local_llm_reachable': ollama_reachable,
        'local_llm_backend': local_llm_backend,
        'local_llm_label': local_llm_label,
    }
    if not (request_is_loopback() or is_owner_user(current_user())):
        payload.pop('cwd', None)
        payload['env'] = {'redacted': True}
        payload['active_sessions'] = None
    return jsonify(payload)


@app.route('/api/lan-ping', methods=['GET'])
def api_lan_ping():
    """Minimal endpoint for phone connectivity test (no auth)."""
    try:
        from api.lan_access import record_lan_probe
        record_lan_probe(
            request.remote_addr or '',
            request.headers.get('User-Agent', ''),
            '/api/lan-ping',
        )
    except Exception:
        pass
    return jsonify({
        'ok': True,
        'from': request.remote_addr,
        'message': 'Cuttle LAN is reachable',
    })


@app.route('/api/network-info', methods=['GET'])
def api_network_info():
    """LAN portal URL and access mode (for phone setup)."""
    try:
        from api.lan_access import (
            is_lan_access_enabled,
            get_lan_ipv4,
            windows_firewall_rule_active,
            lan_portal_url,
            lan_phone_portal_url,
            get_primary_https_port,
            get_phone_https_port,
            get_http_fallback_port,
            lan_phone_http_fallback_url,
            wifi_network_category,
            get_recent_lan_probes,
            get_pc_subnet_hint,
        )
        lan_enabled = is_lan_access_enabled()
        lan_ip = get_lan_ipv4() if lan_enabled else None
        portal_https = lan_portal_url(get_primary_https_port(), lan_ip) if lan_enabled else None
        portal_phone = lan_phone_portal_url(get_phone_https_port(), lan_ip) if lan_enabled else None
        portal_http_fb = lan_phone_http_fallback_url(lan_ip) if lan_enabled else None
        discovery = {}
        try:
            from managers.settings_manager import get_settings_manager
            discovery = get_settings_manager().get_setting('discovery') or {}
        except Exception:
            pass
        net_cat = wifi_network_category()
        probes = get_recent_lan_probes()
        subnet = get_pc_subnet_hint()
        return jsonify({
            'lan_access_enabled': lan_enabled,
            'lan_ipv4': lan_ip,
            'portal_url': portal_phone or portal_https,
            'portal_url_phone': portal_phone,
            'portal_url_http_fallback': portal_http_fb,
            'portal_url_https': portal_https,
            'localhost_url': f'https://127.0.0.1:{get_primary_https_port()}',
            'firewall_rule_active': windows_firewall_rule_active() if lan_enabled else False,
            'wifi_network_category': net_cat,
            'mdns_enabled': bool(discovery.get('mdns_enabled')),
            'phone_subnet': subnet,
            'recent_phone_hits': probes,
            'phone_reach_diagnosis': (
                'No phone has reached this PC yet. If you tried from your phone and this list is empty, '
                'your router is likely blocking phone-to-PC (AP/client isolation) or your phone is on a '
                'different subnet. Check phone Wi‑Fi IP in Android Settings → Wi‑Fi → your network → Details.'
                if lan_enabled and not probes else
                ('Phone reached this PC — LAN path works.' if probes else None)
            ),
            'notes': (
                f'On your phone open portal_url_phone (HTTPS on {get_phone_https_port()}). Accept the self-signed cert once. '
                f'Or use portal_url_http_fallback (plain HTTP on port {get_http_fallback_port()}). '
                'Same Wi‑Fi required.'
                if lan_enabled and portal_phone else
                'LAN access is off. Enable LAN access in Settings, then restart Cuttle.'
            ),
            'setup': (
                [
                    f'Phone (HTTPS): {portal_phone or "(no LAN IP)"}',
                    f'Phone (HTTP fallback): {portal_http_fb or "(no LAN IP)"}',
                    f'Phone IP must be in: {subnet.get("phone_ip_must_be_in", "same subnet as PC")}',
                    f'PC browser: https://127.0.0.1:{get_primary_https_port()}',
                    'After a phone attempt, refresh this URL — recent_phone_hits should list your phone.',
                    ('If phone cannot connect: run enable_lan_firewall.bat as Administrator, then restart Cuttle.'
                     if sys.platform == 'win32' else
                     f'If phone cannot connect: allow TCP {get_primary_https_port()}, {get_phone_https_port()} and '
                     f'{get_http_fallback_port()} in your firewall (e.g. sudo ufw allow '
                     f'{get_primary_https_port()},{get_phone_https_port()},{get_http_fallback_port()}/tcp).'),
                    'Router fix: disable AP isolation / client isolation / use main Wi‑Fi not guest.',
                ] + ([f'Your Wi‑Fi is "{net_cat or "unknown"}" — set to Private in Windows Settings → Network → Wi‑Fi.']
                     if sys.platform == 'win32' else [])
                if lan_enabled else []
            ),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cursor-agent/models', methods=['GET'])
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






@app.route('/api/ollama-models', methods=['GET'])
def ollama_models():
    """Return installed local-LLM model names for chat and settings.

    Works for both backends: llama.cpp via OpenAI /v1/models, Ollama via its
    native /api/tags (richer names) with a /v1/models fallback.
    """
    try:
        from core.local_llm import is_llamacpp, get_local_native_base_url, list_local_models
        if is_llamacpp():
            return jsonify({'success': True, 'models': list_local_models(), 'backend': 'llamacpp'})
        import urllib.request
        base = get_local_native_base_url()
        req = urllib.request.Request(base + '/api/tags', method='GET')
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        models = [m.get('name', '') for m in data.get('models', []) if m.get('name')]
        if not models:
            models = list_local_models()
        return jsonify({'success': True, 'models': models, 'backend': 'ollama'})
    except Exception as e:
        return jsonify({'success': False, 'models': [], 'error': str(e)})


# ============================================================================
# Doctor (config/health checks) and Wizard (setup status)
# ============================================================================

@app.route('/api/doctor', methods=['GET'])
@owner_required
def doctor_endpoint():
    """Run config and health checks; return checks and suggestions."""
    try:
        from api.doctor import run_checks
        result = run_checks()
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'checks': [],
            'suggestions': [str(e)],
            'error': str(e)
        }), 500

# ============================================================================
# Pairing API (channel-level security: approve codes, list pending)
# ============================================================================

@app.route('/api/muse/models', methods=['GET'])
def api_muse_models():
    """Muse Code models for the chat slash palette, plus this chat's pick."""
    try:
        from scripts.utilities.muse_cli_tool import (
            list_muse_catalog_models,
            muse_model_label,
            resolve_muse_default_model,
        )
        from scripts.utilities.muse_cli_session_store import load_muse_model

        from api.agent_harness.agent_defaults import get_starred_model as _starred_m

        session = (request.args.get('session') or '').strip()
        refresh = (request.args.get('refresh') or '').strip().lower() in (
            '1', 'true', 'yes', 'refresh',
        )
        catalog = list_muse_catalog_models(refresh=refresh)
        _sess = (load_muse_model(session) if session else None)
        _star = _starred_m('muse')
        _cli = resolve_muse_default_model()
        preferred = _sess or _star or _cli
        preferred_source = 'session' if _sess else ('starred' if _star else 'cli_default')
        pref_l = (preferred or '').lower()
        models = [
            {**m, 'current': str(m.get('id') or '').lower() == pref_l}
            for m in (catalog.get('models') or [])
            if isinstance(m, dict)
        ]
        if preferred and not any(m.get('current') for m in models):
            models.append({
                'id': preferred,
                'label': muse_model_label(preferred),
                'description': 'Custom model id pinned for this chat',
                'current': True,
            })
        return jsonify({
            'success': True,
            'models': models,
            'preferredModel': preferred,
            'preferredSource': preferred_source,
            'starredModel': _star,
            'sessionModel': _sess,
            'defaultModel': resolve_muse_default_model(),
            'source': catalog.get('source'),
            'count': catalog.get('count'),
            'error': catalog.get('error'),
            'fetchedAt': catalog.get('fetched_at'),
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'models': [],
            'preferredModel': 'muse-spark-1.3',
        }), 500


@app.route('/api/muse/model', methods=['POST'])
def api_set_muse_model():
    """Body: { session, model } — pin the Muse model for one chat ('' resets)."""
    try:
        from scripts.utilities.muse_cli_tool import muse_model_label, resolve_muse_default_model
        from scripts.utilities.muse_cli_session_store import save_muse_model

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
        from api.agent_harness.agent_defaults import get_starred_model as _starred_m2

        saved = save_muse_model(session, model) or _starred_m2('muse') or resolve_muse_default_model()
        return jsonify({
            'success': True,
            'preferredModel': saved,
            'label': muse_model_label(saved),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/muse/effort', methods=['GET'])
def api_muse_effort():
    """Muse reasoning-effort levels for the chat slash palette, plus this chat's pin."""
    try:
        from scripts.utilities.muse_cli_tool import MUSE_REASONING_EFFORTS
        from scripts.utilities.muse_cli_session_store import load_muse_effort

        from api.agent_harness.agent_defaults import get_starred_effort as _starred_me

        session = (request.args.get('session') or '').strip()
        _sess_e = (load_muse_effort(session) if session else None) or ''
        _star_e = _starred_me('muse') or ''
        preferred = _sess_e or _star_e
        levels = [
            {'id': str(e), 'current': str(e) == preferred}
            for e in MUSE_REASONING_EFFORTS
        ]
        return jsonify({
            'success': True,
            'levels': levels,
            'preferredEffort': preferred,
            'preferredSource': 'session' if _sess_e else ('starred' if _star_e else 'none'),
            'starredEffort': _star_e or None,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'levels': []}), 500


@app.route('/api/muse/effort', methods=['POST'])
def api_set_muse_effort():
    """Body: { session, effort } — pin the Muse reasoning effort for one chat ('' resets)."""
    try:
        from scripts.utilities.muse_cli_tool import MUSE_REASONING_EFFORTS
        from scripts.utilities.muse_cli_session_store import save_muse_effort

        data = request.get_json(silent=True) or {}
        session = str(data.get('session') or data.get('session_id') or '').strip()
        if not session:
            return jsonify({'success': False, 'error': 'session is required'}), 400
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        effort = str(data.get('effort') or '').strip().lower()
        known = [str(e) for e in MUSE_REASONING_EFFORTS]
        if effort.lower() in ('default', 'reset', 'clear', 'none'):
            # "none" was a list entry before the CLI declared it invalid;
            # treat it as "no pin" rather than rejecting it.
            effort = ''
            effort = ''
        if effort and effort not in known:
            return jsonify({
                'success': False,
                'error': f"Unknown effort `{effort}`. Pick one of: {', '.join(known)}.",
            }), 400
        saved = save_muse_effort(session, effort) or ''
        return jsonify({'success': True, 'preferredEffort': saved})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hermes/models', methods=['GET'])
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


@app.route('/api/hermes/model', methods=['POST'])
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


@app.route('/api/hermes/effort', methods=['GET'])
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


@app.route('/api/hermes/effort', methods=['POST'])
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


@app.route('/api/codex/models', methods=['GET'])
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


@app.route('/api/codex/model', methods=['POST'])
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


@app.route('/api/codex/effort', methods=['GET'])
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


@app.route('/api/codex/effort', methods=['POST'])
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


@app.route('/api/opencode/models', methods=['GET'])
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


@app.route('/api/opencode/model', methods=['POST'])
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


@app.route('/api/opencode/effort', methods=['GET'])
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


@app.route('/api/opencode/effort', methods=['POST'])
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








@app.route('/api/chat/warnings', methods=['GET'])
def get_chat_warnings_api():
    """Canonical per-chat warning/error log (badge drift, effort skips)."""
    try:
        from api.chat_warnings import get_warnings

        session = (request.args.get('session') or '').strip()
        if not session:
            return jsonify({'success': False, 'error': 'session is required'}), 400
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        return jsonify({'success': True, 'warnings': get_warnings(session)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/chat/warnings', methods=['DELETE'])
def clear_chat_warnings_api():
    try:
        from api.chat_warnings import clear_warnings

        data = request.get_json(silent=True) or {}
        session = str(data.get('session') or request.args.get('session') or '').strip()
        if not session:
            return jsonify({'success': False, 'error': 'session is required'}), 400
        _user, nid, err = require_chat_session_access(session)
        if err:
            return err
        clear_warnings(str(nid))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500










@app.route('/api/turn-feedback', methods=['POST'])
def turn_feedback_api():
    """Thumbs up/down on an assistant reply (router or pinned agent).

    Body: ``{session_id, query_id, feedback: "good"|"bad"|null}``. Stored on the
    outcome row (My Cuttle Performance) and on the message so the UI restores it.
    """
    try:
        data = request.get_json(silent=True) or {}
        query_id = str(data.get('query_id') or '').strip()
        feedback = data.get('feedback')
        feedback = str(feedback).strip().lower() if feedback else None
        if not re.fullmatch(r'[A-Za-z0-9_-]{4,64}', query_id):
            return jsonify({'success': False, 'error': 'query_id required.'}), 400
        if feedback not in (None, 'good', 'bad'):
            return jsonify({'success': False, 'error': 'feedback must be good, bad, or null.'}), 400
        token = get_request_session_token()
        db = get_auth_db()
        user = db.verify_auth_session(token) if token else None
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized.'}), 401
        try:
            session_id = int(str(data.get('session_id') or '').replace('db_session_', ''))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'session_id required.'}), 400
        if not db.get_chat_session(session_id, user['id']):
            return jsonify({'success': False, 'error': 'Session not found.'}), 404
        message_id = db.merge_message_metadata_by_query(
            session_id, query_id, {'user_feedback': feedback}
        )
        if message_id is None:
            return jsonify({'success': False, 'error': 'Reply not found in this chat.'}), 404
        from api.agent_router.outcomes import set_feedback_for_query

        decision_id = set_feedback_for_query(feedback, query_id)
        return jsonify({
            'success': True,
            'feedback': feedback,
            'message_id': message_id,
            'decision_id': decision_id,
            'recorded': decision_id is not None,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500








# ============================================================================
# Sessions API (agent-to-agent: list sessions, send to a session)
# ============================================================================

@app.route('/api/sessions/list', methods=['GET'])
@owner_required
def sessions_list():
    """List in-memory chat session IDs. For agent-to-agent orchestration."""
    try:
        return jsonify({
            'success': True,
            'sessions': list(chat_sessions.keys()),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shell/panes', methods=['GET', 'POST'])
def api_shell_panes():
    """
    Sync / read app-shell split panes (left→right or top→bottom).
    POST body: { version?, orientation?, columns: [{ page, flex? }] } — from app_shell.js persistSplitLayout.
    GET: { panes: [...], orientation }
    """
    global _shell_pane_layout
    try:
        if request.method == 'POST':
            _user, err = require_owner()
            if err:
                return err
            data = request.get_json(silent=True) or {}
            columns = data.get('columns')
            if not isinstance(columns, list):
                return jsonify({'success': False, 'error': 'columns array required'}), 400
            cleaned = []
            for col in columns[:12]:
                if not isinstance(col, dict):
                    continue
                page = str(col.get('page') or '/chat_page.html')[:500]
                flex = str(col.get('flex') or '')[:80]
                cleaned.append({'page': page, 'flex': flex})
            if not cleaned:
                cleaned = [{'page': '/chat_page.html', 'flex': ''}]
            orientation = _normalize_shell_orientation(data.get('orientation'))
            _shell_pane_layout = {
                'version': int(data.get('version') or 1),
                'orientation': orientation,
                'columns': cleaned,
                'updated_at': time.time(),
            }
            return jsonify({
                'success': True,
                'orientation': orientation,
                'panes': _shell_panes_snapshot(),
            })
        _user, err = loopback_or_authenticated()
        if err:
            return err
        return jsonify({
            'success': True,
            'orientation': _normalize_shell_orientation(_shell_pane_layout.get('orientation')),
            'panes': _shell_panes_snapshot(),
            'updated_at': _shell_pane_layout.get('updated_at'),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shell/workspaces', methods=['GET', 'POST'])
def api_shell_workspaces():
    """Named split-pane snapshots (pane count, pages, open chats) for cross-device load."""
    try:
        _user, err = require_owner()
        if err:
            return err
        items = _load_shell_workspaces()
        if request.method == 'GET':
            return jsonify({
                'success': True,
                'workspaces': [_public_shell_workspace(x) for x in items],
            })
        data = request.get_json(silent=True) or {}
        columns = _normalize_shell_workspace_columns(data.get('columns'))
        orientation = _normalize_shell_orientation(data.get('orientation'))
        root = data.get('root') if isinstance(data.get('root'), dict) else None
        name = str(data.get('name') or 'Workspace').strip()[:80] or 'Workspace'
        if name.lower() == 'default':
            return jsonify({
                'success': False,
                'error': 'Default is built-in and cannot be overwritten',
            }), 400
        now = time.time()
        existing_id = str(data.get('id') or '').strip()[:40]
        match = None
        if existing_id:
            match = next((x for x in items if x.get('id') == existing_id), None)
        if match is None:
            lower = name.lower()
            match = next((x for x in items if str(x.get('name') or '').lower() == lower), None)
        if match is None:
            match = {
                'id': uuid.uuid4().hex[:12],
                'name': name,
            }
            items.insert(0, match)
        match['name'] = name
        match['columns'] = columns
        match['orientation'] = orientation
        if root is not None:
            match['root'] = root
        elif 'root' in match:
            match.pop('root', None)
        match['updated_at'] = now
        # Most recently saved first
        items = [match] + [x for x in items if x.get('id') != match.get('id')]
        items = _save_shell_workspaces(items)
        saved = next((x for x in items if x.get('id') == match.get('id')), match)
        return jsonify({
            'success': True,
            'workspace': _public_shell_workspace(saved),
            'workspaces': [_public_shell_workspace(x) for x in items],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shell/workspaces/<workspace_id>', methods=['DELETE'])
@owner_required
def api_shell_workspace_delete(workspace_id):
    try:
        wid = str(workspace_id or '').strip()[:40]
        items = _load_shell_workspaces()
        next_items = [x for x in items if x.get('id') != wid]
        if len(next_items) == len(items):
            return jsonify({'success': False, 'error': 'Workspace not found'}), 404
        items = _save_shell_workspaces(next_items)
        return jsonify({
            'success': True,
            'workspaces': [_public_shell_workspace(x) for x in items],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shell/panes/<int:pane_number>/messages', methods=['GET'])
def api_shell_pane_messages(pane_number):
    """
    Read chat history for the Nth open pane (1 = leftmost).
    Query: limit (default 40, max 200).
    Host agents call this over loopback; signed-in UI may call it too.
    """
    _user, err = loopback_or_authenticated()
    if err:
        return err
    try:
        limit = request.args.get('limit', 40, type=int) or 40
        limit = max(1, min(200, limit))
        panes = _shell_panes_snapshot()
        if pane_number < 1 or pane_number > len(panes):
            return jsonify({
                'success': False,
                'error': f'Pane {pane_number} is not open ({len(panes)} pane(s) currently)',
                'panes': panes,
            }), 404
        pane = panes[pane_number - 1]
        sid = pane.get('session_id')
        if not sid:
            return jsonify({
                'success': True,
                'pane': pane,
                'messages': [],
                'note': 'No chat session in this pane',
            })
        db_sid = _parse_auth_db_session_id(sid)
        messages = []
        if db_sid is not None:
            messages = get_auth_db().get_messages(int(db_sid), limit=limit)
        else:
            sess = chat_sessions.get(sid) or {}
            messages = (sess.get('messages') or [])[-limit:]
        # Trim bulky fields for agent consumption
        out = []
        for msg in messages:
            content = msg.get('content') or ''
            if len(content) > 8000:
                content = content[:8000] + '\n…(truncated)'
            out.append({
                'role': msg.get('role'),
                'content': content,
                'timestamp': msg.get('timestamp'),
            })
        return jsonify({
            'success': True,
            'pane': pane,
            'messages': out,
            'count': len(out),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sessions/send', methods=['POST'])
def sessions_send():
    """Send a message to another session. Body: { target_session: id, message: string }."""
    if not PIPELINE_AVAILABLE:
        return jsonify({'success': False, 'error': 'Chat backend not available'}), 503
    try:
        data = request.get_json() or {}
        message = (data.get('message') or '').strip()
        target_session = data.get('target_session')
        if not message:
            return jsonify({'success': False, 'error': 'message required'}), 400
        if target_session:
            _user, _sid, err = _require_session_actor(target_session)
            if err:
                return err
            res = process_message_with_bot(message, target_session, session_kind='sessions_send', routing_key='sessions_send')
            if res.get('success'):
                return jsonify({'success': True, 'response': res.get('response', ''), 'session_id': target_session})
            return jsonify({'success': False, 'error': res.get('error', 'Unknown')}), 500
        return jsonify({'success': False, 'error': 'target_session required'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# Skills Registry API
# ============================================================================

@app.route('/api/project-commands', methods=['GET'])
@authenticated_required
def project_commands_list():
    """List ``.cuttle/commands/*.md`` for a registered project path / id."""
    try:
        from api.project_commands import list_project_commands
        from scripts.utilities.git_pending_changes import resolve_allowed_project_cwd
    except ImportError as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    path = (request.args.get('path') or '').strip() or None
    pid_raw = request.args.get('project_id')
    project_id = None
    if pid_raw not in (None, ''):
        try:
            project_id = int(pid_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Invalid project_id'}), 400

    try:
        projects = project_manager.get_projects() if project_manager else []
        current = project_manager.get_current_project() if project_manager else None
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    cwd, proj, err = resolve_allowed_project_cwd(path, project_id, projects, current)
    if err or not cwd:
        # Allow listing when path is a real directory even if registry is stale
        # (chat chips sometimes carry a valid path with a missing id).
        if path and Path(path).is_dir():
            cwd = str(Path(path).resolve())
            proj = proj or {'path': cwd, 'name': Path(cwd).name}
        else:
            return jsonify({'success': False, 'error': err or 'No project', 'commands': []}), 400

    commands = list_project_commands(cwd)
    # Palette-safe payload (omit huge bodies)
    slim = []
    for c in commands:
        slim.append({
            'name': c.get('name'),
            'title': c.get('title'),
            'description': c.get('description'),
            'aliases': c.get('aliases') or [],
            'has_run': bool(c.get('run')),
            'execute': c.get('execute'),
            'path': c.get('path'),
            'reserved_collision': bool(c.get('reserved_collision')),
        })
    return jsonify({
        'success': True,
        'project': {
            'id': (proj or {}).get('id'),
            'name': (proj or {}).get('name'),
            'path': cwd,
        },
        'commands': slim,
    })


@app.route('/api/sessions/<session_id>', methods=['GET'])
@owner_required
def get_session_history(session_id):
    """Get chat history for a session"""
    session = chat_sessions.get(session_id)
    if not session:
        return jsonify({
            'success': False,
            'error': 'Session not found'
        }), 404
    
    return jsonify({
        'success': True,
        'session_id': session_id,
        'messages': session.get_recent_messages(20),
        'created_at': session.created_at,
        'last_activity': session.last_activity
    })

@app.route('/api/sessions', methods=['GET'])
@owner_required
def list_sessions():
    """List all active sessions"""
    sessions_info = []
    for session_id, session in chat_sessions.items():
        sessions_info.append({
            'session_id': session_id,
            'message_count': len(session.messages),
            'created_at': session.created_at,
            'last_activity': session.last_activity
        })
    
    return jsonify({
        'success': True,
        'sessions': sessions_info,
        'total_sessions': len(chat_sessions)
    })

@app.route('/api/clear-session/<session_id>', methods=['POST'])
@owner_required
def clear_session(session_id):
    """Clear a specific session"""
    if session_id == 'all':
        chat_sessions.clear()
        return jsonify({
            'success': True,
            'message': 'All sessions cleared'
        })
    elif session_id in chat_sessions:
        del chat_sessions[session_id]
        return jsonify({
            'success': True,
            'message': f'Session {session_id} cleared'
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Session not found'
        }), 404


def _strip_invisible_leading(s: str) -> str:
    """Remove BOM / ZW* chars that break slash-command detection."""
    if not isinstance(s, str):
        return s
    return s.lstrip('\ufeff\u200b\u200c\u200d\u2060')


def _message_is_slash_remote_agent(msg: str) -> bool:
    """Explicit slash forms that must use tool-remote-agent, not default OpenAI."""
    if not isinstance(msg, str) or not msg.strip():
        return False
    t = _strip_invisible_leading(msg)
    low = t.lower()
    if re.match(r'^/claude(\s|$)', low):
        return True
    if re.match(r'^/opencode(\s|$)', low):
        return True
    if re.match(r'^/codex(\s|$)', low):
        return True
    if re.match(r'^/muse(\s|$)', low):
        return True
    if low.startswith('/cursor '):
        return True
    if low.startswith('claude '):
        return True
    return False


# PIPELINE SETTINGS ENDPOINTS
# =================================================================================

# Slow request logging (ms). Set CUTTLE_SLOW_REQUEST_MS=0 to disable.
_SLOW_REQ_MS = int(os.environ.get('CUTTLE_SLOW_REQUEST_MS', '800') or '800')

@app.route('/api/debug-log', methods=['POST'])
@owner_required
def debug_log():
    try:
        payload = request.json or {}
        msg = payload.get('msg', '')
        channel = (payload.get('channel') or 'nav').strip().lower()
        filename = 'cuttle_net_debug.log' if channel == 'net' else 'cuttle_nav_debug.log'
        path = _logs_dir() / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
        return jsonify({'ok': True})
    except Exception:
        return jsonify({'ok': False})


@app.route('/api/net-insight', methods=['GET'])
def net_insight():
    """Server-side snapshot for Electron pool / chat delivery debugging."""
    _user, err = loopback_or_owner()
    if err:
        return err
    busy = []
    pending = 0
    live = []
    try:
        from api import chat_delivery
        busy_map = getattr(chat_delivery, '_busy', {}) or {}
        seen = set()
        for sid, entry in list(busy_map.items()):
            bare = sid[len('db_session_'):] if str(sid).startswith('db_session_') else str(sid)
            if not bare or bare in seen:
                continue
            seen.add(bare)
            busy.append({
                'session_id': bare,
                'since': (entry or {}).get('since'),
                'generating': True,
            })
            try:
                st = get_chat_live_status(bare)
                if st and (st.get('active') or st.get('status')):
                    live.append({
                        'session_id': bare,
                        'active': st.get('active'),
                        'status': st.get('status'),
                        'query_id': st.get('query_id'),
                    })
            except Exception:
                pass
        pending = len(getattr(chat_delivery, '_pending', {}) or {})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({
        'success': True,
        'busy_chats': busy,
        'pending_results': pending,
        'live_status': live,
        'slow_request_log_ms': _SLOW_REQ_MS,
        'hint': 'Client overlay: localStorage.setItem("cuttle_net_debug","1"); Ctrl+Shift+D',
    })


@app.before_request
def _cuttle_req_timer_start():
    try:
        from flask import g
        g._cuttle_t0 = time.time()
    except Exception:
        pass

@app.after_request
def _cuttle_req_timer_end(response):
    try:
        if _SLOW_REQ_MS <= 0:
            return response
        from flask import g
        t0 = getattr(g, '_cuttle_t0', None)
        if t0 is None:
            return response
        ms = (time.time() - t0) * 1000.0
        if ms < _SLOW_REQ_MS:
            return response
        path = request.path or ''
        # Skip noisy static assets
        if path.startswith('/js/') or path.startswith('/css/') or path.startswith('/img/'):
            return response
        print(
            f"[SLOW] {ms:.0f}ms {request.method} {path}"
            f"{('?' + request.query_string.decode('utf-8', 'ignore')[:80]) if request.query_string else ''}"
            f" → {response.status_code}"
        )
    except Exception:
        pass
    return response


def start_listener_servers(app, ports, *, lan_enabled, lan_ip, bind_host, mdns_enabled,
                           cert_files, use_reloader=True,
                           make_server_factory=None, mdns_starter=None,
                           primary_runner=None, log=print):
    """Bring up companion listeners, advertise mDNS, then serve primary HTTPS.

    ``ports`` is an ``api.server_ports.ServerPorts`` triple resolved once by
    the caller. Every socket comes from ``make_server_factory`` / the
    ``mdns_starter`` / ``primary_runner`` seams (real ones by default), so
    tests can record the exact ``app.run`` / ``make_server`` / mDNS args
    without binding a port, touching the network, or spawning a server.
    Returns the resolved plan ``{"primary_port", "phone_https_port",
    "http_port", "mdns_port" | None}``. Companion failures are logged and
    never abort boot; the blocking primary serve propagates (KeyboardInterrupt
    included) to the caller.
    """
    from api.lan_access import lan_phone_portal_url, lan_phone_http_fallback_url

    if mdns_starter is None:
        def mdns_starter(port, name="Cuttle"):
            from api.discovery_mdns import start_mdns
            return start_mdns(port=port, name=name)
    if make_server_factory is None:
        def make_server_factory(host, port, wsgi_app, **kwargs):
            from werkzeug.serving import make_server
            return make_server(host, port, wsgi_app, **kwargs)

    plan = {
        "primary_port": ports.https,
        "phone_https_port": ports.phone_https,
        "http_port": ports.http,
        "mdns_port": ports.https if (lan_enabled and mdns_enabled) else None,
    }

    if lan_enabled and mdns_enabled:
        try:
            mdns_starter(ports.https, name="Cuttle")
        except Exception as e:
            log(f"[DISCOVERY] mDNS start skipped: {e}")

    if lan_enabled and lan_ip:
        phone_url = lan_phone_portal_url(ports.phone_https, lan_ip)
        fb_url = lan_phone_http_fallback_url(lan_ip)
        log(f"[LAN] Phone portal (HTTPS): {phone_url}")
        log(f"[LAN] Phone fallback (HTTP): {fb_url}")
        log(f"[LAN] Accept cert warning on phone once, or use HTTP port {ports.http}")
        try:
            phone_https_srv = make_server_factory(
                '0.0.0.0',
                ports.phone_https,
                app,
                threaded=True,
                ssl_context=(str(cert_files[0]), str(cert_files[1])),
            )
            threading.Thread(
                target=phone_https_srv.serve_forever,
                name='cuttle-lan-phone-https',
                daemon=True,
            ).start()
            log(f"[LAN] Phone HTTPS listening on https://0.0.0.0:{ports.phone_https}")
            http_fb_srv = make_server_factory('0.0.0.0', ports.http, app, threaded=True)
            threading.Thread(
                target=http_fb_srv.serve_forever,
                name='cuttle-lan-http-fallback',
                daemon=True,
            ).start()
            log(f"[LAN] HTTP fallback listening on http://0.0.0.0:{ports.http}")
        except Exception as e:
            log(f"[LAN] Phone LAN servers failed: {e}")
    else:
        # Electron (and local HTTP tooling) need plain HTTP even when LAN is off.
        # Binding loopback-only keeps the portal off the network.
        try:
            http_local_srv = make_server_factory('127.0.0.1', ports.http, app, threaded=True)
            threading.Thread(
                target=http_local_srv.serve_forever,
                name='cuttle-loopback-http',
                daemon=True,
            ).start()
            log(f"[HTTP] Loopback portal listening on http://127.0.0.1:{ports.http}")
        except Exception as e:
            log(f"[HTTP] Loopback portal failed: {e}")

    runner = primary_runner
    if runner is None:
        def runner(*, host, port):
            # threaded=True lets a request make internal API calls back into this app without deadlock
            app.run(
                debug=False,
                host=host,
                port=port,
                use_reloader=use_reloader,
                threaded=True,
                ssl_context=(cert_files[0], cert_files[1]),
            )
    log(f"[HTTPS] Serving on https://{bind_host}:{ports.https}")
    runner(host=bind_host, port=ports.https)
    return plan


if __name__ == '__main__':
    print("Starting Cuttle Web Chat API Server...")
    print("=" * 50)
    print(f"Project root: {project_root}")
    print(f"Bot available: {PIPELINE_AVAILABLE}")
    print("\nAPI endpoints:")
    print("  GET  /                    - App shell")
    print("  GET  /settings_page.html  - Settings page")
    print("  POST /api/chat            - Send chat message")
    print("  GET  /api/health          - Health check")
    print("  GET  /api/sessions        - List sessions")
    print("  GET  /api/sessions/<id>   - Get session history")
    print("  POST /api/clear-session/<id> - Clear session")
    print("\nGit API endpoints:")
    print("  GET  /api/git/status      - Get repository status")
    print("  GET  /api/git/branches    - Get branches")
    print("  GET  /api/git/commits     - Get recent commits")
    print("  GET  /api/git/files       - Get working directory files")
    print("  POST /api/git/commit      - Commit changes")
    print("  POST /api/git/pull        - Pull from remote")
    print("  POST /api/git/push        - Push to remote")
    print("  POST /api/git/branch      - Branch operations")
    print("\nProject Management API endpoints:")
    print("  GET  /api/projects        - Get all projects")
    print("  GET  /api/projects/current - Get current project")
    print("  GET  /api/projects/<id>   - Get specific project")
    print("  POST /api/projects        - Create new project")
    print("  PUT  /api/projects/<id>   - Update project")
    print("  DELETE /api/projects/<id> - Delete project")
    print("  POST /api/projects/<id>/switch - Switch to project")
    print("  POST /api/projects/<id>/sync - Sync remote project")
    print("  GET  /api/projects/history - Get project history")
    print("  GET  /api/projects/stats  - Get project statistics")
    try:
        from api.server_ports import resolve_with_env_file
        _ports = resolve_with_env_file()
    except Exception as exc:
        print(f"[PORTS] Invalid listener-port configuration: {exc}")
        print(f"[PORTS] Fix CUTTLE_HTTPS_PORT/CUTTLE_HTTP_PORT/CUTTLE_PHONE_HTTPS_PORT in {_env_file()}, "
              "then cold-restart the daemon (port changes need a daemon restart, not a Flask-only restart).")
        raise SystemExit(2)

    print(f"\nOpen https://127.0.0.1:{_ports.https} in your browser")
    print("Press Ctrl+C to stop the server")
    print("=" * 50)

    discovery = {}
    try:
        from managers.settings_manager import get_settings_manager
        from api.lan_access import (
            is_lan_access_enabled,
            get_lan_ipv4,
            resolve_bind_host,
            ensure_all_lan_firewall_rules,
            windows_firewall_rule_active,
            lan_portal_url,
            wifi_network_category,
        )
        settings = get_settings_manager()
        discovery = settings.get_setting('discovery') or {}
        lan_enabled = is_lan_access_enabled()
        lan_ip = get_lan_ipv4() if lan_enabled else None
        bind_host = resolve_bind_host(lan_enabled)
        if lan_enabled:
            if not ensure_all_lan_firewall_rules():
                print("[LAN] Firewall rules missing — on Windows run src/scripts/enable_lan_firewall.ps1 as Administrator")
            net_cat = wifi_network_category()
            if net_cat == 'Public':
                print('[LAN] Wi‑Fi is "Public" — phone access may be blocked. Set Wi‑Fi to Private in Windows Settings.')
        else:
            print("[LAN] Web portal is localhost-only (enable discovery.lan_access_enabled for phone access)")
    except Exception as e:
        print(f"[LAN] Startup config skipped: {e}")
        lan_enabled = False
        lan_ip = None
        bind_host = '127.0.0.1'

    try:
        # Disable reloader when running from Electron (FLASK_ENV=production).
        # The reloader spawns a child process that causes a race condition with
        # Electron's health check during startup.
        use_reloader = os.environ.get('FLASK_ENV') != 'production'

        try:
            from api.shared_media import purge_expired

            purged = purge_expired()
            n = purged.get('deleted') or 0
            print(
                f"[SHARED-MEDIA] purge: deleted={n} ttl_days={purged.get('ttl_days')}",
                flush=True,
            )
        except Exception as _pe:
            print(f"[SHARED-MEDIA] purge skipped: {_pe}", flush=True)

        cert_file, key_file = generate_self_signed_cert(lan_ip=lan_ip)

        # Flask started directly resolves the triple once here; the daemon
        # path instead pins the child env to its own boot snapshot.
        start_listener_servers(
            app,
            _ports,
            lan_enabled=lan_enabled,
            lan_ip=lan_ip,
            bind_host=bind_host,
            mdns_enabled=bool(discovery.get('mdns_enabled')),
            cert_files=(cert_file, key_file),
            use_reloader=use_reloader,
        )
    except KeyboardInterrupt:
        print("\n\nWeb Chat API server stopped by user")
    except Exception as e:
        print(f"\nERROR: Error starting server: {e}")
