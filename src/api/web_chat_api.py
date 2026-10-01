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
import queue as queue_module
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
import ipaddress
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from datetime import datetime, timedelta

# Add project root to path (parent of api directory, which is 'src')
script_dir = Path(__file__).parent
project_root = script_dir.parent  # This is 'src' directory (needed for web file serving)
actual_project_root = project_root.parent  # repo root (parent of src/)


def _remote_agent_project_map() -> dict:
    """Legacy pipeline project keys → paths. Extra keys: .cuttle_global/personal/path-aliases.json."""
    mapping = {
        "pc_bot": str(actual_project_root),
        "current": ".",
    }
    try:
        from core.runtime_paths import load_personal_path_aliases

        extra = load_personal_path_aliases(actual_project_root).get("remote_agent_projects") or {}
        if isinstance(extra, dict):
            mapping.update({str(k): str(v) for k, v in extra.items() if str(k) and str(v)})
    except Exception:
        pass
    return mapping

# Paths for self-signed certificate
CERT_DIR = actual_project_root / ".cuttle" / "certs"
CERT_FILE = CERT_DIR / "localhost.pem"
KEY_FILE = CERT_DIR / "localhost-key.pem"

def generate_self_signed_cert(lan_ip: Optional[str] = None, force_regenerate: bool = False):
    """Generate or reuse a self-signed SSL certificate for localhost (+ optional LAN IP)."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)

    if not force_regenerate and not cert_needs_regeneration(CERT_FILE, lan_ip):
        print("[HTTPS] Using existing SSL certificate.")
        return str(CERT_FILE), str(KEY_FILE)

    if CERT_FILE.exists() or KEY_FILE.exists():
        reason = "LAN IP changed" if lan_ip else "certificate SAN mismatch"
        print(f"[HTTPS] Regenerating self-signed SSL certificate ({reason})...")
        try:
            CERT_FILE.unlink(missing_ok=True)
            KEY_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    else:
        print("[HTTPS] Generating new self-signed SSL certificate...")

    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"CA"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, u"Mountain View"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"Cuttle"),
        x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
    ])
    san_entries = [
        x509.DNSName(u"localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    if lan_ip:
        try:
            san_entries.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))
        except Exception:
            pass
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(KEY_FILE, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(encoding=serialization.Encoding.PEM))
    print(f"[HTTPS] Generated: {CERT_FILE} and {KEY_FILE}")
    return str(CERT_FILE), str(KEY_FILE)


def cert_needs_regeneration(cert_file: Path, lan_ip: Optional[str] = None) -> bool:
    """Delegate to lan_access when available; inline fallback for early import order."""
    try:
        from api.lan_access import cert_needs_regeneration as _needs
        return _needs(cert_file, lan_ip)
    except Exception:
        return not cert_file.is_file()

# Load src/.env so API keys are available whether started by daemon or directly
_env_candidates = [
    actual_project_root / 'src' / '.env',
]
for _env_path in _env_candidates:
    if _env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_env_path, override=False)
        except ImportError:
            # Manual parse fallback if python-dotenv not installed
            with open(_env_path) as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith('#') and '=' in _line:
                        _k, _v = _line.split('=', 1)
                        os.environ.setdefault(_k.strip(), _v.strip())
        break
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

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

# Import in-process HTTP helpers (LLM fallback). Visual pipeline graphs are gone.
try:
    from api.internal_http import (
        LLM_INTERNAL_HTTP_TIMEOUT,
        _internal_app_post,
        _internal_response_ok,
        _internal_response_json,
    )
    PIPELINE_AVAILABLE = True  # chat backend is up (legacy flag name)
except ImportError as e:
    print(f"Warning: Internal HTTP helpers not available: {e}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Python path: {sys.path}")
    PIPELINE_AVAILABLE = False
    LLM_INTERNAL_HTTP_TIMEOUT = 300

# Import authentication
try:
    from api.auth_api import auth_bp
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

# Import bot config (for /api/settings)
try:
    from core.config import get_config
except ImportError:
    get_config = None

app = Flask(__name__)
# Restrict CORS to known-safe origins (+ LAN IP when lan_access_enabled).
try:
    from api.lan_access import build_cors_origins
    _CORS_ORIGINS = build_cors_origins()
except Exception:
    _CORS_ORIGINS = [
        'http://localhost:8080',
        'http://127.0.0.1:8080',
        'https://localhost:8080',
        'https://127.0.0.1:8080',
        'app://cuttle',
    ]
CORS(app, origins=_CORS_ORIGINS, supports_credentials=True)


@app.after_request
def _no_cache_ui_assets(response):
    """Cache policy for UI assets.

    HTML stays no-store so Electron/phone WebViews pick up markup changes.
    Versioned JS/CSS (`?v=…`) may be cached — forcing no-store on every
    stylesheet made phone LAN cold-starts re-fetch the whole CSS set over
    Werkzeug HTTPS, which often dropped links and painted an unstyled UI
    (video still worked via inline styles). Bump `?v=` when editing assets.
    """
    path = (request.path or '').lower()
    is_html = path.endswith('.html')
    is_asset = (
        path.endswith('.js')
        or path.endswith('.css')
        or path.startswith('/js/')
        or path.startswith('/css/')
    )
    if is_html:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    elif is_asset:
        # ?v= fingerprint → safe to cache. Retries append &_cssr= (new URL).
        if request.args.get('v'):
            response.headers['Cache-Control'] = 'public, max-age=604800, immutable'
            response.headers.pop('Pragma', None)
            response.headers.pop('Expires', None)
        else:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
    return response


# Rate limiter — shared instance from limiter.py (auth_api.py imports from there too,
# avoiding the circular-import that would occur if it imported from web_chat_api).
from api.limiter import limiter
limiter.init_app(app)

# Live-status store — owned service (chat_live_status.py). Delivery, auth,
# run-registry, and supervised orchestration import the service directly;
# this module keeps thin wrappers so existing callers/tests are untouched.
from api import chat_live_status as _live_status_svc

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
)


def _require_session_actor(session_id):
    """Signed-in caller; numeric chat ids must be owned by that user."""
    from api.cuttle_ui_capabilities import numeric_chat_session_id

    nid = numeric_chat_session_id(session_id)
    if nid is not None:
        return require_chat_session_access(nid)
    user, err = require_authenticated()
    if err:
        return None, None, err
    return user, session_id, None

# Register authentication blueprint
app.register_blueprint(auth_bp)

# Device workers mesh (host-first coordinator queue)
try:
    from api.device_workers.routes import workers_bp
    app.register_blueprint(workers_bp)
except Exception as _workers_err:
    print(f"[DEVICE_WORKERS] Failed to register routes: {_workers_err}")

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

# Projects (transport owned by api.project_routes; logic in managers.project_manager)
try:
    from api.project_routes import projects_bp
    app.register_blueprint(projects_bp)
except Exception as _projects_err:
    print(f"[PROJECTS] Failed to register routes: {_projects_err}")

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

# Tasks (transport owned by api.task_routes; logic in managers.task_manager)
try:
    from api.task_routes import tasks_bp
    app.register_blueprint(tasks_bp)
except Exception as _tasks_err:
    print(f"[TASKS] Failed to register routes: {_tasks_err}")

try:
    from api.jev.watch import ensure_started as _jev_watch_start

    _jev_watch_start()
except Exception as _jev_watch_err:
    print(f"[JEV] regress watcher not started: {_jev_watch_err}")

# Chat widgets (Tasks strip above composer)
try:
    from api.chat_widgets import register_chat_widget_routes
    register_chat_widget_routes(app)
except Exception as _widgets_err:
    print(f"[WIDGETS] Failed to register routes: {_widgets_err}")

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

# Android Capacitor shell updates (LAN APK, same idea as Electron app.asar)
try:
    from api.mobile_android_update import register_mobile_android_update_routes
    register_mobile_android_update_routes(app)
except Exception as _mobile_apk_err:
    print(f"[MOBILE] Failed to register Android update routes: {_mobile_apk_err}")

# Owner identity: set OWNER_USER_EMAIL in src/.env to grant owner privileges
# to a specific authenticated account. Falls back to False for all sessions if unset.
_OWNER_EMAIL = os.getenv('OWNER_USER_EMAIL', '').strip().lower()

# Global state (for backwards compatibility, new users use database)
chat_sessions = {}
session_counter = 0

# Track running persistent pipelines (always empty — graph runtime removed)
retired_pipeline_registry = {}  # Graph era ended: always empty. Kept so health/sessions payloads keep their shape.


def _legacy_process_control_gone_response():
    """HTTP 410 for unauthenticated start/stop/pkill process-control routes."""
    return jsonify({
        'success': False,
        'error': 'legacy_process_control_removed',
        'response': (
            'Starting or stopping Flask/Discord via these endpoints was removed. '
            'Use the daemon-owned Flask restart card or /restart.'
        ),
    }), 410


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


# Session-scoped queues for streaming status updates during pipeline execution.
# Keys: session_id, Values: queue.Queue that receives ('status', message) or ('done', result)
_chat_status_queues: dict = {}

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
_ollama_request_lock = threading.Lock()


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
    if _chat_turn_cancelled(session_id):
        return
    if message:
        try:
            set_chat_live_status(session_id, message, active=True)
        except Exception:
            pass
    q = _chat_status_queues.get(session_id)
    if q:
        try:
            q.put_nowait(('status', message))
        except queue_module.Full:
            pass


def _require_mobile_token(token: Optional[str]) -> bool:
    """Validate the mobile companion shared token."""
    try:
        from api.mobile_companion import verify_mobile_token
        return verify_mobile_token(token)
    except Exception:
        return False


def _parse_webchat_slash_pipeline_command(message: str):
    """If message is `/pipeline PipelineName ...`, return (pipeline_name, task_text). Else None."""
    if not isinstance(message, str):
        return None
    m = re.match(r"^\s*/pipeline\s+([A-Za-z0-9_.-]+)\s*(.*)$", message, re.DOTALL)
    if not m:
        return None
    pname = m.group(1).strip()
    body = (m.group(2) or "").strip()
    task = body if body else message
    return pname, task


def _parse_webchat_shorthand_pipeline_command(message: str, load_pipeline_file_fn):
    """If message is `/PipelineStem ...` and pipelines/{Stem}.json exists, return (stem, task).

    Lets users run `/Self_Improvement tell me ...` the same as `/pipeline Self_Improvement tell me ...`.
    Skips reserved first segments that are normal slash commands.
    """
    if not isinstance(message, str) or not load_pipeline_file_fn:
        return None
    m = re.match(r"^\s*/([A-Za-z0-9_.-]+)\s*(.*)$", message, re.DOTALL)
    if not m:
        return None
    pname = m.group(1).strip()
    low = pname.lower()
    reserved = frozenset(
        {
            'pipeline',
            'pipelines',
            'help',
            'claude',
            'hermes',
            'codex',
            'cursor',
            'cursor-cli',
            'api',
            'model',
            'plan',
            'ask',
            'agent',
            'clear',
            'new',
            'new-chat',
            'newchat',
            'about',
            'sandbox',
            'project',
            'cd',
        }
    )
    if low in reserved or low.startswith('cursor-cli'):
        return None
    if low.startswith('pipeline'):
        return None
    if not load_pipeline_file_fn(pname):
        return None
    body = (m.group(2) or '').strip()
    task = body if body else message
    return pname, task


def _register_first_pipeline_with_trigger(trigger_type: str) -> bool:
    """Graphs are gone; nothing to register."""
    return False


def _emit_chat_complete_mobile(session_id: str, result: dict) -> None:
    try:
        from api.mobile_companion import chat_complete_payload, emit_mobile_event

        emit_mobile_event("chat_complete", chat_complete_payload(session_id, result))
    except Exception as exc:
        print(f"[MOBILE] chat_complete emit failed: {exc}", flush=True)


# ---------------------------------------------------------------------------
# On-demand llama.cpp launch (Local + Auto when local is needed)
# ---------------------------------------------------------------------------
# When llama-server isn't running and a chat needs it, we don't fail or
# silently skip — we stash the message and reply with Yes/No <cuttle_button>
# tags. The web UI renders them; clicking sends "[button:<id>]" back through
# /api/chat.
#
# Triggers:
#   - Local mode: every message (local is required)
#   - Auto/Cloud: /hermes only when Hermes config uses a local backend
#   - Auto: when LLM fallback is about to invoke local (see
#     _offer_local_llm_launch_if_needed)
_LOCAL_LAUNCH_BUTTON_YES = 'launch-local-llm-yes'
_LOCAL_LAUNCH_BUTTON_NO = 'launch-local-llm-no'
# session_id -> original message waiting for the user's launch decision
_pending_local_llm_messages: dict = {}
# Sessions that clicked "Not now" — skip local until they launch or switch to Local
_declined_local_llm_sessions: set = set()

_CUTTLE_BUTTON_LABELS = {
    _LOCAL_LAUNCH_BUTTON_YES: 'Yes, launch llama.cpp',
    _LOCAL_LAUNCH_BUTTON_NO: 'Not now',
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
    """Register <cuttle_confirm> blocks and rewrite them into Confirm/Cancel buttons."""
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


def _persist_auth_launch_gate_reply(db, chat_session_id, user_message: str, launch_reply: dict) -> None:
    """Save button-click + launch-gate assistant reply for authenticated web chat."""
    if not db or chat_session_id is None or not launch_reply:
        return
    hist_user, user_meta = _button_click_history(user_message)
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


def _is_hermes_slash_command(message: str) -> bool:
    if not isinstance(message, str):
        return False
    low = message.lstrip('\ufeff\u200b\u200c\u200d\u2060').strip().lower()
    return low == '/hermes' or low.startswith('/hermes ')


def _hermes_slash_needs_local_llm(message_content) -> bool:
    """True when ``/hermes`` is aimed at a local Hermes backend (llama.cpp / Ollama / …)."""
    if not _is_hermes_slash_command(message_content or ''):
        return False
    try:
        from scripts.utilities.hermes_cli_tool import hermes_uses_local_backend

        return bool(hermes_uses_local_backend())
    except Exception as e:
        print(f"[LOCAL-LLM] hermes backend probe failed: {e}", flush=True)
        # Fail closed to the historical local-first behavior.
        return True


def _message_needs_local_llm(message_content, chat_inference_mode: str) -> bool:
    """True when this chat turn requires the local backend (llama.cpp / Ollama)."""
    mode = (chat_inference_mode or 'auto').strip().lower()
    if mode == 'local':
        return True
    if _hermes_slash_needs_local_llm(message_content):
        return True
    return False


def _local_llm_launch_prompt_for(message_content, chat_inference_mode: str = 'auto') -> str:
    mode = (chat_inference_mode or 'auto').strip().lower()
    if mode == 'local':
        why = (
            "This chat is set to **Local**, but llama.cpp isn't up yet. "
            "Want me to launch it?"
        )
    elif _hermes_slash_needs_local_llm(message_content):
        why = "Hermes needs the local llama.cpp model, but it isn't up yet. Want me to launch it?"
    else:
        why = (
            "This request needs the local llama.cpp model, but it isn't up yet. "
            "Want me to launch it?"
        )
    return (
        "**No local model is running right now.**\n\n"
        f"{why} Loading the model usually takes a minute or two.\n\n"
        "Once it's running it stays up until you close it yourself or ask me to shut it down.\n\n"
        f'<cuttle_button id="{_LOCAL_LAUNCH_BUTTON_YES}" label="Yes, launch llama.cpp"/>\n'
        f'<cuttle_button id="{_LOCAL_LAUNCH_BUTTON_NO}" label="Not now"/>'
    )


def _launch_local_llm_and_wait(session_id, timeout: float = 240.0):
    """Kick off llama-server and block until it answers (with live status updates).

    Returns (ok, error_message).
    """
    from core.local_llm import launch_llamacpp_detached, local_reachable

    emit_chat_status(session_id, 'Launching llama.cpp...')
    ok, err = launch_llamacpp_detached()
    if not ok:
        return False, err

    deadline = time.time() + timeout
    while time.time() < deadline:
        if local_reachable(timeout=2.0):
            _declined_local_llm_sessions.discard(session_id)
            return True, ''
        emit_chat_status(session_id, 'Loading local model (llama.cpp) — this can take 1–2 minutes...')
        time.sleep(3)
    return False, (
        f'llama.cpp did not become ready within {int(timeout)}s. '
        'Check ~/cuttle_logs/llamacpp.log for details.'
    )


def _offer_local_llm_launch_if_needed(session_id, message_content, chat_inference_mode: str = 'auto'):
    """If llama.cpp is the active backend and down, stash message and return a Yes/No reply.

    Returns a reply dict when the chat should stop and show the launch prompt;
    otherwise None (caller continues). Honours prior "Not now" for this session
    unless the message hard-requires local (Local mode / local Hermes).
    """
    if not session_id:
        return None
    try:
        from core.local_llm import is_llamacpp, local_reachable
        if not is_llamacpp() or local_reachable(timeout=1.0):
            _declined_local_llm_sessions.discard(session_id)
            return None
    except Exception as e:
        print(f"[LOCAL-LLM] reachability check failed: {e}")
        return None

    requires = _message_needs_local_llm(message_content, chat_inference_mode)
    if session_id in _declined_local_llm_sessions and not requires:
        return None

    _pending_local_llm_messages[session_id] = message_content
    return {
        'success': True,
        'response': _local_llm_launch_prompt_for(message_content, chat_inference_mode),
        'output': _local_llm_launch_prompt_for(message_content, chat_inference_mode),
        'type': 'local_llm_launch_prompt',
    }


def _handle_local_llm_launch_gate(message_content, session_id, chat_inference_mode):
    """Intercept chats that need llama.cpp when it's down, and Yes/No button replies.

    Returns (reply_dict, new_message_content). reply_dict is a final chat
    response when set; otherwise new_message_content is the (possibly restored
    pending) message to keep processing.
    """
    stripped = (message_content or '').strip()
    mode = (chat_inference_mode or 'auto').strip().lower()

    if stripped.startswith(f'[button:{_LOCAL_LAUNCH_BUTTON_NO}]'):
        pending = _pending_local_llm_messages.pop(session_id, None)
        pending_needs_local = _message_needs_local_llm(pending or '', mode)
        # Local mode or Hermes cannot continue without the model
        if mode == 'local' or pending_needs_local:
            return {
                'success': True,
                'response': (
                    "Okay, leaving llama.cpp off. Switch this chat to **Auto** or **Cloud** "
                    "to keep going without a local model, or send your message again "
                    "whenever you want to launch it."
                ),
                'type': 'local_llm_launch',
            }, message_content
        # Auto: remember decline and continue the pending turn without local
        _declined_local_llm_sessions.add(session_id)
        if pending:
            emit_chat_status(session_id, 'Continuing without llama.cpp...')
            return None, pending
        return {
            'success': True,
            'response': (
                "Okay, leaving llama.cpp off for now. I'll use cloud models when I can. "
                "Send a message again anytime if you want to launch the local model."
            ),
            'type': 'local_llm_launch',
        }, message_content

    if stripped.startswith(f'[button:{_LOCAL_LAUNCH_BUTTON_YES}]'):
        ok, err = _launch_local_llm_and_wait(session_id)
        pending = _pending_local_llm_messages.pop(session_id, None)
        if not ok:
            return {
                'success': True,
                'response': f"❌ **Couldn't start llama.cpp.** {err}",
                'type': 'local_llm_launch',
            }, message_content
        if pending:
            emit_chat_status(session_id, 'llama.cpp is ready — picking up your message...')
            return None, pending
        return {
            'success': True,
            'response': (
                '✅ **llama.cpp is up.** It stays running until you close it or ask me to. '
                'Go ahead and send your message.'
            ),
            'type': 'local_llm_launch',
        }, message_content

    if stripped.startswith('[button:'):
        return None, message_content

    if _message_needs_local_llm(stripped, mode):
        offer = _offer_local_llm_launch_if_needed(session_id, message_content, mode)
        if offer is not None:
            return offer, message_content

    return None, message_content


def process_message_with_bot(
    message_content,
    session_id,
    session_kind=None,
    routing_key=None,
    is_owner=False,
    status_queue=None,
    inference_mode='auto',
):
    """Process a chat/Discord turn: slash agents, router, then a no-graph fallback."""
    if isinstance(message_content, str):
        message_content = _strip_invisible_leading(message_content)
    if not PIPELINE_AVAILABLE:
        return {
            'success': False,
            'error': 'Chat backend not available',
            'response': "I'm sorry, but the chat backend is not available. Please check that all dependencies are installed."
        }

    if status_queue:
        _chat_status_queues[session_id] = status_queue
        emit_pipeline_status(session_id, PHASE_ROUTE, "Routing to top-level agent...")

    try:
        # Get session for context
        session = get_or_create_session(session_id)
        
        from api.inference_mode import normalize_inference_mode

        chat_inference_mode = normalize_inference_mode(inference_mode)

        # Local mode / /hermes / Yes-No launch replies when llama.cpp is down.
        _launch_reply, message_content = _handle_local_llm_launch_gate(
            message_content, session_id, chat_inference_mode
        )
        if _launch_reply is not None:
            if status_queue is None:
                _emit_chat_complete_mobile(session_id, _launch_reply)
            return _launch_reply

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

        # Explicit agent CLIs must not fall through to a missing graph.
        # Bundled agents (incl. legacy /cursor-cli → /cursor) go through the harness only.
        _hm = _match_harness_slash(
            message_content,
            project_path=_resolve_request_project_path({'session_id': session_id}),
        )
        if _hm:
            from api.inference_mode import (
                is_cloud_cli_slash_command,
                cloud_cli_slash_blocked_message,
            )
            if is_cloud_cli_slash_command(message_content):
                blocked = cloud_cli_slash_blocked_message(chat_inference_mode)
                if blocked:
                    return {'success': True, 'response': blocked, 'type': 'mode_blocked'}
            _hid, prompt = _hm
            if not prompt:
                return {
                    'success': True,
                    'response': f'❌ Please provide a prompt after /{_hid}.',
                    'type': f'{_hid}_error',
                }
            return _run_pinned_harness_turn(
                _hid,
                prompt,
                session_id,
                status_queue=status_queue,
                project_path=_resolve_request_project_path({
                    'session_id': session_id,
                }),
                **_harness_identity_run_kwargs(
                    _freeze_send_identity(session_id, message_content, None)
                ),
            )

        # Agent router: plain messages with no sticky/explicit agent selection
        try:
            from api.agent_router.integration import maybe_route_plain_message

            _db_sid = None
            if isinstance(session_id, str) and session_id.startswith('db_session_'):
                try:
                    _db_sid = int(session_id.rsplit('_', 1)[-1])
                except ValueError:
                    _db_sid = None
            elif isinstance(session_id, int):
                _db_sid = session_id
            _routed = maybe_route_plain_message(
                message_content,
                session_id=_db_sid if _db_sid is not None else session_id,
                project_path=_resolve_request_project_path({'session_id': session_id}),
                status_queue=status_queue,
            )
            if _routed is not None:
                if status_queue is None:
                    _emit_chat_complete_mobile(session_id, _routed)
                return _routed
        except Exception as _ar_disp:
            print(f"[AGENT-ROUTER] plain-message dispatch failed: {_ar_disp}", flush=True)

        # Create user context for web UI
        user_context = {
            'display_name': 'Web User',
            'id': session_id,
            'is_owner': is_owner,
            'username': 'web_user',
            'discriminator': '0',
            'session_id': session_id,
            'recent_messages': session.get_recent_messages(5),
            'web_ui': True,  # Mark as web UI for query tracking
            'inference_mode': chat_inference_mode,
        }
        
        # Session kind and routing (Discord DM vs guild vs web)
        sk = session_kind if session_kind is not None else 'web_anon'
        rk = routing_key if routing_key is not None else f'web_anon_{session_id}'
        session_data = {
            'session_id': session_id,
            'user_id': f'web_{session_id}',
            'platform': 'webchat',
            'timestamp': time.time(),
            'session_kind': sk,
            'routing_key': rk,
            'inference_mode': chat_inference_mode,
        }

        return _no_pipeline_chat_result()
        
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
        if status_queue and session_id in _chat_status_queues:
            _chat_status_queues.pop(session_id, None)


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
    data = data or {}

    pid = data.get('project_id')
    if pid is not None and str(pid).strip() != '' and project_manager is not None:
        try:
            proj = project_manager.get_project(int(pid))
        except (TypeError, ValueError):
            proj = None
        if proj and proj.get('path'):
            return str(proj['path']).strip()

    pname = data.get('project_name') or data.get('projectName')
    if isinstance(pname, str) and pname.strip() and project_manager is not None:
        needle = pname.strip().lower()
        try:
            for p in project_manager.get_projects() or []:
                if str(p.get('name') or '').strip().lower() == needle and p.get('path'):
                    return str(p['path']).strip()
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
                    if proj and proj.get('path'):
                        return str(proj['path']).strip()
                spath = stored.get('project_path')
                if isinstance(spath, str) and spath.strip():
                    return spath.strip()
                sname = stored.get('project_name')
                if isinstance(sname, str) and sname.strip() and project_manager is not None:
                    needle = sname.strip().lower()
                    for p in project_manager.get_projects() or []:
                        if str(p.get('name') or '').strip().lower() == needle and p.get('path'):
                            return str(p['path']).strip()
        except Exception as e:
            print(f"[CHAT] session project lookup failed: {e}")

    return _default_chat_cwd()


def _default_chat_cwd() -> str:
    """Fallback cwd when the request/session has no project.

    Prefer the registered Cuttle project (often ``…/src``, matching the Cursor
    workspace) over git-root ``actual_project_root``. Mixing those two on the
    first vs second /cursor turn forks Cursor ``--resume``.
    """
    if project_manager is not None:
        try:
            cur = getattr(project_manager, 'current_project', None)
            if isinstance(cur, dict) and (cur.get('path') or '').strip():
                return str(cur['path']).strip()
            for p in project_manager.get_projects() or []:
                name = str(p.get('name') or '')
                path = str(p.get('path') or '').strip()
                if path and re.search(r'cuttle', name, re.I):
                    return path
        except Exception:
            pass
    return str(actual_project_root)


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
        for key in ('project_id', 'project_name', 'project_path'):
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
    """Write project onto an auth chat from an explicit client chip.

    Fills an empty session on first send, and updates when the client sends a
    different project (``/project`` mid-chat). Never invents Cuttle when the
    body omitted project fields — that used to stamp the default over an EP chip.
    """
    if not user or session_id is None:
        return
    sid = int(session_id)
    db = get_auth_db()
    stored = db.get_session_project(sid) or {}
    data = data or {}
    pid = data.get('project_id')
    pname = data.get('project_name') or data.get('projectName')
    ppath = data.get('project_path') or data.get('projectPath')
    # Only stamp what the client actually sent.
    if pid is None and not (pname or ppath):
        return

    def _norm_path(p):
        return str(p or '').strip().replace('\\', '/').rstrip('/').lower()

    stored_pid = stored.get('project_id')
    stored_path = (stored.get('project_path') or '').strip()
    if stored_pid is not None or stored_path:
        if pid is not None and stored_pid is not None and str(pid) == str(stored_pid):
            return
        if ppath and _norm_path(ppath) == _norm_path(stored_path):
            return
        if (
            pid is None
            and not ppath
            and pname
            and str(stored.get('project_name') or '').strip().lower()
            == str(pname).strip().lower()
        ):
            return

    db.set_session_project(
        sid,
        user['id'],
        project_id=pid,
        project_name=pname,
        project_path=ppath,
    )


def _run_hermes_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
) -> dict:
    """Deprecated shim — Hermes lives under ``agent_harness/agents/hermes/``."""
    return _run_harness_web_command(
        "hermes",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
    )


def _match_harness_slash(message: str, project_path=None):
    """Return ``(agent_id, prompt)`` for harness agents, else None.

    Also maps the legacy ``/cursor-cli`` alias onto ``/cursor``.
    """
    try:
        import re as _re

        raw = (message or "").strip()
        # Legacy alias kept for Discord / old clients; catalog only declares /cursor.
        if _re.match(r"^/cursor-cli(\s|$)", raw, flags=_re.I):
            raw = "/cursor" + raw[len("/cursor-cli") :]
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
    """Shared entry for harness agents (Cursor, Codex, Muse, Claude, …)."""
    from api.agent_harness.kernel import run_agent_web_command

    return run_agent_web_command(
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
    started = time.perf_counter()
    body = _run_harness_web_command(agent_id, prompt, chat_session_id, **kwargs)
    try:
        from api.agent_router.pinned_outcomes import record_pinned_turn

        record_pinned_turn(
            agent_id,
            body,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            session_id=chat_session_id,
            project_path=kwargs.get("project_path"),
        )
    except Exception as exc:
        print(f"[CHAT] pinned outcome record failed: {str(exc)[:200]}", flush=True)
    return body


def _run_cursor_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
) -> dict:
    """Deprecated shim — Cursor lives under ``agent_harness/agents/cursor/``."""
    return _run_harness_web_command(
        "cursor",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
    )


def _run_codex_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
    reasoning_effort=None,
) -> dict:
    """Deprecated shim — Codex lives under ``agent_harness/agents/codex/``."""
    extra = {}
    if reasoning_effort:
        extra["reasoning_effort"] = reasoning_effort
    return _run_harness_web_command(
        "codex",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
        execute_kwargs=extra or None,
    )


def _run_muse_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
) -> dict:
    """Deprecated shim — Muse lives under ``agent_harness/agents/muse/``."""
    return _run_harness_web_command(
        "muse",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
    )


def _run_claude_web_command(
    prompt: str,
    chat_session_id,
    status_queue=None,
    project_path=None,
    model_override=None,
) -> dict:
    """Deprecated shim — Claude Code lives under ``agent_harness/agents/claude/``."""
    return _run_harness_web_command(
        "claude",
        prompt,
        chat_session_id,
        status_queue=status_queue,
        project_path=project_path,
        model_override=model_override,
    )


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

    Muse runs inside WSL, where the Flask API on the Windows host is
    unreachable and the transcripts are a gitignored binary SQLite file. Without
    this block Muse searches the working tree, finds only source that mentions
    chats, and reports that there are none.

    ``chat_session_id`` scopes the read recipe to the chat the turn belongs to;
    without it an agent asked about "this chat" has no id to use.
    """
    from api.cuttle_ui_capabilities import cuttle_chat_store_addon
    from scripts.utilities.muse_cli_tool import muse_resolution

    blocks = []
    try:
        pane_addon = _build_shell_pane_prompt_addon(prompt)
        if pane_addon:
            blocks.append(pane_addon)
    except Exception as pane_err:
        print(f"[Muse] Pane context skipped: {pane_err}")

    if _CHAT_LOOKUP_RE.search(prompt or ""):
        try:
            in_wsl = (muse_resolution().get("mode") or "") == "wsl"
            store_addon = cuttle_chat_store_addon(
                wsl=in_wsl,
                current_session_id=chat_session_id,
            )
            if store_addon:
                blocks.append(store_addon)
        except Exception as store_err:
            print(f"[Muse] Chat store context skipped: {store_err}")

    if not blocks:
        return prompt
    return "\n\n".join(blocks) + "\n\n---\n\n" + prompt


