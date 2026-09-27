/* ================================================================
   Cuttle Toast System — app-wide modern toast notifications
   Works in app_shell (iframe parent) and standalone pages.
   Use: showToast('Message', 'success'|'error'|'info'|'warning')
   ================================================================ */

(function() {
    'use strict';

    const TOAST_DURATION = 4000;
    const NOTIFICATION_HISTORY_MAX = 50;
    const VARIANTS = {
        success: { bg: '#0d3320', border: '#2ea043', icon: '✓' },
        error:   { bg: '#3d1f1f', border: '#f85149', icon: '✕' },
        warning: { bg: '#3d2e0f', border: '#d29922', icon: '⚠' },
        info:    { bg: '#1a2332', border: '#58a6ff', icon: 'ℹ' }
    };

    const notificationHistory = [];
    let _chirpAudio = null;
    let _lastChirpAt = 0;
    let _chirpInFlight = false;
    const CHIRP_DEBOUNCE_MS = 5000;
    const CHIRP_STORAGE_KEY = 'responseChirpEnabled';
    const CHIRP_SHARED_AT_KEY = 'cuttleLastChirpAt';

    function isResponseChirpEnabled() {
        try {
            // Default on; respect notifications master switch if present
            if (localStorage.getItem('notificationsEnabled') === 'false') return false;
            return localStorage.getItem(CHIRP_STORAGE_KEY) !== 'false';
        } catch (_) {
            return true;
        }
    }

    function playWebChirp() {
        try {
            if (!_chirpAudio) {
                _chirpAudio = new Audio('/sounds/completion-chirp.wav');
                _chirpAudio.preload = 'auto';
                _chirpAudio.volume = 0.55;
            }
            _chirpAudio.currentTime = 0;
            const p = _chirpAudio.play();
            if (p && typeof p.catch === 'function') p.catch(() => {});
            return true;
        } catch (_) {
            return false;
        }
    }

    function claimChirpSlot() {
        const now = Date.now();
        if (_chirpInFlight) return false;
        if (now - _lastChirpAt < CHIRP_DEBOUNCE_MS) return false;
        try {
            const shared = parseInt(localStorage.getItem(CHIRP_SHARED_AT_KEY) || '0', 10) || 0;
            if (now - shared < CHIRP_DEBOUNCE_MS) return false;
            localStorage.setItem(CHIRP_SHARED_AT_KEY, String(now));
        } catch (_) {}
        _lastChirpAt = now;
        return true;
    }

    /**
     * Play the short "reply ready" chirp.
     * Electron minimized/tray: main-process native WAV (always audible).
     * Browser / focused Electron: HTML5 Audio from /sounds/.
     * Chat iframes should postMessage { type: 'cuttle-completion-chirp' } to the shell
     * and must NOT play locally — parent owns debounce + single playback.
     */
    async function playCuttleCompletionChirp() {
        if (!isResponseChirpEnabled()) return;

        // Inside app_shell iframe → ask parent (has Electron bridge + toast host).
        // Do not consume the debounce slot here; parent is the single player.
        if (window.parent !== window) {
            try {
                window.parent.postMessage({ type: 'cuttle-completion-chirp' }, '*');
            } catch (_) {}
            return;
        }

        if (!claimChirpSlot()) return;
        _chirpInFlight = true;
        try {
            const electron = window.electron;
            if (electron && electron.isElectron && typeof electron.playChirp === 'function') {
                let obscured = document.visibilityState === 'hidden';
                if (!obscured && typeof electron.isWindowObscured === 'function') {
                    try {
                        obscured = !!(await electron.isWindowObscured());
                    } catch (_) {}
                }
                if (obscured) {
                    electron.playChirp();
                    return;
                }
                // Focused: in-page audio only — never also fire native (that was a
                // double-chirp when play() appeared to fail then recovered).
                if (!playWebChirp()) electron.playChirp();
                return;
            }

            playWebChirp();
        } finally {
            _chirpInFlight = false;
        }
    }

    window.playCuttleCompletionChirp = playCuttleCompletionChirp;

    function createToastEl(message, variant, options) {
        const v = VARIANTS[variant] || VARIANTS.info;
        const linkUrl = options && options.linkUrl;
        const linkText = (options && options.linkText) || 'View query log';
        const el = document.createElement('div');
        el.className = 'cuttle-toast cuttle-toast-' + (variant || 'info');
        el.setAttribute('role', 'alert');
        const linkHtml = linkUrl ? ` <a href="${escapeHtml(linkUrl)}" target="_blank" rel="noopener" class="cuttle-toast-link">${escapeHtml(linkText)}</a>` : '';
        el.innerHTML = `
            <span class="cuttle-toast-icon">${v.icon}</span>
            <span class="cuttle-toast-message">${escapeHtml(String(message))}${linkHtml}</span>
            <button type="button" class="cuttle-toast-close" aria-label="Close">&times;</button>
        `;
        el.style.cssText = `
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 14px 12px 14px 20px;
            background: ${v.bg};
            border: 1px solid ${v.border};
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.05) inset;
            color: #e6edf3;
            font-size: 14px;
            font-weight: 500;
            z-index: 999999;
            animation: cuttle-toast-in 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
            max-width: 380px;
            line-height: 1.4;
        `;

        const icon = el.querySelector('.cuttle-toast-icon');
        if (icon) icon.style.cssText = 'color:' + v.border + ';font-size:18px;flex-shrink:0';

        const closeBtn = el.querySelector('.cuttle-toast-close');
        if (closeBtn) {
            closeBtn.style.cssText = 'flex-shrink:0;width:24px;height:24px;padding:0;border:none;background:transparent;color:rgba(255,255,255,0.6);font-size:20px;line-height:1;cursor:pointer;border-radius:4px;display:flex;align-items:center;justify-content:center;transition:color 0.15s, background 0.15s;';
            closeBtn.addEventListener('mouseenter', function() { this.style.color = '#fff'; this.style.background = 'rgba(255,255,255,0.1)'; });
            closeBtn.addEventListener('mouseleave', function() { this.style.color = 'rgba(255,255,255,0.6)'; this.style.background = 'transparent'; });
        }
        const toastLink = el.querySelector('.cuttle-toast-link');
        if (toastLink) toastLink.style.cssText = 'color:' + v.border + ';margin-left:6px;white-space:nowrap;text-decoration:underline;';

        return el;
    }

    function escapeHtml(s) {
        const div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }

    function ensureContainer() {
        let c = document.getElementById('cuttle-toast-container');
        if (!c) {
            c = document.createElement('div');
            c.id = 'cuttle-toast-container';
            c.style.cssText = 'position:fixed;bottom:0;right:0;padding:24px;z-index:999998;pointer-events:none;display:flex;flex-direction:column;align-items:flex-end;gap:10px;';
            document.body.appendChild(c);
        }
        return c;
    }

    let _notifSeq = 0;

    function pushNotificationHistory(message, variant, options) {
        const rec = {
            id: 'n_' + Date.now() + '_' + (++_notifSeq),
            message: String(message),
            variant: (variant || 'info').toLowerCase(),
            time: Date.now(),
            read: false,
            unread: !!(options && options.unread),
            kind: (options && options.kind) || null,
            sessionId: (options && options.sessionId != null) ? String(options.sessionId) : null
        };
        if (options && options.linkUrl) {
            rec.linkUrl = options.linkUrl;
            rec.linkText = options.linkText || 'View query log';
        }
        notificationHistory.unshift(rec);
        if (notificationHistory.length > NOTIFICATION_HISTORY_MAX) notificationHistory.pop();
        window.dispatchEvent(new CustomEvent('cuttle-notification-added', {
            detail: {
                count: notificationHistory.length,
                unreadCount: countUnreadNotifications()
            }
        }));
    }

    function countUnreadNotifications() {
        return notificationHistory.filter((n) => n && n.unread && !n.read).length;
    }

    function markNotificationsRead(ids) {
        let changed = false;
        if (ids == null) {
            notificationHistory.forEach((n) => {
                if (n && !n.read) {
                    n.read = true;
                    changed = true;
                }
            });
        } else {
            const want = new Set((Array.isArray(ids) ? ids : [ids]).map(String));
            notificationHistory.forEach((n) => {
                if (n && want.has(String(n.id)) && !n.read) {
                    n.read = true;
                    changed = true;
                }
            });
        }
        if (changed) {
            window.dispatchEvent(new CustomEvent('cuttle-notification-added', {
                detail: {
                    count: notificationHistory.length,
                    unreadCount: countUnreadNotifications()
                }
            }));
        }
        return changed;
    }

    function displayToast(message, variant, options) {
        if (!message) return;
        variant = (variant || 'info').toLowerCase();
        if (!VARIANTS[variant]) variant = 'info';
        options = options && typeof options === 'object' ? options : {};

        pushNotificationHistory(message, variant, options);

        const container = ensureContainer();
        const el = createToastEl(message, variant, options);
        container.appendChild(el);

        const animateOut = () => {
            el.style.animation = 'cuttle-toast-out 0.25s ease forwards';
            setTimeout(() => el.remove(), 250);
        };

        const timeout = setTimeout(animateOut, TOAST_DURATION);

        const dismiss = (e) => {
            if (e && e.target && e.target.classList && e.target.classList.contains('cuttle-toast-close')) return;
            clearTimeout(timeout);
            animateOut();
        };

        el.querySelector('.cuttle-toast-close')?.addEventListener('click', (e) => {
            e.stopPropagation();
            clearTimeout(timeout);
            animateOut();
        });
        el.addEventListener('click', (e) => {
            if (e.target.closest('.cuttle-toast-close')) return;
            const actionId = options && options.actionId;
            if (actionId) {
                try {
                    window.dispatchEvent(new CustomEvent('cuttle-toast-action', {
                        detail: { actionId: String(actionId) }
                    }));
                } catch (_) {}
                try {
                    document.querySelectorAll('iframe').forEach((frame) => {
                        try {
                            frame.contentWindow && frame.contentWindow.postMessage({
                                type: 'cuttle-toast-action',
                                actionId: String(actionId)
                            }, '*');
                        } catch (_2) {}
                    });
                } catch (_) {}
            }
            dismiss(e);
        });
    }

    function notifyTrayIfHidden(message, variant) {
        if (document.visibilityState === 'hidden') {
            fetch('/api/toast', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message, variant: variant || 'info' })
            }).catch(() => {});
        }
    }

    function showToast(message, variant, options) {
        if (!message) return;
        options = options && typeof options === 'object' ? options : {};
        if (window.parent !== window) {
            window.parent.postMessage({
                type: 'cuttle-toast',
                message,
                variant,
                linkUrl: options.linkUrl,
                linkText: options.linkText,
                actionId: options.actionId || null,
                unread: !!options.unread,
                sessionId: options.sessionId != null ? String(options.sessionId) : null,
                kind: options.kind || null
            }, '*');
            return;
        }
        displayToast(message, variant, options);
    }

    function handleMessage(e) {
        if (e.data && e.data.type === 'cuttle-completion-chirp') {
            playCuttleCompletionChirp();
            return;
        }
        if (e.data && e.data.type === 'cuttle-toast-action' && e.data.actionId) {
            try {
                window.dispatchEvent(new CustomEvent('cuttle-toast-action', {
                    detail: { actionId: String(e.data.actionId) }
                }));
            } catch (_) {}
            return;
        }
        if (e.data && e.data.type === 'cuttle-toast') {
            const { message, variant, linkUrl, linkText, unread, sessionId, kind, actionId } = e.data;
            const options = {
                unread: !!unread,
                sessionId: sessionId != null ? String(sessionId) : null,
                kind: kind || null,
                actionId: actionId || null
            };
            if (linkUrl) {
                options.linkUrl = linkUrl;
                options.linkText = linkText || 'View query log';
            }
            displayToast(message, variant, options);
            if (e.data.tray !== false) notifyTrayIfHidden(message, variant);
        }
    }

    window.addEventListener('message', handleMessage);
    window.showToast = window.showCuttleToast = showToast;
    window.getCuttleNotificationHistory = function() { return notificationHistory.slice(); };
    window.getCuttleUnreadNotificationCount = countUnreadNotifications;
    window.markCuttleNotificationsRead = markNotificationsRead;
    window.clearCuttleNotificationHistory = function() {
        notificationHistory.length = 0;
        window.dispatchEvent(new CustomEvent('cuttle-notification-added', {
            detail: { count: 0, unreadCount: 0 }
        }));
    };

    const style = document.createElement('style');
    style.textContent = `
        @keyframes cuttle-toast-in {
            from { opacity: 0; transform: translateY(20px) scale(0.95); }
            to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes cuttle-toast-out {
            from { opacity: 1; transform: translateY(0) scale(1); }
            to   { opacity: 0; transform: translateY(-10px) scale(0.95); }
        }
        .cuttle-toast { pointer-events: auto; cursor: pointer; }
        .cuttle-toast-close { margin-left: auto; }
    `;
    document.head.appendChild(style);
})();
