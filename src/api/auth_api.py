#!/usr/bin/env python3
"""
Authentication API endpoints for Cuttle
Supports username/password (local) and OAuth (Google, Facebook, Microsoft)
"""

import os
import re
import json
import secrets
import requests
from urllib.parse import urlencode
from flask import Blueprint, request, jsonify, redirect, make_response
from api.auth_db import get_auth_db
from api.auth_session import get_request_session_token, wants_session_token_in_body
from api.limiter import limiter as _auth_limiter

# Create Flask blueprint for auth routes
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_]{3,32}$')


def _cookie_secure() -> bool:
    """Only mark cookies Secure on HTTPS — HTTP :8000 (phone LAN) must keep Secure=False."""
    if request.is_secure:
        return True
    return (request.headers.get('X-Forwarded-Proto') or '').lower() == 'https'


def _set_session_cookie(response, session_token: str, clear: bool = False):
    kwargs = {
        'httponly': True,
        'secure': _cookie_secure(),
        'samesite': 'Lax',
        'path': '/',
    }
    if clear:
        response.set_cookie('session_token', '', max_age=0, **kwargs)
    else:
        response.set_cookie(
            'session_token',
            session_token,
            max_age=30 * 24 * 60 * 60,
            **kwargs,
        )


def _user_public(user: dict) -> dict:
    return {
        'id': user['id'],
        'username': user.get('username'),
        'email': user.get('email'),
        'display_name': user.get('display_name'),
        'profile_image': user.get('profile_image'),
        'auth_provider': user.get('auth_provider'),
        'is_guest': (user.get('auth_provider') or '') == 'guest',
        # Local accounts keep auth_provider=local; provider_user_id means Google (etc.) linked.
        'google_linked': bool(user.get('provider_user_id')) and (
            user.get('auth_provider') in ('local', 'google', 'guest')
        ),
    }


def _oauth_public_base() -> str:
    """Origin Google/Microsoft/Facebook redirect to after login.

    Must match the configured primary HTTPS listener and an Authorized
    redirect URI in the provider console. Prefer OAUTH_REDIRECT_BASE,
    else CUTTLE_API_URL, else the configured primary port.
    """
    from api.server_ports import resolve_with_env_file

    raw = (
        os.getenv('OAUTH_REDIRECT_BASE')
        or os.getenv('CUTTLE_API_URL')
        or f'https://localhost:{resolve_with_env_file().https}'
    ).strip().rstrip('/')
    return raw


def _oauth_redirect_uri(provider: str) -> str:
    return f"{_oauth_public_base()}/api/auth/callback/{provider}"


# OAuth client env names per provider (read at request time so a key added
# to src/.env without a restart is honored).
_OAUTH_ENV_NAMES = {
    'google': ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'),
    'microsoft': ('MICROSOFT_CLIENT_ID', 'MICROSOFT_CLIENT_SECRET'),
    'facebook': ('FACEBOOK_APP_ID', 'FACEBOOK_APP_SECRET'),
}

_OAUTH_SETUP_HINT = (
    'Add the client ID and secret to src/.env (see the OAuth section of the '
    'Settings page or docs) and restart Flask, then try again.'
)


def _oauth_live_config(provider: str) -> dict:
    """OAuth config with credentials resolved from the live environment.

    Credentials come strictly from the live environment — never from the
    import-time ``OAUTH_CONFIGS`` snapshot, so a key added to ``src/.env``
    (or removed) is honored without depending on import order.
    """
    base = dict(OAUTH_CONFIGS.get(provider, {}))
    names = _OAUTH_ENV_NAMES.get(provider, (None, None))
    base['client_id'] = os.getenv(names[0], '') if names[0] else ''
    base['client_secret'] = os.getenv(names[1], '') if names[1] else ''
    return base


def _oauth_configured(provider: str) -> bool:
    cfg = _oauth_live_config(provider)
    return bool((cfg.get('client_id') or '').strip() and (cfg.get('client_secret') or '').strip())


# OAuth configuration (redirect_uri resolved at request time via helpers above)
OAUTH_CONFIGS = {
    'google': {
        'client_id': os.getenv('GOOGLE_CLIENT_ID', ''),
        'client_secret': os.getenv('GOOGLE_CLIENT_SECRET', ''),
        'authorize_url': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token_url': 'https://oauth2.googleapis.com/token',
        'userinfo_url': 'https://www.googleapis.com/oauth2/v2/userinfo',
        'scopes': ['openid', 'email', 'profile'],
    },
    'microsoft': {
        'client_id': os.getenv('MICROSOFT_CLIENT_ID', ''),
        'client_secret': os.getenv('MICROSOFT_CLIENT_SECRET', ''),
        'authorize_url': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
        'token_url': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
        'userinfo_url': 'https://graph.microsoft.com/v1.0/me',
        'scopes': ['openid', 'email', 'profile'],
    },
    'facebook': {
        'client_id': os.getenv('FACEBOOK_APP_ID', ''),
        'client_secret': os.getenv('FACEBOOK_APP_SECRET', ''),
        'authorize_url': 'https://www.facebook.com/v12.0/dialog/oauth',
        'token_url': 'https://graph.facebook.com/v12.0/oauth/access_token',
        'userinfo_url': 'https://graph.facebook.com/me',
        'scopes': ['email', 'public_profile'],
    }
}


