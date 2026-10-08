/**
 * Authentication and session management for Cuttle
 */

// Current user state
let currentUser = null;
let currentChatSession = null;
let chatSessions = [];

/** localStorage key for mobile bearer fallback when WebView drops Set-Cookie. */
const SESSION_TOKEN_STORAGE_KEY = 'cuttle_session_token';

function _isCuttleMobileClient() {
    return !!(window.isCuttleMobile || window.cuttleMobile?.isNative)
        || /\bCuttleMobile\//.test(navigator.userAgent || '');
}

function getStoredSessionToken() {
    try {
        return String(localStorage.getItem(SESSION_TOKEN_STORAGE_KEY) || '').trim();
    } catch (_) {
        return '';
    }
}

function setStoredSessionToken(token) {
    try {
        const t = String(token || '').trim();
        if (t) localStorage.setItem(SESSION_TOKEN_STORAGE_KEY, t);
        else localStorage.removeItem(SESSION_TOKEN_STORAGE_KEY);
    } catch (_) {}
}

function _flushMobileAuthCookies(token) {
    if (!_isCuttleMobileClient()) return;
    try {
        const native = window.CuttleShellNative || window.cuttleMobile;
        const base = (typeof native?.getServerUrl === 'function' && native.getServerUrl())
            || window.location.origin
            || '';
        if (token && typeof native?.setSessionCookie === 'function') {
            native.setSessionCookie(String(base), String(token));
        }
        if (typeof native?.flushCookies === 'function') {
            native.flushCookies();
        }
    } catch (_) {}
}

function _persistSessionTokenFromAuthResponse(data) {
    const token = data && data.session_token ? String(data.session_token).trim() : '';
    if (!token) return;
    setStoredSessionToken(token);
    _flushMobileAuthCookies(token);
}

/** Ensure fetch() sends the stored bearer when the HttpOnly cookie is missing. */
function _installAuthFetchPatch() {
    if (typeof window === 'undefined' || window.__cuttleAuthFetchPatched) return;
    window.__cuttleAuthFetchPatched = true;
    const orig = window.fetch.bind(window);
    window.fetch = function cuttleAuthFetch(input, init) {
        const token = getStoredSessionToken();
        if (!token) return orig(input, init);
        const next = init ? Object.assign({}, init) : {};
        const headers = new Headers(next.headers || undefined);
        if (!headers.has('Authorization') && !headers.has('X-Cuttle-Session-Token')) {
            headers.set('Authorization', 'Bearer ' + token);
        }
        if (_isCuttleMobileClient() && !headers.has('X-Cuttle-Client')) {
            headers.set('X-Cuttle-Client', 'mobile');
        }
        next.headers = headers;
        if (next.credentials == null) next.credentials = 'include';
        return orig(input, next);
    };
}

_installAuthFetchPatch();

