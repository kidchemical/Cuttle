/* Shell notifications panel: the rail bell's popover of past toasts.
 *
 * History itself lives in shared/toast.js (window.getCuttleNotificationHistory,
 * markCuttleNotificationsRead, removeCuttleNotification,
 * clearCuttleNotificationHistory). This file renders it, opens the chat or
 * page a notification came from, dismisses one or all, and keeps the rail
 * badge in sync. Pane navigation and the shared backdrop are host-owned and
 * injected through deps.
 */
(function (root) {
    'use strict';

    const VARIANT_ICONS = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function itemHtml(n) {
        const icon = VARIANT_ICONS[n.variant] || 'ℹ';
        const timeStr = n.time ? new Date(n.time).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '';
        const linkHtml = n.linkUrl ? ` <a href="${escapeHtml(n.linkUrl)}" target="_blank" rel="noopener" class="notifications-popover-item-link">${escapeHtml(n.linkText || 'View query log')}</a>` : '';
        const isUnread = !!(n.unread && !n.read);
        const unreadIcon = isUnread ? '<span class="notifications-unread-icon" title="Unread response" aria-label="Unread response"></span>' : '';
        const target = n.sessionId || n.sourcePage;
        const cls = 'notifications-popover-item notifications-popover-item-' + escapeHtml(n.variant)
            + (isUnread ? ' is-unread' : '') + (target ? ' is-clickable' : '');
        const title = n.sessionId ? 'Open chat' : (n.sourcePage ? 'Open page' : '');
        return `<div class="${cls}" data-notif-id="${escapeHtml(n.id)}"${title ? ` title="${title}"` : ''}>`
            + `<span class="notifications-popover-item-icon">${escapeHtml(icon)}</span>`
            + `<span class="notifications-popover-item-message">${escapeHtml(n.message)}${linkHtml}</span>`
            + unreadIcon
            + `<span class="notifications-popover-item-time">${escapeHtml(timeStr)}</span>`
            + '<button type="button" class="notifications-popover-item-dismiss" aria-label="Dismiss notification" title="Dismiss">&times;</button>'
            + '</div>';
    }

    function create(deps) {
        const win = deps.win || root;
        const doc = deps.doc || win.document;
        const store = {
            list: () => (typeof win.getCuttleNotificationHistory === 'function' ? win.getCuttleNotificationHistory() : []),
            unread: () => (typeof win.getCuttleUnreadNotificationCount === 'function' ? win.getCuttleUnreadNotificationCount() : 0),
            markRead: (id) => { if (typeof win.markCuttleNotificationsRead === 'function') win.markCuttleNotificationsRead(id); },
            remove: (id) => { if (typeof win.removeCuttleNotification === 'function') win.removeCuttleNotification(id); },
            clear: () => { if (typeof win.clearCuttleNotificationHistory === 'function') win.clearCuttleNotificationHistory(); },
        };

        const popover = () => doc.getElementById('notificationsPopover');
        const backdrop = () => doc.getElementById('notificationsPopoverBackdrop');

        function isOpen() {
            const pop = popover();
            return !!(pop && pop.classList.contains('notifications-popover-open'));
        }

        function open() {
            if (deps.closeOtherPopovers) deps.closeOtherPopovers();
            const pop = popover();
            if (pop) pop.classList.add('notifications-popover-open');
            const back = backdrop();
            if (back) back.classList.add('notifications-popover-backdrop-visible');
            render();
        }

        function close() {
            const pop = popover();
            if (pop) pop.classList.remove('notifications-popover-open');
            if (deps.syncBackdrop) deps.syncBackdrop();
            else {
                const back = backdrop();
                if (back) back.classList.remove('notifications-popover-backdrop-visible');
            }
        }

        function toggle() {
            if (isOpen()) close();
            else open();
        }

        function updateBadge() {
            const count = store.unread();
            doc.querySelectorAll('.rail-badge-notifications').forEach((badge) => {
                badge.textContent = String(count);
                badge.style.display = count > 0 ? '' : 'none';
            });
        }

        function render() {
            const listEl = doc.getElementById('notificationsPopoverList');
            const emptyEl = doc.getElementById('notificationsPopoverEmpty');
            const clearBtn = doc.getElementById('notificationsClearBtn');
            if (!listEl || !emptyEl) return;
            const history = store.list();
            if (clearBtn) clearBtn.disabled = history.length === 0;
            if (history.length === 0) {
                listEl.innerHTML = '';
                listEl.style.display = 'none';
                emptyEl.style.display = 'block';
                return;
            }
            emptyEl.style.display = 'none';
            listEl.style.display = 'block';
            listEl.innerHTML = history.map(itemHtml).join('');
        }

        function activate(id) {
            const rec = store.list().find((n) => String(n.id) === String(id));
            if (!rec) return;
            store.markRead(rec.id);
            if (rec.sessionId) {
                close();
                deps.openChat(rec.sessionId);
            } else if (rec.sourcePage) {
                close();
                deps.openPage(rec.sourcePage);
            } else {
                render();
            }
        }

        function onListClick(e) {
            const item = e.target.closest('.notifications-popover-item');
            if (!item) return;
            const id = item.dataset.notifId;
            if (e.target.closest('.notifications-popover-item-dismiss')) {
                e.stopPropagation();
                store.remove(id);
                render();
                return;
            }
            if (e.target.closest('a')) return;
            activate(id);
        }

        function bind() {
            const listEl = doc.getElementById('notificationsPopoverList');
            if (listEl) listEl.addEventListener('click', onListClick);
            const clearBtn = doc.getElementById('notificationsClearBtn');
            if (clearBtn) {
                clearBtn.addEventListener('click', () => {
                    store.clear();
                    render();
                });
            }
            win.addEventListener('cuttle-notification-added', () => {
                updateBadge();
                if (isOpen()) render();
            });
            updateBadge();
        }

        bind();
        return { isOpen, open, close, toggle, render, updateBadge };
    }

    root.CuttleShellNotifications = { create, itemHtml };
})(typeof window !== 'undefined' ? window : globalThis);