@auth_bp.route('/oauth/status', methods=['GET'])
def oauth_status():
    """Which OAuth providers are usable (public — reveals nothing secret)."""
    return jsonify({
        'success': True,
        'providers': {
            name: {'configured': _oauth_configured(name)}
            for name in OAUTH_CONFIGS
        },
    })

@auth_bp.route('/register', methods=['POST'])
@_auth_limiter.limit("5 per minute")
def register():
    """Register new user with username/password (local account)."""
    try:
        data = request.get_json() or {}

        username = (data.get('username') or '').strip()
        password = data.get('password', '')
        display_name = (data.get('display_name') or '').strip() or username

        # Legacy: accept email-shaped signup by using local-part as username
        email_in = (data.get('email') or '').strip().lower()
        if not username and email_in and '@' in email_in:
            username = email_in.split('@', 1)[0]

        if not username or not password:
            return jsonify({
                'success': False,
                'error': 'Username and password are required'
            }), 400

        if not _USERNAME_RE.match(username):
            return jsonify({
                'success': False,
                'error': 'Username must be 3–32 characters: letters, numbers, underscore'
            }), 400

        if len(password) < 8:
            return jsonify({
                'success': False,
                'error': 'Password must be at least 8 characters long'
            }), 400

        db = get_auth_db()
        uname = username.lower()

        if db.get_user_by_username(uname):
            return jsonify({
                'success': False,
                'error': 'Username already taken'
            }), 409

        # Synthetic email keeps NOT NULL + UNIQUE email column happy for local accounts
        email = f'{uname}@local'

        user_id = db.create_user(
            email=email,
            display_name=display_name,
            auth_provider='local',
            password=password,
            username=uname,
        )

        if not user_id:
            return jsonify({
                'success': False,
                'error': 'Failed to create user'
            }), 500

        session_token = db.create_auth_session(user_id)
        user = db.get_user_by_id(user_id)

        body = {
            'success': True,
            'message': 'Registration successful',
            'user': _user_public(user),
        }
        # Mobile WebView often drops Set-Cookie; return token so the client can
        # persist it (Bearer + native CookieManager) and keep chat history.
        if wants_session_token_in_body():
            body['session_token'] = session_token
        response = jsonify(body)
        _set_session_cookie(response, session_token)
        return response

    except Exception as e:
        print(f"Registration error: {e}")
        return jsonify({
            'success': False,
            'error': 'Registration failed'
        }), 500

@auth_bp.route('/login', methods=['POST'])
@_auth_limiter.limit("5 per minute")
def login():
    """Login with username/password (email still accepted for legacy accounts)."""
    try:
        data = request.get_json() or {}

        login_id = (data.get('username') or data.get('email') or data.get('login') or '').strip()
        password = data.get('password', '')

        if not login_id or not password:
            return jsonify({
                'success': False,
                'error': 'Username and password are required'
            }), 400

        db = get_auth_db()
        user_id = db.verify_password(login_id, password)

        if not user_id:
            return jsonify({
                'success': False,
                'error': 'Invalid username or password'
            }), 401

        session_token = db.create_auth_session(user_id)
        user = db.get_user_by_id(user_id)

        body = {
            'success': True,
            'message': 'Login successful',
            'user': _user_public(user),
        }
        if wants_session_token_in_body():
            body['session_token'] = session_token
        response = jsonify(body)
        _set_session_cookie(response, session_token)
        return response

    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({
            'success': False,
            'error': 'Login failed'
        }), 500


@auth_bp.route('/guest', methods=['POST'])
@_auth_limiter.limit("20 per hour")
def guest_login():
    """Create or reuse a lightweight guest session (no password)."""
    try:
        db = get_auth_db()
        token = secrets.token_hex(4)
        username = f'g_{token}'
        email = f'{username}@guest.local'
        user_id = db.create_user(
            email=email,
            display_name='Guest',
            auth_provider='guest',
            password=None,
            username=username,
        )
        if not user_id:
            return jsonify({'success': False, 'error': 'Could not start a guest session'}), 500
        session_token = db.create_auth_session(user_id)
        user = db.get_user_by_id(user_id)
        body = {
            'success': True,
            'message': 'Guest session started',
            'user': _user_public(user),
        }
        if wants_session_token_in_body():
            body['session_token'] = session_token
        response = jsonify(body)
        _set_session_cookie(response, session_token)
        return response
    except Exception as e:
        print(f"Guest login error: {e}")
        return jsonify({'success': False, 'error': 'Guest sign-in failed'}), 500

@auth_bp.route('/logout', methods=['POST'])
def logout():
    """Logout current user"""
    try:
        session_token = get_request_session_token()
        
        if session_token:
            db = get_auth_db()
            db.delete_auth_session(session_token)
        
        response = jsonify({
            'success': True,
            'message': 'Logged out successfully'
        })
        _set_session_cookie(response, '', clear=True)
        return response
        
    except Exception as e:
        print(f"Logout error: {e}")
        return jsonify({
            'success': False,
            'error': 'Logout failed'
        }), 500