function _escapeHtmlAttr(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/** Initials for avatar fallback — shared by shell rail, chat messages, and landing user menu. */
function userAvatarInitials(user) {
    if (!user) return '?';
    const label = user.display_name || user.username || user.email || '?';
    const initials = label.trim().split(/[\s._-]+/).filter(Boolean).map((p) => p[0]).join('');
    return (initials || '?').toUpperCase().slice(0, 2);
}

/** Inner HTML for a circular user avatar (profile image or initials). */
function userAvatarInnerHtml(user, opts) {
    if (!user) return '?';
    if (user.profile_image) {
        const alt = (opts && opts.alt) || user.display_name || user.username || 'User';
        return `<img src="${_escapeHtmlAttr(user.profile_image)}" alt="${_escapeHtmlAttr(alt)}">`;
    }
    return userAvatarInitials(user);
}

let _authRequiredGate = false;

function _setAuthRequiredGate(on) {
    _authRequiredGate = !!on;
    try {
        document.documentElement.classList.toggle('auth-required', _authRequiredGate);
        document.body.classList.toggle('auth-required', _authRequiredGate);
    } catch (_) {}
    const close = document.getElementById('authModalClose');
    if (close) close.hidden = _authRequiredGate;
}

function _userCanLinkGoogle(user) {
    if (!user) return false;
    const provider = user.auth_provider || 'local';
    const linked = !!(user.google_linked || user.provider_user_id);
    if (linked) return false;
    return provider === 'local' || provider === 'guest' || !!user.is_guest;
}

window.CuttleAuth = {
    getUser: () => currentUser,
    getCurrentChatSession: () => currentChatSession,
    getSessions: () => chatSessions,
    adoptSessions: sessions => { chatSessions = sessions || []; },
    isAuthenticated: () => !!currentUser,
    setCurrentChatSession: (id) => { currentChatSession = id; },
    loadChatSessions: () => loadChatSessions(),
    openAuthModal: (tab) => openAuthModal(tab || 'login'),
    logout: (opts) => handleLogout(opts || {}),
    userAvatarInitials: (user) => userAvatarInitials(user),
    userAvatarInnerHtml: (user, opts) => userAvatarInnerHtml(user, opts),
    userCanLinkGoogle: (user) => _userCanLinkGoogle(user),
};

function _authChangedDetail() {
    return {
        user: currentUser,
        sessions: chatSessions,
        currentSession: currentChatSession,
    };
}

// Prevents shell ↔ iframe auth.js from ping-ponging forever.
let _applyingRemoteAuth = false;
let _lastBroadcastUserId = undefined; // undefined = never broadcast; null = signed out

function _userId(user) {
    return user && user.id != null ? user.id : null;
}

function _emitAuthChanged(opts) {
    const detail = _authChangedDetail();
    // Always notify same-window listeners (chat_page UI, shell account button).
    window.dispatchEvent(new CustomEvent('cuttle-auth-changed', { detail }));

    // Receivers must never re-broadcast — that is what caused the infinite loop.
    if (_applyingRemoteAuth) return;
    if (opts && opts.localOnly) return;

    const uid = _userId(currentUser);
    // Skip identical cross-window broadcasts (login + loadChatSessions used to double-fire).
    if (uid === _lastBroadcastUserId) return;
    _lastBroadcastUserId = uid;

    const payload = { type: 'cuttle-auth-changed', user: currentUser };

    if (window.parent && window.parent !== window) {
        // Chat iframe must not tell the shell "signed out" — phone cold-start
        // /api/auth/me races used to wipe a good shell session and empty history.
        if (!currentUser && !(opts && opts.allowParentSignOut)) return;
        try { window.parent.postMessage(payload, '*'); } catch (_) {}
        return; // iframe: only bubble up; never also fan out
    }

    document.querySelectorAll('.shell-main iframe, #contentFrame').forEach(function (fr) {
        try { fr.contentWindow?.postMessage(payload, '*'); } catch (_) {}
    });
}

function _applyRemoteAuthUser(incoming) {
    const nextId = _userId(incoming);
    if (nextId === _userId(currentUser)) {
        // Same account, fresh fields (e.g. username changed in the shell
        // account panel) — adopt them so local labels don't go stale.
        if (incoming && JSON.stringify(incoming) !== JSON.stringify(currentUser)) {
            currentUser = incoming;
            updateUIForAuthenticatedUser();
            _emitAuthChanged({ localOnly: true });
        }
        return;
    }

    _applyingRemoteAuth = true;

    if (incoming) {
        currentUser = incoming;
        updateUIForAuthenticatedUser();
        if (_pageNeedsChatSessions()) {
            // Fetch sessions in this window only — never broadcast afterward.
            loadChatSessions().finally(() => {
                _emitAuthChanged({ localOnly: true });
                _applyingRemoteAuth = false;
            });
            return;
        }
    } else {
        currentUser = null;
        currentChatSession = null;
        chatSessions = [];
        updateUIForUnauthenticatedUser();
        // A sign-out broadcast from another viewport opens the dialog here
        // too, so logout looks the same in every open pane.
        openAuthModal('login');
    }

    _emitAuthChanged({ localOnly: true });
    _applyingRemoteAuth = false;
}

function _isEmbeddedInShell() {
    try {
        return !!(window.parent && window.parent !== window);
    } catch (_) {
        return true;
    }
}

// Initialize auth on page load
document.addEventListener('DOMContentLoaded', function() {
    // Chat iframe keeps a duplicate auth modal; those password fields make iPad
    // Chrome show the key/card/pin autofill bar on the composer. Shell owns sign-in.
    if (_isEmbeddedInShell()) {
        const dup = document.getElementById('authModal');
        if (dup) dup.remove();
    }

    // Scrub any legacy <template> password markup from older cached HTML.
    const legacyTpl = document.getElementById('authFormsTemplate');
    if (legacyTpl) legacyTpl.remove();
    document.querySelectorAll('#authModal input[type="password"]').forEach((el) => el.remove());

    setupAuthEventListeners();
    checkAuthRedirect();

    window.addEventListener('message', function(e) {
        if (!e.data || typeof e.data !== 'object') return;
        if (e.data.type === 'cuttle-open-auth') {
            // Only the top-level shell should open a modal (ignore iframe echoes).
            if (!_isEmbeddedInShell()) {
                openAuthModal(e.data.tab === 'signup' ? 'signup' : 'login');
            }
            return;
        }
        if (e.data.type === 'cuttle-auth-changed') {
            _applyRemoteAuthUser(e.data.user || null);
        }
    });

    const forceSignIn = new URLSearchParams(window.location.search).has('signin');
    // Explicit ?signin=1 opens immediately (account rail / deep-link). Cold start waits
    // on /api/auth/me so a valid session cookie never flashes the modal or signs you out.
    if (forceSignIn && !_isEmbeddedInShell()) {
        openAuthModal('login');
    }
    checkAuthStatus().then((result) => {
        if (_isEmbeddedInShell()) return;
        // Only force the modal when the server explicitly said "not authenticated".
        // Network blips on phone cold start must not look like a logout.
        if (result && result.authenticated === false) {
            _setAuthRequiredGate(true);
            openAuthModal('login');
        }
        if (result && result.authenticated === true) {
            _setAuthRequiredGate(false);
        }
    });
});

/**
 * Check if user is authenticated.
 * Returns { authenticated: true|false|null }. null = network/parse failure —
 * do not treat that as signed-out (phone cold-start used to broadcast null and
 * force the login modal every launch).
 */
async function checkAuthStatus() {
    const mobile = _isCuttleMobileClient();
    const attempts = mobile ? 4 : 1;
    let lastErr = null;

    for (let i = 0; i < attempts; i++) {
        try {
            const response = await fetch('/api/auth/me', {
                credentials: 'include',
                cache: 'no-store',
            });
            let data = null;
            try {
                data = await response.json();
            } catch (parseErr) {
                lastErr = parseErr;
                if (i < attempts - 1) {
                    await new Promise((r) => setTimeout(r, 350 * (i + 1)));
                    continue;
                }
                break;
            }

            // Only trust an explicit boolean. HTML error pages / 5xx JSON without
            // `authenticated` used to fall through as "logged out" and open the modal.
            if (!data || typeof data.authenticated !== 'boolean') {
                lastErr = new Error('auth/me missing authenticated field (HTTP ' + response.status + ')');
                if (i < attempts - 1) {
                    await new Promise((r) => setTimeout(r, 350 * (i + 1)));
                    continue;
                }
                break;
            }

            if (data.authenticated && data.user) {
                currentUser = data.user;
                updateUIForAuthenticatedUser();
                // Shell has no chat UI — skip session fetch (avoids useless API storm).
                if (_pageNeedsChatSessions()) {
                    await loadChatSessions();
                }
                _emitAuthChanged();
                return { authenticated: true };
            }

            // Explicit signed-out only on OK or 401. A 5xx body that says
            // authenticated:false is a server blip — retry / keep prior state.
            if (data.authenticated === false && (response.ok || response.status === 401)) {
                // Stale localStorage token after server-side revoke — drop it.
                if (getStoredSessionToken()) setStoredSessionToken('');
                currentUser = null;
                updateUIForUnauthenticatedUser();
                // Embedded chat: local UI only — never sign the shell out on a race.
                _emitAuthChanged({ localOnly: _isEmbeddedInShell() });
                return { authenticated: false };
            }

            lastErr = new Error('auth/me ambiguous (HTTP ' + response.status + ')');
            if (i < attempts - 1) {
                await new Promise((r) => setTimeout(r, 350 * (i + 1)));
                continue;
            }
            break;
        } catch (error) {
            lastErr = error;
            if (i < attempts - 1) {
                await new Promise((r) => setTimeout(r, 350 * (i + 1)));
            }
        }
    }

    console.error('Error checking auth status:', lastErr);
    // Keep whatever auth state we already have; never emit a false logout.
    return { authenticated: null, networkError: true };
}

/** True when this document actually renders chat sessions (not the app shell). */
function _pageNeedsChatSessions() {
    return !!(
        document.getElementById('chatHistory') ||
        document.getElementById('chatMessages') ||
        document.getElementById('chatSessionsSidebar') ||
        document.getElementById('sessionsList')
    );
}

/**
 * Check for OAuth redirect
 */
function checkAuthRedirect() {
    const urlParams = new URLSearchParams(window.location.search);
    
    if (urlParams.has('auth_success') || urlParams.has('auth_linked')) {
        // Successful OAuth login or Google link (avatar sync)
        checkAuthStatus().then(() => {
            if (urlParams.has('auth_linked') && typeof updateUIForAuthenticatedUser === 'function') {
                updateUIForAuthenticatedUser();
            }
            const linkGoogle = document.getElementById('userMenuLinkGoogle');
            if (linkGoogle && currentUser) {
                linkGoogle.hidden = !_userCanLinkGoogle(currentUser);
            }
        });
        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (urlParams.has('auth_error')) {
        const error = urlParams.get('auth_error');
        const m = /^([a-z]+)_oauth_not_configured$/.exec(error || '');
        if (m) {
            const provider = m[1].charAt(0).toUpperCase() + m[1].slice(1);
            openAuthModal('login');
            showAuthError(
                `${provider} login is not set up on this server yet. ` +
                'Add the client ID and secret to Cuttle\'s .env, restart Flask, then try again — ' +
                'or continue with a local account or as guest.'
            );
        } else {
            showAuthError(`Authentication failed: ${error}`);
        }
        window.history.replaceState({}, document.title, window.location.pathname);
    }
}

/**
 * Setup event listeners for auth components
 */
function setupAuthEventListeners() {
    // Login/Signup tabs
    const loginTab = document.getElementById('loginTab');
    const signupTab = document.getElementById('signupTab');
    
    if (loginTab) {
        loginTab.addEventListener('click', () => switchAuthTab('login'));
    }
    
    if (signupTab) {
        signupTab.addEventListener('click', () => switchAuthTab('signup'));
    }

    // Modal controls + delegated form handlers (forms may only exist while modal is open)
    const authModal = document.getElementById('authModal');
    const authModalClose = document.getElementById('authModalClose');
    const loginButton = document.getElementById('loginButton');
    
    if (authModalClose) {
        authModalClose.addEventListener('click', closeAuthModal);
    }
    
    if (authModal) {
        authModal.addEventListener('click', (e) => {
            if (e.target === authModal && !_authRequiredGate) {
                closeAuthModal();
            }
        });
        authModal.addEventListener('click', (e) => {
            const t = e.target;
            if (t && (t.id === 'guestSignInBtn' || (t.closest && t.closest('.auth-guest-button')))) {
                e.preventDefault();
                handleGuestSignIn();
            }
        });
        authModal.addEventListener('submit', (e) => {
            const form = e.target;
            if (!(form instanceof HTMLFormElement)) return;
            if (form.id === 'loginForm') handleLogin(e);
            else if (form.id === 'signupForm') handleSignup(e);
        });
        authModal.addEventListener('change', (e) => {
            const t = e.target;
            if (!(t instanceof HTMLInputElement)) return;
            if (t.id === 'loginShowPassword') {
                const pw = document.getElementById('loginPassword');
                if (pw) pw.type = t.checked ? 'text' : 'password';
            } else if (t.id === 'signupShowPassword') {
                const type = t.checked ? 'text' : 'password';
                const pw = document.getElementById('signupPassword');
                const confirm = document.getElementById('signupConfirmPassword');
                if (pw) pw.type = type;
                if (confirm) confirm.type = type;
            }
        });
    }
    
    if (loginButton) {
        loginButton.addEventListener('click', () => openAuthModal('login'));
    }

    // Ensure credential fields are not in the live DOM while closed.
    if (authModal && !authModal.classList.contains('active')) {
        _setAuthCredentialFieldsArmed(false);
    }
}

/**
 * Resolve the login/signup <form> elements (supports shell + legacy *FormContent ids).
 */
function _authForms() {
    const login = document.getElementById('loginForm');
    const signup = document.getElementById('signupForm');
    return {
        login: (login && login.tagName === 'FORM') ? login : document.getElementById('loginFormContent'),
        signup: (signup && signup.tagName === 'FORM') ? signup : document.getElementById('signupFormContent'),
    };
}

/**
 * Switch between login and signup tabs
 */
function switchAuthTab(tab) {
    const loginTab = document.getElementById('loginTab');
    const signupTab = document.getElementById('signupTab');
    const { login: loginForm, signup: signupForm } = _authForms();

    if (!loginTab || !signupTab || !loginForm || !signupForm) return;

    if (tab === 'login') {
        loginTab.classList.add('active');
        signupTab.classList.remove('active');
        loginForm.classList.add('active');
        signupForm.classList.remove('active');
    } else {
        signupTab.classList.add('active');
        loginTab.classList.remove('active');
        signupForm.classList.add('active');
        loginForm.classList.remove('active');
    }

    hideAuthError();
}

/**
 * Login/signup forms are created with document.createElement only while the
 * modal is open. iPad/iOS Chrome indexes type=password during *HTML parse*
 * (including <template> contents) and then shows the key/card/pin autofill bar
 * on the chat composer — so password fields must never appear in .html source.
 */
function _authFormsHost(modal) {
    if (!modal) return null;
    return modal.querySelector('#authFormsHost')
        || modal.querySelector('.auth-modal')
        || modal;
}

function _el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
        Object.keys(attrs).forEach((key) => {
            const val = attrs[key];
            if (val == null || val === false) return;
            if (key === 'className') node.className = val;
            else if (key === 'text') node.textContent = val;
            else if (key === 'html') node.innerHTML = val;
            else if (val === true) node.setAttribute(key, '');
            else node.setAttribute(key, String(val));
        });
    }
    (children || []).forEach((child) => {
        if (child == null) return;
        node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
}

function _authOauthBlock(dividerText) {
    const buttons = [
        {
            href: '/api/auth/oauth/google',
            label: 'Continue with Google',
            svg: '<svg class="auth-oauth-icon" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>',
        },
    ];
    const list = _el('div', { className: 'auth-oauth-buttons' });
    buttons.forEach((b) => {
        const a = _el('a', {
            href: b.href,
            className: 'auth-oauth-button',
            'data-oauth-provider': (b.href || '').split('/').pop() || '',
        });
        a.innerHTML = b.svg + '<span>' + b.label + '</span>';
        list.appendChild(a);
    });
    _refreshOauthAvailability(list);
    return _el('div', null, [
        _el('div', { className: 'auth-divider' }, [_el('span', { text: dividerText })]),
        list,
    ]);
}

/**
 * Disable OAuth buttons whose provider is not configured server-side.
 * Unavailable providers never look like functioning login methods: the
 * button stays visible but inert, with setup guidance instead of a raw
 * backend error after the click.
 */
function _refreshOauthAvailability(listEl) {
    if (!listEl || typeof fetch !== 'function') return;
    fetch('/api/auth/oauth/status', { credentials: 'include' })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
            const providers = (data && data.providers) || {};
            Array.from(listEl.querySelectorAll('[data-oauth-provider]')).forEach((a) => {
                const p = a.getAttribute('data-oauth-provider');
                const st = providers[p];
                if (st && st.configured === false) {
                    a.classList.add('auth-oauth-unavailable');
                    a.setAttribute('aria-disabled', 'true');
                    a.title = 'Not set up on this server — add the client ID and secret to Cuttle\'s .env';
                    a.addEventListener('click', (e) => {
                        e.preventDefault();
                        showAuthError(
                            'Google login is not set up on this server yet. ' +
                            'Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to Cuttle\'s .env, ' +
                            'restart Flask, then try again — or continue with a local account or as guest.'
                        );
                    });
                }
            });
        })
        .catch(() => { /* leave buttons enabled; server explains on click */ });
}