@app.route('/api/mobile/events', methods=['GET'])
def mobile_events_stream():
    """SSE stream for the Capacitor mobile app (and Android companion). Query params: device_id, token"""
    try:
        device_id = (request.args.get('device_id') or '').strip()
        token = (request.args.get('token') or '').strip()
        if not device_id:
            return jsonify({'success': False, 'error': 'device_id required'}), 400
        if not _require_mobile_token(token):
            return jsonify({'success': False, 'error': 'unauthorized'}), 401

        from api.mobile_companion import register_device_queue, unregister_device_queue
        q = register_device_queue(device_id)
        print(f"[MOBILE] SSE connected device_id={device_id}", flush=True)

        def generate():
            yield f"data: {json.dumps({'type': 'status', 'message': 'connected', 'device_id': device_id})}\n\n"
            last_keepalive = time.time()
            try:
                while True:
                    try:
                        ev = q.get(timeout=1.0)
                    except Exception:
                        if time.time() - last_keepalive > 10:
                            yield ": keepalive\n\n"
                            last_keepalive = time.time()
                        continue
                    yield f"data: {ev.to_sse_data()}\n\n"
            finally:
                try:
                    unregister_device_queue(device_id)
                except Exception:
                    pass

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive'
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mobile/poll', methods=['GET'])
def mobile_events_poll():
    """Short JSON long-poll for the Capacitor app. Query: device_id, token, timeout (1-25)."""
    try:
        device_id = (request.args.get('device_id') or '').strip()
        token = (request.args.get('token') or '').strip()
        if not device_id:
            return jsonify({'success': False, 'error': 'device_id required'}), 400
        if not _require_mobile_token(token):
            return jsonify({'success': False, 'error': 'unauthorized'}), 401
        try:
            timeout_s = int(request.args.get('timeout') or 20)
        except (TypeError, ValueError):
            timeout_s = 20
        timeout_s = max(0, min(25, timeout_s))
        from api.mobile_companion import drain_events
        wait = 0.05 if timeout_s <= 0 else float(timeout_s)
        events = drain_events(device_id, timeout_s=wait)
        return jsonify({'success': True, 'events': events})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mobile/reply', methods=['POST'])
