/* Shell account panel: profile summary, Google link, sign-out, and the
 * change-username / change-password forms. The shell owns the signed-in user
 * and pane propagation; this module owns the panel DOM and its requests.
 */
(function (root) {
    'use strict';

    function create(deps) {
        const user = () => deps.getUser();

        function getModal() {
            return document.getElementById('logoutConfirmModal');
        }

        function close() {
            const modal = getModal();
            if (!modal) return;
            modal.classList.remove('active');
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
            deps.syncServerRows();
            clearProfileForms();

            const name = deps.displayName(user());
            const title = document.getElementById('logoutConfirmTitle');
            if (title) title.textContent = name || 'Account';

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
                if (isGuest && !linked) {
                    subtitle.textContent = uname
                        ? `${uname} — sign in with Google to keep this account, or log out.`
                        : 'Sign in with Google to keep this account, or log out.';
                } else {
                    subtitle.textContent = uname
                        ? `${uname} — connect Google for your avatar, or log out.`
                        : 'Connect Google for your avatar, or log out.';
                }
            }

            const avatar = document.getElementById('logoutConfirmAvatar');
            if (avatar) {
                avatar.innerHTML = deps.avatarHtml(user());
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

            const linkBtn = document.getElementById('logoutConfirmLinkGoogle');
            const refreshBtn = document.getElementById('logoutConfirmRefreshGoogle');
            const statusEl = document.getElementById('logoutConfirmGoogleStatus');

            if (linkBtn) {
                linkBtn.hidden = !canLinkGoogle;
                linkBtn.textContent = isGuest ? 'Continue with Google' : 'Connect Google';
                linkBtn.href = '/api/auth/oauth/google?link=1';
            }
            if (refreshBtn) {
                refreshBtn.hidden = !(canManageGoogle && linked && !isGuest);
            }
            if (statusEl) {
                statusEl.hidden = !(canManageGoogle && linked);
                statusEl.textContent = linked ? 'Google linked — avatar syncs to this account.' : '';
            }

            modal.classList.add('active');
            const cancel = document.getElementById('logoutConfirmCancel');
            if (cancel) cancel.focus();
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
                    deps.setUser(null);
                    return;
                }
                try {
                    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
                } catch (_) {}
                deps.setUser(null);
                document.querySelectorAll('.split-column .shell-main iframe').forEach(fr => {
                    try {
                        fr.contentWindow?.postMessage({ type: 'cuttle-auth-changed', user: null }, '*');
                    } catch (_) {}
                });
            } finally {
                close();
            }
        }

        function setup() {
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