function _hostWantsOauth(host) {
    if (!host) return true;
    const v = String(host.getAttribute('data-include-oauth') || '1').toLowerCase();
    return v !== '0' && v !== 'false' && v !== 'no';
}

function _buildAuthForms(includeOauth) {
    const frag = document.createDocumentFragment();

    const loginPwGroup = _el('div', { className: 'auth-form-group' }, [
        _el('label', { className: 'auth-form-label', for: 'loginPassword', text: 'Password' }),
        _el('input', {
            type: 'password', className: 'auth-form-input', id: 'loginPassword',
            name: 'password', placeholder: '••••••••', autocomplete: 'current-password', required: true,
        }),
        _el('label', { className: 'auth-show-password' }, [
            _el('input', { type: 'checkbox', id: 'loginShowPassword', autocomplete: 'off' }),
            document.createTextNode(' Show password'),
        ]),
    ]);

    const loginForm = _el('form', { className: 'auth-form active', id: 'loginForm', autocomplete: 'on' }, [
        _el('div', { className: 'auth-form-group' }, [
            _el('label', { className: 'auth-form-label', for: 'loginUsername', text: 'Username' }),
            _el('input', {
                type: 'text', className: 'auth-form-input', id: 'loginUsername',
                name: 'username', placeholder: 'yourname', autocomplete: 'username', required: true,
            }),
        ]),
        loginPwGroup,
        _el('button', { type: 'submit', className: 'auth-submit-button', text: 'Login' }),
    ]);
        if (includeOauth) loginForm.appendChild(_authOauthBlock('or continue with'));
    frag.appendChild(loginForm);

    const guestBtn = _el('button', {
        type: 'button',
        className: 'auth-guest-button',
        id: 'guestSignInBtn',
        text: 'Continue as guest',
    });
    frag.appendChild(guestBtn);

    const signupForm = _el('form', { className: 'auth-form', id: 'signupForm', autocomplete: 'on' }, [
        _el('div', { className: 'auth-form-group' }, [
            _el('label', { className: 'auth-form-label', for: 'signupName', text: 'Display Name' }),
            _el('input', {
                type: 'text', className: 'auth-form-input', id: 'signupName',
                name: 'display_name', placeholder: 'Your Name', required: true,
            }),
        ]),
        _el('div', { className: 'auth-form-group' }, [
            _el('label', { className: 'auth-form-label', for: 'signupUsername', text: 'Username' }),
            _el('input', {
                type: 'text', className: 'auth-form-input', id: 'signupUsername',
                name: 'username', placeholder: 'yourname', autocomplete: 'username', required: true,
            }),
        ]),
        _el('div', { className: 'auth-form-group' }, [
            _el('label', { className: 'auth-form-label', for: 'signupPassword', text: 'Password' }),
            _el('input', {
                type: 'password', className: 'auth-form-input', id: 'signupPassword',
                name: 'password', placeholder: '••••••••', autocomplete: 'new-password', required: true,
            }),
        ]),
        _el('div', { className: 'auth-form-group' }, [
            _el('label', { className: 'auth-form-label', for: 'signupConfirmPassword', text: 'Confirm Password' }),
            _el('input', {
                type: 'password', className: 'auth-form-input', id: 'signupConfirmPassword',
                name: 'confirm_password', placeholder: '••••••••', autocomplete: 'new-password', required: true,
            }),
            _el('label', { className: 'auth-show-password' }, [
                _el('input', { type: 'checkbox', id: 'signupShowPassword', autocomplete: 'off' }),
                document.createTextNode(' Show password'),
            ]),
        ]),
        _el('button', { type: 'submit', className: 'auth-submit-button', text: 'Create Account' }),
    ]);
    if (includeOauth) signupForm.appendChild(_authOauthBlock('or sign up with'));
    frag.appendChild(signupForm);

    return frag;
}

