/* Shell account panel: profile summary, Google link, sign-out, and the
 * change-username / change-password forms. This module owns the account
 * lifetime: the signed-in user, the rail account button rendering, the
 * /api/auth/me refresh, the account click (sign-in / sign-out) and the
 * auth query-flag cleanup. The shell supplies transport/effects it does
 * not own (mobile server rows, rail apps broadcast, spaces activity
 * reset) as injected deps.
 */
(function (root) {
    'use strict';

    function create(deps) {
        const win = deps.win || (typeof window !== 'undefined' ? window : globalThis);
        const doc = deps.doc || win.document;
        const fetchImpl = deps.fetch || ((...a) => win.fetch(...a));
        const escapeHtml = deps.escapeHtml || ((s) => String(s));
        const navigate = deps.navigate;
        const syncServerRows = () => { try { if (deps.syncServerRows) deps.syncServerRows(); } catch (_) {} };
        let currentUser = null;
        const user = () => currentUser;

        const ACCOUNT_PLACEHOLDER_SVG =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';

        function displayName(u) {
            if (!u) return '';
            return u.username ? '@' + u.username : (u.display_name || u.email || 'Signed in');
        }

        function initials(u) {
            if (win.CuttleAuth && win.CuttleAuth.userAvatarInitials) {
                return win.CuttleAuth.userAvatarInitials(u);
            }
            const label = u.display_name || u.username || u.email || '?';
            const letters = label.trim().split(/[\s._-]+/).filter(Boolean).map(p => p[0]).join('');
            return (letters || '?').toUpperCase().slice(0, 2);
        }

        function avatarInnerHtml(u) {
            if (win.CuttleAuth && win.CuttleAuth.userAvatarInnerHtml) {
                return win.CuttleAuth.userAvatarInnerHtml(u);
            }
            if (u.profile_image) {
                return `<img src="${escapeHtml(u.profile_image)}" alt="" referrerpolicy="no-referrer">`;
            }
            return initials(u);
        }

        function renderButtons() {
            doc.querySelectorAll('.rail-account').forEach(btn => {
                const avatar = btn.querySelector('.rail-account-avatar');
                btn.classList.toggle('signed-in', !!currentUser);
                btn.classList.toggle('signed-out', !currentUser);
                if (currentUser) {
                    const name = displayName(currentUser);
                    btn.dataset.tooltip = 'Account — ' + name;
                    btn.setAttribute('aria-label', 'Account — signed in as ' + name + '. Click to log out');
                    if (avatar) {
                        avatar.innerHTML = avatarInnerHtml(currentUser);
                    }
                } else {
                    btn.dataset.tooltip = 'Account — signed out';
                    btn.setAttribute('aria-label', 'Account — signed out. Click to sign in');
                    if (avatar) avatar.innerHTML = ACCOUNT_PLACEHOLDER_SVG;
                }
            });
        }

        function getUser() { return currentUser; }

        function setUser(next) {
            const previousId = currentUser && currentUser.id;
            currentUser = next || null;
            if (previousId !== (currentUser && currentUser.id)) {
                try { if (deps.resetActivity) deps.resetActivity(); } catch (_) {}
                try { if (deps.refreshActivityBroker) deps.refreshActivityBroker(); } catch (_) {}
            }
            renderButtons();
            // The Account Apps-grid tile carries the profile photo — rebroadcast so
            // an open Apps page swaps the placeholder for the picture on sign in/out.
            try { if (deps.broadcastAppsList) deps.broadcastAppsList(); } catch (_) {}
        }

        async function refreshAuth() {
            try {
                const res = await fetchImpl('/api/auth/me', { credentials: 'include', cache: 'no-store' });
                const data = await res.json().catch(() => null);
                if (data && typeof data.authenticated === 'boolean') {
                    setUser(data.authenticated ? data.user : null);
                }
                // Network/parse failure: keep prior user (don't flash signed-out).
            } catch (_) {
                /* keep prior */
            }
        }

        /** Open the app-wide sign-in modal (lives on the shell, not the chat page). */
        function openSignIn() {
            if (typeof win.openAuthModal === 'function') {
                win.openAuthModal('login');
                return;
            }
            if (win.CuttleAuth && typeof win.CuttleAuth.openAuthModal === 'function') {
                win.CuttleAuth.openAuthModal('login');
                return;
            }
            // Fallback: ask the chat iframe to open its modal (standalone / older builds)
            if (typeof navigate === 'function') navigate(0, '/chat_page.html?signin=1');
        }

        /** Rail account button: sign in when signed out, else open the panel. */
        function handleClick() {
            if (!currentUser) {
                openSignIn();
                return;
            }
            open();
        }

        function handleAuthQueryFlags() {
            try {
                const params = new win.URLSearchParams(win.location.search);
                if (params.has('auth_linked') || params.has('auth_success')) {
                    refreshAuth().then(() => {
                        params.delete('auth_linked');
                        params.delete('auth_success');
                        params.delete('auth_error');
                        const q = params.toString();
                        const next = win.location.pathname + (q ? '?' + q : '') + win.location.hash;
                        win.history.replaceState({}, doc.title, next);
                    });
                } else if (params.has('auth_error')) {
                    const err = params.get('auth_error') || 'unknown';
                    console.warn('OAuth error:', err);
                    params.delete('auth_error');
                    const q = params.toString();
                    win.history.replaceState({}, doc.title, win.location.pathname + (q ? '?' + q : ''));
                }
            } catch (_) {}
        }

        function getModal() {
            return document.getElementById('logoutConfirmModal');
        }

        function modalController() {
            const modal = getModal();
            return root.CuttleModal ? root.CuttleModal.create(modal) : null;
        }

        function close() {
            const modal = getModal();
            if (!modal) return;
            const controller = modalController();
            if (controller) controller.close();
            else modal.classList.remove('active');
            // Drop credential fields from the DOM when the panel closes.
            clearProfileForms();
            const submit = document.getElementById('logoutConfirmSubmit');
            if (submit) {
                submit.disabled = false;
                submit.textContent = 'Log out';
            }
        }

        function open() {
            if (!user()) return;
            const modal = getModal();
            if (!modal) return;
            syncServerRows();
            clearProfileForms();

            const name = displayName(user());
            const title = document.getElementById('logoutConfirmTitle');
            if (title) title.textContent = name || 'Account';

            const avatar = document.getElementById('logoutConfirmAvatar');
            if (avatar) {
                avatar.innerHTML = avatarInnerHtml(user());
            }

            const roleChip = document.getElementById('logoutConfirmRole');
            if (roleChip) {
                const role = (user() && user().role) || '';
                const label = role.charAt(0).toUpperCase() + role.slice(1);
                roleChip.hidden = !label;
                roleChip.textContent = label;
                roleChip.classList.toggle('is-owner', role === 'owner');
                roleChip.classList.toggle('is-guest', role === 'guest');
            }

            renderGoogleControls();
            if (win.CuttleAuth?.refreshGoogleSignInUi) win.CuttleAuth.refreshGoogleSignInUi();

            const controller = modalController();
            if (controller) controller.open();
            else modal.classList.add('active');
            const cancel = document.getElementById('logoutConfirmCancel');
            if (cancel) cancel.focus();
        }

        function renderGoogleControls() {
            if (!user()) return;
            const googleEnabled = win.CuttleAuth?.googleSignInEnabled?.() === true;
            const provider = user().auth_provider || 'local';
            const isGuest = !!(user().is_guest || provider === 'guest');
            const isLocal = provider === 'local';
            const isGoogle = provider === 'google';
            const linked = !!(user().google_linked || user().provider_user_id);
            const canLinkGoogle = window.CuttleAuth && window.CuttleAuth.userCanLinkGoogle
                ? window.CuttleAuth.userCanLinkGoogle(user())
                : ((isLocal || isGuest) && !linked);
            const canManageGoogle = isLocal || isGuest || isGoogle;

            const subtitle = document.getElementById('logoutConfirmSubtitle');
            if (subtitle) {
                const uname = user().username ? `@${user().username}` : '';
                if (!googleEnabled) {
                    subtitle.textContent = uname ? `${uname} — manage your account or log out.` : 'Manage your account or log out.';
                } else if (isGuest && !linked) {
                    subtitle.textContent = uname
                        ? `${uname} — sign in with Google to keep this account, or log out.`
                        : 'Sign in with Google to keep this account, or log out.';
                } else {
                    subtitle.textContent = uname
                        ? `${uname} — connect Google for your avatar, or log out.`
                        : 'Connect Google for your avatar, or log out.';
                }
            }

            const linkBtn = document.getElementById('logoutConfirmLinkGoogle');
            const refreshBtn = document.getElementById('logoutConfirmRefreshGoogle');
            const statusEl = document.getElementById('logoutConfirmGoogleStatus');

            if (linkBtn) {
                linkBtn.hidden = !(googleEnabled && canLinkGoogle);
                linkBtn.textContent = isGuest ? 'Continue with Google' : 'Connect Google';
                linkBtn.href = '/api/auth/oauth/google?link=1';
            }
            if (refreshBtn) {
                refreshBtn.hidden = !(googleEnabled && canManageGoogle && linked && !isGuest);
            }
            if (statusEl) {
                statusEl.hidden = !(googleEnabled && canManageGoogle && linked);
                statusEl.textContent = linked ? 'Google linked — avatar syncs to this account.' : '';
            }
        }

        async function logout() {
            const submit = document.getElementById('logoutConfirmSubmit');
            if (submit) {
                submit.disabled = true;
                submit.textContent = 'Logging out…';
            }
            try {
                if (typeof handleLogout === 'function') {
                    await handleLogout({ skipRedirect: true });
                    setUser(null);
                    return;
                }
                try {
                    await fetchImpl('/api/auth/logout', { method: 'POST', credentials: 'include' });
                } catch (_) {}
                setUser(null);
                document.querySelectorAll('.split-column .shell-main iframe').forEach(fr => {
                    try {
                        fr.contentWindow?.postMessage({ type: 'cuttle-auth-changed', user: null }, '*');
                    } catch (_) {}
                });
            } finally {
                close();
            }
        }

        function bindModal() {
            const modal = getModal();
            if (!modal || modal.dataset.bound === '1') return;
            modal.dataset.bound = '1';

            const closeBtn = document.getElementById('logoutConfirmClose');
            const cancelBtn = document.getElementById('logoutConfirmCancel');
            const submitBtn = document.getElementById('logoutConfirmSubmit');
            const refreshBtn = document.getElementById('logoutConfirmRefreshGoogle');
            const changeUsernameBtn = document.getElementById('logoutConfirmChangeUsername');
            const changePasswordBtn = document.getElementById('logoutConfirmChangePassword');

            if (closeBtn) closeBtn.addEventListener('click', close);
            if (cancelBtn) cancelBtn.addEventListener('click', close);
            if (submitBtn) submitBtn.addEventListener('click', logout);
            if (changeUsernameBtn) changeUsernameBtn.addEventListener('click', showUsernameForm);
            if (changePasswordBtn) changePasswordBtn.addEventListener('click', showPasswordForm);
            if (refreshBtn) {
                refreshBtn.addEventListener('click', () => {
                    window.location.href = '/api/auth/oauth/google?link=1';
                });
            }

            modal.addEventListener('click', (e) => {
                if (e.target === modal) close();
            });

            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && modal.classList.contains('active')) {
                    close();
                }
            });
        }

        /** Wire the account lifetime once: panel DOM, rail button, auth refresh
         *  and the OAuth query-flag cleanup. Called once by app_shell. */
        function setup() {
            bindModal();
            win.addEventListener('cuttle-google-signin-changed', renderGoogleControls);
            renderButtons();
            refreshAuth();
            handleAuthQueryFlags();
            win.addEventListener('focus', refreshAuth);
            win.addEventListener('cuttle-auth-changed', function (ev) {
                setUser((ev.detail && ev.detail.user) || null);
            });
            doc.addEventListener('click', (e) => {
                const t = e.target?.closest?.('#logoutConfirmSwitchServer, #authServerChange');
                if (!t) return;
                e.preventDefault();
                close();
                try { if (deps.openMobileServerSettings) deps.openMobileServerSettings(); } catch (_) {}
            });
        }

        // ── Account panel: change username / change password ────────────
        // Forms are built only while the panel is open (never in static HTML),
        // same as the sign-in modal — keeps password fields out of the parsed DOM.

        function getProfileFormsHost() {
            return document.getElementById('logoutConfirmProfileForms');
        }

        function getProfileStatusEl() {
            return document.getElementById('logoutConfirmProfileStatus');
        }

        function setProfileStatus(message, kind) {
            const el = getProfileStatusEl();
            if (!el) return;
            el.hidden = !message;
            el.textContent = message || '';
            el.classList.toggle('logout-confirm-profile-error', kind === 'error');
            el.classList.toggle('logout-confirm-profile-ok', kind === 'ok');
        }

        function clearProfileForms() {
            const host = getProfileFormsHost();
            if (host) {
                host.innerHTML = '';
                host.hidden = true;
            }
            setProfileStatus('', null);
        }

        function broadcastShellAuthUser() {
            if (!user()) return;
            document.querySelectorAll('.split-column .shell-main iframe').forEach(fr => {
                try {
                    fr.contentWindow?.postMessage({ type: 'cuttle-auth-changed', user: user() }, '*');
                } catch (_) {}
            });
        }

        function buildProfileField(labelText, input) {
            const group = document.createElement('div');
            group.className = 'auth-form-group';
            const label = document.createElement('label');
            label.className = 'auth-form-label';
            label.textContent = labelText;
            group.appendChild(label);
            group.appendChild(input);
            return group;
        }

        function buildProfileInput(id, type, opts) {
            const input = document.createElement('input');
            input.className = 'auth-form-input';
            input.id = id;
            input.type = type;
            input.name = id;
            input.autocomplete = type === 'password' ? 'new-password' : 'off';
            if (opts && opts.value != null) input.value = opts.value;
            if (opts && opts.placeholder) input.placeholder = opts.placeholder;
            return input;
        }

        function buildProfileForm(id, onSubmit) {
            clearProfileForms();
            const host = getProfileFormsHost();
            if (!host) return;
            const form = document.createElement('form');
            form.className = 'auth-form';
            form.id = id;
            form.setAttribute('autocomplete', 'off');
            form.addEventListener('submit', onSubmit);
            host.appendChild(form);
            host.hidden = false;
            return form;
        }

        function showUsernameForm() {
            const form = buildProfileForm('logoutConfirmUsernameForm', submitUsernameChange);
            if (!form || !user()) return;
            form.appendChild(buildProfileField(
                'New username (3–32: letters, numbers, underscore)',
                buildProfileInput('profileUsernameInput', 'text', {
                    value: user().username || '',
                    placeholder: 'username',
                })
            ));
            const save = document.createElement('button');
            save.type = 'submit';
            save.className = 'auth-submit-button';
            save.textContent = 'Save username';
            form.appendChild(save);
            const input = form.querySelector('input');
            if (input) {
                input.focus();
                input.select();
            }
        }

        function showPasswordForm() {
            const form = buildProfileForm('logoutConfirmPasswordForm', submitPasswordChange);
            if (!form) return;
            const needsCurrent = !!(user() && user().auth_provider === 'local'
                && !user().is_guest);
            form.appendChild(buildProfileField(
                needsCurrent ? 'Current password' : 'Current password (leave blank if none set)',
                buildProfileInput('profileCurrentPasswordInput', 'password', {})
            ));
            form.appendChild(buildProfileField(
                'New password (min 8 characters)',
                buildProfileInput('profileNewPasswordInput', 'password', {})
            ));
            form.appendChild(buildProfileField(
                'Confirm new password',
                buildProfileInput('profileConfirmPasswordInput', 'password', {})
            ));
            const save = document.createElement('button');
            save.type = 'submit';
            save.className = 'auth-submit-button';
            save.textContent = 'Save password';
            form.appendChild(save);
            const input = form.querySelector('input');
            if (input) input.focus();
        }

        async function submitUsernameChange(e) {
            e.preventDefault();
            const input = document.getElementById('profileUsernameInput');
            const username = (input && input.value || '').trim();
            if (!/^[a-zA-Z0-9_]{3,32}$/.test(username)) {
                setProfileStatus('Username must be 3–32 characters: letters, numbers, underscore.', 'error');
                return;
            }
            setProfileStatus('', null);
            try {
                const res = await fetch('/api/auth/me', {
                    method: 'PATCH',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username }),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || !data.success) {
                    setProfileStatus((data && data.error) || 'Could not change username.', 'error');
                    return;
                }
                deps.setUser(data.user);
                open(); // refresh title/subtitle for the new handle
                setProfileStatus('Username changed to @' + data.user.username + '.', 'ok');
                broadcastShellAuthUser();
            } catch (_) {
                setProfileStatus('Could not change username (network error).', 'error');
            }
        }

        async function submitPasswordChange(e) {
            e.preventDefault();
            const current = document.getElementById('profileCurrentPasswordInput');
            const next = document.getElementById('profileNewPasswordInput');
            const confirm = document.getElementById('profileConfirmPasswordInput');
            const newPassword = (next && next.value) || '';
            if (newPassword.length < 8) {
                setProfileStatus('New password must be at least 8 characters long.', 'error');
                return;
            }
            if (newPassword !== ((confirm && confirm.value) || '')) {
                setProfileStatus('New passwords do not match.', 'error');
                return;
            }
            setProfileStatus('', null);
            try {
                const res = await fetch('/api/auth/password', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        current_password: (current && current.value) || '',
                        new_password: newPassword,
                    }),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || !data.success) {
                    setProfileStatus((data && data.error) || 'Could not change password.', 'error');
                    return;
                }
                clearProfileForms();
                setProfileStatus('Password updated.', 'ok');
            } catch (_) {
                setProfileStatus('Could not change password (network error).', 'error');
            }
        }

        return { open, close, setup };
    }

    const api = { create };
    root.CuttleAccountPanel = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