def mobile_submit_reply():
    """Reply endpoint for Android notification actions. Body: { token, interaction_id, answer }"""
    try:
        data = request.get_json() or {}
        token = (data.get('token') or '').strip()
        if not _require_mobile_token(token):
            return jsonify({'success': False, 'error': 'unauthorized'}), 401
        interaction_id = (data.get('interaction_id') or '').strip()
        answer = (data.get('answer') or '').strip()
        if not interaction_id or not answer:
            return jsonify({'success': False, 'error': 'interaction_id and answer required'}), 400

        from api.mobile_companion import submit_interaction_answer
        ok = submit_interaction_answer(interaction_id, answer)
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _generate_chat_stream(process_fn, session_id_for_status, on_result=None, on_claimed=None):
    """Run process_fn() in a thread and yield SSE events for status updates + final response.

    on_result(result) persists the reply (e.g. save to db). It runs on the worker
    thread, not in the SSE loop, so closing the tab mid-reply no longer discards
    a finished answer; the reply is also parked in chat_delivery for the client
    to collect when it reconnects.

    on_claimed() runs only after this chat's busy slot is acquired — persist the
    user turn here so a second send cannot land in history before the lock.
    """
    from api import chat_delivery

    status_queue = queue_module.Queue()
    result_holder = {'result': None}

    def _sse_json(obj: dict) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception as enc_err:
            return json.dumps(
                {
                    'type': 'response',
                    'success': False,
                    'response': f'(Could not encode assistant reply: {enc_err})',
                    'session_id': session_id_for_status,
                },
                ensure_ascii=False,
                default=str,
            )

    # One reply per chat at a time — a re-send while an agent is still working
    # would otherwise start a second run competing for the same conversation.
    if not chat_delivery.try_begin(session_id_for_status):
        yield f"data: {_sse_json({'type': 'status', 'message': 'A reply is already generating…'})}\n\n"
        yield f"data: {_sse_json({'type': 'busy', 'session_id': session_id_for_status})}\n\n"
        yield f"data: {_sse_json({'type': 'done'})}\n\n"
        return

    # Identifies this turn for the rest of its life. If the user stops this run
    # and sends again, a late finish must not write into the newer turn.
    turn_token = chat_delivery.current_turn(session_id_for_status)

    # Persist the user turn only after we own the busy slot (avoids ghost
    # user bubbles when a second send loses the race).
    if on_claimed:
        try:
            on_claimed()
        except Exception as claim_err:
            print(f"[CHAT] on_claimed failed: {claim_err}")
            try:
                chat_delivery.end(session_id_for_status)
            except Exception:
                pass
            yield f"data: {_sse_json({'type': 'response', 'success': False, 'response': f'Failed to save your message: {claim_err}', 'session_id': session_id_for_status})}\n\n"
            yield f"data: {_sse_json({'type': 'done'})}\n\n"
            return

    # Send immediate event to prove stream is live and force early flush so status updates show in UI
    try:
        set_chat_live_status(session_id_for_status, 'Connecting...', active=True)
    except Exception:
        pass
    yield f"data: {_sse_json({'type': 'status', 'message': 'Connecting...'})}\n\n"

    def run_process():
        try:
            result_holder['result'] = process_fn(status_queue=status_queue)
        except Exception as e:
            import traceback
            traceback.print_exc()
            result_holder['result'] = {
                'success': False,
                'error': str(e),
                'response': f'I encountered an error: {str(e)}. Please try again.'
            }
        result = result_holder['result'] or {}
        superseded = chat_delivery.is_stale_turn(session_id_for_status, turn_token)
        try:
            superseded = superseded or chat_delivery.is_turn_cancelled(session_id_for_status)
        except Exception:
            pass
        if superseded:
            print(
                f"[CHAT] discarding late reply for session {session_id_for_status}: "
                f"turn {turn_token} was superseded",
                flush=True,
            )
        # Persist here rather than in the SSE loop below: the loop dies with the
        # browser connection, this thread does not. Also save failure replies that
        # still have response text (timeouts / errors) so closed tabs keep them.
        if not superseded and on_result and (result.get('success') or result.get('response')):
            try:
                on_result(result)
            except Exception as save_err:
                print(f"[CHAT] on_result persistence failed: {save_err}")
        # Cursor / Codex / other harness replies never go through process_message_with_bot,
        # so the phone listener only hears about them from this stream completion.
        if not superseded and (result.get('success') or result.get('response')):
            try:
                _emit_chat_complete_mobile(session_id_for_status, result)
            except Exception:
                pass
        if not superseded:
            try:
                chat_delivery.store_result(session_id_for_status, result)
            except Exception:
                pass
        try:
            # Passing the token keeps a stale worker from releasing the lock of
            # the turn that replaced it.
            chat_delivery.end(session_id_for_status, turn=turn_token)
        except Exception:
            pass
        status_queue.put(('done', result))

    thread = threading.Thread(target=run_process, daemon=True)
    thread.start()

    # The SSE loop below dies with the browser connection, so it cannot be the
    # thing that publishes live status. This pump outlives it, keeping the
    # status current for whoever reconnects, and hands events to the loop.
    sse_queue = queue_module.Queue()

    def pump_status():
        while True:
            item = status_queue.get()
            kind, payload = item[0], item[1]
            # A superseded/cancelled run must not narrate over the turn that
            # replaced it (or resurrect activity after Stop).
            if kind != 'done' and (
                chat_delivery.is_stale_turn(session_id_for_status, turn_token)
                or chat_delivery.is_turn_cancelled(session_id_for_status)
            ):
                continue
            try:
                if kind == 'status':
                    set_chat_live_status(session_id_for_status, payload, active=True)
                elif kind == 'query_started':
                    set_chat_live_status(
                        session_id_for_status,
                        None,
                        active=True,
                        query_id=payload.get('query_id'),
                        report_url=payload.get('report_url'),
                    )
            except Exception:
                pass
            sse_queue.put(item)
            if kind == 'done':
                stale = chat_delivery.is_stale_turn(session_id_for_status, turn_token)
                try:
                    # Belt-and-suspenders: worker already ends busy before putting
                    # done; if that failed, don't leave history spinners stuck.
                    chat_delivery.end(session_id_for_status, turn=turn_token)
                except Exception:
                    pass
                cancelled = False
                try:
                    cancelled = chat_delivery.is_turn_cancelled(session_id_for_status)
                except Exception:
                    cancelled = False
                _clear_live_status_on_stream_done(
                    session_id_for_status, stale=stale, cancelled=cancelled
                )
                break

    pump = threading.Thread(target=pump_status, daemon=True)
    pump.start()

    try:
        while True:
            try:
                item = sse_queue.get(timeout=0.15)
            except queue_module.Empty:
                if not pump.is_alive():
                    break
                continue
            kind, payload = item[0], item[1]
            if kind == 'status':
                yield f"data: {_sse_json({'type': 'status', 'message': payload})}\n\n"
                yield ": " + (" " * 128) + "\n\n"  # SSE comment to encourage flush so chat page gets status promptly
            elif kind == 'query_started':
                yield f"data: {_sse_json({'type': 'query_started', 'query_id': payload.get('query_id'), 'report_url': payload.get('report_url')})}\n\n"
            elif kind == 'done':
                result = payload or {}
                # Belt-and-suspenders: rewrite confirms here too in case on_result
                # was missing/failed — client must never see stuck "Waiting…" cards.
                try:
                    result = _rewrite_assistant_response_actions(
                        result,
                        session_id_for_status,
                        (result.get('cursor_run') or {}).get('cwd')
                        or result.get('project_path')
                        or '',
                    ) or result
                except Exception as _rw_err:
                    print(f"[CHAT] SSE confirm rewrite failed: {_rw_err}", flush=True)
                resp = {'type': 'response', 'success': result.get('success', False), 'response': result.get('response', ''), 'session_id': session_id_for_status if session_id_for_status is not None else result.get('session_id'), 'response_type': result.get('type', 'unknown')}
                if result.get('query_id'):
                    resp['query_id'] = result['query_id']
                if result.get('report_url'):
                    resp['report_url'] = result['report_url']
                if result.get('cursor_run'):
                    resp['cursor_run'] = result['cursor_run']
                # Agent/model badge fields. Without these the streaming client
                # never learns which model answered, so reply chips fall back to
                # the bare agent name (e.g. "Muse Code" with no model).
                # agent_effort / agent_id are canonical (CH-000497-8); legacy
                # per-agent keys kept for older clients.
                for _passthrough in (
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
                # Token/cost footer: prefer enriched meta (CLI $ or models.dev est.).
                try:
                    _usage = _usage_meta_from_assistant_result(result)
                    if _usage:
                        resp['usage'] = _usage
                except Exception:
                    if isinstance(result.get('usage'), dict) and result.get('usage'):
                        resp['usage'] = result['usage']
                    if result.get('cost') is not None:
                        resp['cost'] = result['cost']
                yield f"data: {_sse_json(resp)}\n\n"
                yield f"data: {_sse_json({'type': 'done'})}\n\n"
                # Do NOT clear_result here. Writing the SSE chunk is not proof the
                # browser parsed it — clients often detach the fetch body (~45s)
                # right as the final event is written, and clearing here dropped
                # the parked reply (chirp / done UI, blank transcript until refresh).
                # Client take_result (or TTL) clears pending after a real delivery.
                break
    finally:
        # Deliberately no clear_chat_live_status here: a disconnected browser
        # does not stop the worker, and the pump clears status when the run
        # actually ends.
        thread.join(timeout=0.5)


@app.route('/phone')
def serve_phone_landing():
    """Short LAN setup page — open from phone browser."""
    try:
        from api.lan_access import lan_phone_portal_url, lan_phone_http_fallback_url, get_lan_ipv4, LAN_PHONE_HTTPS_PORT
        ip = get_lan_ipv4() or request.host.split(':')[0]
        base = lan_phone_portal_url(LAN_PHONE_HTTPS_PORT, ip) or f'https://{ip}:{LAN_PHONE_HTTPS_PORT}'
        http_fb = lan_phone_http_fallback_url(ip) or f'http://{ip}:8000'
    except Exception:
        base = request.url_root.rstrip('/')
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
<p class="warn">Android Chrome uses <strong>HTTPS</strong> on port <strong>8888</strong>. Accept the certificate warning once.</p>
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
<li>Try <strong>{http_fb}/api/lan-ping</strong> (port 8000, plain HTTP)</li>
<li>Run <strong>Enable Cuttle LAN Firewall</strong> as Administrator on PC</li>
</ul>
<p>PC only: <a href="https://127.0.0.1:8080" style="display:inline;background:none;padding:0;color:#60a5fa">https://127.0.0.1:8080</a></p>
</body></html>'''


@app.route('/')
def serve_app_shell():
    """Serve the app shell (main Electron entry point)"""
    return send_from_directory(project_root / 'web', 'app_shell.html')

@app.route('/landing_page.html')
def serve_landing_page():
    """Serve the landing/welcome page (loaded inside app shell iframe)"""
    return send_from_directory(project_root / 'web', 'landing_page.html')

@app.route('/media_player.html')
def serve_media_player():
    """Thin overlay for media playback (uses shell video background)."""
    return send_from_directory(project_root / 'web', 'media_player.html')

@app.route('/app_shell.html')
def serve_app_shell_direct():
    """Serve app shell directly"""
    return send_from_directory(project_root / 'web', 'app_shell.html')

@app.route('/query_log.html')
def serve_query_log():
    """In-pane / pop-out query inspector (JSON-backed)."""
    return send_from_directory(project_root / 'web', 'query_log.html')


@app.route('/test_reports.html')
def serve_test_reports():
    """Serve the test reports page"""
    return send_from_directory(project_root / 'web', 'test_reports.html')

@app.route('/router_editor.html')
def serve_router_editor():
    """Serve the Router editor page (replaces the retired pipeline node editor)"""
    return send_from_directory(project_root / 'web', 'router_editor.html')

@app.route('/node_editor.html')
def serve_node_editor():
    """Legacy pipeline node editor URL — the editor was rebuilt as the Router page."""
    from flask import redirect as _flask_redirect

    return _flask_redirect('/router_editor.html')

@app.route('/control_panel.html')
def serve_control_panel():
    """Serve the control panel page"""
    return send_from_directory(project_root / 'web', 'control_panel.html')

@app.route('/task_management.html')
def serve_task_management():
    """Serve the task management page"""
    return send_from_directory(project_root / 'web', 'task_management.html')


@app.route('/jobs_page.html')
def serve_jobs_page():
    """Serve the Jobs page (pipelines/scheduled jobs browser)"""
    return send_from_directory(project_root / 'web', 'jobs_page.html')


@app.route('/dashboards_page.html')
def serve_dashboards_page():
    """Serve the Dashboards hub (Model Benchmarks and future tiles)."""
    return send_from_directory(project_root / 'web', 'dashboards_page.html')


@app.route('/apps_page.html')
def serve_apps_page():
    """Serve the Apps launcher (Cuttle web apps grid; pin/unpin to the blade bar)."""
    return send_from_directory(project_root / 'web', 'apps_page.html')


@app.route('/job_insight.html')
def serve_job_insight():
    """Serve the Job Insight page (pipeline detail with live status and execution feed)"""
    return send_from_directory(project_root / 'web', 'job_insight.html')


@app.route('/pipeline_chat.html')
def serve_pipeline_chat():
    """Legacy graph chat URL — graphs were removed."""
    from flask import redirect as _flask_redirect

    return _flask_redirect('/jobs_page.html')


@app.route('/settings_page.html')
def serve_settings_page():
    """Serve the settings page"""
    return send_from_directory(project_root / 'web', 'settings_page.html')

@app.route('/tools_page.html')
def serve_tools_page():
    """Serve the Tools page (MCP servers and tools)."""
    return send_from_directory(project_root / 'web', 'tools_page.html')

@app.route('/home_automation.html')
def serve_home_automation_page():
    """Govee / home lighting themes and time-of-day schedule."""
    return send_from_directory(project_root / 'web', 'home_automation.html')

@app.route('/about_page.html')
def serve_about_page():
    """Serve the about page"""
    return send_from_directory(project_root / 'web', 'about_page.html')


@app.route('/api/status', methods=['GET'])
def api_status():
    """Liveness ping for the daemon watchdog and the app shell status panel.

    Keep this cheap: no subprocesses, no outbound probes. Diagnostic checks
    belong on GET /api/health (claude CLI, WSL, local LLM).
    """
    return jsonify({
        'status': 'ok',
        'flask': True,
        'discord_connected': False,
        'running_pipeline_count': len(retired_pipeline_registry),
    })

@app.route('/api/executing-jobs', methods=['GET'])
@owner_required
def api_executing_jobs():
    """Return list of pipeline runs currently executing (not merely listening). Used by Jobs panel."""
    try:
        from api.active_executions import get_executing_jobs
        jobs = get_executing_jobs()
        return jsonify({
            'executing_jobs': jobs
        })
    except ImportError:
        return jsonify({'executing_jobs': []})


def _enrich_cuttle_job_row(job: dict) -> dict:
    """Normalize remote or local Gitea job rows for the Jobs cockpit."""
    from datetime import datetime, timezone

    from api.cuttle_jobs.status_store import issue_url_for

    if not isinstance(job, dict):
        return {}
    job_id = job.get('job_id') or job.get('id')
    repo = str(job.get('repository') or '')
    issue = job.get('issue_number')
    gitea_url = job.get('gitea_url') or ''
    if not gitea_url and repo and issue:
        gitea_url = issue_url_for(repo, issue)
    result = job.get('result') if isinstance(job.get('result'), dict) else None
    status = str(job.get('status') or '')
    start_time = job.get('start_time') or job.get('claimed_at') or job.get('created_at') or ''
    end_time = job.get('end_time') or job.get('completed_at') or ''
    duration_sec = job.get('duration_sec')

    def _parse_ts(ts):
        ts = str(ts or '').strip()
        if not ts:
            return None
        try:
            if ts.endswith('Z'):
                return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except Exception:
            return None

    if duration_sec is None and start_time and end_time:
        sdt = _parse_ts(start_time)
        edt = _parse_ts(end_time)
        if sdt and edt:
            duration_sec = max(0, int((edt - sdt).total_seconds()))

    return {
        'id': job_id,
        'job_id': job_id,
        'command': str(job.get('command') or ''),
        'repository': repo,
        'issue_number': issue,
        'issue_title': str(job.get('issue_title') or ''),
        'gitea_url': gitea_url,
        'triggering_user': str(job.get('triggering_user') or ''),
        'status': status,
        'start_time': start_time,
        'end_time': end_time,
        'created_at': job.get('created_at') or '',
        'claimed_at': job.get('claimed_at') or '',
        'completed_at': job.get('completed_at') or end_time or '',
        'duration_sec': duration_sec,
        'attempt_count': job.get('attempt_count'),
        'last_error': job.get('last_error') or job.get('error'),
        'error': job.get('error') or job.get('last_error'),
        'result': result,
        'pr_url': (result or {}).get('pr_url') if result else None,
        'branch': (result or {}).get('branch') if result else None,
        'source': job.get('source') or 'cuttle_jobs',
        'claimed_by': job.get('claimed_by') or '',
    }


@app.route('/api/cuttle-jobs', methods=['GET'])
@owner_required
def api_cuttle_jobs():
    """Gitea @cuttle worker jobs: active snapshot + remote/local history for Jobs cockpit."""
    view = (request.args.get('view') or 'all').strip().lower()
    status = (request.args.get('status') or '').strip() or None
    try:
        limit = max(1, min(int(request.args.get('limit') or 50), 200))
    except ValueError:
        limit = 50

    from api.cuttle_jobs.status_store import list_history, list_running

    active = []
    for j in list_running():
        row = dict(j)
        row['status'] = 'processing'
        row['id'] = j.get('job_id')
        active.append(_enrich_cuttle_job_row(row))

    history = []
    history_source = 'local'
    remote_ok = False
    try:
        from api.cuttle_jobs.client import CuttleJobsClient, jobs_enabled
        if jobs_enabled():
            client = CuttleJobsClient()
            list_status = status
            if view == 'history' and not list_status:
                list_status = 'completed,failed,pending'
            elif view == 'active' and not list_status:
                list_status = 'processing,pending'
            remote_jobs = client.list_jobs(status=list_status, limit=limit)
            remote_ok = True
            history_source = 'remote'
            history = [_enrich_cuttle_job_row(j) for j in remote_jobs]
    except Exception as e:
        print(f'[cuttle-jobs] remote list failed: {e}')

    if not remote_ok or view in ('history', 'all'):
        local = [_enrich_cuttle_job_row(j) for j in list_history(limit=limit)]
        if not remote_ok:
            history = local
            history_source = 'local'
        else:
            seen = {h.get('job_id') for h in history}
            for row in local:
                jid = row.get('job_id')
                if jid not in seen:
                    history.append(row)
                    seen.add(jid)
            local_by_id = {r.get('job_id'): r for r in local}
            for h in history:
                loc = local_by_id.get(h.get('job_id'))
                if loc and h.get('duration_sec') is None and loc.get('duration_sec') is not None:
                    h['duration_sec'] = loc['duration_sec']
                if loc and not h.get('gitea_url') and loc.get('gitea_url'):
                    h['gitea_url'] = loc['gitea_url']

    if status and history:
        wanted = {s.strip().lower() for s in status.split(',') if s.strip()}
        history = [h for h in history if str(h.get('status') or '').lower() in wanted]

    completed_today = 0
    failed_recent = 0
    pending_count = 0
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for h in history:
        st = str(h.get('status') or '')
        if st == 'pending':
            pending_count += 1
        end = str(h.get('completed_at') or h.get('end_time') or '')
        if st == 'completed' and end.startswith(today):
            completed_today += 1
        if st == 'failed':
            failed_recent += 1

    payload = {
        'success': True,
        'active': active,
        'history': history if view != 'active' else [],
        'history_source': history_source,
        'remote_ok': remote_ok,
        'stats': {
            'running': len(active),
            'pending': pending_count,
            'completed_today': completed_today,
            'failed_recent': failed_recent,
        },
    }
    if view == 'history':
        # Keep active for badge consistency while focusing the history list.
        payload['active'] = active
    return jsonify(payload)


def _pipeline_name_from_query_log(data: dict) -> Optional[str]:
    """Resolve pipeline name from a query_data JSON record."""
    ctx = data.get('user_context') or {}
    pname = (ctx.get('pipeline_name') or '').strip()
    if pname:
        return pname
    inp = data.get('input_source') or {}
    details = inp.get('details', '') or ''
    m = re.search(r'Pipeline:\s*([^\s(]+)', details)
    if m:
        return m.group(1).strip()
    return None


def _effective_query_success_for_job_insight(data: dict) -> bool:
    """True only if the query actually succeeded end-to-end.

    Top-level ``success`` can stay True when the pipeline walk finishes even though
    an output node or a tracked LLM/tool call failed; the job insight UI should match
    the query report and graph node status.
    """
    if not data.get('success', True):
        return False
    em = data.get('error_message')
    if em is not None and str(em).strip():
        return False
    for stage in data.get('execution_stages') or []:
        if stage.get('success') is False:
            return False
    for c in data.get('llm_calls') or []:
        if c.get('success') is False:
            return False
    for t in data.get('tool_calls') or []:
        if t.get('success') is False:
            return False
    for node in (data.get('graph_structure') or {}).get('nodes') or []:
        if node.get('executed') and node.get('success') is False:
            return False
    return True


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
    """Inject a follow-up into the chat's running agent turn (Codex / Muse Code).

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


@app.route('/api/job-watch/cancel', methods=['POST'])
def job_watch_cancel():
    """Kill a running watch job (Stop on the progress card)."""
    data = request.get_json(silent=True) or {}
    job_id = str(data.get('id') or data.get('job_id') or '').strip()
    sid = data.get('session_id')
    if not job_id:
        return jsonify({'success': False, 'error': 'id required'}), 400

    from api.cuttle_ui_capabilities import numeric_chat_session_id

    db_sid = numeric_chat_session_id(sid) if sid is not None else None
    if db_sid is not None:
        _user, nid, err = require_chat_session_access(db_sid)
        if err:
            return err
    else:
        _user, err = require_authenticated()
        if err:
            return err

    try:
        from api.job_watch import cancel_job
        info = cancel_job(job_id) or {}
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        print(f"[CHAT] job-watch cancel failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

    if sid is not None:
        try:
            from api.chat_run_registry import clear_session_jobs
            clear_session_jobs(sid, job_id)
        except Exception:
            pass

    return jsonify({
        'success': bool(info.get('ok')),
        'cancelled': True,
        'killed': int(info.get('killed') or 0),
        'id': info.get('id') or job_id,
        'state': info.get('state') or info.get('already') or '',
    })


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


@app.route('/api/job-insight', methods=['GET'])
@owner_required
def api_job_insight():
    """Return job insight for a specific pipeline: metadata, live status, and recent execution feed."""
    pipeline_name = request.args.get('pipeline', '').strip()
    if not pipeline_name:
        return jsonify({'error': 'pipeline query param required'}), 400
    limit = min(int(request.args.get('limit', 30)), 100)
    try:
        from managers.settings_manager import get_settings_manager
        settings_mgr = get_settings_manager()
        pipeline_path = settings_mgr.get_pipeline_path(pipeline_name)
        if not pipeline_path or not pipeline_path.exists():
            return jsonify({'error': f'Pipeline not found: {pipeline_name}'}), 404
        with open(pipeline_path, 'r', encoding='utf-8') as f:
            pipeline_data = json.load(f)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    description = pipeline_data.get('description', '')
    nodes = pipeline_data.get('nodes', [])
    triggers = [n for n in nodes if n.get('type', '').startswith('trigger-') and n.get('type') != 'trigger-manual']
    schedules = []
    for n in nodes:
        if n.get('type') == 'trigger-schedule':
            cfg = n.get('config', {})
            schedules.append({
                'cron': cfg.get('schedule', ''),
                'name': n.get('name', ''),
                'enabled': cfg.get('enabled', True),
                'node_id': n.get('id'),
            })
    info = retired_pipeline_registry.get(pipeline_name, {})
    is_running = pipeline_name in retired_pipeline_registry
    executions = []
    logs_dir = project_root / 'web' / 'logs'
    if logs_dir.exists():
        import glob
        import os
        for path in glob.glob(str(logs_dir / 'query_data_*.json')):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                ctx = data.get('user_context') or {}
                pname = _pipeline_name_from_query_log(data)
                if pname != pipeline_name:
                    continue
                executions.append({
                    'query_id': data.get('query_id'),
                    'report_filename': data.get('report_filename'),
                    'timestamp': data.get('timestamp'),
                    'user_input': (data.get('user_input') or '')[:200],
                    'success': _effective_query_success_for_job_insight(data),
                    'error_message': data.get('error_message'),
                    'execution_time': data.get('total_execution_time', 0),
                    'total_tokens': data.get('total_tokens', 0),
                    'total_cost': data.get('total_cost', 0),
                    'execution_stages': data.get('execution_stages', []),
                    'llm_calls': data.get('llm_calls', []),
                    'tool_calls': data.get('tool_calls', []),
                    'graph_structure': data.get('graph_structure', {}),
                })
            except Exception:
                continue
    seen_qids = {e['query_id'] for e in executions if e.get('query_id')}
    try:
        from api.active_executions import get_executing_jobs
        for ej in get_executing_jobs():
            if ej.get('pipeline_name') != pipeline_name:
                continue
            qid = ej.get('query_id')
            if not qid or qid in seen_qids:
                continue
            seen_qids.add(qid)
            executions.append({
                'query_id': qid,
                'report_filename': f'query_report_{qid}.html',
                'timestamp': ej.get('start_time'),
                'user_input': '',
                'success': None,
                'pending': True,
                'execution_time': None,
                'total_tokens': 0,
                'total_cost': 0,
                'execution_stages': [],
                'llm_calls': [],
                'tool_calls': [],
                'graph_structure': {},
            })
    except Exception:
        pass
    executions.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    executions = executions[:limit]
    return jsonify({
        'pipeline': pipeline_name,
        'description': description,
        'is_running': is_running,
        'status': info.get('status', 'stopped'),
        'start_time': info.get('start_time'),
        'triggers': [{'name': t.get('name'), 'type': t.get('type')} for t in triggers],
        'schedules': schedules,
        'executions': executions,
    })


# Notify queue path for tray notifications (daemon watches this file)
NOTIFY_QUEUE_PATH = actual_project_root / "cuttle_notify_queue.jsonl"

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
        from api.cuttle_jobs.status_store import notify_tray
        notify_tray(message, variant=str(variant or 'info'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ui-toasts', methods=['GET'])
def api_ui_toasts():
    """Pull+clear pending UI toasts for Electron/app_shell (Gitea jobs, etc.)."""
    _user, err = loopback_or_authenticated()
    if err:
        return err
    try:
        from api.cuttle_jobs.status_store import pull_ui_toasts
        return jsonify({'toasts': pull_ui_toasts()})
    except Exception:
        return jsonify({'toasts': []})

@app.route('/wizard_page.html')
def serve_wizard_page():
    """Serve the setup wizard and doctor page"""
    return send_from_directory(project_root / 'web', 'wizard_page.html')

@app.route('/git_graph_page.html')
def serve_git_graph_page():
    """Serve the vertical git topology visualizer."""
    return send_from_directory(project_root / 'web', 'git_graph_page.html')

@app.route('/git_ui.html')
def serve_git_ui():
    """Serve the custom Git UI page"""
    return send_from_directory(project_root / 'web', 'git_ui.html')

@app.route('/chat_page.html')
def serve_chat_page():
    """Serve the chat page"""
    return send_from_directory(project_root / 'web', 'chat_page.html')

@app.route('/api/ungit/status')
def check_ungit_status():
    """Check if Ungit is running on port 8448"""
    import requests
    try:
        response = requests.get('http://localhost:8448', timeout=2)
        if response.status_code == 200:
            return jsonify({
                'success': True,
                'running': True,
                'url': 'http://localhost:8448'
            })
        else:
            return jsonify({
                'success': True,
                'running': False,
                'error': f'Ungit responded with status {response.status_code}'
            })
    except requests.exceptions.RequestException as e:
        return jsonify({
            'success': True,
            'running': False,
            'error': str(e)
        })

@app.route('/git-webui/')
def serve_git_webui():
    """Serve the standard Ungit interface with project integration"""
    try:
        # Get current project from project manager
        current_project = project_manager.get_current_project()
        project_path = current_project['path'] if current_project else None
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Git Web UI - Ungit</title>
            <script>
                // Get current project path from server
                const currentProjectPath = {json.dumps(project_path)};
                
                // Try to open Ungit on common ports
                const ports = [8448, 8081, 8000];
                let currentPort = 0;
                
                function tryUngit() {{
                    if (currentPort >= ports.length) {{
                        // Try using backend API as fallback
                        fetch('/api/ungit/status')
                            .then(response => response.json())
                            .then(data => {{
                                if (data.success && data.running) {{
                                    let url = data.url;
                                    if (currentProjectPath) {{
                                        url += `?path=${{encodeURIComponent(currentProjectPath)}}`;
                                    }}
                                    window.location.href = url;
                                }} else {{
                                    showUngitNotRunning();
                                }}
                            }})
                            .catch(error => {{
                                console.error('Backend API check failed:', error);
                                showUngitNotRunning();
                            }});
                        return;
                    }}
                    
                    const port = ports[currentPort];
                    // Include project path in URL if available
                    let url = `http://localhost:${{port}}`;
                    if (currentProjectPath) {{
                        url += `?path=${{encodeURIComponent(currentProjectPath)}}`;
                    }}
                    
                    // Try to detect Ungit by creating an image element (works around CORS)
                    const img = new Image();
                    img.onload = function() {{
                        // Ungit is running on this port
                        window.location.href = url;
                    }};
                    img.onerror = function() {{
                        // Try next port
                        currentPort++;
                        document.getElementById('status').innerHTML = 
                            `<p>Trying port ${{port}}... not available, trying next port...</p>`;
                        setTimeout(tryUngit, 1000);
                    }};
                    // Try to load a small image from Ungit (this bypasses CORS)
                    img.src = `http://localhost:${{port}}/images/icon.png?t=${{Date.now()}}`;
                }}
                
                function showUngitNotRunning() {{
                    document.getElementById('status').innerHTML = 
                        '<div style="color: red; margin: 20px;">' +
                        '<h3>Ungit not running</h3>' +
                        '<p>Please start the Cuttle launcher (Option 1) to automatically install and start Ungit.</p>' +
                        '<p>Or start Ungit manually:</p>' +
                        '<pre style="background: #f5f5f5; padding: 10px; border-radius: 5px; text-align: left; display: inline-block;">ungit</pre>' +
                        '<p style="margin-top: 15px;"><strong>Note:</strong> Current project: <code>{project_path or 'None'}</code></p>' +
                        '</div>';
                }}
                
                window.onload = function() {{
                    let statusText = 'Connecting to Ungit...';
                    if (currentProjectPath) {{
                        statusText += `<br><small>Current project: ${{currentProjectPath}}</small>`;
                    }}
                    document.getElementById('status').innerHTML = `<p>${{statusText}}</p>`;
                    tryUngit();
                }};
            </script>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f8f9fa; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #333; margin-bottom: 20px; }}
                pre {{ background: #f5f5f5; padding: 10px; border-radius: 5px; text-align: left; display: inline-block; margin: 10px 0; }}
                .fallback-link {{ margin-top: 20px; }}
                .fallback-link a {{ color: #007bff; text-decoration: none; }}
                .fallback-link a:hover {{ text-decoration: underline; }}
                .project-info {{ background: #e8f4f8; padding: 15px; border-radius: 8px; margin: 15px 0; border-left: 4px solid #007bff; }}
                .project-info h4 {{ margin: 0 0 10px 0; color: #0056b3; }}
                .project-info code {{ background: #f8f9fa; padding: 2px 6px; border-radius: 3px; font-family: monospace; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🌐 Git Web UI - Ungit</h1>
                {f'''
                <div class="project-info">
                    <h4>📁 Current Project</h4>
                    <p><strong>{current_project['name']}</strong> ({current_project['type']})</p>
                    <p><code>{project_path}</code></p>
                    {f'<p><em>{current_project["description"]}</em></p>' if current_project.get('description') else ''}
                </div>
                ''' if current_project else '<p style="color: #666;">No project currently selected</p>'}
                <div id="status">Loading...</div>
                <div class="fallback-link">
                    <p><a href="/git_ui.html">Use Custom Git UI instead</a></p>
                </div>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Git Web UI - Error</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f8f9fa; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                .error {{ color: #dc3545; background: #f8d7da; padding: 15px; border-radius: 5px; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🌐 Git Web UI - Error</h1>
                <div class="error">
                    <p>Error loading project information: {str(e)}</p>
                </div>
                <p><a href="/git_ui.html">Use Custom Git UI instead</a></p>
            </div>
        </body>
        </html>
        """, 500

@app.route('/api/start-ungit', methods=['POST'])
@owner_required
def start_ungit():
    """Start Ungit with the current project"""
    try:
        import subprocess
        import threading
        
        # Get current project
        current_project = project_manager.get_current_project()
        if not current_project:
            return jsonify({
                'success': False,
                'error': 'No current project selected'
            }), 400
        
        project_path = Path(current_project['path'])
        if not project_path.exists():
            return jsonify({
                'success': False,
                'error': f'Project path does not exist: {project_path}'
            }), 400
        
        # Check if Ungit is already running
        try:
            import requests
            response = requests.get('http://localhost:8448', timeout=2)
            if response.status_code == 200:
                return jsonify({
                    'success': True,
                    'message': 'Ungit is already running',
                    'url': 'http://localhost:8448'
                })
        except:
            pass  # Ungit not running, continue
        
        # Start Ungit in background
        def start_ungit_process():
            try:
                subprocess.Popen([
                    'ungit', 
                    '--port', '8448',
                    '--launchBrowser', 'false',
                    '--rootPath', str(project_path)
                ], cwd=project_root)
            except Exception as e:
                print(f"Error starting Ungit: {e}")
        
        # Start Ungit in a separate thread
        ungit_thread = threading.Thread(target=start_ungit_process, daemon=True)
        ungit_thread.start()
        
        return jsonify({
            'success': True,
            'message': f'Ungit started for project: {current_project["name"]}',
            'project': current_project['name'],
            'path': str(project_path),
            'url': 'http://localhost:8448'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to start Ungit: {str(e)}'
        }), 500

@app.route('/img/<path:filename>')
def serve_image(filename):
    """Serve images from the img directory"""
    return send_from_directory(project_root / 'img', filename)

@app.route('/./img/<path:filename>')
def serve_image_alt(filename):
    """Serve images from the img directory (alternative path)"""
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

            ensure_poster_sidecar_file(Path(norm).name, project_root=actual_project_root)
    except Exception as e:
        print(f"[OUTPUT] poster ensure failed for {filename}: {e}")
    resp = make_response(send_from_directory(project_root / 'output', filename))
    # Phone / LAN download: ?download=1 forces attachment disposition
    as_download = (request.args.get('download') or '').strip().lower() in (
        '1', 'true', 'yes',
    )
    if as_download:
        base = Path(filename).name
        resp.headers['Content-Disposition'] = f'attachment; filename="{base}"'
    return resp


@app.route('/api/shared-media/stage', methods=['POST'])
@owner_required
def api_shared_media_stage():
    """Copy a local image/video into /output/shared/ for chat previews (LAN-safe)."""
    try:
        from api.shared_media import stage_file

        data = request.get_json(silent=True) or {}
        raw_path = (data.get('path') or data.get('src') or '').strip()
        if not raw_path:
            return jsonify({'success': False, 'error': 'path required'}), 400
        preferred = (data.get('filename') or data.get('name') or '').strip() or None
        result = stage_file(raw_path, project_root=actual_project_root, preferred_name=preferred)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shared-media/poster', methods=['POST', 'GET'])
@authenticated_required
def api_shared_media_poster():
    """Ensure a mid-frame poster exists for a staged /output/shared video."""
    try:
        from api.shared_media import ensure_poster_for_shared_url

        if request.method == 'GET':
            url = (request.args.get('url') or request.args.get('src') or '').strip()
        else:
            data = request.get_json(silent=True) or {}
            url = (data.get('url') or data.get('src') or '').strip()
        if not url:
            return jsonify({'success': False, 'error': 'url required'}), 400
        result = ensure_poster_for_shared_url(url, project_root=actual_project_root)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shared-media/meta', methods=['GET', 'POST'])
@authenticated_required
def api_shared_media_meta():
    """Return original filename/path for a staged /output/shared media URL."""
    try:
        from api.shared_media import read_stage_meta

        if request.method == 'GET':
            url = (request.args.get('url') or request.args.get('src') or '').strip()
        else:
            data = request.get_json(silent=True) or {}
            url = (data.get('url') or data.get('src') or '').strip()
        if not url:
            return jsonify({'success': False, 'error': 'url required'}), 400
        result = read_stage_meta(url, project_root=actual_project_root)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shared-media/purge', methods=['POST', 'GET'])
def api_shared_media_purge():
    """Delete staged shared-media files older than TTL (default 7 days)."""
    _user, err = loopback_or_owner()
    if err:
        return err
    try:
        from api.shared_media import purge_expired

        result = purge_expired(project_root=actual_project_root)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/logs/')
def serve_logs_directory():
    """Serve the logs directory listing"""
    return send_from_directory(project_root / 'web' / 'logs', 'index.html')

@app.route('/logs/<path:filename>')
def serve_logs(filename):
    """Serve files from the web/logs directory"""
    if filename.startswith('query_report_') and filename.endswith('.html'):
        stem = filename[len('query_report_'):-5]
        qid = (stem.split('_')[0] if stem else '').strip()
        if qid:
            return redirect(f'/query_log.html?id={qid}', code=302)
    resp = make_response(
        send_from_directory(project_root / 'web' / 'logs', filename)
    )
    return resp

@app.route('/api/test-reports')
@owner_required
def get_test_reports():
    """Get list of test reports"""
    try:
        test_reports = get_reports_by_type('test_report', limit=100)
        return jsonify({
            'success': True,
            'reports': test_reports,
            'total': len(test_reports)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/test-api-key', methods=['POST'])
@owner_required
def test_api_key():
    """Test an API key for validity"""
    try:
        data = request.get_json()
        api_type = data.get('api_type')
        api_key = data.get('api_key')
        
        print(f"[API_TEST] Testing {api_type} key: {api_key[:10]}..." if api_key else "[API_TEST] No key provided")
        
        if not api_type or not api_key:
            return jsonify({
                'success': False,
                'message': 'API type and key are required'
            }), 400
        
        if api_type == 'openai':
            return test_openai_key(api_key)
        elif api_type == 'anthropic':
            return test_anthropic_key(api_key)
        elif api_type == 'discord':
            return test_discord_key(api_key)
        else:
            return jsonify({
                'success': False,
                'message': f'Unknown API type: {api_type}'
            }), 400
            
    except Exception as e:
        print(f"[API_TEST] Error: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

def test_openai_key(api_key):
    """Test OpenAI API key"""
    try:
        print(f"[OPENAI_TEST] Starting test for key: {api_key[:10]}...")
        
        try:
            import openai
            from openai import OpenAI
        except ImportError as e:
            print(f"[OPENAI_TEST] Import error: {e}")
            return jsonify({
                'success': False,
                'message': 'OpenAI package not installed. Run: pip install openai'
            })
        
        # Validate API key format first
        if not api_key.startswith('sk-'):
            print(f"[OPENAI_TEST] Invalid format: {api_key[:10]}...")
            return jsonify({
                'success': False,
                'message': 'Invalid API key format. OpenAI keys should start with "sk-"'
            })
        
        print(f"[OPENAI_TEST] Creating client...")
        client = OpenAI(api_key=api_key)
        
        print(f"[OPENAI_TEST] Making API call...")
        # Test with a simple completion
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=1,
            timeout=10
        )
        
        print(f"[OPENAI_TEST] Success! Model: {response.model}")
        return jsonify({
            'success': True,
            'message': f'Valid OpenAI key (Model: {response.model})'
        })
        
    except openai.AuthenticationError as e:
        print(f"[OPENAI_TEST] Auth error: {e}")
        return jsonify({
            'success': False,
            'message': 'Invalid API key or insufficient permissions'
        })
    except openai.RateLimitError as e:
        print(f"[OPENAI_TEST] Rate limit: {e}")
        return jsonify({
            'success': False,
            'message': 'Rate limit exceeded. Please try again later'
        })
    except openai.APITimeoutError as e:
        print(f"[OPENAI_TEST] Timeout: {e}")
        return jsonify({
            'success': False,
            'message': 'Request timed out. Please check your connection'
        })
    except Exception as e:
        print(f"[OPENAI_TEST] General error: {e}")
        return jsonify({
            'success': False,
            'message': f'OpenAI API error: {str(e)}'
        })

def test_anthropic_key(api_key):
    """Test Anthropic API key"""
    try:
        print(f"[ANTHROPIC_TEST] Starting test for key: {api_key[:10]}...")
        
        try:
            import anthropic
            from anthropic import Anthropic
        except ImportError as e:
            print(f"[ANTHROPIC_TEST] Import error: {e}")
            return jsonify({
                'success': False,
                'message': 'Anthropic package not installed. Run: pip install anthropic'
            })
        
        # Validate API key format first
        if not api_key.startswith('sk-ant-'):
            print(f"[ANTHROPIC_TEST] Invalid format: {api_key[:10]}...")
            return jsonify({
                'success': False,
                'message': 'Invalid API key format. Anthropic keys should start with "sk-ant-"'
            })
        
        print(f"[ANTHROPIC_TEST] Creating client...")
        client = Anthropic(api_key=api_key)
        
        print(f"[ANTHROPIC_TEST] Making API call...")
        # Test with a simple message
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=1,
            messages=[{"role": "user", "content": "Hi"}]
        )
        
        print(f"[ANTHROPIC_TEST] Success! Model: {response.model}")
        return jsonify({
            'success': True,
            'message': f'Valid Anthropic key (Model: {response.model})'
        })
        
    except Exception as e:
        error_msg = str(e)
        print(f"[ANTHROPIC_TEST] Error: {error_msg}")
        
        if "authentication" in error_msg.lower() or "unauthorized" in error_msg.lower():
            return jsonify({
                'success': False,
                'message': 'Invalid API key or insufficient permissions'
            })
        elif "rate" in error_msg.lower() or "limit" in error_msg.lower():
            return jsonify({
                'success': False,
                'message': 'Rate limit exceeded. Please try again later'
            })
        elif "timeout" in error_msg.lower():
            return jsonify({
                'success': False,
                'message': 'Request timed out. Please check your connection'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Anthropic API error: {error_msg}'
            })

def test_discord_key(token):
    """Test Discord bot token"""
    try:
        print(f"[DISCORD_TEST] Starting test for token: {token[:10]}...")
        import requests
        
        # Validate token format first
        if not token or len(token) < 50:
            print(f"[DISCORD_TEST] Invalid format: {token[:10]}...")
            return jsonify({
                'success': False,
                'message': 'Invalid token format. Discord bot tokens should be longer'
            })
        
        # Test the token by making a request to Discord's API
        headers = {
            'Authorization': f'Bot {token}',
            'Content-Type': 'application/json'
        }
        
        print(f"[DISCORD_TEST] Making API call to Discord...")
        # Try to get bot information
        response = requests.get(
            'https://discord.com/api/v10/users/@me',
            headers=headers,
            timeout=10
        )
        
        print(f"[DISCORD_TEST] Response status: {response.status_code}")
        
        if response.status_code == 200:
            bot_data = response.json()
            print(f"[DISCORD_TEST] Success! Bot: {bot_data.get('username', 'Unknown')}")
            return jsonify({
                'success': True,
                'message': f'Valid Discord bot token (Bot: {bot_data.get("username", "Unknown")})'
            })
        elif response.status_code == 401:
            print(f"[DISCORD_TEST] Auth failed")
            return jsonify({
                'success': False,
                'message': 'Invalid Discord bot token'
            })
        else:
            print(f"[DISCORD_TEST] HTTP error: {response.status_code}")
            return jsonify({
                'success': False,
                'message': f'Discord API error: HTTP {response.status_code}'
            })
            
    except requests.exceptions.Timeout as e:
        print(f"[DISCORD_TEST] Timeout: {e}")
        return jsonify({
            'success': False,
            'message': 'Request timed out. Please check your connection'
        })
    except requests.exceptions.RequestException as e:
        print(f"[DISCORD_TEST] Request error: {e}")
        return jsonify({
            'success': False,
            'message': f'Network error: {str(e)}'
        })
    except Exception as e:
        print(f"[DISCORD_TEST] General error: {e}")
        return jsonify({
            'success': False,
            'message': f'Discord API error: {str(e)}'
        })

@app.route('/api/save-api-key', methods=['POST'])
@owner_required
def save_api_key():
    """Save an API key securely.

    Only the named credential is modified; every other line of ``src/.env``
    (comments, blanks, ``export`` prefixes, unrelated keys) is preserved.
    ``DISCORD_BOT_TOKEN`` is the canonical settings key — saving a Discord
    token also clears a legacy ``DISCORD_TOKEN`` line so a stale shadowed
    value cannot win at runtime. Saved values are applied to
    ``os.environ`` immediately so new requests pick them up without a
    Flask restart.
    """
    try:
        data = request.get_json() or {}
        api_type = (data.get('api_type') or '').strip().lower()
        api_key = (data.get('api_key') or '').strip()

        _key_names = {
            'openai': 'OPENAI_API_KEY',
            'anthropic': 'ANTHROPIC_API_KEY',
            'discord': 'DISCORD_BOT_TOKEN',
        }
        if api_type not in _key_names:
            return jsonify({
                'success': False,
                'message': f'Unknown API type: {data.get("api_type")}'
            }), 400
        if not api_key:
            return jsonify({
                'success': False,
                'message': 'API type and key are required'
            }), 400
        if '•' in api_key or '…' in api_key:
            # Display hints are not credentials — refuse to store one.
            return jsonify({
                'success': False,
                'message': 'That looks like a masked hint, not a credential. Type or paste the full key.'
            }), 400

        env_name = _key_names[api_type]
        # Save to src/.env only
        env_file = actual_project_root / 'src' / '.env'

        lines: list = []
        if env_file.exists():
            with open(env_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

        found = False
        out_lines: list = []
        for line in lines:
            stripped = line.strip()
            # Preserve comments, blanks, and non-assignment lines verbatim.
            if not stripped or stripped.startswith('#') or '=' not in line:
                out_lines.append(line if line.endswith('\n') else line + '\n')
                continue
            lhs, _, _rhs = line.partition('=')
            lhs_name = lhs.strip()
            # Drop `export ` prefix when comparing, preserve it on write.
            bare = lhs_name[7:].strip() if lhs_name.startswith('export ') else lhs_name
            if bare == env_name:
                prefix = lhs[:len(lhs) - len(lhs_name)] + (
                    lhs_name[:len(lhs_name) - len(bare)] if bare != lhs_name else ''
                )
                out_lines.append(f'{prefix}{env_name}={api_key}\n')
                found = True
            elif api_type == 'discord' and bare == 'DISCORD_TOKEN':
                # Converge on the canonical settings key; a legacy line
                # would otherwise shadow the saved value at runtime.
                continue
            else:
                out_lines.append(line if line.endswith('\n') else line + '\n')
        if not found:
            out_lines.append(f'{env_name}={api_key}\n')

        env_file.parent.mkdir(parents=True, exist_ok=True)
        with open(env_file, 'w', encoding='utf-8') as f:
            f.writelines(out_lines)

        # Apply immediately for new requests (no Flask restart needed).
        os.environ[env_name] = api_key
        if api_type == 'discord':
            os.environ.pop('DISCORD_TOKEN', None)

        return jsonify({
            'success': True,
            'message': f'{api_type} API key saved successfully',
            'configured': {api_type: True},
            'hint': {api_type: _mask_credential_for_display(api_key)},
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


def _mask_credential_for_display(value: str) -> str:
    """Presence hint for a stored secret — never the secret itself."""
    v = (value or '').strip()
    if not v:
        return ''
    if len(v) < 12:
        return '••••••••'
    return f'{v[:4]}••••••••{v[-4:]}'


def _read_env_credential(names) -> str:
    """First non-empty value for one of ``names`` (env, then ``src/.env``)."""
    if isinstance(names, str):
        names = (names,)
    for name in names:
        v = (os.environ.get(name) or '').strip()
        if v:
            return v
    try:
        env_file = actual_project_root / 'src' / '.env'
        if env_file.exists():
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith('#') or '=' not in line:
                        continue
                    lhs, _, rhs = line.partition('=')
                    bare = lhs.strip()
                    if bare.startswith('export '):
                        bare = bare[7:].strip()
                    if bare in names:
                        v = rhs.strip().strip('"').strip("'")
                        if v:
                            return v
    except OSError:
        pass
    return ''


@app.route('/api/load-api-keys')
@owner_required
def load_api_keys():
    """Credential-presence indicators for the Settings UI.

    Never returns full secrets — only masked hints plus ``configured``
    flags. The browser cannot reconstruct a key from this response; to
    change a credential, POST the new value to ``/api/save-api-key``.
    """
    try:
        openai_val = _read_env_credential('OPENAI_API_KEY')
        anthropic_val = _read_env_credential('ANTHROPIC_API_KEY')
        # Runtime precedence accepts the legacy name; the settings UI
        # converges saves onto DISCORD_BOT_TOKEN (see save_api_key).
        discord_val = _read_env_credential(('DISCORD_BOT_TOKEN', 'DISCORD_TOKEN'))

        hints = {
            'openai': _mask_credential_for_display(openai_val),
            'anthropic': _mask_credential_for_display(anthropic_val),
            'discord': _mask_credential_for_display(discord_val),
        }
        return jsonify({
            'success': True,
            'api_keys': hints,
            'configured': {
                'openai': bool(openai_val),
                'anthropic': bool(anthropic_val),
                'discord': bool(discord_val),
            },
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

def get_reports_by_type(report_type, limit=100):
    """Get reports of a specific type"""
    try:
        import os
        import glob
        from pathlib import Path
        
        # Look for specific report files in the web/logs directory
        logs_dir = project_root / 'web' / 'logs'
        if not logs_dir.exists():
            return []
        
        # Find specific report type files
        pattern = f'{report_type}_*.html'
        report_files = glob.glob(str(logs_dir / pattern))
        
        # Sort by modification time (newest first) and limit
        report_files.sort(key=os.path.getmtime, reverse=True)
        report_files = report_files[:limit]
        
        reports = []
        for file_path in report_files:
            try:
                filename = os.path.basename(file_path)
                
                # Get file modification time and size
                mod_time = os.path.getmtime(file_path)
                file_size = os.path.getsize(file_path)
                import time
                mod_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mod_time))
                
                # Determine report type and extract metadata
                report_type_name = get_report_type_from_filename(filename)
                report_data = parse_report_html(file_path, report_type_name)
                
                # Create title from filename
                title = create_report_title(filename, report_data)
                
                reports.append({
                    'filename': filename,
                    'title': title,
                    'type': report_type_name,
                    'date': mod_time_str,
                    'size': file_size,
                    'url': f'/logs/{filename}',
                    'preview': report_data.get('preview', 'No preview available'),
                    'success': report_data.get('success', True),
                    'metadata': report_data
                })
            except Exception as e:
                print(f"Error processing report file {file_path}: {e}")
                continue
        
        return reports
    except Exception as e:
        print(f"Error getting {report_type} reports: {e}")
        return []


def get_report_type_from_filename(filename):
    """Determine report type from filename (test reports only)."""
    if 'test_report' in filename:
        return 'Test Report'
    return 'Report'


def create_report_title(filename, report_data):
    """Create a human-readable title for the report."""
    if 'test_report' in filename:
        return "System Test Results"
    return filename.replace('.html', '').replace('_', ' ').title()


def parse_report_html(file_path, report_type):
    """Parse HTML report to extract key data."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        if report_type == 'Test Report':
            return parse_test_report_html(content)
        return {'preview': 'Report data available', 'success': True}
    except Exception as e:
        print(f"Error parsing report HTML {file_path}: {e}")
        return {'preview': 'Error parsing report', 'success': False}


def parse_test_report_html(content):
    """Parse test report HTML"""
    import re
    data = {}
    
    # Extract test results - try multiple patterns
    passed_count = 0
    failed_count = 0
    total_count = 0
    
    # Pattern 1: Look for stat sections with stat-label and stat-number
    total_match = re.search(r'<span class="stat-number">(\d+)</span>\s*<span class="stat-label">Total Tests</span>', content, re.IGNORECASE)
    if total_match:
        total_count = int(total_match.group(1))
    
    passed_match = re.search(r'<span class="stat-number[^"]*">(\d+)</span>\s*<span class="stat-label">Passed</span>', content, re.IGNORECASE)
    if passed_match:
        passed_count = int(passed_match.group(1))
    
    failed_match = re.search(r'<span class="stat-number[^"]*">(\d+)</span>\s*<span class="stat-label">Failed</span>', content, re.IGNORECASE)
    if failed_match:
        failed_count = int(failed_match.group(1))
    
    # Pattern 2: "X / Y tests passed"
    if total_count == 0:
        success_match = re.search(r'(\d+)\s*/\s*(\d+)\s*tests? passed', content, re.IGNORECASE)
        if success_match:
            passed_count = int(success_match.group(1))
            total_count = int(success_match.group(2))
            failed_count = total_count - passed_count
    
    # Pattern 3: Look for pass/fail counts in stat sections (alternative format)
    if total_count == 0:
        passed_match2 = re.search(r'Passed:.*?(\d+)', content, re.IGNORECASE)
        failed_match2 = re.search(r'Failed:.*?(\d+)', content, re.IGNORECASE)
        if passed_match2:
            passed_count = int(passed_match2.group(1))
        if failed_match2:
            failed_count = int(failed_match2.group(1))
        total_count = passed_count + failed_count
    
    # Extract success rate if available
    success_rate_match = re.search(r'<span class="stat-number">([0-9.]+)%</span>\s*<span class="stat-label">Success Rate</span>', content, re.IGNORECASE)
    success_rate = float(success_rate_match.group(1)) if success_rate_match else (passed_count / total_count * 100 if total_count > 0 else 0)
    
    # Extract execution time if available
    exec_time_match = re.search(r'Execution Time:.*?([0-9.]+)\s*(s|seconds)', content, re.IGNORECASE)
    exec_time = float(exec_time_match.group(1)) if exec_time_match else None
    
    # Extract overall status
    status_match = re.search(r'Overall Status:\s*(\w+)', content, re.IGNORECASE)
    overall_status = status_match.group(1).upper() if status_match else None
    
    # Determine success status
    success = failed_count == 0 and total_count > 0
    if overall_status:
        success = overall_status in ['PASS', 'SUCCESS']
    
    # Create comprehensive preview
    if total_count > 0:
        status_icon = "✅" if success else "⚠️" if overall_status == 'PARTIAL' else "❌"
        
        preview_parts = [
            f"{status_icon} {passed_count}/{total_count} passed ({success_rate:.0f}%)"
        ]
        
        if failed_count > 0:
            preview_parts.append(f"❌ {failed_count} failed")
        
        if exec_time is not None:
            preview_parts.append(f"⏱️ {exec_time:.1f}s")
        
        if overall_status and overall_status not in ['PASS', 'SUCCESS', 'FAIL']:
            preview_parts.append(f"📊 {overall_status}")
        
        data['preview'] = " | ".join(preview_parts)
    else:
        data['preview'] = "📊 System test results available"
    
    data['success'] = success
    data['passed'] = passed_count
    data['failed'] = failed_count
    data['total'] = total_count
    data['overall_status'] = overall_status
    
    return data


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


_HARNESS_IDENTITY_AGENTS = frozenset({'muse', 'hermes', 'opencode', 'codex'})


def _harness_agent_from_message(message_text: str) -> Optional[str]:
    m = re.match(
        r'^/(muse|hermes|opencode|codex|cursor)\b',
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


def _muse_model_label(model) -> str:
    """Human label used by Muse reply badges."""
    from scripts.utilities.muse_cli_tool import muse_model_label
    return muse_model_label(model)


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


def _usage_meta_from_assistant_result(res: Optional[dict]) -> Optional[dict]:
    """Normalize CLI usage (+ models.dev estimate) for chat bubble footers."""
    if not isinstance(res, dict):
        return None
    raw = res.get('usage') if isinstance(res.get('usage'), dict) else {}
    cursor_run = res.get('cursor_run') if isinstance(res.get('cursor_run'), dict) else {}
    if not raw and isinstance(cursor_run.get('usage'), dict):
        raw = cursor_run.get('usage') or {}
    # Cursor stores full camelCase usage on cursor_run even when the top-level
    # usage blob was stripped to prompt/completion only — merge cache fields.
    elif isinstance(cursor_run.get('usage'), dict):
        cu = cursor_run.get('usage') or {}
        merged = dict(raw)
        for src, dst in (
            ('cacheReadTokens', 'cache_read_tokens'),
            ('cache_read_tokens', 'cache_read_tokens'),
            ('cacheWriteTokens', 'cache_write_tokens'),
            ('cache_write_tokens', 'cache_write_tokens'),
            ('context_tokens', 'context_tokens'),
            ('peak_context_tokens', 'peak_context_tokens'),
        ):
            if merged.get(dst) is not None:
                continue
            if cu.get(src) is None:
                continue
            try:
                n = int(cu.get(src) or 0)
            except (TypeError, ValueError):
                continue
            if n > 0:
                merged[dst] = n
        raw = merged
    model = str(
        res.get('agent_model')
        or res.get('model')
        or res.get('muse_model')
        or res.get('hermes_model')
        or res.get('opencode_model')
        or res.get('codex_model')
        or ''
    ).strip()
    if not model and cursor_run:
        model = str(
            cursor_run.get('reported_model')
            or cursor_run.get('requested_model')
            or ''
        ).strip()
    merged = dict(raw) if isinstance(raw, dict) else {}
    if res.get('cost') is not None and merged.get('cost') is None:
        merged['cost'] = res.get('cost')
    try:
        from api.model_pricing import enrich_usage_for_display
        return enrich_usage_for_display(merged, model=model or None)
    except Exception as exc:
        print(f"[CHAT] usage meta enrich failed: {exc}", flush=True)
        return None


def _current_request_data() -> dict:
    try:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            return body
    except Exception:
        pass
    return {}


def _user_badge_metadata(message_text: str, session_id, identity: Optional[dict] = None) -> Optional[dict]:
    """Snapshot the agent badge (model + effort) for a user turn at send time.

    History renders user bubbles from this stored chip. Without it the
    frontend re-derives badges from the *current* session pins, so changing
    the effort pin later rewrites every older bubble on refresh.

    ``identity`` is the frozen send-time tuple (same values the CLI gets).
    When present it wins over a second store/starred lookup.
    """
    import re as _re

    text = str(message_text or '').lstrip()
    m = _re.match(r'^/(muse|hermes|opencode|codex|cursor)\b', text, _re.IGNORECASE)
    if not m:
        return None
    agent = m.group(1).lower()
    sid = str(session_id) if session_id is not None else ''
    ident = identity if isinstance(identity, dict) else None
    if ident and str(ident.get('agent') or '').strip().lower() != agent:
        ident = None
    ident_model = str((ident or {}).get('model') or '').strip() if ident else ''
    ident_effort = str((ident or {}).get('effort') or '').strip() if ident else ''

    def _starred(model_fn=None, effort_fn=None, aid=''):
        sm = se = ''
        try:
            from api.agent_harness.agent_defaults import (
                get_starred_effort,
                get_starred_model,
            )
            if aid:
                sm = str(get_starred_model(aid) or '').strip()
                se = str(get_starred_effort(aid) or '').strip()
        except Exception:
            pass
        return sm, se

    if agent == 'muse':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from scripts.utilities.muse_cli_session_store import (
                    load_muse_effort,
                    load_muse_model,
                )
                if sid:
                    model = str(load_muse_model(sid) or '').strip()
                    effort = str(load_muse_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='muse')
                model = model or sm
                effort = effort or se
        if not model:
            try:
                from scripts.utilities.muse_cli_tool import resolve_muse_default_model
                model = str(resolve_muse_default_model() or '').strip()
            except Exception:
                model = ''
        label_model = model
        try:
            label_model = _muse_model_label(model) if model else model
        except Exception:
            pass
        label = 'Muse Code' + (f' - {label_model}' if label_model else '')
        meta = '/muse' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'muse'}]}}
    if agent == 'hermes':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from scripts.utilities.hermes_cli_session_store import (
                    load_hermes_effort,
                    load_hermes_model,
                )
                if sid:
                    model = str(load_hermes_model(sid) or '').strip()
                    effort = str(load_hermes_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='hermes')
                model = model or sm
                effort = effort or se
        label_model = model
        try:
            from scripts.utilities.hermes_cli_tool import hermes_model_label
            label_model = hermes_model_label(model) if model else model
        except Exception:
            pass
        label = 'Hermes' + (f' - {label_model}' if label_model else '')
        meta = '/hermes' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'hermes'}]}}
    if agent == 'opencode':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from api.agent_harness.agents.opencode.session_store import (
                    load_opencode_effort,
                    load_opencode_model,
                )
                if sid:
                    model = str(load_opencode_model(sid) or '').strip()
                    effort = str(load_opencode_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='opencode')
                model = model or sm
                effort = effort or se
        label_model = model
        try:
            from api.agent_harness.agents.opencode.adapter import opencode_model_label
            label_model = opencode_model_label(model) if model else model
        except Exception:
            pass
        label = 'OpenCode' + (f' - {label_model}' if label_model else '')
        meta = '/opencode' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'opencode'}]}}
    if agent == 'codex':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from scripts.utilities.codex_cli_session_store import (
                    load_codex_effort,
                    load_codex_model,
                )
                if sid:
                    model = str(load_codex_model(sid) or '').strip()
                    effort = str(load_codex_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='codex')
                model = model or sm
                effort = effort or se
        label_model = model
        try:
            from api.agent_harness.agents.codex.model_catalog import codex_model_label
            label_model = codex_model_label(model) if model else model
        except Exception:
            pass
        label = 'Codex' + (f' - {label_model}' if label_model else '')
        meta = '/codex' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'codex'}]}}
    # Cursor effort is baked into the model id.
    model = ident_model if ident is not None else ''
    try:
        if not model and sid:
            from scripts.utilities.cursor_cli_session_store import load_cursor_agent_options
            model = str((load_cursor_agent_options('', sid) or {}).get('model') or '').strip()
    except Exception:
        pass
    if not model:
        sm, _ = _starred(aid='cursor')
        model = sm or 'auto'
    pretty = model
    if not model or model.lower() in ('auto', 'default'):
        pretty = 'Auto'
    else:
        try:
            from api.cursor_agent_commands import list_cursor_agent_models
            for _m in list_cursor_agent_models() or []:
                if str(_m.get('id') or '').lower() == model.lower() and _m.get('label'):
                    pretty = str(_m['label']).strip() or model
                    break
        except Exception:
            pass
        if pretty == model:
            text = model[7:] if model.lower().startswith('cursor-') else model
            pretty = text.replace('-', ' ').replace('_', ' ').title()
    return {'slash_command': {'chips': [{
        'label': f'Cursor - {pretty}',
        'meta': f'/cursor · requested {pretty}',
        'category': 'cursor',
    }]}}


def _persist_auth_user_message(chat_session_id, message_text: str, metadata=None) -> None:
    """Save a user turn for authenticated slash-command chats (/cursor, /hermes, …)."""
    if chat_session_id is None or not message_text:
        return
    try:
        db = get_auth_db()
        metadata = _merge_project_into_meta(
            metadata, _current_request_data(), chat_session_id
        )
        db.add_message(chat_session_id, 'user', message_text, metadata=metadata or None)
    except Exception as e:
        print(f"[CHAT] persist user message failed: {e}")


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

    uploads_root = actual_project_root / 'src' / 'output' / 'uploads'
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


def _make_auth_assistant_saver(chat_session_id, inference_mode=None, project_path=None):
    """Return on_result callback that persists assistant replies for auth DB sessions."""
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
                        proj = _resolve_request_project_path({'session_id': chat_session_id}) or ''
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
                        text, project_root=actual_project_root
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
        _asst_meta = _assistant_message_metadata(nonlocal_res)
        _asst_meta = _merge_project_into_meta(
            _asst_meta,
            _current_request_data(),
            chat_session_id,
            nonlocal_res.get('cursor_run') if isinstance(nonlocal_res.get('cursor_run'), dict) else None,
        )
        try:
            from api.subagents.service import attach_batches_to_assistant_meta

            _asst_meta = attach_batches_to_assistant_meta(
                chat_session_id, _asst_meta or {}
            ) or _asst_meta
        except Exception as _sa:
            print(f"[CHAT] subagent attach meta failed: {_sa}", flush=True)
        try:
            db = get_auth_db()
            _mid = db.add_message(
                chat_session_id,
                'assistant',
                nonlocal_res.get('response', '') or '',
                metadata=_asst_meta or None,
            )
            try:
                from api.subagents.store import bind_unattached_batches

                if _mid:
                    bind_unattached_batches(db, chat_session_id, int(_mid))
            except Exception as _sb:
                print(f"[CHAT] subagent bind failed: {_sb}", flush=True)
        except Exception as e:
            print(f"[CHAT] persist assistant message failed: {e}")
            return
        try:
            from api.chat_titler import schedule_session_autoname
            schedule_session_autoname(chat_session_id, inference_mode)
        except Exception as _te:
            print(f"[TITLER] hook failed: {_te}")

    return on_save


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
        upload_dir = actual_project_root / 'src' / 'output' / 'uploads' / safe_session
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
            # Public URL via existing /output/ static serve (src/output/...)
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

        if not data or 'message' not in data:
            return jsonify({
                'success': False,
                'error': 'No message provided'
            }), 400
        
        message_content = _strip_invisible_leading(data.get('message') or '').strip()
        raw_attachments = data.get('attachments') or []
        chat_session_id = data.get('session_id')
        from api.inference_mode import normalize_inference_mode

        chat_inference_mode = normalize_inference_mode(data.get('inference_mode'))
        _sv = data.get('stream', True)
        if _sv is False:
            wants_stream = False
        elif isinstance(_sv, str) and _sv.strip().lower() in ('false', '0', 'no', 'off'):
            wants_stream = False
        else:
            wants_stream = True

        if not message_content and not raw_attachments:
            return jsonify({
                'success': False,
                'error': 'Empty message'
            }), 400

        _auth_user_early, auth_err = require_authenticated()
        if auth_err:
            return auth_err

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
                                chat_session_id, chat_inference_mode
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

        # Supervised control lane — status/cancel/followup/coordinator config.
        # Must run even while a child worker is active; never waits on chat busy
        # or the pending-prompt queue (frontend also bypasses for these).
        #
        # Exactly-once contract:
        #   parse → authorize → mutate once → persist control id → terminal return.
        # Never fall through into router/agent dispatch after recognizing a control
        # command (even if post-mutation persist/formatting fails).
        _sup_control_recognized = False
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
            if is_supervised_control_message(_sup_msg):
                _sup_control_recognized = True
                message_content = _sup_msg
                # Authorize / resolve session BEFORE mutation or _auth_user use.
                _auth_user, chat_session_id, _created_new_session = _resolve_auth_chat_session(
                    chat_session_id
                )
                _kind, _args = classify_supervised_message(message_content)
                _router_proj = _resolve_request_project_path({
                    **(data or {}),
                    'session_id': chat_session_id if chat_session_id is not None else data.get('session_id'),
                })
                _control_id = (
                    (data or {}).get('control_request_id')
                    or (data or {}).get('idempotency_key')
                    or request.headers.get('X-Cuttle-Control-Id')
                    or ''
                )
                _control_id = str(_control_id or '').strip()
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
                if wants_stream:
                    def _sup_control_stream():
                        yield _sse_session_event(chat_session_id)
                        yield f"data: {json.dumps({'type': 'response', **_sr}, ensure_ascii=False, default=str)}\n\n"
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
                return jsonify(_sr)
        except Exception as _sc_err:
            print(f"[CHAT] supervised control handler failed: {_sc_err}", flush=True)
            if _sup_control_recognized:
                # Recognized control must NEVER fall through (prevents double apply).
                _err_body = {
                    'success': True,
                    'response': (
                        f"❌ Supervised control command failed after recognition: {_sc_err}. "
                        "No further router/agent dispatch will run for this request."
                    ),
                    'type': 'supervised_control_error',
                    'control_lane': True,
                    'control_handled': True,
                    'session_id': chat_session_id,
                }
                if wants_stream:
                    def _sup_err_stream():
                        yield _sse_session_event(chat_session_id)
                        yield f"data: {json.dumps({'type': 'response', **_err_body}, ensure_ascii=False, default=str)}\n\n"
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return Response(
                        stream_with_context(_sup_err_stream()),
                        mimetype="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "X-Accel-Buffering": "no",
                            "Connection": "keep-alive",
                        },
                    )
                return jsonify(_err_body)

        # Check for slash commands
        if message_content.startswith('/help'):
            help_text = """**🦑 Cuttle Web Chat Commands**

**AI Commands (Auto / Cloud mode):**
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
• `/hermes "prompt"` - Hermes on local llama.cpp (also works in Local mode)
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

**Local mode:** only `/hermes` (and help). Switch to Auto or Cloud for the others.

**Graphs:** visual pipelines were removed. Use a slash agent or the router.

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
                allow_cloud_cli=(chat_inference_mode != 'local'),
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
            """Persist the user turn as history sees it (no digest, keeps thumbnails)."""
            _turn_meta = dict(_user_msg_meta) if isinstance(_user_msg_meta, dict) else {}
            try:
                _badge = _user_badge_metadata(
                    message_content, sid, identity=_send_identity
                )
                if _badge and 'slash_command' not in _turn_meta:
                    _turn_meta['slash_command'] = _badge['slash_command']
            except Exception:
                pass
            _persist_auth_user_message(
                sid,
                _attachment_history_text(message_content, _att_note),
                metadata=_turn_meta or None,
            )

        # On-demand llama.cpp for /hermes and launch Yes/No buttons (before slash handlers).
        # Local-mode prompts still run in process_message_with_bot (after the user message is saved).
        _launch_gate_sid = (
            f"db_session_{chat_session_id}" if _auth_user and chat_session_id is not None
            else (str(chat_session_id) if chat_session_id is not None else None)
        )
        _mc_stripped = (message_content or '').strip()
        if _launch_gate_sid and (
            _mc_stripped.startswith(f'[button:{_LOCAL_LAUNCH_BUTTON_YES}]')
            or _mc_stripped.startswith(f'[button:{_LOCAL_LAUNCH_BUTTON_NO}]')
            or _is_hermes_slash_command(_mc_stripped)
        ):
            _launch_reply, message_content = _handle_local_llm_launch_gate(
                message_content, _launch_gate_sid, chat_inference_mode
            )
            if _launch_reply is not None:
                body = dict(_launch_reply)
                body['session_id'] = chat_session_id
                if _auth_user and chat_session_id is not None:
                    _persist_auth_launch_gate_reply(
                        get_auth_db(), chat_session_id, _mc_stripped, _launch_reply
                    )
                return jsonify(body)

        # Project confirm actions (no LLM) — e.g. Discord post after <cuttle_confirm>.
        if _launch_gate_sid and _mc_stripped.startswith('[button:project-action-'):
            try:
                from api.project_actions import handle_project_action_button
                _action_reply = handle_project_action_button(
                    _mc_stripped,
                    session_id=str(_launch_gate_sid),
                )
            except Exception as _pa_err:
                print(f"[CHAT] project action button failed: {_pa_err}", flush=True)
                _action_reply = {
                    'success': False,
                    'response': f'Action failed: {_pa_err}',
                    'type': 'project_action',
                }
            if _action_reply is not None:
                body = dict(_action_reply)
                body['session_id'] = chat_session_id
                if _auth_user and chat_session_id is not None:
                    try:
                        _persist_auth_launch_gate_reply(
                            get_auth_db(), chat_session_id, _mc_stripped, _action_reply
                        )
                    except Exception as _pe:
                        print(f"[CHAT] persist project action reply failed: {_pe}", flush=True)
                return jsonify(body)

        # Dynamic action forms (no LLM) — choice / multi / form → allowlisted actions.
        if _launch_gate_sid and _mc_stripped.startswith('[action-form:'):
            try:
                from api.action_forms import handle_action_form_chat_message
                _form_reply = handle_action_form_chat_message(
                    _mc_stripped,
                    session_id=str(_launch_gate_sid),
                )
            except Exception as _af_err:
                print(f"[CHAT] action form failed: {_af_err}", flush=True)
                _form_reply = {
                    'success': False,
                    'response': f'Action form failed: {_af_err}',
                    'type': 'action_form',
                    'silent': False,
                }
            if _form_reply is not None:
                body = dict(_form_reply)
                body['session_id'] = chat_session_id
                # Silent forms: toast + card lock only — no "Selected: …" user bubble.
                if _auth_user and chat_session_id is not None and not body.get('silent'):
                    try:
                        _persist_auth_launch_gate_reply(
                            get_auth_db(), chat_session_id, _mc_stripped, _form_reply
                        )
                    except Exception as _pe:
                        print(f"[CHAT] persist action form reply failed: {_pe}", flush=True)
                elif (
                    _auth_user
                    and chat_session_id is not None
                    and body.get('silent')
                    and body.get('success')
                    and not body.get('reusable')
                    and (body.get('lock') or 'form') == 'form'
                    and body.get('form_id')
                ):
                    try:
                        from api.action_forms import mark_action_form_consumed_in_history
                        mark_action_form_consumed_in_history(
                            session_id=f'db_session_{chat_session_id}',
                            form_id=str(body.get('form_id')),
                            selected=list(body.get('selected') or []),
                            toast=str(body.get('toast') or ''),
                        )
                    except Exception as _pe:
                        print(f"[CHAT] action form lock persist failed: {_pe}", flush=True)
                return jsonify(body)

        # Expand project-local ``.cuttle/commands/*.md`` (e.g. /build, /cmd deploy).
        # Runs before /cursor handlers so ``/cursor /build`` becomes a full agent prompt.
        try:
            from api.project_commands import (
                try_expand_message_project_command,
                run_project_command_shell,
                format_project_command_shell_reply,
            )
            _proj_path = _resolve_request_project_path({
                **(data or {}),
                'session_id': chat_session_id if chat_session_id is not None else data.get('session_id'),
            })
            _expanded = try_expand_message_project_command(message_content, _proj_path)
            if _expanded and _expanded.get('action') == 'shell':
                _cmd = _expanded.get('command') or {}
                busy_resp = _reject_if_chat_busy(chat_session_id, wants_stream)
                if busy_resp is not None:
                    return busy_resp
                result = run_project_command_shell(
                    _cmd,
                    _expanded.get('args') or '',
                    session_id=chat_session_id,
                )
                body = format_project_command_shell_reply(_cmd, result)
                reply = {
                    'success': bool(result.get('success')),
                    'response': body,
                    'session_id': chat_session_id,
                    'type': 'project_command',
                }
                if chat_session_id is not None:
                    reply = _rewrite_assistant_response_actions(
                        reply,
                        f'db_session_{chat_session_id}',
                        _proj_path or '',
                    ) or reply
                if _auth_user and chat_session_id is not None:
                    try:
                        _persist_user_turn(chat_session_id)
                        _saver = _make_auth_assistant_saver(chat_session_id, chat_inference_mode)
                        if _saver:
                            _saver({
                                'success': bool(reply.get('success')),
                                'response': reply.get('response') or body,
                                'type': 'project_command',
                            })
                    except Exception as _pe:
                        print(f"[CHAT] project command persist failed: {_pe}", flush=True)
                return jsonify(reply)
            if _expanded and _expanded.get('action') == 'prompt' and _expanded.get('message'):
                print(
                    f"[CHAT] project command /{(_expanded.get('command') or {}).get('name')} "
                    f"expanded (sticky={_expanded.get('sticky')!r}) cwd={_proj_path!r}",
                    flush=True,
                )
                message_content = _expanded['message']
        except Exception as _pc_err:
            print(f"[CHAT] project command expand failed: {_pc_err}", flush=True)

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
                _on_save = _make_auth_assistant_saver(
                    chat_session_id if _auth_user else None,
                    chat_inference_mode,
                    project_path=_router_proj,
                )

                def stream_gen_router_family():
                    if chat_session_id is not None:
                        yield _sse_session_event(chat_session_id)

                    def _claimed():
                        if _auth_user:
                            _persist_user_turn(chat_session_id)

                    def _run(status_queue):
                        from api.agent_router.integration import handle_router_family_command as _hr
                        return _hr(
                            message_content,
                            session_id=chat_session_id,
                            project_path=_router_proj,
                            status_queue=status_queue,
                        )

                    for chunk in _generate_chat_stream(
                        _run,
                        chat_session_id,
                        on_result=_on_save,
                        on_claimed=_claimed if _auth_user else None,
                    ):
                        yield chunk

                return Response(
                    stream_with_context(stream_gen_router_family()),
                    mimetype='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache',
                        'X-Accel-Buffering': 'no',
                        'Connection': 'keep-alive',
                    },
                )
            from api import chat_delivery as _chat_delivery
            if chat_session_id is not None and not _chat_delivery.try_begin(chat_session_id):
                return jsonify(_chat_busy_response(chat_session_id)), 409
            _turn_token = _chat_delivery.current_turn(chat_session_id)
            try:
                if _auth_user:
                    _persist_user_turn(chat_session_id)
                from api.agent_router.integration import handle_router_family_command as _hrf2
                body = _hrf2(
                    message_content,
                    session_id=chat_session_id,
                    project_path=_router_proj,
                ) or {}
                body = dict(body)
                body.setdefault('session_id', chat_session_id)
                if _auth_user and body.get('success'):
                    saver = _make_auth_assistant_saver(chat_session_id, chat_inference_mode)
                    if saver:
                        try:
                            saver(body)
                        except Exception as _se:
                            print(f"[CHAT] router persist failed: {_se}")
                return jsonify(body)
            finally:
                if chat_session_id is not None:
                    try:
                        _chat_delivery.end(chat_session_id, turn=_turn_token)
                    except Exception:
                        pass

        # Cloud CLI slash commands require Auto/Cloud inference mode
        from api.inference_mode import is_cloud_cli_slash_command, cloud_cli_slash_blocked_message
        if is_cloud_cli_slash_command(message_content):
            blocked = cloud_cli_slash_blocked_message(chat_inference_mode)
            if blocked:
                return jsonify({
                    'success': True,
                    'response': blocked,
                    'session_id': chat_session_id,
                    'type': 'mode_blocked',
                })

        # Start a new command-dispatch chain here.  The mode check above also
        # matches every allowed cloud CLI command; making this an ``elif``
        # caused Auto/Cloud commands to skip their native handlers entirely
        # whenever ``blocked`` was false and fall through to the pipeline.
        # Harness agents live under api/agent_harness/agents/<id>/ (folder per agent)
        # plus optional drop-ins under .cuttle_global/agents, {project}/.cuttle/agents,
        # and src/data/harness_agents.
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
                _h_on_save = _make_auth_assistant_saver(
                    chat_session_id if _auth_user else None,
                    chat_inference_mode,
                    project_path=project_path,
                )
                def stream_gen_harness(_agent=_hid, _prompt=prompt, _pp=project_path):
                    if chat_session_id is not None:
                        yield _sse_session_event(chat_session_id)
                    def _h_claimed():
                        if _auth_user:
                            _persist_user_turn(chat_session_id)
                    for chunk in _generate_chat_stream(
                        lambda status_queue: _run_pinned_harness_turn(
                            _agent,
                            _prompt,
                            chat_session_id,
                            status_queue=status_queue,
                            project_path=_pp,
                            **_ident_run_kw,
                        ),
                        chat_session_id,
                        on_result=_h_on_save,
                        on_claimed=_h_claimed if _auth_user else None,
                    ):
                        yield chunk
                return Response(
                    stream_with_context(stream_gen_harness()),
                    mimetype='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache',
                        'X-Accel-Buffering': 'no',
                        'Connection': 'keep-alive',
                    },
                )
            from api import chat_delivery as _chat_delivery
            if chat_session_id is not None and not _chat_delivery.try_begin(chat_session_id):
                return jsonify(_chat_busy_response(chat_session_id)), 409
            _turn_token = _chat_delivery.current_turn(chat_session_id)
            try:
                if _auth_user:
                    _persist_user_turn(chat_session_id)
                body = _run_pinned_harness_turn(
                    _hid, prompt, chat_session_id, project_path=project_path,
                    **_ident_run_kw,
                )
                if isinstance(body, dict):
                    _emit_chat_complete_mobile(chat_session_id, body)
                if isinstance(body, dict) and chat_session_id is not None:
                    body.setdefault('session_id', chat_session_id)
                    if _auth_user and body.get('success'):
                        saver = _make_auth_assistant_saver(
                            chat_session_id,
                            chat_inference_mode,
                            project_path=project_path,
                        )
                        if saver:
                            try:
                                saver(body)
                            except Exception as _se:
                                print(f'[CHAT] harness persist failed: {_se}')
                return jsonify(body)
            finally:
                if chat_session_id is not None:
                    try:
                        _chat_delivery.end(chat_session_id, turn=_turn_token)
                    except Exception:
                        pass

        # Per-agent /cursor|/muse|/codex|/claude|/hermes elifs removed —
        # those slash commands are handled only via `_match_harness_slash` above.
        # Legacy `/cursor-cli` is rewritten to `/cursor` inside `_match_harness_slash`.

        elif message_content.startswith('/pipelines') or message_content.startswith('/pipeline'):
            return jsonify({
                'success': True,
                'response': (
                    'Visual pipeline graphs were removed. Use `/cursor`, `/codex`, '
                    'or another agent chip — or send a plain message for the router.'
                ),
                'session_id': chat_session_id,
                'type': 'pipelines_removed',
            })
        
        # Channel-level security: pairing / allowFrom for Web Chat
        if PAIRING_AVAILABLE:
            try:
                settings = get_settings_manager()
                channel_cfg = settings.get_channel_config("webchat")
                dm_policy = channel_cfg.get("dmPolicy", "open")
                allow_from = channel_cfg.get("allowFrom") or ["*"]
                identity = None
                if get_request_session_token():
                    db = get_auth_db()
                    user = db.verify_auth_session(get_request_session_token())
                    if user:
                        identity = f"web_user_{user['id']}"
                if identity is None:
                    identity = chat_session_id or request.remote_addr or "web_anon"
                pm = get_pairing_manager()
                access = pm.check_access("webchat", identity, dm_policy, allow_from, meta={"session_id": chat_session_id})
                if not access["allowed"]:
                    print(f"[API] POST /api/chat: pairing blocked - {access.get('message', 'not allowed')}")
                    return jsonify({
                        "success": False,
                        "error": "pairing_required",
                        "response": access["message"],
                        "pairing_code": access.get("pairing_code"),
                        "session_id": chat_session_id,
                    }), 403
            except Exception as e:
                print(f"[PAIRING] Web chat check error: {e}")

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
            # Local accounts are owners unless OWNER_USER_EMAIL is set and doesn't match
            _uname = (user.get('username') or '').lower()
            _email = (user.get('email') or '').lower()
            _user_is_owner = (not _OWNER_EMAIL) or (_email == _OWNER_EMAIL) or (_uname and _uname == _OWNER_EMAIL)
            _session_id = f"db_session_{chat_session_id}"
            if not wants_stream:
                if not _chat_delivery.try_begin(chat_session_id):
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
                _turn_token = _chat_delivery.current_turn(chat_session_id)
                try:
                    # Add user message only after we own the busy slot
                    db.add_message(
                        chat_session_id,
                        'user',
                        history_message,
                        metadata=_user_msg_meta,
                    )
                    res = process_message_with_bot(
                        message_content, _session_id,
                        session_kind='web_user', routing_key=f'web_user_{user["id"]}',
                        is_owner=_user_is_owner, status_queue=None,
                        inference_mode=chat_inference_mode,
                    )
                    # A reply that outlived its turn still goes back to the
                    # caller, but must not land in the newer turn's history.
                    _superseded = _chat_delivery.is_stale_turn(
                        chat_session_id, _turn_token
                    ) or _chat_delivery.is_turn_cancelled(chat_session_id)
                    if res.get('success') and not _superseded:
                        try:
                            _proj = _resolve_request_project_path({
                                **(data or {}),
                                'session_id': chat_session_id,
                            })
                        except Exception:
                            _proj = ''
                        res = _rewrite_assistant_response_actions(
                            res, _session_id, _proj or ''
                        ) or res
                        _asst_meta = _assistant_message_metadata(res)
                        db.add_message(
                            chat_session_id,
                            'assistant',
                            res.get('response', ''),
                            metadata=_asst_meta or None,
                        )
                        try:
                            from api.chat_titler import schedule_session_autoname
                            schedule_session_autoname(chat_session_id, chat_inference_mode)
                        except Exception as _te:
                            print(f"[TITLER] hook failed: {_te}")
                    body = {
                        'success': bool(res.get('success')),
                        'response': res.get('response', '') or '',
                        'session_id': chat_session_id,
                        'type': res.get('type', 'pipeline_execution'),
                    }
                    if res.get('query_id'):
                        body['query_id'] = res['query_id']
                    if res.get('report_url'):
                        body['report_url'] = res['report_url']
                    if res.get('cursor_run'):
                        body['cursor_run'] = res['cursor_run']
                    try:
                        _usage = _usage_meta_from_assistant_result(res)
                        if _usage:
                            body['usage'] = _usage
                    except Exception:
                        pass
                    return jsonify(body)
                finally:
                    try:
                        _chat_delivery.end(chat_session_id, turn=_turn_token)
                    except Exception:
                        pass
            _process_fn = lambda status_queue: process_message_with_bot(
                message_content, _session_id,
                session_kind='web_user', routing_key=f'web_user_{user["id"]}',
                is_owner=_user_is_owner, status_queue=status_queue,
                inference_mode=chat_inference_mode,
            )
            # Stream pipeline responses for status updates
            def on_save(res):
                try:
                    _proj = _resolve_request_project_path({
                        **(data or {}),
                        'session_id': chat_session_id,
                    })
                except Exception:
                    _proj = ''
                res = _rewrite_assistant_response_actions(
                    res, _session_id, _proj or ''
                ) or res
                _asst_meta = _assistant_message_metadata(res)
                db.add_message(
                    chat_session_id,
                    'assistant',
                    res.get('response', ''),
                    metadata=_asst_meta or None,
                )
                try:
                    from api.chat_titler import schedule_session_autoname
                    schedule_session_autoname(chat_session_id, chat_inference_mode)
                except Exception as _te:
                    print(f"[TITLER] hook failed: {_te}")

            def on_claimed():
                db.add_message(
                    chat_session_id,
                    'user',
                    history_message,
                    metadata=_user_msg_meta,
                )
            def stream_gen():
                # Announce session id immediately so history / URL update before the reply.
                yield _sse_session_event(chat_session_id)
                for chunk in _generate_chat_stream(
                    _process_fn, chat_session_id, on_result=on_save, on_claimed=on_claimed
                ):
                    yield chunk
            return Response(
                stream_with_context(stream_gen()),
                mimetype='text/event-stream',
                headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
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

def _no_pipeline_chat_result():
    """Chat/Discord fallback when no slash agent, router, or pipeline handled the turn."""
    return {
        'success': True,
        'type': 'no_pipeline',
        'error': 'no_pipeline',
        'response': (
            'No graph is running — Cuttle chat and Discord use slash agents '
            '(`/cursor`, `/codex`, …) or the agent router. Pick an agent chip, or send a plain message for the router.'
        ),
    }


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


@app.route('/api/local-llm/start', methods=['POST'])
@owner_required
def local_llm_start():
    """Launch llama-server on demand (detached; survives Flask restarts)."""
    from core.local_llm import launch_llamacpp_detached, local_reachable
    if local_reachable(timeout=1.5):
        return jsonify({'success': True, 'running': True, 'message': 'Local model server already running'})
    ok, err = launch_llamacpp_detached()
    if not ok:
        return jsonify({'success': False, 'running': False, 'error': err}), 400
    return jsonify({'success': True, 'running': False, 'message': 'llama-server launching — model load can take 1–2 minutes'})


@app.route('/api/local-llm/stop', methods=['POST'])
@owner_required
def local_llm_stop():
    """Stop llama-server (e.g. when the user asks Cuttle to close the local model)."""
    from core.local_llm import stop_llamacpp, local_reachable
    stop_llamacpp()
    time.sleep(1.0)
    still_up = local_reachable(timeout=1.5)
    return jsonify({'success': not still_up, 'running': still_up})


@app.route('/api/health', methods=['GET'])
def health_check():
    """Diagnostic health (Python, cwd, env flags, CLI / local-LLM probes).

    Not a liveness probe. The daemon watchdog uses GET /api/status instead —
    this handler can run `claude --version` (3s) then `wsl which claude` (5s),
    which exceeds the watchdog's 5s HTTP timeout and caused false restarts.
    """
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

    cursor_exe = bool(shutil.which("cursor"))
    try:
        from managers.settings_manager import get_settings_manager
        plimits = get_settings_manager().get_pipeline_limits()
    except Exception:
        plimits = {"max_tool_nodes_per_execution": 64}

    payload = {
        'status': 'healthy',
        'service': 'cuttle',
        'bot_available': PIPELINE_AVAILABLE,
        'active_sessions': len(chat_sessions),
        'hostname': os.environ.get('COMPUTERNAME', platform.node()),
        'version': '1.0.0',
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
        'pipeline_limits': plimits,
    }
    if not (request_is_loopback() or is_owner_user(current_user())):
        payload.pop('cwd', None)
        payload.pop('pipeline_limits', None)
        payload['env'] = {'redacted': True}
        payload['active_sessions'] = None
    return jsonify(payload)


@app.route('/api/supervised/tasks/<task_id>/control', methods=['POST'])
def supervised_task_control(task_id):
    """Typed, model-free controls for the Activity disclosure."""
    try:
        from api.agent_router.supervised.control import session_owns_task
        from api.agent_router.supervised.delivery import public_indicator
        from api.agent_router.supervised.orchestrator import cancel_task, user_followup
        from api.agent_router.supervised.store import load_task

        data = request.get_json(silent=True) or {}
        action = str(data.get('action') or '').strip().lower()
        task = load_task(task_id)
        if not task:
            return jsonify({'success': False, 'error': 'Task not found.'}), 404
        token = get_request_session_token()
        db = get_auth_db()
        user = db.verify_auth_session(token) if token else None
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized.'}), 401
        parent = str(task.parent_session_id or '')
        bare = parent[len('db_session_'):] if parent.startswith('db_session_') else parent
        try:
            session_id = int(bare)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Task has no authenticated session.'}), 403
        if not db.get_chat_session(session_id, user['id']):
            return jsonify({'success': False, 'error': 'Task belongs to another session.'}), 403
        if not session_owns_task(session_id, task):
            return jsonify({'success': False, 'error': 'Task belongs to another session.'}), 403
        control_id = str(data.get('control_request_id') or f"ui_{task_id}_{action}_{uuid.uuid4().hex[:10]}")
        if action == 'status':
            result = {'success': True, 'acknowledgement': 'Activity refreshed.'}
        elif action == 'followup':
            instruction = str(data.get('instruction') or '').strip()
            if not instruction:
                return jsonify({'success': False, 'error': 'Instruction is required.'}), 400
            result = user_followup(session_id, instruction, control_request_id=control_id)
            result['acknowledgement'] = 'Instruction added.'
        elif action == 'cancel':
            result = cancel_task(session_id, control_request_id=control_id)
            result['acknowledgement'] = 'Task cancelled.'
        else:
            return jsonify({'success': False, 'error': 'Unknown control action.'}), 400
        latest = load_task(task_id) or task
        result['supervised_task'] = public_indicator(latest)
        result['control_request_id'] = control_id
        return jsonify(result)
    except Exception as e:
        print(f'[CHAT] supervised typed control failed: {e}', flush=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/supervised/tasks/<task_id>/runs/<run_id>/raw', methods=['GET'])
def supervised_task_raw_artifact(task_id, run_id):
    """Session/task-scoped read-only viewer for durable worker raw output.

    Validates ownership and confines reads to the supervised task runs directory.
    """
    tid = str(task_id or '').strip()
    rid = str(run_id or '').strip()
    if not tid or not rid:
        return jsonify({'success': False, 'error': 'task_id and run_id required'}), 400
    if '/' in tid or '\\' in tid or '..' in tid or '/' in rid or '\\' in rid or '..' in rid:
        return jsonify({'success': False, 'error': 'invalid id'}), 400

    session_token = get_request_session_token()
    if not session_token:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    except Exception as e:
        return jsonify({'success': False, 'error': f'Unauthorized: {e}'}), 401

    try:
        from api.agent_router.supervised.store import load_task, store_dir
        from api.agent_router.supervised.control import session_owns_task

        task = load_task(tid)
        if not task:
            return jsonify({'success': False, 'error': 'Task not found'}), 404
        parent = str(task.parent_session_id or '')
        bare = parent[len('db_session_'):] if parent.startswith('db_session_') else parent
        try:
            db_sid = int(bare)
        except (TypeError, ValueError):
            db_sid = None
        if db_sid is None or not db.get_chat_session(db_sid, user['id']):
            return jsonify({'success': False, 'error': 'Forbidden'}), 403
        if not session_owns_task(db_sid, task) and not session_owns_task(parent, task):
            return jsonify({'success': False, 'error': 'Forbidden'}), 403

        root = (store_dir() / tid / 'runs').resolve()
        path = (root / f'{rid}.raw.txt').resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return jsonify({'success': False, 'error': 'path rejected'}), 400
        if not path.is_file():
            return jsonify({'success': False, 'error': 'artifact not found'}), 404
        size = path.stat().st_size
        max_bytes = 2_000_000
        data = path.read_bytes()[:max_bytes]
        text = data.decode('utf-8', errors='replace')
        as_download = (request.args.get('download') or '').strip() in ('1', 'true', 'yes')
        if as_download or request.args.get('format') == 'text':
            from flask import Response as FlaskResponse

            return FlaskResponse(
                text,
                mimetype='text/plain; charset=utf-8',
                headers={
                    'Content-Disposition': (
                        f'attachment; filename="{tid}_{rid}.raw.txt"'
                        if as_download
                        else 'inline'
                    ),
                    'X-Content-Type-Options': 'nosniff',
                },
            )
        return jsonify({
            'success': True,
            'task_id': tid,
            'run_id': rid,
            'bytes': size,
            'truncated': size > max_bytes,
            'content': text,
            'content_type': 'text/plain',
        })
    except Exception as e:
        print(f'[CHAT] supervised raw artifact failed: {e}', flush=True)
        return jsonify({'success': False, 'error': 'failed to read artifact'}), 500


@app.route('/api/flask/restart/status', methods=['GET'])
def api_flask_restart_status():
    """Durable restart status + active work (survives Flask replacement)."""
    _user, err = loopback_or_authenticated()
    if err:
        return err
    try:
        from api.flask_restart import status_snapshot, mark_outcome_visible, build_completion_message

        snap = status_snapshot()
        st = snap.get('status') or {}
        consume = (request.args.get('consume') or '').strip().lower() in ('1', 'true', 'yes')
        rid = st.get('restart_id')
        if consume and rid and st.get('state') in ('healthy', 'failed', 'timed_out'):
            if not st.get('client_notified'):
                mark_outcome_visible(str(rid))
                snap['completion_message'] = build_completion_message(st)
                snap['status'] = {**st, 'client_notified': True, 'outcome_visible': True}
        gen = os.environ.get('CUTTLE_FLASK_GENERATION')
        snap['live_flask_pid'] = os.getpid()
        snap['live_generation'] = int(gen) if gen and str(gen).isdigit() else None
        try:
            from api.flask_restart import shared_restart_form_id
            snap['restart_form_id'] = shared_restart_form_id(
                snap['live_generation'] if snap['live_generation'] is not None else None
            )
        except Exception:
            snap['restart_form_id'] = None
        return jsonify(snap)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/flask/restart', methods=['POST'])
@owner_required
def api_flask_restart():
    """Request a daemon-owned Flask restart (drain-first)."""
    try:
        from api.flask_restart import request_restart

        data = request.get_json(silent=True) or {}
        mode = (data.get('mode') or data.get('action') or 'graceful').strip().lower()
        session_id = data.get('session_id')
        confirm = bool(data.get('confirm') or data.get('force_confirm') or data.get('yes'))
        notify_raw = data.get('chat_notify')
        if notify_raw is None:
            notify_raw = data.get('notify')
        if notify_raw is None:
            chat_notify = True
        elif isinstance(notify_raw, str):
            chat_notify = notify_raw.strip().lower() not in ('0', 'false', 'no', 'off', 'card', 'none')
        else:
            chat_notify = bool(notify_raw)
        result = request_restart(
            mode=mode,
            session_id=session_id,
            user_source=str(data.get('source') or 'api'),
            force_confirm=confirm,
            triggering_task_id=data.get('task_id'),
            chat_notify=chat_notify,
        )
        code = 200 if result.get('success') or result.get('needs_confirm') or result.get('state') == 'rejected' else 400
        if result.get('needs_confirm'):
            code = 409
        return jsonify(result), code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'type': 'flask_restart'}), 500


@app.route('/api/flask/restart/notify', methods=['POST'])
@loopback_required
def api_flask_restart_notify():
    """Daemon → new Flask: persist post-restart completion into the requesting chat once."""
    try:
        from api.flask_restart import read_status, write_status, build_completion_message

        data = request.get_json(silent=True) or {}
        rid = str(data.get('restart_id') or '').strip()
        sid = data.get('session_id')
        msg = (data.get('message') or '').strip()
        st = read_status()
        if not rid or str(st.get('restart_id')) != rid:
            return jsonify({'success': False, 'error': 'restart_id mismatch'}), 400
        if st.get('post_restart_message_written'):
            return jsonify({'success': True, 'deduped': True})
        if st.get('chat_notify') is False:
            # Card-driven restart: the card polls status and shows the outcome,
            # so a completion bubble would just be noise in the transcript.
            write_status({**st, 'post_restart_message_written': True})
            return jsonify({'success': True, 'skipped': 'chat_notify_disabled'})
        if not msg:
            msg = build_completion_message(st)
        bare = str(sid or st.get('session_id') or '').strip()
        if bare.startswith('db_session_'):
            bare = bare[len('db_session_'):]
        try:
            sid_int = int(bare)
        except (TypeError, ValueError):
            write_status({**st, 'post_restart_message_written': True})
            return jsonify({'success': True, 'skipped': 'no session'})
        db = get_auth_db()
        db.add_message(
            sid_int,
            'assistant',
            msg,
            metadata={
                'type': 'flask_restart',
                'restart_id': rid,
                'kind': 'complete',
                'delivery': 'persisted',
            },
        )
        write_status({**st, 'post_restart_message_written': True})
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/restart', methods=['POST'])
@owner_required
def api_restart_legacy():
    """Legacy UI endpoint → graceful Flask restart (daemon-owned)."""
    try:
        from api.flask_restart import request_restart

        data = request.get_json(silent=True) or {}
        result = request_restart(
            mode='graceful',
            session_id=data.get('session_id'),
            user_source='legacy_ui',
            force_confirm=False,
        )
        return jsonify({
            'success': bool(result.get('success')),
            'message': result.get('response') or result.get('error'),
            **{k: v for k, v in result.items() if k not in ('success',)},
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
            LAN_HTTP_PORT,
            LAN_PHONE_HTTPS_PORT,
            LAN_HTTP_FALLBACK_PORT,
            lan_phone_http_fallback_url,
            wifi_network_category,
            get_recent_lan_probes,
            get_pc_subnet_hint,
        )
        lan_enabled = is_lan_access_enabled()
        lan_ip = get_lan_ipv4() if lan_enabled else None
        portal_https = lan_portal_url(8080, lan_ip) if lan_enabled else None
        portal_phone = lan_phone_portal_url(LAN_PHONE_HTTPS_PORT, lan_ip) if lan_enabled else None
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
            'localhost_url': 'https://127.0.0.1:8080',
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
                'On your phone open portal_url_phone (HTTPS on 8888). Accept the self-signed cert once. '
                f'Or use portal_url_http_fallback (plain HTTP on port {LAN_HTTP_FALLBACK_PORT}). '
                'Same Wi‑Fi required.'
                if lan_enabled and portal_phone else
                'LAN access is off. Set discovery.lan_access_enabled in settings.json, then restart Cuttle.'
            ),
            'setup': (
                [
                    f'Phone (HTTPS): {portal_phone or "(no LAN IP)"}',
                    f'Phone (HTTP fallback): {portal_http_fb or "(no LAN IP)"}',
                    f'Phone IP must be in: {subnet.get("phone_ip_must_be_in", "same subnet as PC")}',
                    'PC browser: https://127.0.0.1:8080',
                    'After a phone attempt, refresh this URL — recent_phone_hits should list your phone.',
                    'If phone cannot connect: run enable_lan_firewall.bat as Administrator, then restart Cuttle.',
                    'Router fix: disable AP isolation / client isolation / use main Wi‑Fi not guest.',
                    f'Your Wi‑Fi is "{net_cat or "unknown"}" — set to Private in Windows Settings → Network → Wi‑Fi.',
                ]
                if lan_enabled else []
            ),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Home automation (provider socket + Govee lighting) -----------------------

@app.route('/api/home-automation/providers', methods=['GET'])
@owner_required
def api_home_automation_providers():
    """Catalog of home-automation plugs (Govee live, Nest stub, …). No secrets."""
    try:
        from api.home_automation_socket import list_all_devices, list_providers

        include_devices = str(request.args.get('devices') or '').lower() in ('1', 'true', 'yes')
        providers = [p.to_dict() for p in list_providers(include_devices=include_devices)]
        payload = {
            'success': True,
            'providers': providers,
        }
        if include_devices:
            payload['devices'] = [d.to_dict() for d in list_all_devices()]
        return jsonify(payload)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/home-automation/themes', methods=['GET'])
@owner_required
def api_home_automation_themes():
    try:
        from managers.home_automation import THEME_QUICK, GOVEE_DEVICES, load_schedule

        sched = load_schedule()
        return jsonify(
            {
                'success': True,
                'quickThemes': THEME_QUICK,
                'devices': GOVEE_DEVICES,
                'schedule': sched,
                'hasApiKey': bool(os.environ.get('GOVEE_API_KEY') or os.environ.get('Govee_API_Key')),
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/home-automation/apply-theme', methods=['POST'])
@owner_required
def api_home_automation_apply_theme():
    try:
        from managers.home_automation import apply_theme

        data = request.get_json() or {}
        theme = (data.get('theme') or '').strip()
        if not theme:
            return jsonify({'success': False, 'error': 'theme is required'}), 400
        result = apply_theme(theme)
        if result.get('rateLimited'):
            code = 429
        else:
            code = 200 if result.get('success') else 500
        return jsonify(result), code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/home-automation/apply-theme-stream', methods=['POST'])
@owner_required
def api_home_automation_apply_theme_stream():
    """NDJSON stream of theme apply progress + final result."""
    import json as json_lib

    try:
        from managers.home_automation import iter_apply_theme_stream_events

        data = request.get_json() or {}
        theme = (data.get('theme') or '').strip()
        if not theme:
            return jsonify({'success': False, 'error': 'theme is required'}), 400

        def gen():
            for ev in iter_apply_theme_stream_events(theme):
                yield json_lib.dumps(ev, default=str) + '\n'

        return Response(
            stream_with_context(gen()),
            mimetype='application/x-ndjson',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            },
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/home-automation/apply-schedule-now', methods=['POST'])
@owner_required
def api_home_automation_apply_schedule_now():
    """Apply the theme for the current time-of-day window immediately (same as daemon would, no skip)."""
    try:
        from managers.home_automation import apply_scheduled_theme_now

        result = apply_scheduled_theme_now()
        if not result.get("success") and result.get("error") == "No schedule period matches the current time":
            return jsonify(result), 400
        if result.get("rateLimited"):
            code = 429
        else:
            code = 200 if result.get("success") else 500
        return jsonify(result), code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/home-automation/apply-schedule-now-stream', methods=['POST'])
@owner_required
def api_home_automation_apply_schedule_now_stream():
    """NDJSON stream of schedule apply progress + final result (same outcome as apply-schedule-now)."""
    import json as json_lib

    try:
        from managers.home_automation import iter_apply_scheduled_theme_stream_events

        def gen():
            for ev in iter_apply_scheduled_theme_stream_events():
                yield json_lib.dumps(ev, default=str) + '\n'

        return Response(
            stream_with_context(gen()),
            mimetype='application/x-ndjson',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            },
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/home-automation/schedule', methods=['GET', 'POST'])
@owner_required
def api_home_automation_schedule():
    try:
        from managers.home_automation import load_schedule, save_schedule, default_schedule

        if request.method == 'GET':
            return jsonify({'success': True, 'schedule': load_schedule()})
        data = request.get_json() or {}
        sched = data.get('schedule')
        if not isinstance(sched, dict):
            return jsonify({'success': False, 'error': 'schedule object required'}), 400
        base = load_schedule()
        if 'autoApplyEnabled' in sched:
            base['autoApplyEnabled'] = bool(sched['autoApplyEnabled'])
        if 'reassertThemeMinutes' in sched:
            try:
                base['reassertThemeMinutes'] = max(0, min(int(sched['reassertThemeMinutes']), 24 * 60))
            except (TypeError, ValueError):
                pass
        if isinstance(sched.get('periods'), dict):
            for k, v in sched['periods'].items():
                if isinstance(v, dict) and k in base.get('periods', {}):
                    base['periods'][k].update(v)
                elif isinstance(v, dict):
                    if 'periods' not in base:
                        base['periods'] = {}
                    base['periods'][k] = v
        save_schedule(base)
        return jsonify({'success': True, 'schedule': base})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/home-automation/auto-tick', methods=['POST'])
@owner_required
def api_home_automation_auto_tick():
    """Optional: run scheduled lighting check (daemon calls in-process instead)."""
    try:
        from managers.home_automation import maybe_apply_scheduled_theme

        return jsonify(maybe_apply_scheduled_theme())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/home-automation/status', methods=['GET'])
@owner_required
def api_home_automation_status():
    """Live schedule heartbeat (daemon) + Govee device list with online/power/brightness."""
    try:
        from managers.home_automation import (
            get_schedule_daemon_heartbeat_status,
            list_govee_devices_with_live_state,
            load_schedule,
        )

        sched = load_schedule()
        hb = get_schedule_daemon_heartbeat_status()
        has_key = bool(os.environ.get('GOVEE_API_KEY') or os.environ.get('Govee_API_Key'))
        if not has_key:
            devices = []
            dev_err = None
            rate_limited = False
        else:
            dev_block = list_govee_devices_with_live_state()
            devices = dev_block.get('devices') or []
            dev_err = dev_block.get('error')
            rate_limited = bool(dev_block.get('rateLimited'))
        return jsonify(
            {
                'success': True,
                'hasApiKey': has_key,
                'scheduleAutoApply': bool(sched.get('autoApplyEnabled', False)),
                'heartbeat': hb,
                'devices': devices,
                'devicesError': dev_err,
                'devicesRateLimited': rate_limited,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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


@app.route('/api/agent-context', methods=['GET'])
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
                path = _resolve_request_project_path({
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


@app.route('/api/agent-context/compact', methods=['POST'])
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
        _user, _sid, err = _require_session_actor(session)
        if err:
            return err
        path = (data.get('path') or data.get('project_path') or '').strip()
        if not path:
            try:
                path = _resolve_request_project_path(data if isinstance(data, dict) else {}) or ''
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


@app.route('/api/ollama-models', methods=['GET'])
def ollama_models():
    """Return installed local-LLM model names for the node editor dropdown.

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

@app.route('/api/wizard/status', methods=['GET'])
@owner_required
def wizard_status():
    """Return setup wizard status: which steps are done (default pipeline, API keys, channels)."""
    try:
        from managers.settings_manager import get_settings_manager
        settings = get_settings_manager()
        default_pipeline_set = True  # graphs are optional; chat uses slash agents
        api_keys_set = bool(os.environ.get('OPENAI_API_KEY') or os.environ.get('API_KEY') or os.environ.get('ANTHROPIC_API_KEY'))
        channels_cfg = (settings.get_setting('channels') or {})
        webchat_cfg = channels_cfg.get('webchat') or {}
        channels_configured = bool(webchat_cfg.get('allowFrom'))
        next_step = None
        if not api_keys_set:
            next_step = 'api_keys'
        elif not channels_configured:
            next_step = 'channels'
        else:
            next_step = 'done'
        return jsonify({
            'success': True,
            'steps': {
                'default_pipeline': default_pipeline_set,
                'api_keys': api_keys_set,
                'channels': channels_configured,
            },
            'next_step': next_step,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# Pairing API (channel-level security: approve codes, list pending)
# ============================================================================

@app.route('/api/pairing/approve', methods=['POST'])
@limiter.limit("5 per minute")
@owner_required
def pairing_approve():
    """Approve a pairing code. Adds identity to allowlist."""
    if not PAIRING_AVAILABLE:
        return jsonify({'success': False, 'error': 'Pairing not available'}), 503
    try:
        data = request.get_json() or {}
        code = data.get('code', '').strip()
        if not code:
            return jsonify({'success': False, 'error': 'No code provided'}), 400
        pm = get_pairing_manager()
        ok, result = pm.approve(code)
        if ok:
            return jsonify({'success': True, 'approved': result})
        return jsonify({'success': False, 'error': result}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/pairing/pending', methods=['GET'])
@limiter.limit("5 per minute")
@owner_required
def pairing_pending():
    """List pending pairing requests (for admin UI)."""
    if not PAIRING_AVAILABLE:
        return jsonify({'success': False, 'error': 'Pairing not available'}), 503
    try:
        pm = get_pairing_manager()
        return jsonify({'success': True, 'pending': pm.list_pending()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/pairing/status', methods=['GET'])
@owner_required
def pairing_status():
    """Get pairing/allowlist status for current channel (query: channel, identity)."""
    if not PAIRING_AVAILABLE:
        return jsonify({'success': False, 'error': 'Pairing not available'}), 503
    try:
        channel = request.args.get('channel', 'webchat')
        identity = request.args.get('identity', '')
        settings = get_settings_manager()
        channel_cfg = settings.get_channel_config(channel)
        dm_policy = channel_cfg.get('dmPolicy', 'open')
        allow_from = channel_cfg.get('allowFrom') or ['*']
        pm = get_pairing_manager()
        allowed = pm.is_allowed(channel, identity, dm_policy, allow_from)
        return jsonify({
            'success': True,
            'channel': channel,
            'identity': identity,
            'allowed': allowed,
            'dmPolicy': dm_policy,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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


@app.route('/api/agents', methods=['GET'])
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
        return jsonify({
            'success': True,
            'agents': agents,
            'count': len(agents),
            'roots': discovery_roots(project_path),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/agent-defaults/<agent_id>', methods=['GET'])
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


@app.route('/api/agent-defaults/<agent_id>', methods=['POST'])
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
        if aid == 'codex':
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
                from api.agent_harness.agents.codex.model_catalog import codex_efforts_for_model

                supported = codex_efforts_for_model(target_model or None)
                if target_effort not in supported:
                    model_note = (
                        f" for `{target_model}`"
                        if target_model
                        else " across all Codex models"
                    )
                    values = ', '.join(supported) or 'none verified'
                    return jsonify({
                        'success': False,
                        'error': (
                            f"Codex effort `{target_effort}` is not supported{model_note}. "
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


@app.route('/api/agent-router/options', methods=['GET'])
@owner_required
def get_agent_router_options_api():
    """Modes, models and agents for the Router editor page (no CLI calls).

    Owner-only: the payload includes the live ``current`` provider config
    (endpoints, targets, fallbacks), same sensitivity class as
    ``GET /api/router/config``.
    """
    try:
        from api.agent_router.config import load_router_config
        from api.agent_router.registry import (
            CURSOR_KNOWN_MODELS,
            KNOWN_AGENTS,
            OPENAI_ROUTER_MODELS,
        )
        from api.agent_router.types import VALID_MODES
        from scripts.utilities.muse_cli_tool import MUSE_KNOWN_MODELS

        cfg = load_router_config()
        agent_models = {
            'cursor': sorted(CURSOR_KNOWN_MODELS),
            'codex': ['gpt-5-codex', 'gpt-5', 'o4-mini'],
            'claude': ['sonnet', 'opus', 'haiku'],
            'opencode': [],
            'muse': [m['id'] for m in MUSE_KNOWN_MODELS],
            'hermes': ['default'],
        }
        try:
            from api.agent_harness.catalog import list_agent_manifests

            # Union (not override) — manifest lists are often shorter than the
            # static known-model sets (e.g. Cursor's manifest lists 4 of 12).
            for m in list_agent_manifests():
                merged = set(agent_models.get(m.id, [])) | set(m.models or [])
                agent_models[m.id] = sorted(merged)
        except Exception:
            pass
        return jsonify({
            'success': True,
            'modes': sorted(VALID_MODES),
            'api_models': sorted(set(OPENAI_ROUTER_MODELS) | {'jev', 'jev-latest'}),
            'agents': [
                {'id': 'jev', 'label': 'Jev (TypeSafe System One)'},
            ] + [
                {'id': aid, 'label': info.get('label') or aid}
                for aid, info in sorted(KNOWN_AGENTS.items())
            ],
            'agent_models': {**agent_models, 'jev': ['jev-latest']},
            'current': {
                'mode': cfg.provider.mode,
                'api_model': cfg.provider.api_model,
                'local_model': cfg.provider.local_model,
                'local_endpoint': cfg.provider.local_endpoint,
                'agent_id': cfg.provider.agent_id,
                'agent_model': cfg.provider.agent_model,
                'default_target': cfg.default_target.to_dict(),
                'escalation_target': cfg.escalation_target.to_dict(),
                'fallbacks': [t.to_dict() for t in cfg.fallbacks.ordered],
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/router/config', methods=['GET'])
@owner_required
def get_router_config_api():
    """Full router state for the Router editor page (config + use cases + demotions + rage)."""
    try:
        from api.agent_router.config import load_router_config
        from api.agent_router.drift import active_demotions
        from api.agent_router.frustration import rage_config_to_dict
        from api.agent_router.use_cases import load_use_cases

        cfg = load_router_config()
        return jsonify({
            'success': True,
            'config': cfg.to_dict(),
            'use_cases': load_use_cases(),
            'demotions': active_demotions(),
            'rage': rage_config_to_dict(),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/router/config', methods=['PUT'])
@owner_required
def put_router_config_api():
    """Update router config and/or the use-case table (same settings the agents edit)."""
    try:
        from api.agent_router.config import load_router_config, update_router_config
        from api.agent_router.drift import active_demotions
        from api.agent_router.frustration import rage_config_to_dict
        from api.agent_router.use_cases import load_use_cases, save_use_cases

        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({'success': False, 'error': 'Body must be a JSON object'}), 400

        allowed = (
            'mode', 'api_model', 'local_model', 'local_endpoint',
            'agent_id', 'agent_model', 'default_target', 'escalation_target', 'fallbacks',
        )
        updates = {k: body[k] for k in allowed if k in body}

        # Full-config saves (page / agents) always carry every provider field.
        # Drop the empty ones that belong to a *different* mode than the one
        # being saved, or validation would demand e.g. a local model while
        # saving mode=api.
        try:
            active_mode = str(updates.get('mode') or '').strip().lower()
            if not active_mode:
                active_mode = str(load_router_config().provider.mode or 'off').strip().lower()
            mode_fields = {
                'api': ('api_model',),
                'local': ('local_endpoint', 'local_model'),
                'agent': ('agent_id', 'agent_model'),
            }
            for m, fields in mode_fields.items():
                if m == active_mode:
                    continue
                for f in fields:
                    if f in updates and not str(updates[f] or '').strip():
                        del updates[f]
        except Exception:
            pass

        error = None
        if updates:
            _cfg, error = update_router_config(**updates)

        use_cases = body.get('use_cases')
        if error is None and use_cases is not None:
            if not isinstance(use_cases, list):
                error = 'use_cases must be a list'
            else:
                _saved, uc_err = save_use_cases(use_cases)
                error = uc_err

        if error is None and body.get('rage') is not None:
            from api.agent_router.frustration import save_rage_config

            _saved, rage_err = save_rage_config(body.get('rage'))
            error = rage_err

        cfg = load_router_config()
        return jsonify({
            'success': error is None,
            'error': error,
            'config': cfg.to_dict(),
            'use_cases': load_use_cases(),
            'demotions': active_demotions(),
            'rage': rage_config_to_dict(),
        }), (200 if error is None else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _router_health_payload(report):
    from api.agent_router.outcomes import metrics_summary

    demoted = report.get('demotions') if isinstance(report, dict) else None
    return {
        'success': True,
        'metrics': metrics_summary(days=7),
        'report': report,
        'demotions': demoted or {},
        'generated_at': int(time.time()),
    }


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


@app.route('/api/router/health', methods=['GET'])
@owner_required
def get_router_health_api():
    """7d metrics + current drift report + active demotions (read-only)."""
    try:
        from api.agent_router.drift import active_demotions, compute_drift

        return jsonify(_router_health_payload({
            'report': compute_drift(),
            'demotions': active_demotions(),
            'changes': [],
        }))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/router/health/refresh', methods=['POST'])
@owner_required
def refresh_router_health_api():
    """Re-evaluate drift and apply/clear temporary demotions."""
    try:
        from api.agent_router.drift import evaluate_drift

        report = evaluate_drift()
        return jsonify(_router_health_payload(report))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/router/demotion/clear', methods=['POST'])
@owner_required
def clear_router_demotion_api():
    """Manually re-promote a demoted target."""
    try:
        from api.agent_router.drift import clear_demotion

        body = request.get_json(silent=True) or {}
        agent = str(body.get('agent') or '').strip()
        model = str(body.get('model') or '').strip()
        if not agent:
            return jsonify({'success': False, 'error': 'agent is required'}), 400
        removed = clear_demotion(agent, model)
        return jsonify({'success': True, 'removed': removed})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# Sessions API (agent-to-agent: list pipelines/sessions, send to pipeline/session)
# ============================================================================

@app.route('/api/sessions/list', methods=['GET'])
@owner_required
def sessions_list():
    """List active pipelines (running) and session IDs (chat_sessions). For agent-to-agent orchestration."""
    try:
        pipelines = []
        for name, info in retired_pipeline_registry.items():
            triggers = info.get('pipeline_data', {}).get('triggers', [])
            pipelines.append({'name': name, 'triggers': [t.get('type') for t in triggers]})
        sessions = list(chat_sessions.keys())
        return jsonify({
            'success': True,
            'pipelines': pipelines,
            'sessions': sessions,
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

@app.route('/api/start-launcher', methods=['POST'])
def start_launcher():
    return _legacy_process_control_gone_response()

@app.route('/api/start-webapi', methods=['POST'])
def start_webapi():
    return _legacy_process_control_gone_response()

@app.route('/api/start-discord', methods=['POST'])
def start_discord():
    return _legacy_process_control_gone_response()

@app.route('/api/stop-launcher', methods=['POST'])
def stop_launcher():
    return _legacy_process_control_gone_response()

@app.route('/api/stop-webapi', methods=['POST'])
def stop_webapi():
    return _legacy_process_control_gone_response()

@app.route('/api/stop-discord', methods=['POST'])
def stop_discord():
    return _legacy_process_control_gone_response()

# ==================== NODE EDITOR API ENDPOINTS ====================

def _normalize_tools_config(tools_config) -> Optional[dict]:
    """Split unified tools payload: MCP section vs bundled CLI/API toolsets."""
    if tools_config is None:
        return None
    if not isinstance(tools_config, dict):
        return None
    if 'mcp' in tools_config or 'bundledCli' in tools_config or 'bundledApi' in tools_config:
        return {
            'mcp': tools_config.get('mcp'),
            'bundledCli': tools_config.get('bundledCli'),
            'bundledApi': tools_config.get('bundledApi'),
        }
    return {'mcp': dict(tools_config), 'bundledCli': None, 'bundledApi': None}


def _mcp_cuttle_server_enabled() -> bool:
    """Cuttle does not host an MCP tool server. Guest CLIs keep their own MCP."""
    return False


def _fetch_mcp_tools_openai_format(mcp_config: Optional[dict]) -> list:
    """No in-process Cuttle MCP tools. Guest harnesses own MCP."""
    return []


def _get_mcp_tools_openai_format(tools_config=None):
    """OpenAI-format tool list. Cuttle does not expose an MCP tool catalog."""
    return _fetch_mcp_tools_openai_format({} if tools_config is None else tools_config)


def _get_combined_openai_tools(tools_config) -> list:
    """MCP tools plus bundled CLI/API tools."""
    norm = _normalize_tools_config(tools_config)
    from api.bundled_llm_tools import bundled_tool_specs_openai

    out = []
    if norm:
        mcp = norm.get('mcp')
        if mcp is not None:
            out.extend(_fetch_mcp_tools_openai_format(mcp))
        out.extend(bundled_tool_specs_openai(norm.get('bundledCli'), norm.get('bundledApi')))
    return out


def _invoke_llm_tool(name: str, arguments: dict, tools_config, session_id: Optional[str]) -> str:
    """Dispatch a single tool call from OpenAI/Anthropic/Ollama tool rounds."""
    from api.bundled_llm_tools import BUNDLED_TOOL_NAMES, invoke_bundled_tool
    if name in BUNDLED_TOOL_NAMES:
        norm = _normalize_tools_config(tools_config)
        return invoke_bundled_tool(
            name,
            arguments or {},
            (norm or {}).get('bundledCli'),
            (norm or {}).get('bundledApi'),
            project_root=str(actual_project_root),
            session_id=session_id,
        )
    return f"Error: Cuttle does not host MCP tools (tool {name}). Use a guest harness MCP or python -m api.*."


def _try_direct_file_tool_fulfillment(prompt: str) -> Optional[str]:
    """Was MCP write_file/read_file fallback. Cuttle does not host those tools."""
    return None


def _try_direct_file_tool_fulfillment_from_texts(*chunks: Optional[str]) -> Optional[str]:
    """First matching chunk; file-tool fallback is disabled."""
    for c in chunks:
        if not c or not isinstance(c, str):
            continue
        out = _try_direct_file_tool_fulfillment(c.strip())
        if out:
            return out
    return None


def _build_cuttle_trace_block(query_id: str) -> str:
    """
    Collapsible appendix for web chat: which LLM/tool steps actually ran, plus a verification disclaimer.
    Rendered by chat UI as <cuttle_trace> (see formatMessage in chat_page.js).
    """
    if not query_id:
        return ""
    try:
        from api.query_tracker import get_query_tracker
        t = get_query_tracker(query_id)
        if not t or t.query_id != query_id or not getattr(t, "execution_data", None):
            return ""
        ed = t.execution_data
    except Exception:
        return ""
    import html as html_mod

    lines = [
        f"Query {query_id} — full report: /query_log.html?id={query_id}",
        "",
        "WHAT ACTUALLY RAN (query log; not the model’s story)",
    ]
    llms = ed.get("llm_calls") or []
    for i, c in enumerate(llms, 1):
        nid = c.get("node_id")
        model = c.get("model") or ""
        prev = (c.get("response_preview") or "").replace("\n", " ")[:320]
        lines.append(f"{i}. LLM node_id={nid} model={model}")
        lines.append(f"   preview: {prev}")
    tools = ed.get("tool_calls") or []
    if tools:
        lines.append("")
        lines.append("TOOLS INVOKED")
        for tc in tools:
            lines.append(f"- {tc.get('tool_name', '?')}")
    lines.append("")
    lines.append(
        "VERIFICATION: The assistant message may claim work that was not performed. "
        "Only the tools above actually ran. For security or code claims, confirm with git diff or open the named files."
    )
    body = "\n".join(lines)
    return f"<cuttle_trace>\n{html_mod.escape(body)}\n</cuttle_trace>"


def _record_failed_llm_for_query(
    query_id,
    node_id,
    model: str,
    start_time: float,
    error_message: str,
    prompt_preview: str = "",
):
    """Append a failed LLM row to the active query report (only when tracker matches query_id)."""
    if not query_id:
        return
    try:
        from api.query_tracker import get_query_tracker
        tracker = get_query_tracker(query_id)
        if not tracker or tracker.query_id != query_id or not tracker.execution_data:
            return
        end_time = time.time()
        err = (error_message or "")[:800]
        pp = (prompt_preview or "")[:400]
        tracker.add_llm_call(
            model=model or "unknown",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            start_time=start_time,
            end_time=end_time,
            success=False,
            response_preview=err,
            prompt_preview=pp,
            node_id=node_id,
            notes="LLM request failed",
        )
    except Exception as ex:
        print(f"[QUERY] Error recording failed LLM call: {ex}")


@app.route('/api/llm-request', methods=['POST'])
@owner_required
def llm_request():
    """Execute an LLM request. Supports toolsConfig from MCP toolset for tool-calling."""
    import time
    start_time = time.time()
    
    try:
        data = request.get_json()
        
        node_type = data.get('nodeType', 'llm-openai')
        provider = data.get('provider', None)  # Provider for unified llm node
        model = data.get('model', 'gpt-4o-mini')
        prompt = data.get('prompt', '')
        system_prompt = data.get('systemPrompt') or 'You are a helpful AI assistant.'
        tools_config = data.get('toolsConfig')
        knowledge_inputs = data.get('knowledgeInputs') if isinstance(data.get('knowledgeInputs'), list) else []
        try:
            from api.cuttle_ui_capabilities import cuttle_ui_system_addon

            system_prompt = (system_prompt or '').rstrip() + cuttle_ui_system_addon()
        except Exception as _ui_cap_err:
            print(f"[LLM] UI capabilities inject skipped: {_ui_cap_err}")
        # When tools are available, instruct the model to use them and reason about results
        if _get_combined_openai_tools(tools_config):
            try:
                from core.mcp_tool_coaching import mcp_tools_system_prompt_suffix
                system_prompt = (system_prompt or '').rstrip() + mcp_tools_system_prompt_suffix()
            except Exception:
                system_prompt = (system_prompt or '').rstrip() + (
                    "\n\nYou have access to tools. Call them via the tool API; do not imitate shell commands."
                )
        temperature = data.get('temperature', 0.7)
        max_tokens = data.get('maxTokens', 2000)
        query_id = data.get('queryId', None)  # Optional query ID for tracking
        node_id = data.get('nodeId', None)  # Optional node ID for tracking
        session_id = data.get('sessionId', None)  # Optional; used to emit chat status (e.g. "Waiting for local LLM...")
        extended_thinking = data.get('extendedThinking', False)
        thinking_budget = int(data.get('thinkingBudget', 10000))
        
        # Determine provider for unified llm node
        if node_type == 'llm':
            if provider == 'anthropic':
                node_type = 'llm-anthropic'
            elif provider == 'local':
                node_type = 'llm-local'
            else:
                node_type = 'llm-openai'  # Default to OpenAI
        
        if 'openai' in node_type:
            from openai import OpenAI
            import asyncio
            api_key = os.getenv("OPENAI_API_KEY")
            
            if not api_key:
                _record_failed_llm_for_query(
                    query_id, node_id, model, start_time,
                    'OpenAI API key not configured', prompt_preview=prompt or '',
                )
                return jsonify({
                    'success': False,
                    'error': 'OpenAI API key not configured'
                }), 400
            
            client = OpenAI(api_key=api_key)
            openai_tools = _get_combined_openai_tools(tools_config)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
            create_kw = dict(model=model, temperature=temperature, max_tokens=max_tokens, messages=messages)
            if openai_tools:
                prompt_lower = (prompt or '').lower()
                requires_tool = any(kw in prompt_lower for kw in [
                    'read file', 'write file', 'create file', 'create a file', 'make file', 'delete file',
                    'file named', 'file at', 'project root', 'exact file path', 'folder', 'directory',
                    'run command', 'execute command', 'powershell', 'python script',
                    'navigate', 'click', 'fill', 'browser', 'screenshot'
                ])
                create_kw["tools"] = openai_tools
                create_kw["tool_choice"] = "required" if requires_tool else "auto"
            response = client.chat.completions.create(**create_kw)
            response_message = response.choices[0].message
            response_text = response_message.content or ''
            max_tool_rounds = 10
            while openai_tools and getattr(response_message, 'tool_calls', None) and max_tool_rounds > 0:
                max_tool_rounds -= 1
                if session_id:
                    emit_chat_status(session_id, "Calling Tools...")
                messages.append({
                    "role": "assistant",
                    "content": response_message.content or None,
                    "tool_calls": [
                        {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in response_message.tool_calls
                    ]
                })
                for tc in response_message.tool_calls:
                    name = tc.function.name
                    try:
                        import json as _json
                        args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except Exception:
                        args = {}
                    if session_id:
                        emit_chat_status(session_id, f"Waiting for Tools... ({name})")
                    content = _invoke_llm_tool(name, args, tools_config, session_id)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": content
                    })
                if session_id:
                    emit_chat_status(session_id, "Thinking further...")
                response = client.chat.completions.create(
                    model=model, messages=messages, tools=openai_tools, tool_choice="auto",
                    temperature=temperature, max_tokens=max_tokens
                )
                response_message = response.choices[0].message
                response_text = response_message.content or ''

            if openai_tools and requires_tool and not getattr(response_message, 'tool_calls', None):
                direct_file_response = _try_direct_file_tool_fulfillment(prompt)
                if direct_file_response:
                    response_text = direct_file_response
            
            if session_id and openai_tools:
                emit_chat_status(session_id, "Finalizing...")
            end_time = time.time()
            # Track in query report if query_id provided
            if query_id:
                try:
                    from api.query_tracker import get_query_tracker
                    tracker = get_query_tracker(query_id)
                    if tracker and tracker.query_id == query_id:
                        usage = getattr(response, 'usage', None)
                        tracker.add_llm_call(
                            model=model,
                            prompt_tokens=usage.prompt_tokens if usage else 0,
                            completion_tokens=usage.completion_tokens if usage else 0,
                            total_tokens=usage.total_tokens if usage else 0,
                            start_time=start_time,
                            end_time=end_time,
                            success=True,
                            response_preview=response_text,
                            prompt_preview=prompt,
                            node_id=node_id,
                            knowledge_inputs=knowledge_inputs,
                        )
                except Exception as track_error:
                    print(f"[QUERY] Error tracking LLM call: {track_error}")
            
            return jsonify({
                'success': True,
                'response': response_text,
                'usage': {
                    'prompt_tokens': getattr(response.usage, 'prompt_tokens', 0),
                    'completion_tokens': getattr(response.usage, 'completion_tokens', 0),
                    'total_tokens': getattr(response.usage, 'total_tokens', 0)
                }
            })
            
        elif 'anthropic' in node_type:
            from anthropic import Anthropic
            import asyncio
            api_key = os.getenv("ANTHROPIC_API_KEY")
            
            if not api_key:
                _record_failed_llm_for_query(
                    query_id, node_id, model, start_time,
                    'Anthropic API key not configured', prompt_preview=prompt or '',
                )
                return jsonify({
                    'success': False,
                    'error': 'Anthropic API key not configured'
                }), 400
            
            client = Anthropic(api_key=api_key)
            openai_tools = _get_combined_openai_tools(tools_config)
            anthropic_tools = None
            if openai_tools:
                anthropic_tools = [
                    {"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}})}
                    for t in openai_tools
                ]
            messages = [{"role": "user", "content": prompt}]
            create_kwargs = dict(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages
            )
            if anthropic_tools:
                create_kwargs["tools"] = anthropic_tools
            if extended_thinking:
                create_kwargs['thinking'] = {"type": "enabled", "budget_tokens": thinking_budget}
            else:
                create_kwargs['temperature'] = temperature
            response = client.messages.create(**create_kwargs)
            response_text = next((block.text for block in response.content if getattr(block, 'type', None) == 'text'), '')
            max_tool_rounds = 10
            while anthropic_tools and max_tool_rounds > 0:
                tool_uses = [b for b in response.content if getattr(b, 'type', None) == 'tool_use']
                if not tool_uses:
                    break
                max_tool_rounds -= 1
                if session_id:
                    emit_chat_status(session_id, "Calling Tools...")
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for tu in tool_uses:
                    tid = getattr(tu, 'id', None)
                    name = getattr(tu, 'name', None)
                    args = getattr(tu, 'input', None) or {}
                    if session_id:
                        emit_chat_status(session_id, f"Waiting for Tools... ({name})")
                    content = _invoke_llm_tool(name, args if isinstance(args, dict) else {}, tools_config, session_id)
                    tool_results.append({"type": "tool_result", "tool_use_id": tid, "content": content})
                messages.append({"role": "user", "content": tool_results})
                if session_id:
                    emit_chat_status(session_id, "Thinking further...")
                response = client.messages.create(
                    model=model, max_tokens=max_tokens, system=system_prompt, messages=messages,
                    tools=anthropic_tools, temperature=create_kwargs.get('temperature', temperature)
                )
                response_text = next((block.text for block in response.content if getattr(block, 'type', None) == 'text'), '')
            end_time = time.time()
            if session_id and anthropic_tools:
                emit_chat_status(session_id, "Finalizing...")
            if query_id:
                try:
                    from api.query_tracker import get_query_tracker
                    tracker = get_query_tracker(query_id)
                    if tracker and tracker.query_id == query_id:
                        tracker.add_llm_call(
                            model=model,
                            prompt_tokens=response.usage.input_tokens,
                            completion_tokens=response.usage.output_tokens,
                            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
                            start_time=start_time,
                            end_time=end_time,
                            success=True,
                            response_preview=response_text,
                            prompt_preview=prompt,
                            node_id=node_id,
                            knowledge_inputs=knowledge_inputs,
                        )
                except Exception as track_error:
                    print(f"[QUERY] Error tracking LLM call: {track_error}")
            return jsonify({
                'success': True,
                'response': response_text,
                'usage': {
                    'input_tokens': response.usage.input_tokens,
                    'output_tokens': response.usage.output_tokens,
                    'total_tokens': response.usage.input_tokens + response.usage.output_tokens
                }
            })
        elif node_type == 'llm-local':
            from api.inference_mode import normalize_inference_mode

            chat_inference_mode = normalize_inference_mode(
                data.get('inferenceMode') or data.get('inference_mode') or 'auto'
            )
            if session_id:
                offer = _offer_local_llm_launch_if_needed(session_id, prompt, chat_inference_mode)
                if offer is not None:
                    return jsonify({
                        'success': True,
                        'response': offer.get('response') or offer.get('output') or '',
                        'type': 'local_llm_launch_prompt',
                    })
            # Local inference — OpenAI-compatible; supports MCP tools when toolsConfig provided.
            # Backend is selected by LOCAL_LLM_BACKEND (ollama | llamacpp). Both speak the OpenAI API.
            # We serialize requests (one at a time) and show "Waiting for local LLM..." when queued.
            from openai import OpenAI as _OAI
            from core.local_llm import (
                get_local_base_url, get_local_api_key, get_local_label,
                resolve_local_model, list_local_models,
            )
            import asyncio
            ollama_base = get_local_base_url()
            local_label = get_local_label()
            ollama_model = resolve_local_model(model, with_tools=bool(tools_config))
            client = _OAI(base_url=ollama_base, api_key=get_local_api_key())
            waited_for_ollama = False
            try:
                if not _ollama_request_lock.acquire(blocking=False):
                    if session_id:
                        emit_chat_status(session_id, f"Waiting for local LLM ({local_label})...")
                    _ollama_request_lock.acquire(blocking=True)
                    waited_for_ollama = True
                if session_id:
                    emit_chat_status(session_id, f"Calling local LLM ({local_label})...")
                openai_tools = _get_combined_openai_tools(tools_config)
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]
                # Deterministic fallback: for explicit file create/read prompts,
                # do it directly via MCP instead of relying on Ollama tool emission.
                if openai_tools:
                    direct_file_response = _try_direct_file_tool_fulfillment_from_texts(prompt)
                    if direct_file_response:
                        if session_id:
                            emit_chat_status(session_id, "Finalizing...")
                        return jsonify({
                            'success': True,
                            'response': direct_file_response,
                            'usage': {
                                'prompt_tokens': 0,
                                'completion_tokens': 0,
                                'total_tokens': 0,
                            }
                        })
                create_kw = dict(model=ollama_model, messages=messages, temperature=temperature, max_tokens=max_tokens)
                if openai_tools:
                    create_kw["tools"] = openai_tools
                    # auto: many local models behave poorly with forced tool_choice=required on every turn;
                    # plan-then-act (pipeline) + follow-up auto lets them finish with text after tool results.
                    create_kw["tool_choice"] = "auto"
                # Tool rounds + slow local models need more than 45s per completion.
                request_timeout_sec = int(
                    os.getenv('OLLAMA_REQUEST_TIMEOUT_SEC', '300' if openai_tools else '120')
                )
                create_kw["timeout"] = request_timeout_sec

                def _list_ollama_models() -> list:
                    return list_local_models()

                def _chat_completion_with_fallback(base_kwargs: dict, preferred_model: str):
                    model_ids = _list_ollama_models()
                    candidates = [preferred_model] + [m for m in model_ids if m and m != preferred_model]
                    last_err = None
                    # Try up to 2 candidates: preferred, then first alternate.
                    max_attempts = int(os.getenv('OLLAMA_FALLBACK_MAX_ATTEMPTS', '5'))
                    for cand in candidates[:max_attempts]:
                        try:
                            kwargs = dict(base_kwargs)
                            kwargs["model"] = cand
                            resp = client.chat.completions.create(**kwargs)
                            msg = resp.choices[0].message
                            content = getattr(msg, "content", None)
                            tool_calls = getattr(msg, "tool_calls", None)
                            # Some Ollama models may "succeed" but return empty content.
                            # If we didn't get tool calls (or tools aren't requested), treat as failure and try fallback.
                            if (not openai_tools and (content is None or not str(content).strip())) or (
                                openai_tools and (not tool_calls) and (content is None or not str(content).strip())
                            ):
                                last_err = RuntimeError("Empty content from Ollama")
                                continue
                            return resp
                        except Exception as e:
                            last_err = e
                            continue
                    raise last_err if last_err else RuntimeError("Ollama call failed")

                response = _chat_completion_with_fallback(create_kw, ollama_model)
                response_message = response.choices[0].message
                response_text = response_message.content or ''
                # If the model narrates file create/read instead of emitting tool calls, apply a deterministic fallback.
                # Run even when openai_tools is empty (MCP schema fetch failed): magic-phrase fulfillment still works.
                if not getattr(response_message, 'tool_calls', None):
                    direct_file_response = _try_direct_file_tool_fulfillment_from_texts(
                        response_text,
                        prompt,
                        f"{prompt}\n\n{response_text}",
                    )
                    if direct_file_response:
                        response_text = direct_file_response
                max_tool_rounds = 10
                while openai_tools and getattr(response_message, 'tool_calls', None) and max_tool_rounds > 0:
                    max_tool_rounds -= 1
                    if session_id:
                        emit_chat_status(session_id, "Calling Tools...")
                    messages.append({
                        "role": "assistant",
                        "content": response_message.content or None,
                        "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in response_message.tool_calls]
                    })
                    for tc in response_message.tool_calls:
                        import json as _json
                        args = _json.loads(tc.function.arguments) if getattr(tc.function, 'arguments', None) else {}
                        if session_id:
                            emit_chat_status(session_id, f"Waiting for Tools... ({tc.function.name})")
                        content = _invoke_llm_tool(tc.function.name, args, tools_config, session_id)
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
                    if session_id:
                        emit_chat_status(session_id, "Thinking further...")
                    follow_up_kw = dict(
                        model=ollama_model,
                        messages=messages,
                        tools=openai_tools,
                        tool_choice="auto",
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=request_timeout_sec,
                    )
                    response = _chat_completion_with_fallback(follow_up_kw, ollama_model)
                    response_message = response.choices[0].message
                    response_text = response_message.content or ''
                end_time = time.time()
                if session_id and openai_tools:
                    emit_chat_status(session_id, "Finalizing...")
                # Preserve <redacted_thinking>...</redacted_thinking> for the web UI (chat_page.js).
                usage = response.usage
                # Track in query report if query_id provided (Ollama may not return usage)
                if query_id:
                    try:
                        from api.query_tracker import get_query_tracker
                        tracker = get_query_tracker(query_id)
                        if tracker.query_id == query_id:
                            if usage:
                                prompt_tokens = getattr(usage, 'prompt_tokens', None) or 0
                                completion_tokens = getattr(usage, 'completion_tokens', None) or 0
                                total_tokens = getattr(usage, 'total_tokens', None) or (prompt_tokens + completion_tokens)
                            else:
                                prompt_tokens = completion_tokens = total_tokens = 0
                            tracker.add_llm_call(
                                model=ollama_model,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=total_tokens,
                                start_time=start_time,
                                end_time=end_time,
                                success=True,
                                response_preview=response_text[:200],
                                prompt_preview=prompt[:200] if prompt else '',
                                node_id=node_id,
                                notes=(f'Waited for prior local LLM ({local_label}) request to finish; requests are serialized—only one runs at a time.' if waited_for_ollama else f'Local LLM ({local_label}); requests are serialized—only one runs at a time.'),
                                knowledge_inputs=knowledge_inputs,
                            )
                    except Exception as track_error:
                        print(f"[QUERY] Error tracking Ollama LLM call: {track_error}")
                return jsonify({
                    'success': True,
                    'response': response_text,
                    'usage': {
                        'prompt_tokens': usage.prompt_tokens if usage else 0,
                        'completion_tokens': usage.completion_tokens if usage else 0,
                        'total_tokens': usage.total_tokens if usage else 0,
                    }
                })
            except Exception as ollama_err:
                err_txt = f'Local LLM ({local_label}) error (is {local_label} running at {ollama_base}?): {ollama_err}'
                _record_failed_llm_for_query(
                    query_id, node_id, ollama_model, start_time,
                    err_txt, prompt_preview=(prompt or '')[:400],
                )
                return jsonify({
                    'success': False,
                    'error': err_txt
                }), 502
            finally:
                _ollama_request_lock.release()
        else:
            _record_failed_llm_for_query(
                query_id, node_id, model, start_time,
                f'Unknown LLM type: {node_type}', prompt_preview=prompt or '',
            )
            return jsonify({
                'success': False,
                'error': f'Unknown LLM type: {node_type}'
            }), 400
            
    except Exception as e:
        print(f"LLM request error: {e}")
        _qid = locals().get("query_id")
        _nid = locals().get("node_id")
        _model = locals().get("model") or "unknown"
        _prompt = locals().get("prompt") or ""
        if _qid:
            _record_failed_llm_for_query(
                _qid, _nid, _model, start_time, str(e),
                prompt_preview=_prompt[:400] if _prompt else "",
            )
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def _strip_invisible_leading(s: str) -> str:
    """Remove BOM / ZW* chars that break slash-command detection."""
    if not isinstance(s, str):
        return s
    return s.lstrip('\ufeff\u200b\u200c\u200d\u2060')


def _message_is_slash_remote_agent(msg: str) -> bool:
    """Explicit slash (and legacy) forms that must use tool-remote-agent, not default OpenAI."""
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
    if re.match(r'^/cursor-cli(\s|$)', low):
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

NAV_LOG_PATH = os.path.join(os.path.expanduser('~'), 'cuttle_nav_debug.log')
NET_LOG_PATH = os.path.join(os.path.expanduser('~'), 'cuttle_net_debug.log')

@app.route('/api/debug-log', methods=['POST'])
@owner_required
def debug_log():
    try:
        payload = request.json or {}
        msg = payload.get('msg', '')
        channel = (payload.get('channel') or 'nav').strip().lower()
        path = NET_LOG_PATH if channel == 'net' else NAV_LOG_PATH
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


if __name__ == '__main__':
    print("Starting Cuttle Web Chat API Server...")
    print("=" * 50)
    print(f"Project root: {project_root}")
    print(f"Bot available: {PIPELINE_AVAILABLE}")
    print("\nAPI endpoints:")
    print("  GET  /                    - Landing page")
    print("  GET  /control_panel.html  - Control panel")
    print("  GET  /git_ui.html         - Git Web UI")
    print("  GET  /settings_page.html  - Settings page")
    print("  GET  /about_page.html     - About page")
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
    print("\nOpen http://localhost:8080 in your browser")
    print("Press Ctrl+C to stop the server")
    print("=" * 50)

    try:
        from managers.settings_manager import get_settings_manager
        from api.lan_access import (
            is_lan_access_enabled,
            get_lan_ipv4,
            resolve_bind_host,
            ensure_all_lan_firewall_rules,
            windows_firewall_rule_active,
            lan_portal_url,
            lan_phone_portal_url,
            LAN_HTTP_PORT,
            LAN_PHONE_HTTPS_PORT,
            LAN_HTTP_FALLBACK_PORT,
            lan_phone_http_fallback_url,
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
            if discovery.get('mdns_enabled'):
                try:
                    from api.discovery_mdns import start_mdns
                    start_mdns(port=8080, name="Cuttle")
                except Exception as e:
                    print(f"[DISCOVERY] mDNS start skipped: {e}")
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

            purged = purge_expired(project_root=actual_project_root)
            n = purged.get('deleted') or 0
            print(
                f"[SHARED-MEDIA] purge: deleted={n} ttl_days={purged.get('ttl_days')}",
                flush=True,
            )
        except Exception as _pe:
            print(f"[SHARED-MEDIA] purge skipped: {_pe}", flush=True)

        cert_file, key_file = generate_self_signed_cert(lan_ip=lan_ip)

        if lan_enabled and lan_ip:
            phone_url = lan_phone_portal_url(LAN_PHONE_HTTPS_PORT, lan_ip)
            from api.lan_access import lan_phone_http_fallback_url, LAN_PHONE_HTTPS_PORT, LAN_HTTP_FALLBACK_PORT
            fb_url = lan_phone_http_fallback_url(lan_ip)
            print(f"[LAN] Phone portal (HTTPS): {phone_url}")
            print(f"[LAN] Phone fallback (HTTP): {fb_url}")
            print(f"[LAN] Accept cert warning on phone once, or use HTTP port {LAN_HTTP_FALLBACK_PORT}")
            try:
                from werkzeug.serving import make_server
                phone_https_srv = make_server(
                    '0.0.0.0',
                    LAN_PHONE_HTTPS_PORT,
                    app,
                    threaded=True,
                    ssl_context=(str(cert_file), str(key_file)),
                )
                threading.Thread(
                    target=phone_https_srv.serve_forever,
                    name='cuttle-lan-phone-https',
                    daemon=True,
                ).start()
                print(f"[LAN] Phone HTTPS listening on https://0.0.0.0:{LAN_PHONE_HTTPS_PORT}")
                http_fb_srv = make_server('0.0.0.0', LAN_HTTP_FALLBACK_PORT, app, threaded=True)
                threading.Thread(
                    target=http_fb_srv.serve_forever,
                    name='cuttle-lan-http-fallback',
                    daemon=True,
                ).start()
                print(f"[LAN] HTTP fallback listening on http://0.0.0.0:{LAN_HTTP_FALLBACK_PORT}")
            except Exception as e:
                print(f"[LAN] Phone LAN servers failed: {e}")
        else:
            # Electron (and local HTTP tooling) need plain HTTP even when LAN is off.
            # Binding loopback-only keeps the portal off the network.
            try:
                from werkzeug.serving import make_server
                from api.lan_access import LAN_HTTP_FALLBACK_PORT
                http_local_srv = make_server('127.0.0.1', LAN_HTTP_FALLBACK_PORT, app, threaded=True)
                threading.Thread(
                    target=http_local_srv.serve_forever,
                    name='cuttle-loopback-http',
                    daemon=True,
                ).start()
                print(f"[HTTP] Loopback portal listening on http://127.0.0.1:{LAN_HTTP_FALLBACK_PORT}")
            except Exception as e:
                print(f"[HTTP] Loopback portal failed: {e}")

        # threaded=True allows pipeline triggers to make internal API calls (e.g. llm-request) without deadlock
        print(f"[HTTPS] Serving on https://{bind_host}:8080")
        app.run(
            debug=False,
            host=bind_host,
            port=8080,
            use_reloader=use_reloader,
            threaded=True,
            ssl_context=(cert_file, key_file),
        )
    except KeyboardInterrupt:
        print("\n\nWeb Chat API server stopped by user")
    except Exception as e:
        print(f"\nERROR: Error starting server: {e}")