/**
 * Arm/disarm login+signup credential fields.
 */
function _setAuthCredentialFieldsArmed(armed) {
    const modal = document.getElementById('authModal');
    if (!modal) return;
    const host = _authFormsHost(modal);

    // Legacy: remove any leftover <template> with passwords from older HTML.
    const tpl = document.getElementById('authFormsTemplate');
    if (tpl) tpl.remove();

    if (armed) {
        modal.removeAttribute('inert');
        if (host && !host.querySelector('form.auth-form')) {
            host.innerHTML = '';
            host.appendChild(_buildAuthForms(_hostWantsOauth(host)));
        }
        return;
    }

    modal.setAttribute('inert', '');

    if (host) {
        host.innerHTML = '';
    }

    document.querySelectorAll('#authModal input[type="password"]').forEach((el) => {
        el.remove();
    });
}

/**
 * Open auth modal
 */
function openAuthModal(tab = 'login') {
    // Prefer the shell modal when chat (or any page) is embedded in an iframe.
    if (_isEmbeddedInShell()) {
        try {
            window.parent.postMessage(
                { type: 'cuttle-open-auth', tab: tab === 'signup' ? 'signup' : 'login' },
                '*'
            );
            return;
        } catch (_) { /* fall through to local modal if any */ }
    }

    const modal = document.getElementById('authModal');
    if (modal) {
        _setAuthCredentialFieldsArmed(true);
        modal.classList.add('active');
        if (typeof window.cuttleSyncMobileServerRows === 'function') {
            try { window.cuttleSyncMobileServerRows(); } catch (_) {}
        }
        switchAuthTab(tab);
        // Prevent body scrolling when modal is open
        document.body.style.overflow = 'hidden';
    }
}