@auth_bp.route('/me', methods=['GET'])
def get_current_user():
    """Get current authenticated user"""
    try:
        session_token = get_request_session_token()
        
        if not session_token:
            return jsonify({
                'success': False,
                'authenticated': False
            }), 401
        
        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        
        if not user:
            return jsonify({
                'success': False,
                'authenticated': False
            }), 401
        
        return jsonify({
            'success': True,
            'authenticated': True,
            'user': _user_public(user),
        })
        
    except Exception as e:
        print(f"Get current user error: {e}")
        # Do not claim authenticated:false — clients treat that as a real logout
        # and (as of 129b07f) auto-open the sign-in modal on cold start.
        return jsonify({
            'success': False,
            'error': 'auth check failed',
        }), 503

# ==================== OAuth Routes ====================

@auth_bp.route('/oauth/<provider>', methods=['GET'])
@_auth_limiter.limit("10 per minute")
def oauth_login(provider):
    """Initiate OAuth flow.

    ``?link=1`` — while signed in, attach this provider to the current account
    (keeps username/password; syncs profile picture). Used for Google avatar link.
    """
    try:
        if provider not in OAUTH_CONFIGS:
            return jsonify({
                'success': False,
                'error': f'Unknown OAuth provider: {provider}'
            }), 400
        
        config = _oauth_live_config(provider)

        if not (config.get('client_id') or '').strip() or not (config.get('client_secret') or '').strip():
            message = f'{provider.title()} login is not set up on this server. {_OAUTH_SETUP_HINT}'
            # Browser navigation (the login button is a plain link) lands on
            # the app with guidance in the auth dialog instead of raw JSON.
            best = request.accept_mimetypes.best_match(['text/html', 'application/json'])
            if best == 'text/html':
                return redirect(f"/?auth_error={provider}_oauth_not_configured")
            return jsonify({
                'success': False,
                'error': message,
                'code': 'oauth_not_configured',
                'provider': provider,
                'setup': _OAUTH_SETUP_HINT,
            }), 503
        
        db = get_auth_db()
        link_user_id = None
        want_link = str(request.args.get('link') or '').strip().lower() in ('1', 'true', 'yes')
        if want_link:
            session_token = get_request_session_token()
            if not session_token:
                return redirect('/?auth_error=login_required_to_link')
            session = db.verify_auth_session(session_token)
            if not session:
                return redirect('/?auth_error=login_required_to_link')
            link_user_id = int(session['id'])

        state = db.create_oauth_state(provider, link_user_id=link_user_id)
        redirect_uri = _oauth_redirect_uri(provider)
        
        # Build authorization URL
        params = {
            'client_id': config['client_id'],
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': ' '.join(config['scopes']),
            'state': state
        }
        # Ask Google for a fresh consent when linking so picture/email scopes apply.
        if provider == 'google' and link_user_id is not None:
            params['prompt'] = 'select_account'
        
        auth_url = f"{config['authorize_url']}?{urlencode(params)}"
        
        return redirect(auth_url)
        
    except Exception as e:
        print(f"OAuth init error: {e}")
        return jsonify({
            'success': False,
            'error': 'OAuth initialization failed'
        }), 500

