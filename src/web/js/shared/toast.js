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
        // Achievement cards carry their own icon + rarity accent + progress
        // line, so they render as a small card instead of icon + text.
        const ach = options && options.achievement;
        if (ach) {
            el.classList.add('cuttle-toast-achievement');
            el.innerHTML = `
                <span class="cuttle-toast-icon">${escapeHtml(String(ach.icon || '\u{1F3C6}'))}</span>
                <span class="cuttle-toast-message">
                    <strong class="cuttle-toast-ach-title">${escapeHtml(String(ach.title || message))}</strong>
                    ${ach.description ? `<span class="cuttle-toast-ach-desc">${escapeHtml(String(ach.description))}</span>` : ''}
                    ${ach.progress ? `<span class="cuttle-toast-ach-progress">${escapeHtml(String(ach.progress))}</span>` : ''}
                </span>
                <button type="button" class="cuttle-toast-close" aria-label="Close">&times;</button>
            `;
            el.style.borderColor = ach.color || v.border;
            el.style.boxShadow = `0 10px 40px rgba(0,0,0,0.45), 0 0 24px ${ach.color || v.border}55 inset`;
        } else {
        const linkHtml = linkUrl ? ` <a href="${escapeHtml(linkUrl)}" target="_blank" rel="noopener" class="cuttle-toast-link">${escapeHtml(linkText)}</a>` : '';
        el.innerHTML = `
            <span class="cuttle-toast-icon">${v.icon}</span>
            <span class="cuttle-toast-message">${escapeHtml(String(message))}${linkHtml}</span>
            <button type="button" class="cuttle-toast-close" aria-label="Close">&times;</button>
        `;
        }
        if (options && options.progress) {
            const bar = document.createElement('div');
            bar.className = 'cuttle-toast-progress';
            bar.setAttribute('aria-hidden', 'true');
            const fill = document.createElement('span');
            fill.style.background = v.border;
            bar.appendChild(fill);
            el.appendChild(bar);
        }
        el.style.cssText = `
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            padding: 10px 10px 10px 14px;
            background: ${v.bg};
            border: 1px solid ${v.border};
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.05) inset;
            color: #e6edf3;
            font-size: 13px;
            font-weight: 500;
            z-index: 999999;
            animation: cuttle-toast-in 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
            max-width: 300px;
            line-height: 1.4;
        `;

        const icon = el.querySelector('.cuttle-toast-icon');
        if (icon) icon.style.cssText = 'color:' + v.border + ';font-size:16px;flex-shrink:0';

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

        // Transient progress toasts (e.g. "Pushing …") opt out so one job
        // leaves one history entry — the outcome, not the play-by-play.
        if (!options.skipHistory) pushNotificationHistory(message, variant, options);

        const toastId = options.toastId != null ? String(options.toastId) : '';
        const sticky = !!options.sticky;
        if (toastId) closeStickyToast(toastId); // replace same-id toast

        const container = ensureContainer();
        const el = createToastEl(message, variant, options);
        if (toastId) el.dataset.toastId = toastId;
        container.appendChild(el);
        if (sticky && toastId) stickyToasts[toastId] = el;

        const dropStickyRef = () => {
            const key = el.dataset && el.dataset.toastId;
            if (key && stickyToasts[key] === el) delete stickyToasts[key];
        };
        const animateOut = () => {
            dropStickyRef();
            el.style.animation = 'cuttle-toast-out 0.25s ease forwards';
            setTimeout(() => el.remove(), 250);
        };

        if (sticky) {
            // No auto-dismiss and no body-click dismiss; × still closes it.
            el.querySelector('.cuttle-toast-close')?.addEventListener('click', (e) => {
                e.stopPropagation();
                animateOut();
            });
            return;
        }
        const timeout = setTimeout(animateOut, options.duration > 0 ? options.duration : TOAST_DURATION);

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

    // Sticky (persistent, manually dismissed) toasts, keyed by toastId so a
    // long job can show progress and close it when done. Progress is
    // indeterminate: the push path is one POST with no server-sent phases.
    const stickyToasts = {};

    function closeStickyToast(id) {
        const key = id != null ? String(id) : '';
        const el = key && stickyToasts[key];
        if (!el) return false;
        delete stickyToasts[key];
        try { el.remove(); } catch (_) {}
        return true;
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
                kind: options.kind || null,
                sticky: !!options.sticky,
                progress: !!options.progress,
                skipHistory: !!options.skipHistory,
                duration: options.duration > 0 ? options.duration : null,
                achievement: options.achievement || null,
                toastId: options.toastId != null ? String(options.toastId) : null
            }, '*');
            return;
        }
        displayToast(message, variant, options);
    }

    // Close a sticky toast by id. Forwards to the shell from iframes.
    function closeCuttleToast(id) {
        if (id == null) return false;
        if (window.parent !== window) {
            try {
                window.parent.postMessage({ type: 'cuttle-toast-close', toastId: String(id) }, '*');
            } catch (_) {}
            return true;
        }
        return closeStickyToast(id);
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
        if (e.data && e.data.type === 'cuttle-toast-close' && e.data.toastId != null) {
            closeStickyToast(e.data.toastId);
            return;
        }
        if (e.data && e.data.type === 'cuttle-toast') {
            const { message, variant, linkUrl, linkText, unread, sessionId, kind, actionId } = e.data;
            const options = {
                unread: !!unread,
                sessionId: sessionId != null ? String(sessionId) : null,
                kind: kind || null,
                actionId: actionId || null,
                sticky: !!e.data.sticky,
                progress: !!e.data.progress,
                skipHistory: !!e.data.skipHistory,
                duration: e.data.duration > 0 ? e.data.duration : null,
                achievement: e.data.achievement || null,
                toastId: e.data.toastId != null ? String(e.data.toastId) : null
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
    window.closeCuttleToast = closeCuttleToast;
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
        @keyframes cuttle-toast-progress-slide {
            from { transform: translateX(-110%); }
            to   { transform: translateX(300%); }
        }
        .cuttle-toast { pointer-events: auto; cursor: pointer; position: relative; }
        .cuttle-toast-close { margin-left: auto; }
        .cuttle-toast-progress { flex-basis: 100%; height: 4px; border-radius: 2px; background: rgba(255,255,255,0.12); overflow: hidden; }
        .cuttle-toast-progress > span { display: block; height: 100%; width: 35%; border-radius: 2px; animation: cuttle-toast-progress-slide 1.2s ease-in-out infinite; }
        .cuttle-toast-achievement { min-width: 260px; }
        .cuttle-toast-achievement .cuttle-toast-icon { font-size: 30px; line-height: 1; }
        .cuttle-toast-achievement .cuttle-toast-message { display: flex; flex-direction: column; gap: 3px; }
        .cuttle-toast-ach-title { font-size: 15px; font-weight: 700; letter-spacing: 0.01em; }
        .cuttle-toast-ach-desc { font-size: 12.5px; font-weight: 400; color: rgba(230,237,243,0.82); }
        .cuttle-toast-ach-progress { font-size: 11px; font-weight: 500; color: rgba(230,237,243,0.6); }
        @media (prefers-reduced-motion: reduce) {
            .cuttle-toast-progress > span { animation: none; width: 100%; }
        }
    `;
    document.head.appendChild(style);
})();