/**
 * Close auth modal
 */
function closeAuthModal() {
    if (_authRequiredGate) return;
    const modal = document.getElementById('authModal');
    if (modal) {
        modal.classList.remove('active');
        _setAuthCredentialFieldsArmed(false);
        hideAuthError();
        hideAuthSuccess();
        // Restore body scrolling when modal is closed
        document.body.style.overflow = '';
    }
}

/**
 * Handle login form submission
 */
async function handleLogin(e) {
    e.preventDefault();
    
    const usernameEl = document.getElementById('loginUsername') || document.getElementById('loginEmail');
    const username = (usernameEl && usernameEl.value || '').trim();
    const password = document.getElementById('loginPassword').value;
    
    if (!username || !password) {
        showAuthError('Please enter username and password');
        return;
    }
    
    const submitButton = e.target.querySelector('button[type="submit"]');
    submitButton.disabled = true;
    submitButton.textContent = 'Logging in...';
    
    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(_isCuttleMobileClient() ? { 'X-Cuttle-Client': 'mobile' } : {}),
            },
            credentials: 'include',
            body: JSON.stringify({ username, password })
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentUser = data.user;
            _persistSessionTokenFromAuthResponse(data);
            showAuthSuccess('Login successful!');
            _setAuthRequiredGate(false);
            _emitAuthChanged();
            
            setTimeout(() => {
                closeAuthModal();
                updateUIForAuthenticatedUser();
                loadChatSessions();
            }, 600);
        } else {
            showAuthError(data.error || 'Login failed');
        }
    } catch (error) {
        console.error('Login error:', error);
        showAuthError('Network error. Please try again.');
    } finally {
        submitButton.disabled = false;
        submitButton.textContent = 'Login';
    }
}