@auth_bp.route('/callback/<provider>', methods=['GET'])
@_auth_limiter.limit("10 per minute")
def oauth_callback(provider):
    """Handle OAuth callback (sign-in or link-to-existing-account)."""
    try:
        if provider not in OAUTH_CONFIGS:
            return f"Error: Unknown provider {provider}", 400
        
        code = request.args.get('code')
        state = request.args.get('state')
        error = request.args.get('error')
        
        if error:
            return redirect(f'/?auth_error={error}')
        
        if not code or not state:
            return redirect('/?auth_error=missing_code_or_state')
        
        db = get_auth_db()
        
        state_row = db.consume_oauth_state(state, provider)
        if not state_row:
            return redirect('/?auth_error=invalid_state')
        link_user_id = state_row.get('link_user_id')
        
        config = _oauth_live_config(provider)
        redirect_uri = _oauth_redirect_uri(provider)
        
        # Exchange code for token (redirect_uri must match the authorize request)
        token_data = {
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'code': code,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code'
        }
        
        token_response = requests.post(config['token_url'], data=token_data)
        token_json = token_response.json()
        
        if 'access_token' not in token_json:
            return redirect('/?auth_error=token_exchange_failed')
        
        access_token = token_json['access_token']
        
        # Get user info
        headers = {'Authorization': f'Bearer {access_token}'}
        
        if provider == 'facebook':
            # Facebook requires fields parameter
            userinfo_url = f"{config['userinfo_url']}?fields=id,name,email,picture"
        else:
            userinfo_url = config['userinfo_url']
        
        userinfo_response = requests.get(userinfo_url, headers=headers)
        userinfo = userinfo_response.json()
        
        # Extract user data based on provider
        if provider == 'google':
            email = userinfo.get('email')
            display_name = userinfo.get('name')
            provider_user_id = userinfo.get('id')
            profile_image = userinfo.get('picture')
        elif provider == 'microsoft':
            email = userinfo.get('mail') or userinfo.get('userPrincipalName')
            display_name = userinfo.get('displayName')
            provider_user_id = userinfo.get('id')
            profile_image = None  # Microsoft Graph doesn't provide direct picture URL
        elif provider == 'facebook':
            email = userinfo.get('email')
            display_name = userinfo.get('name')
            provider_user_id = userinfo.get('id')
            profile_image = userinfo.get('picture', {}).get('data', {}).get('url')
        else:
            return redirect('/?auth_error=unknown_provider')
        
        if not email:
            return redirect('/?auth_error=no_email')
        
        email = email.lower().strip()

        # --- Link Google (etc.) onto an already-signed-in local account ---
        if link_user_id is not None:
            ok, reason = db.link_oauth_provider(
                int(link_user_id),
                provider,
                provider_user_id,
                profile_image=profile_image,
            )
            if not ok:
                return redirect(f'/?auth_error={reason}')
            # Keep the existing session; just refresh cookie activity via same token if present
            session_token = get_request_session_token()
            if session_token and db.verify_auth_session(session_token):
                response = make_response(redirect('/?auth_linked=1'))
                _set_session_cookie(response, session_token)
                return response
            # Cookie lost during Google redirect — mint a fresh session for the linked user
            session_token = db.create_auth_session(int(link_user_id))
            response = make_response(redirect('/?auth_linked=1'))
            _set_session_cookie(response, session_token)
            return response
        
        # --- Normal OAuth sign-in / register ---
        # Prefer existing link by provider subject (e.g. local ian already linked)
        linked = db.get_user_by_provider_user_id(provider_user_id) if provider_user_id else None
        if linked:
            user_id = linked['id']
            if profile_image:
                db.update_profile_image(user_id, profile_image)
        else:
            user = db.get_user_by_email(email)
            if user:
                user_id = user['id']
                if profile_image:
                    db.update_profile_image(user_id, profile_image)
            else:
                user_id = db.create_user(
                    email=email,
                    display_name=display_name,
                    auth_provider=provider,
                    provider_user_id=provider_user_id,
                    profile_image=profile_image
                )
                if not user_id:
                    return redirect('/?auth_error=user_creation_failed')
        
        # Create auth session
        session_token = db.create_auth_session(user_id)
        
        # Redirect to main page with session cookie
        response = make_response(redirect('/?auth_success=true'))
        _set_session_cookie(response, session_token)
        return response
        
    except Exception as e:
        print(f"OAuth callback error: {e}")
        import traceback
        traceback.print_exc()
        return redirect('/?auth_error=callback_failed')

# ==================== Session Management ====================

@auth_bp.route('/sessions', methods=['GET'])
def get_chat_sessions():
    """Get all chat sessions for current user"""
    try:
        session_token = get_request_session_token()
        
        if not session_token:
            return jsonify({
                'success': False,
                'error': 'Not authenticated'
            }), 401
        
        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        
        if not user:
            return jsonify({
                'success': False,
                'error': 'Invalid session'
            }), 401
        
        sessions = db.get_user_chat_sessions(user['id'])

        # Mark chats that still have an in-flight reply (history spinner after refresh).
        try:
            from api import chat_delivery as _chat_delivery
            from api.chat_live_status import active_live_session_ids as _active_live_ids
            # busy_session_ids() reconciles zombie locks (busy with no live work).
            busy = set(_chat_delivery.busy_session_ids())
            busy.update(_active_live_ids())
            awaiting_action = set()
            try:
                from api.flask_restart import awaiting_action_session_ids

                awaiting_action = set(awaiting_action_session_ids() or [])
                busy.update(awaiting_action)
            except Exception:
                pass
            for s in sessions:
                sid = s.get('id')
                bare = str(sid) if sid is not None else ""
                s['generating'] = bare in busy
                s['awaiting_action'] = bare in awaiting_action
        except Exception:
            for s in sessions:
                s['generating'] = False
                s['awaiting_action'] = False

        try:
            ids = [int(s['id']) for s in sessions if s.get('id') is not None]
            linked = db.list_discord_links_for_sessions(ids)
            for s in sessions:
                sid = s.get('id')
                s['discord_participants'] = list(linked.get(int(sid), [])) if sid is not None else []
        except Exception:
            for s in sessions:
                s['discord_participants'] = []

        return jsonify({
            'success': True,
            'sessions': sessions
        })
        
    except Exception as e:
        print(f"Get sessions error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to get sessions'
        }), 500