async function handleGuestSignIn() {
    const guestButtons = document.querySelectorAll('.auth-guest-button');
    guestButtons.forEach((el) => {
        el.disabled = true;
        el.textContent = 'Starting guest…';
    });
    try {
        const response = await fetch('/api/auth/guest', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(_isCuttleMobileClient() ? { 'X-Cuttle-Client': 'mobile' } : {}),
            },
            credentials: 'include',
            body: '{}',
        });
        const data = await response.json();
        if (data.success) {
            currentUser = data.user;
            _persistSessionTokenFromAuthResponse(data);
            _setAuthRequiredGate(false);
            _emitAuthChanged();
            closeAuthModal();
            updateUIForAuthenticatedUser();
            loadChatSessions();
        } else {
            showAuthError(data.error || 'Guest sign-in failed');
        }
    } catch (error) {
        console.error('Guest sign-in error:', error);
        showAuthError('Network error. Please try again.');
    } finally {
        guestButtons.forEach((el) => {
            el.disabled = false;
            el.textContent = 'Continue as guest';
        });
    }
}

/**
 * Handle signup form submission
 */
async function handleSignup(e) {
    e.preventDefault();
    
    const displayName = document.getElementById('signupName').value.trim();
    const usernameEl = document.getElementById('signupUsername') || document.getElementById('signupEmail');
    const username = (usernameEl && usernameEl.value || '').trim();
    const password = document.getElementById('signupPassword').value;
    const confirmPassword = document.getElementById('signupConfirmPassword').value;
    
    if (!displayName || !username || !password || !confirmPassword) {
        showAuthError('Please fill in all fields');
        return;
    }
    
    if (password !== confirmPassword) {
        showAuthError('Passwords do not match');
        return;
    }
    
    if (password.length < 8) {
        showAuthError('Password must be at least 8 characters long');
        return;
    }
    
    const submitButton = e.target.querySelector('button[type="submit"]');
    submitButton.disabled = true;
    submitButton.textContent = 'Creating account...';
    
    try {
        const response = await fetch('/api/auth/register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(_isCuttleMobileClient() ? { 'X-Cuttle-Client': 'mobile' } : {}),
            },
            credentials: 'include',
            body: JSON.stringify({ 
                display_name: displayName,
                username,
                password 
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentUser = data.user;
            _persistSessionTokenFromAuthResponse(data);
            showAuthSuccess('Account created!');
            _setAuthRequiredGate(false);
            _emitAuthChanged();
            
            setTimeout(() => {
                closeAuthModal();
                updateUIForAuthenticatedUser();
                loadChatSessions();
            }, 600);
        } else {
            showAuthError(data.error || 'Registration failed');
        }
    } catch (error) {
        console.error('Signup error:', error);
        showAuthError('Network error. Please try again.');
    } finally {
        submitButton.disabled = false;
        submitButton.textContent = 'Create Account';
    }
}

/**
 * Handle logout
 * @param {Object} [opts]
 * @param {boolean} [opts.skipRedirect] - when true (app shell), don't navigate away
 */
async function handleLogout(opts = {}) {
    const skipRedirect = !!(opts && opts.skipRedirect);
    try {
        const response = await fetch('/api/auth/logout', {
            method: 'POST',
            credentials: 'include'
        });

        const data = await response.json();

        if (data.success) {
            currentUser = null;
            currentChatSession = null;
            chatSessions = [];
            setStoredSessionToken('');
            _flushMobileAuthCookies('');
            updateUIForUnauthenticatedUser();
            _emitAuthChanged({ allowParentSignOut: true });
            // Sign-out returns to the auth dialog (Guest stays an explicit
            // alternative on the login tab). Remote viewports follow via
            // _applyRemoteAuthUser so every pane agrees.
            openAuthModal('login');

            // Hard redirect only for standalone pages (landing), not the app shell.
            if (!skipRedirect && !document.getElementById('appShell') && window.parent === window) {
                window.location.href = '/';
            }
        }
    } catch (error) {
        console.error('Logout error:', error);
    }
}

/**
 * Load user's chat sessions (does not broadcast — callers emit once if needed).
 */
async function loadChatSessions() {
    if (!_pageNeedsChatSessions()) return;
    const requestingUserId = currentUser ? currentUser.id : null;
    try {
        const broker = window.parent !== window && window.parent.cuttleActivityBroker;
        const data = broker ? {success: true, sessions: await broker.sessions(false, requestingUserId)}
            : await (await fetch('/api/auth/sessions', {credentials: 'include'})).json();

        if ((currentUser ? currentUser.id : null) !== requestingUserId) return;
        if (data.success) {
            chatSessions = data.sessions || [];

            if (chatSessions.length > 0) {
                // Keep the active session if it's still in the list; otherwise leave
                // it alone (chat_page welcome uses null = new chat, don't force latest).
                const stillThere = chatSessions.some(s => String(s.id) === String(currentChatSession));
                if (currentChatSession == null || !stillThere) {
                    // Only auto-pick latest on pages that hydrate the message pane here
                    // (landing). chat_page owns selection via its own UI.
                    if (document.getElementById('chatMessages') && !document.getElementById('chatHistory')) {
                        currentChatSession = chatSessions[0].id;
                        await loadChatHistory(currentChatSession);
                    }
                } else if (document.getElementById('chatMessages') && !document.getElementById('chatHistory')) {
                    await loadChatHistory(currentChatSession);
                }
            }

            updateSessionsSidebar();
        }
    } catch (error) {
        console.error('Error loading chat sessions:', error);
    }
}

/**
 * Strip db_session_ prefix — auth routes only accept bare integer ids.
 */
function toAuthDbSessionId(sessionId) {
    if (sessionId == null || sessionId === '') return null;
    let s = String(sessionId).trim();
    if (s.startsWith('db_session_')) s = s.slice('db_session_'.length);
    return s || null;
}

/**
 * Load chat history for a session
 */
async function loadChatHistory(sessionId) {
    try {
        const authSid = toAuthDbSessionId(sessionId);
        const response = await fetch(`/api/auth/sessions/${encodeURIComponent(authSid)}/messages`, {
            credentials: 'include'
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        
        if (data.success) {
            // Clear existing messages
            const chatMessages = document.getElementById('chatMessages');
            if (chatMessages) {
                chatMessages.innerHTML = '';
                
                // Add messages
                for (const msg of data.messages) {
                    displayMessage(msg.role, msg.content, false);
                }
                
                // Scroll to bottom
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
        }
    } catch (error) {
        console.error('Error loading chat history:', error);
    }
}

/**
 * Create new chat session
 */
async function createNewChatSession() {
    try {
        const sessionName = prompt('Enter session name (or leave empty for auto-name):');
        
        if (sessionName === null) {
            return; // User cancelled
        }
        
        const response = await fetch('/api/auth/sessions', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({ session_name: sessionName })
        });
        
        const data = await response.json();
        
        if (data.success) {
            await loadChatSessions();
            switchChatSession(data.session_id);
        }
    } catch (error) {
        console.error('Error creating chat session:', error);
    }
}

/**
 * Delete a chat session
 */
async function deleteChatSession(sessionId, event) {
    event.stopPropagation();
    
    if (!confirm('Are you sure you want to delete this chat session?')) {
        return;
    }
    
    try {
        const authSid = toAuthDbSessionId(sessionId);
        const response = await fetch(`/api/auth/sessions/${encodeURIComponent(authSid)}`, {
            method: 'DELETE',
            credentials: 'include'
        });
        
        const data = await response.json();
        
        if (data.success) {
            await loadChatSessions();
            
            // If deleted current session, switch to first available
            if (String(currentChatSession) === String(sessionId)
                || String(toAuthDbSessionId(currentChatSession)) === String(authSid)) {
                if (chatSessions.length > 0) {
                    switchChatSession(chatSessions[0].id);
                } else {
                    // No sessions left, create a new one
                    await createNewChatSession();
                }
            }
        }
    } catch (error) {
        console.error('Error deleting chat session:', error);
    }
}

/**
 * Switch to a different chat session
 */
function switchChatSession(sessionId) {
    currentChatSession = sessionId;
    loadChatHistory(sessionId);
    updateSessionsSidebar();
}

/**
 * Update sessions sidebar
 */
function updateSessionsSidebar() {
    const sidebar = document.getElementById('chatSessionsSidebar');
    const sessionsList = document.getElementById('sessionsList');
    
    if (!sidebar || !sessionsList) return;
    
    if (currentUser && chatSessions.length > 0) {
        sidebar.classList.add('active');
        
        sessionsList.innerHTML = '';
        
        for (const session of chatSessions) {
            const sessionItem = document.createElement('div');
            sessionItem.className = 'session-item' + (session.id === currentChatSession ? ' active' : '');
            sessionItem.onclick = () => switchChatSession(session.id);
            
            const sessionName = document.createElement('div');
            sessionName.className = 'session-name';
            sessionName.textContent = session.session_name || `Chat ${session.id}`;
            
            const sessionMeta = document.createElement('div');
            sessionMeta.className = 'session-meta';
            sessionMeta.textContent = `${session.message_count || 0} messages`;
            
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'session-delete';
            deleteBtn.innerHTML = '×';
            deleteBtn.onclick = (e) => deleteChatSession(session.id, e);
            
            sessionItem.appendChild(sessionName);
            sessionItem.appendChild(sessionMeta);
            sessionItem.appendChild(deleteBtn);
            
            sessionsList.appendChild(sessionItem);
        }
    } else {
        sidebar.classList.remove('active');
    }
}

/**
 * Update UI for authenticated user
 */
function updateUIForAuthenticatedUser() {
    const homePage = document.getElementById('homePage');
    if (homePage) homePage.classList.remove('auth-landing-hidden');
    const loginButton = document.getElementById('loginButton');
    const userMenu = document.getElementById('userMenu');
    const userName = document.getElementById('userName');
    const userAvatar = document.getElementById('userAvatar');
    const authAccountLabel = document.getElementById('authAccountLabel');
    
    if (loginButton) {
        loginButton.style.display = 'none';
    }
    
    if (userMenu) {
        userMenu.classList.add('active');
        userMenu.style.display = '';
    }
    
    if (userName && currentUser) {
        userName.textContent = currentUser.display_name || currentUser.username || 'User';
    }

    if (authAccountLabel && currentUser) {
        authAccountLabel.textContent = currentUser.username
            ? `@${currentUser.username}`
            : (currentUser.display_name || 'Signed in');
    }
    
    if (userAvatar && currentUser) {
        userAvatar.innerHTML = userAvatarInnerHtml(currentUser);
    }

    const linkGoogle = document.getElementById('userMenuLinkGoogle');
    if (linkGoogle && currentUser) {
        linkGoogle.hidden = !_userCanLinkGoogle(currentUser);
    }
    
    // Show fullscreen chat view (hide hero section)
    enterFullscreenChat();
}

/**
 * Update UI for unauthenticated user
 */
function updateUIForUnauthenticatedUser() {
    const homePage = document.getElementById('homePage');
    if (homePage) homePage.classList.add('auth-landing-hidden');
    const loginButton = document.getElementById('loginButton');
    const userMenu = document.getElementById('userMenu');
    const authAccountLabel = document.getElementById('authAccountLabel');
    
    if (loginButton) {
        loginButton.style.display = 'block';
    }
    
    if (userMenu) {
        userMenu.classList.remove('active');
        userMenu.style.display = 'none';
    }

    if (authAccountLabel) {
        authAccountLabel.textContent = '';
    }
    
    // Show normal view
    exitFullscreenChat();
}

/**
 * Enter fullscreen chat mode (landing page only)
 */
function enterFullscreenChat() {
    const heroSection = document.querySelector('.hero');
    if (!heroSection) return;
    const featuresSection = document.querySelector('.features');
    const chatContainer = document.querySelector('.chat-container');

    heroSection.style.display = 'none';

    if (featuresSection) {
        featuresSection.style.display = 'none';
    }

    if (chatContainer) {
        chatContainer.style.height = 'calc(100vh - 120px)';
        chatContainer.style.maxHeight = 'calc(100vh - 120px)';
    }
}

/**
 * Exit fullscreen chat mode
 */
function exitFullscreenChat() {
    const heroSection = document.querySelector('.hero');
    const featuresSection = document.querySelector('.features');
    const chatContainer = document.querySelector('.chat-container');
    
    if (heroSection) {
        heroSection.style.display = 'block';
    }
    
    if (featuresSection) {
        featuresSection.style.display = 'block';
    }
    
    if (chatContainer) {
        chatContainer.style.height = '';
        chatContainer.style.maxHeight = '';
    }
}

/**
 * Toggle user menu dropdown
 */
function toggleUserMenu() {
    const dropdown = document.getElementById('userMenuDropdown');
    if (dropdown) {
        dropdown.classList.toggle('active');
    }
}

// Close dropdown when clicking outside
document.addEventListener('click', function(e) {
    const userMenuButton = document.getElementById('userMenuButton');
    const userMenuDropdown = document.getElementById('userMenuDropdown');
    
    if (userMenuButton && userMenuDropdown) {
        if (!userMenuButton.contains(e.target) && !userMenuDropdown.contains(e.target)) {
            userMenuDropdown.classList.remove('active');
        }
    }
});

/**
 * Show auth error message
 */
function showAuthError(message) {
    const errorDiv = document.getElementById('authError');
    if (errorDiv) {
        errorDiv.textContent = message;
        errorDiv.classList.add('active');
    }
}

/**
 * Hide auth error message
 */
function hideAuthError() {
    const errorDiv = document.getElementById('authError');
    if (errorDiv) {
        errorDiv.classList.remove('active');
    }
}

/**
 * Show auth success message
 */
function showAuthSuccess(message) {
    const successDiv = document.getElementById('authSuccess');
    if (successDiv) {
        successDiv.textContent = message;
        successDiv.classList.add('active');
    }
}

/**
 * Hide auth success message
 */
function hideAuthSuccess() {
    const successDiv = document.getElementById('authSuccess');
    if (successDiv) {
        successDiv.classList.remove('active');
    }
}

/**
 * Helper function to display messages in the chat
 */
function displayMessage(sender, text) {
    const chatMessages = document.getElementById('chatMessages');
    if (!chatMessages) {
        console.error('chatMessages element not found');
        return;
    }
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;
    
    const agentName = localStorage.getItem('agentName') || 'Cuttle';
    const prefix = sender === 'user' ? '👤 You:' : `🦑 ${agentName}:`;
    messageDiv.innerHTML = `<strong>${prefix}</strong><br>${text.replace(/\n/g, '<br>')}`;
    
    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

/**
 * Provide displayMessage function on window for other scripts to use
 */
window.displayMessage = displayMessage;

/**
 * Update sendMessage function to use current session
 * We'll check if we're on the landing page, and if so, let the landing page's
 * sendMessage handle everything. Otherwise, provide a basic implementation.
 */

// Wait for DOM to be ready before checking if we're on landing page
document.addEventListener('DOMContentLoaded', function() {
    // Check if we're on the landing page by looking for the fullscreen chat
    const isLandingPage = document.getElementById('fullscreenChat') !== null;
    
    // Only define sendMessage if NOT on landing page and it doesn't exist yet
    if (!isLandingPage && typeof window.sendMessage !== 'function') {
        window.sendMessage = async function() {
            const chatInput = document.getElementById('chatInput');
            const sendButton = document.getElementById('sendButton');
            
            if (!chatInput || !sendButton) return;
            
            const message = chatInput.value.trim();
            
            if (!message) return;
            
            // Disable input
            chatInput.disabled = true;
            sendButton.disabled = true;
            sendButton.textContent = 'Sending...';
            
            // Display user message
            displayMessage('user', message);
            
            // Clear input
            chatInput.value = '';
            
            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    credentials: 'include',
                    body: JSON.stringify({
                        message: message,
                        session_id: currentChatSession
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    displayMessage('assistant', data.response);
                    
                    // Update session ID if changed
                    if (data.session_id) {
                        currentChatSession = data.session_id;
                    }
                } else {
                    displayMessage('assistant', data.response || 'Sorry, I encountered an error processing your message.');
                }
            } catch (error) {
                console.error('Error sending message:', error);
                displayMessage('assistant', 'Sorry, I encountered a network error. Please try again.');
            } finally {
                // Re-enable input
                chatInput.disabled = false;
                sendButton.disabled = false;
                sendButton.textContent = 'Send';
                chatInput.focus();
            }
        };
    }
});