@auth_bp.route('/sessions/search', methods=['GET'])
def search_chat_sessions():
    """Search the signed-in user's chats: titles first, then message bodies."""
    try:
        session_token = get_request_session_token()
        if not session_token:
            return jsonify({
                'success': False,
                'error': 'Not authenticated'
            }), 401

        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        if not user:
            return jsonify({
                'success': False,
                'error': 'Invalid session'
            }), 401

        query = (request.args.get('q') or request.args.get('query') or '').strip()
        if not query:
            return jsonify({
                'success': True,
                'query': '',
                'sessions': [],
            })

        sessions = db.search_user_chats(user['id'], query)
        try:
            from api import chat_delivery as _chat_delivery
            from api.chat_live_status import active_live_session_ids as _active_live_ids
            busy = set(_chat_delivery.busy_session_ids())
            busy.update(_active_live_ids())
            awaiting_action = set()
            try:
                from api.flask_restart import awaiting_action_session_ids

                awaiting_action = set(awaiting_action_session_ids() or [])
                busy.update(awaiting_action)
            except Exception:
                pass
            for s in sessions:
                sid = s.get('id')
                bare = str(sid) if sid is not None else ""
                s['generating'] = bare in busy
                s['awaiting_action'] = bare in awaiting_action
        except Exception:
            for s in sessions:
                s['generating'] = False
                s['awaiting_action'] = False

        try:
            ids = [int(s['id']) for s in sessions if s.get('id') is not None]
            linked = db.list_discord_links_for_sessions(ids)
            for s in sessions:
                sid = s.get('id')
                s['discord_participants'] = list(linked.get(int(sid), [])) if sid is not None else []
        except Exception:
            for s in sessions:
                s['discord_participants'] = []

        return jsonify({
            'success': True,
            'query': query,
            'sessions': sessions,
        })
    except Exception as e:
        print(f"Search sessions error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to search sessions'
        }), 500

@auth_bp.route('/sessions', methods=['POST'])
def create_chat_session():
    """Create new chat session"""
    try:
        session_token = get_request_session_token()
        
        if not session_token:
            return jsonify({
                'success': False,
                'error': 'Not authenticated'
            }), 401
        
        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        
        if not user:
            return jsonify({
                'success': False,
                'error': 'Invalid session'
            }), 401
        
        data = request.get_json()
        session_name = data.get('session_name', '')
        
        session_id = db.create_chat_session(user['id'], session_name)
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'message': 'Chat session created'
        })
        
    except Exception as e:
        print(f"Create session error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to create session'
        }), 500

@auth_bp.route('/sessions/<int:session_id>', methods=['PATCH'])
def patch_chat_session(session_id):
    """Update chat session fields (name, starred, and/or working project)."""
    try:
        session_token = get_request_session_token()

        if not session_token:
            return jsonify({
                'success': False,
                'error': 'Not authenticated'
            }), 401

        db = get_auth_db()
        user = db.verify_auth_session(session_token)

        if not user:
            return jsonify({
                'success': False,
                'error': 'Invalid session'
            }), 401

        data = request.get_json(silent=True) or {}
        touched = False
        out = {'success': True, 'session_id': session_id}

        if 'session_name' in data or 'name' in data:
            raw_name = data.get('session_name', data.get('name'))
            name = (raw_name if isinstance(raw_name, str) else str(raw_name or '')).strip()
            if not name:
                return jsonify({
                    'success': False,
                    'error': 'session_name must be a non-empty string'
                }), 400
            # Descriptive name only — slash chips come from the transcript, not typing.
            from api.chat_titler import (
                _strip_slash_lead,
                collect_slash_prefixes,
                compose_session_title,
            )
            messages = db.get_messages(session_id) or []
            descriptive = _strip_slash_lead(name)
            if not descriptive:
                return jsonify({
                    'success': False,
                    'error': 'session_name must include a display name (slash chips alone are not enough)'
                }), 400
            composed = compose_session_title(descriptive, messages) if messages else descriptive
            success = db.rename_chat_session(session_id, user['id'], composed)
            if not success:
                return jsonify({
                    'success': False,
                    'error': 'Session not found or access denied'
                }), 404
            out['session_name'] = composed[:80]
            out['title'] = descriptive[:80]
            out['slash_prefixes'] = collect_slash_prefixes(messages)
            out['name_auto'] = 0
            touched = True

        if 'starred' in data:
            starred = bool(data.get('starred'))
            success = db.set_session_starred(session_id, user['id'], starred)
            if not success:
                return jsonify({
                    'success': False,
                    'error': 'Session not found or access denied'
                }), 404
            out['starred'] = starred
            touched = True

        project_keys = ('project_id', 'project_name', 'project_path', 'projectId', 'projectName', 'projectPath')
        if any(k in data for k in project_keys):
            pid = data.get('project_id', data.get('projectId'))
            pname = data.get('project_name', data.get('projectName'))
            ppath = data.get('project_path', data.get('projectPath'))
            success = db.set_session_project(
                session_id,
                user['id'],
                project_id=pid,
                project_name=pname,
                project_path=ppath,
            )
            if not success:
                return jsonify({
                    'success': False,
                    'error': 'Session not found or access denied'
                }), 404
            out['project_id'] = pid
            out['project_name'] = (pname or '').strip() or None
            out['project_path'] = (ppath or '').strip() or None
            touched = True

        if not touched:
            return jsonify({
                'success': False,
                'error': 'No supported fields to update (expected session_name, starred, and/or project_id/project_name/project_path)'
            }), 400

        return jsonify(out)

    except Exception as e:
        print(f"Patch session error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to update session'
        }), 500


@auth_bp.route('/sessions/<int:session_id>/suggest-title', methods=['POST'])
def suggest_chat_session_title(session_id):
    """Suggest a chat title from the transcript (does not persist)."""
    try:
        session_token = get_request_session_token()
        if not session_token:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        if not user:
            return jsonify({'success': False, 'error': 'Invalid session'}), 401

        session = db.get_chat_session(session_id, user['id'])
        if not session:
            return jsonify({
                'success': False,
                'error': 'Session not found or access denied'
            }), 404

        data = request.get_json(silent=True) or {}
        inference_mode = (
            data.get('inference_mode')
            or data.get('inferenceMode')
            or 'auto'
        )
        avoid_raw = data.get('avoid') or data.get('avoid_titles') or data.get('previous')
        avoid_titles = []
        if isinstance(avoid_raw, list):
            for item in avoid_raw:
                if isinstance(item, str) and item.strip():
                    avoid_titles.append(item.strip()[:80])
        elif isinstance(avoid_raw, str) and avoid_raw.strip():
            avoid_titles.append(avoid_raw.strip()[:80])

        from api.chat_titler import suggest_session_title
        result = suggest_session_title(
            session_id,
            inference_mode=str(inference_mode),
            avoid_titles=avoid_titles or None,
        )
        title = (result.get('title') or '').strip()
        prefixes = (result.get('slash_prefixes') or '').strip()
        composed = (result.get('composed') or '').strip()
        if not title and not composed:
            title = (session.get('session_name') or '').strip() or f'Chat {session_id}'
            from api.chat_titler import _strip_slash_lead, collect_slash_prefixes
            messages = db.get_messages(session_id) or []
            prefixes = collect_slash_prefixes(messages)
            title = _strip_slash_lead(title) or title
            composed = (f"{prefixes} {title}".strip() if prefixes else title)
            result = {
                'title': title,
                'slash_prefixes': prefixes,
                'composed': composed,
                'source': 'current',
            }

        return jsonify({
            'success': True,
            'session_id': session_id,
            'title': (result.get('title') or title or '').strip(),
            'slash_prefixes': (result.get('slash_prefixes') or prefixes or '').strip(),
            'composed': (result.get('composed') or composed or '').strip(),
            'source': result.get('source') or 'current',
            'current_title': (session.get('session_name') or '').strip() or None,
        })
    except Exception as e:
        print(f"Suggest session title error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to suggest title'
        }), 500


@auth_bp.route('/sessions/<int:session_id>', methods=['DELETE'])
def delete_chat_session(session_id):
    """Delete chat session"""
    try:
        session_token = get_request_session_token()
        
        if not session_token:
            return jsonify({
                'success': False,
                'error': 'Not authenticated'
            }), 401
        
        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        
        if not user:
            return jsonify({
                'success': False,
                'error': 'Invalid session'
            }), 401
        
        success = db.delete_chat_session(session_id, user['id'])
        
        if not success:
            return jsonify({
                'success': False,
                'error': 'Session not found or access denied'
            }), 404

        # Stop any in-flight Cursor/agent work tied to this chat so it does not
        # keep running after the conversation is deleted.
        cancel_info = {'cancelled': False}
        try:
            from api.chat_run_registry import cancel_session_runs
            cancel_info = cancel_session_runs(session_id) or cancel_info
        except Exception as ce:
            print(f"[AUTH] cancel_session_runs failed for {session_id}: {ce}")

        # Delete uploaded attachments tied to this session
        try:
            import shutil
            from pathlib import Path
            from werkzeug.utils import secure_filename
            # uploads are stored under src/output/uploads/{safe_session}
            for sid in (str(session_id), secure_filename(str(session_id))):
                up = Path(__file__).resolve().parent.parent / 'output' / 'uploads' / sid
                if up.exists():
                    shutil.rmtree(up, ignore_errors=True)
        except Exception as ce:
            print(f"[AUTH] upload cleanup failed for {session_id}: {ce}")

        # Forget Brain briefing receipts + handoff cursors for this chat.
        try:
            from api.cuttle_brain.state import forget_chat
            forget_chat(session_id)
        except Exception as ce:
            print(f"[AUTH] brain state cleanup failed for {session_id}: {ce}")
        
        return jsonify({
            'success': True,
            'message': 'Session deleted',
            'cancelled_run': bool(cancel_info.get('cancelled')),
            'killed_procs': int(cancel_info.get('killed_procs') or 0),
        })
        
    except Exception as e:
        print(f"Delete session error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to delete session'
        }), 500

@auth_bp.route('/sessions/<int:session_id>/messages', methods=['GET'])
def get_session_messages(session_id):
    """Get messages from a chat session"""
    try:
        session_token = get_request_session_token()
        
        if not session_token:
            return jsonify({
                'success': False,
                'error': 'Not authenticated'
            }), 401
        
        db = get_auth_db()
        user = db.verify_auth_session(session_token)
        
        if not user:
            return jsonify({
                'success': False,
                'error': 'Invalid session'
            }), 401
        
        # Verify session ownership
        session = db.get_chat_session(session_id, user['id'])
        if not session:
            return jsonify({
                'success': False,
                'error': 'Session not found or access denied'
            }), 404

        # Optional windowing: ?limit=10 (newest N), ?before_id=… (older page),
        # ?after_id=… (sync tail). Omitting limit keeps the full transcript for
        # callers that still expect every row (tests, recover paths).
        limit = request.args.get('limit', type=int)
        before_id = request.args.get('before_id', type=int)
        after_id = request.args.get('after_id', type=int)
        if limit is not None:
            limit = max(1, min(int(limit), 200))

        messages = db.get_messages(
            session_id,
            limit=limit,
            before_id=before_id,
            after_id=after_id,
        )

        has_more = False
        older_visible_count = 0
        if after_id is None and (limit is not None or before_id is not None):
            oldest_id = messages[0]['id'] if messages else before_id
            meta_page = db.message_page_meta(session_id, oldest_id)
            has_more = bool(meta_page.get('has_more'))
            older_visible_count = int(meta_page.get('older_visible_count') or 0)

        # Flatten query-report fields from metadata so the chat UI can restore links
        for msg in messages:
            meta = msg.get('metadata')
            if isinstance(meta, dict):
                if meta.get('report_url') and not msg.get('report_url'):
                    msg['report_url'] = meta['report_url']
                if meta.get('query_id') and not msg.get('query_id'):
                    msg['query_id'] = meta['query_id']
                for key in ('project_id', 'project_name', 'project_path'):
                    if meta.get(key) not in (None, '') and not msg.get(key):
                        msg[key] = meta[key]
                if meta.get('kind') and not msg.get('kind'):
                    msg['kind'] = meta['kind']

        try:
            from api.subagents.identity import hydrate_subagent_message_badges

            hydrate_subagent_message_badges(
                db, session_id, messages, pin_session=True
            )
        except Exception:
            pass
        try:
            from api.subagents.service import hydrate_parent_fleet

            hydrate_parent_fleet(db, messages)
        except Exception:
            pass
        
        # Session-level Muse/Hermes/OpenCode/Codex/Claude pins so the UI can seed badges before
        # first paint instead of flashing defaults while /api/*/model|effort
        # round-trips.
        # Effort mirrors the runtime order (chat pin → starred default). The
        # client treats a present-but-blank pin as "resolved, don't fetch", so
        # a blank here hid the starred effort on chats that never pinned this
        # agent (e.g. re-pinning Muse → Claude in an existing chat).
        def _effective_effort(agent_id, session_effort):
            try:
                from api.agent_harness.agent_defaults import resolve_effective_effort
                return resolve_effective_effort(agent_id, session_effort=session_effort)[0] or ''
            except Exception:
                return str(session_effort or '')

        muse_model = None
        muse_effort = ''
        hermes_model = None
        hermes_effort = ''
        opencode_model = None
        opencode_effort = ''
        codex_model = None
        codex_effort = ''
        claude_model = None
        claude_effort = ''
        try:
            from scripts.utilities.muse_cli_session_store import (
                load_muse_effort,
                load_muse_model,
            )
            from scripts.utilities.muse_cli_tool import resolve_muse_default_model
            muse_model = load_muse_model(session_id) or resolve_muse_default_model()
            muse_effort = _effective_effort('muse', load_muse_effort(session_id))
        except Exception:
            pass
        try:
            from scripts.utilities.hermes_cli_session_store import (
                load_hermes_effort,
                load_hermes_model,
            )
            from scripts.utilities.hermes_cli_tool import resolve_hermes_default_model
            hermes_model = load_hermes_model(session_id) or resolve_hermes_default_model()
            hermes_effort = _effective_effort('hermes', load_hermes_effort(session_id))
        except Exception:
            pass
        try:
            from api.agent_harness.agent_defaults import get_starred_model
            from api.agent_harness.agents.opencode.session_store import (
                load_opencode_effort,
                load_opencode_model,
            )
            # Unpinned means the CLI default wins — never force the old
            # hardcoded GLM id onto history badges (same bug as CH-000419).
            opencode_model = load_opencode_model(session_id) or get_starred_model('opencode') or ''
            opencode_effort = _effective_effort('opencode', load_opencode_effort(session_id))
        except Exception:
            pass
        try:
            from api.agent_harness.agent_defaults import get_starred_model as _star_codex
            from scripts.utilities.codex_cli_session_store import (
                load_codex_effort,
                load_codex_model,
            )
            codex_model = load_codex_model(session_id) or _star_codex('codex') or ''
            codex_effort = _effective_effort('codex', load_codex_effort(session_id))
        except Exception:
            pass
        try:
            from api.agent_harness.agent_defaults import get_starred_model as _star_claude
            from scripts.utilities.claude_cli_session_store import (
                load_claude_effort,
                load_claude_model,
            )
            claude_model = load_claude_model(session_id) or _star_claude('claude') or ''
            claude_effort = _effective_effort('claude', load_claude_effort(session_id))
        except Exception:
            pass

        return jsonify({
            'success': True,
            'messages': messages,
            'has_more': has_more,
            'older_visible_count': older_visible_count,
            # Title for the open-chat chrome — seed on first paint so refresh
            # doesn't show bare "Chat" until the history panel loads sessions.
            'session_name': session.get('session_name') or '',
            'project_id': session.get('project_id'),
            'project_name': session.get('project_name'),
            'project_path': session.get('project_path'),
            'followups': db.get_followup_queue(session_id, user['id']),
            'display_name': session.get('display_name') or '',
            'avatar': session.get('avatar') or '',
            'agent_profile_id': session.get('agent_profile_id') or '',
            'parent_session_id': session.get('parent_session_id'),
            'origin': session.get('origin') or '',
            'agent_pins': {
                'muse': {'model': muse_model, 'effort': muse_effort},
                'hermes': {'model': hermes_model, 'effort': hermes_effort},
                'opencode': {'model': opencode_model, 'effort': opencode_effort},
                'codex': {'model': codex_model, 'effort': codex_effort},
                'claude': {'model': claude_model, 'effort': claude_effort},
            },
        })
        
    except Exception as e:
        print(f"Get messages error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to get messages'
        }), 500


@auth_bp.route('/sessions/<int:session_id>/messages', methods=['POST'])
def post_session_message(session_id):
    """Append a durable chat-log notice (system role only).

    Client status lines like "Stopped generating" / "Project: …" used to be
    DOM-only (``persist: true`` meant "don't fade"), so refresh wiped them.
    """
    try:
        pair, err = _auth_user_or_error()
        if err:
            return err
        db, user = pair

        session = db.get_chat_session(session_id, user['id'])
        if not session:
            return jsonify({
                'success': False,
                'error': 'Session not found or access denied'
            }), 404

        data = request.get_json(silent=True) or {}
        role = str(data.get('role') or '').strip().lower()
        content = data.get('content')
        if role != 'system':
            return jsonify({
                'success': False,
                'error': 'Only role=system may be posted from the client'
            }), 400
        if not isinstance(content, str) or not content.strip():
            return jsonify({
                'success': False,
                'error': 'content is required'
            }), 400

        meta = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
        meta = dict(meta)
        kind = data.get('kind') or meta.get('kind')
        if kind:
            meta['kind'] = str(kind)

        message_id = db.add_message(
            session_id,
            'system',
            content.strip(),
            meta or None,
        )
        return jsonify({
            'success': True,
            'message_id': message_id,
            'role': 'system',
            'content': content.strip(),
            'kind': meta.get('kind'),
        })
    except Exception as e:
        print(f"Post session message error: {e}")
        return jsonify({
            'success': False,
            'error': 'Failed to append message'
        }), 500


def _auth_user_or_error():
    session_token = get_request_session_token()
    if not session_token:
        return None, (jsonify({'success': False, 'error': 'Not authenticated'}), 401)
    db = get_auth_db()
    user = db.verify_auth_session(session_token)
    if not user:
        return None, (jsonify({'success': False, 'error': 'Invalid session'}), 401)
    return (db, user), None


@auth_bp.route('/sessions/<int:session_id>/followups', methods=['GET', 'PUT', 'POST'])
def session_followups(session_id):
    """Shared composer follow-up queue (phone + PC see the same items)."""
    try:
        pair, err = _auth_user_or_error()
        if err:
            return err
        db, user = pair
        session = db.get_chat_session(session_id, user['id'])
        if not session:
            return jsonify({'success': False, 'error': 'Session not found or access denied'}), 404
        if request.method == 'GET':
            items = db.get_followup_queue(session_id, user['id'])
            return jsonify({'success': True, 'followups': items})
        data = request.get_json(silent=True) or {}
        if request.method == 'PUT':
            items = db.set_followup_queue(session_id, user['id'], data.get('followups') or [])
            if items is None:
                return jsonify({'success': False, 'error': 'Session not found or access denied'}), 404
            return jsonify({'success': True, 'followups': items})
        item = data.get('followup') or data.get('item') or data
        items = db.append_followup(session_id, user['id'], item)
        if items is None:
            return jsonify({'success': False, 'error': 'Session not found or access denied'}), 404
        return jsonify({'success': True, 'followups': items})
    except Exception as e:
        print(f"Followups error: {e}")
        return jsonify({'success': False, 'error': 'Failed to update follow-ups'}), 500


@auth_bp.route('/sessions/<int:session_id>/followups/take', methods=['POST'])
def take_session_followups(session_id):
    """Atomically drain unpaused queue items; paused ones stay for later."""
    try:
        pair, err = _auth_user_or_error()
        if err:
            return err
        db, user = pair
        result = db.take_followup_queue(session_id, user['id'])
        if result is None:
            return jsonify({'success': False, 'error': 'Session not found or access denied'}), 404
        return jsonify({
            'success': True,
            'followups': result.get('taken') or [],
            'remaining': result.get('remaining') or [],
        })
    except Exception as e:
        print(f"Take followups error: {e}")
        return jsonify({'success': False, 'error': 'Failed to take follow-ups'}), 500

