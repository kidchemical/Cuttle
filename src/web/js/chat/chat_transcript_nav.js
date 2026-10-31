/* ================================================================
   Cuttle Chat — transcript navigation (chat_transcript_nav.js).
   Owner: where the reader is in the transcript. Follow-latest scrolling
   (sticky pin measured on user scroll, programmatic-scroll hold windows,
   deep-link autoscroll suppression), the numbered message-nav rail with
   per-session pins (`chat_message_pins_v1`), jump-to-bottom (and its
   unseen/error glow), jump-to-message, `?msg=N` deep links that page older
   history in until the bubble exists, and message refs (`CH-…-N` ⇄ bubble
   index, share URLs).

   Interface: `create(deps)` → one controller per chat frame.
   deps: { win, storage, escapeHtmlInline, recordSelector,
           currentSessionId(), formatChatDisplayId, olderVisibleCount(),
           hasOlderMessages(), loadOlderMessages(), unseenBelow(),
           attentionIsError(), clearUnseenBelow(), activateViewport(),
           copyText(text) }. The page keeps session identity, the history
   window, attention state, and message copy/speak text.
   Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * One transcript-nav controller per chat frame.
     * @param {object} deps see header
     */
    function create(deps) {
        const frameWin = deps.win;
        const frameDoc = frameWin.document;
        const storage = deps.storage;
        const escapeHtmlInline = deps.escapeHtmlInline;
        const recordSelector = deps.recordSelector;
        const currentSessionId = deps.currentSessionId;
        const formatChatDisplayId = deps.formatChatDisplayId;
        const olderVisibleCount = deps.olderVisibleCount;
        const hasOlderMessages = deps.hasOlderMessages;
        const loadOlderMessages = deps.loadOlderMessages;
        const unseenBelow = deps.unseenBelow;
        const attentionIsError = deps.attentionIsError;
        const clearUnseenBelow = deps.clearUnseenBelow;
        const activateViewport = deps.activateViewport;
        const copyText = deps.copyText;

        /** Distance from the bottom that still counts as following the conversation. */
        const AUTOSCROLL_STICK_PX = 120;
        /** Sticky follow: measured on user scroll, not after we insert a tall bubble. */
        let chatPinnedToBottom = true;
        let _ignoreChatScrollPinUntil = 0;
        /** Blocks autoScroll-to-bottom while a CH-…-N deep-link jump settles. */
        let _suppressAutoScrollUntil = 0;
        /** Right-side message nav (numbered circles) — same sticky-follow behavior, don't steal hover/scroll. */
        let messageNavPinnedToBottom = true;
        let _ignoreNavScrollPinUntil = 0;
        const NAV_AUTOSCROLL_STICK_PX = 24;

        /** Compact share id: CH-000102-9 (chat display id + 1-based bubble index). */
        function formatMessageRef(sessionId, messageIndex) {
            const chatId = formatChatDisplayId(sessionId);
            const idx = Number(messageIndex);
            if (!chatId || !Number.isFinite(idx) || idx < 1) return null;
            return chatId + '-' + Math.floor(idx);
        }

        function parseMessageRef(raw) {
            const s = String(raw || '').trim();
            const m = s.match(/^(CH-[A-Z0-9]+)-(\d+)$/i);
            if (!m) return null;
            const idx = parseInt(m[2], 10);
            if (!Number.isFinite(idx) || idx < 1) return null;
            return { chatId: m[1].toUpperCase(), messageIndex: idx };
        }

        function getMessageRefForButton(btn) {
            const messageEl = btn && btn.closest ? btn.closest('.message') : null;
            // Stopped bubbles keep their transcript slot (Stop persists an
            // interrupted-agent-turn row), so their ref stays copyable.
            if (!messageEl) return null;
            let idx = parseInt(messageEl.dataset.messageIndex || '', 10);
            if (!Number.isFinite(idx) || idx < 1) {
                const box = frameDoc.getElementById('chatMessages');
                const list = box
                    ? Array.from(box.querySelectorAll(recordSelector + ':not(.system)'))
                    : [];
                idx = list.indexOf(messageEl) + 1;
                if (idx > 0) messageEl.dataset.messageIndex = String(idx);
            }
            return formatMessageRef(currentSessionId(), idx);
        }

        function buildMessageShareUrl(ref) {
            if (!ref) return '';
            try {
                const url = new URL(frameWin.location.href);
                const parsed = parseMessageRef(ref);
                if (!parsed) return '';
                url.searchParams.set('chat', parsed.chatId);
                url.searchParams.set('msg', String(parsed.messageIndex));
                url.searchParams.delete('session');
                return url.pathname + url.search + url.hash;
            } catch (_) {
                return '';
            }
        }

        async function copyMessageRefToClipboard(btn) {
            const ref = getMessageRefForButton(btn);
            if (!ref) return { ok: false, ref: null };
            const ok = await copyText(ref);
            return { ok, ref };
        }

        const MESSAGE_PINS_STORAGE_KEY = 'chat_message_pins_v1';
        const MESSAGE_NAV_PIN_SVG =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
            + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            + '<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.13-1.87V5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v3.89a2 2 0 0 1-1.13 1.87L12 13z"/>'
            + '<path d="M7 5h10"/>'
            + '</svg>';

        function readPinnedMessageKeys() {
            try {
                const sid = currentSessionId() != null ? String(currentSessionId()) : '';
                if (!sid) return new Set();
                const raw = storage.getItem(MESSAGE_PINS_STORAGE_KEY);
                const map = raw ? JSON.parse(raw) : {};
                const list = Array.isArray(map[sid]) ? map[sid] : [];
                return new Set(list.map(String));
            } catch (_) {
                return new Set();
            }
        }

        function writePinnedMessageKeys(keySet) {
            try {
                const sid = currentSessionId() != null ? String(currentSessionId()) : '';
                if (!sid) return;
                const raw = storage.getItem(MESSAGE_PINS_STORAGE_KEY);
                const map = raw ? JSON.parse(raw) : {};
                map[sid] = Array.from(keySet);
                storage.setItem(MESSAGE_PINS_STORAGE_KEY, JSON.stringify(map));
            } catch (_) {}
        }

        function getMessagePinKey(el, idx) {
            if (!el) return 'idx:' + idx;
            if (el.dataset.messageId) return 'id:' + el.dataset.messageId;
            const role = el.classList.contains('user') ? 'u' : 'a';
            const tsEl = el.querySelector('[data-ts]');
            const ts = tsEl && tsEl.dataset.ts ? String(tsEl.dataset.ts) : '';
            const text = ((el.querySelector('.message-content') || {}).textContent || '')
                .replace(/\s+/g, ' ')
                .trim()
                .slice(0, 80);
            if (ts || text) return 'fp:' + role + ':' + ts + ':' + text;
            return 'idx:' + idx;
        }

        function updateMessageNav() {
            const nav = frameDoc.getElementById('messageNav');
            const messagesContainer = frameDoc.getElementById('chatMessages');
            if (!nav || !messagesContainer) return;

            const messages = Array.from(messagesContainer.querySelectorAll(recordSelector + ':not(.system)'));
            if (messages.length === 0) {
                nav.innerHTML = '';
                nav.style.display = 'none';
                updateJumpToBottomVisibility();
                return;
            }

            const pinnedKeys = readPinnedMessageKeys();
            const indexBase = Math.max(0, Number(olderVisibleCount()) || 0);
            const items = messages.map((el, i) => {
                const idx = indexBase + i + 1;
                const pinKey = getMessagePinKey(el, idx);
                el.dataset.messageIndex = String(idx);
                el.dataset.pinKey = pinKey;
                const pinned = pinnedKeys.has(pinKey);
                el.classList.toggle('is-pinned-message', pinned);
                const ref = formatMessageRef(currentSessionId(), idx);
                if (ref) {
                    el.querySelectorAll('.message-share-ref-btn').forEach((btn) => {
                        btn.title = 'Copy message ref (' + ref + ')';
                        btn.setAttribute('aria-label', 'Copy message reference ' + ref);
                    });
                }
                return { idx, pinKey, pinned };
            });

            const pinnedItems = items.filter((it) => it.pinned);
            const restItems = items.filter((it) => !it.pinned);

            const renderItem = (it) => (
                `<button type="button" class="message-nav-item${it.pinned ? ' is-pinned' : ''}" `
                + `data-index="${it.idx}" data-pin-key="${escapeHtmlInline(it.pinKey)}" `
                + `title="Jump to message ${it.idx}">`
                + `<span class="message-nav-pin" data-pin-toggle="1" title="${it.pinned ? 'Unpin' : 'Pin for quick jump'}" `
                + `aria-label="${it.pinned ? 'Unpin message' : 'Pin message'}">${MESSAGE_NAV_PIN_SVG}</span>`
                + `<span class="message-nav-num">${it.idx}</span>`
                + `</button>`
            );

            let html = pinnedItems.map(renderItem).join('');
            if (pinnedItems.length && restItems.length) {
                html += '<div class="message-nav-sep" aria-hidden="true"></div>';
            }
            html += restItems.map(renderItem).join('');

            nav.style.display = 'flex';
            const wasPinned = messageNavPinnedToBottom;
            // Preserve scroll position across rebuild — innerHTML resets scrollTop to 0.
            const prevTop = nav.scrollTop;
            const prevHeight = nav.scrollHeight;
            nav.innerHTML = html;
            setupMessageNavScrollSync();
            setupMessageNavClicks();
            updateJumpToBottomVisibility();
            // Default to bottom on first paint; otherwise follow only when on standby
            // (not scrolled up, not hovered/focused).
            if (wasPinned && !isMessageNavInteracting()) {
                autoScrollMessageNavToBottom(true);
            } else if (!wasPinned) {
                // Restore where user was if we shouldn't auto-follow.
                try {
                    // If content grew, keep same visual offset; otherwise restore absolute top.
                    const newHeight = nav.scrollHeight;
                    nav.scrollTop = prevTop + (newHeight - prevHeight);
                } catch (_) {}
            }
        }

        let _messageNavClickSetup = false;
        function setupMessageNavClicks() {
            const nav = frameDoc.getElementById('messageNav');
            if (!nav || _messageNavClickSetup) return;
            _messageNavClickSetup = true;
            nav.addEventListener('click', (e) => {
                const pinBtn = e.target && e.target.closest
                    ? e.target.closest('[data-pin-toggle]')
                    : null;
                if (pinBtn) {
                    e.preventDefault();
                    e.stopPropagation();
                    const item = pinBtn.closest('.message-nav-item');
                    if (!item) return;
                    toggleMessagePin(item.dataset.pinKey);
                    return;
                }
                const item = e.target && e.target.closest
                    ? e.target.closest('.message-nav-item')
                    : null;
                if (!item) return;
                e.preventDefault();
                jumpToMessage(parseInt(item.dataset.index, 10));
            });
        }

        function toggleMessagePin(pinKey) {
            if (!pinKey) return;
            const keys = readPinnedMessageKeys();
            if (keys.has(pinKey)) keys.delete(pinKey);
            else keys.add(pinKey);
            writePinnedMessageKeys(keys);
            updateMessageNav();
        }

        let _messageNavScrollSetup = false;
        function setupMessageNavScrollSync() {
            const messagesContainer = frameDoc.getElementById('chatMessages');
            if (!messagesContainer || _messageNavScrollSetup) return;
            _messageNavScrollSetup = true;

            const checkActive = () => {
                syncChatPinnedFromScroll();
                syncMessageNavPinnedFromScroll();
                const messages = Array.from(messagesContainer.querySelectorAll(recordSelector + ':not(.system)'));
                const navItems = frameDoc.querySelectorAll('.message-nav-item');
                if (navItems.length) {
                    const rect = messagesContainer.getBoundingClientRect();
                    const containerMid = rect.top + rect.height * 0.4;
                    let activeIdx = null;
                    messages.forEach((el) => {
                        const r = el.getBoundingClientRect();
                        if (r.top <= containerMid) {
                            const idx = parseInt(el.dataset.messageIndex, 10);
                            if (Number.isFinite(idx)) activeIdx = idx;
                        }
                    });
                    navItems.forEach((btn) => {
                        btn.classList.toggle(
                            'active',
                            activeIdx != null && parseInt(btn.dataset.index, 10) === activeIdx
                        );
                    });
                }
                updateJumpToBottomVisibility();
            };
            messagesContainer.addEventListener('scroll', checkActive, { passive: true });
            // Also track nav's own scroll/hover so we don't steal interaction
            const nav = frameDoc.getElementById('messageNav');
            if (nav) {
                nav.addEventListener('scroll', syncMessageNavPinnedFromScroll, { passive: true });
                nav.addEventListener('mouseenter', syncMessageNavPinnedFromScroll, { passive: true });
                nav.addEventListener('wheel', syncMessageNavPinnedFromScroll, { passive: true });
                nav.addEventListener('touchstart', syncMessageNavPinnedFromScroll, { passive: true });
            }
            checkActive();
        }

        function isChatScrolledUp(thresholdPx) {
            const box = frameDoc.getElementById('chatMessages');
            if (!box) return false;
            const gap = box.scrollHeight - box.scrollTop - box.clientHeight;
            return gap > (thresholdPx == null ? 140 : thresholdPx);
        }

        function syncChatPinnedFromScroll() {
            if (Date.now() < _ignoreChatScrollPinUntil) return;
            chatPinnedToBottom = !isChatScrolledUp(AUTOSCROLL_STICK_PX);
            if (chatPinnedToBottom) {
                // Reached the latest content — clear jump glow only (title may still need ack).
                clearUnseenBelow();
            }
        }

        function isMessageNavScrolledUp(thresholdPx) {
            const nav = frameDoc.getElementById('messageNav');
            if (!nav) return false;
            // Hidden or not scrollable → treat as pinned
            if (nav.style.display === 'none' || nav.scrollHeight <= nav.clientHeight + 4) return false;
            const gap = nav.scrollHeight - nav.scrollTop - nav.clientHeight;
            return gap > (thresholdPx == null ? NAV_AUTOSCROLL_STICK_PX : thresholdPx);
        }

        function isMessageNavInteracting() {
            const nav = frameDoc.getElementById('messageNav');
            if (!nav) return false;
            try {
                if (nav.matches(':hover')) return true;
                if (nav.matches(':focus-within')) return true;
                // Any child focused (keyboard nav)
                if (frameDoc.activeElement && nav.contains(frameDoc.activeElement)) return true;
            } catch (_) {}
            return false;
        }

        function syncMessageNavPinnedFromScroll() {
            if (Date.now() < _ignoreNavScrollPinUntil) return;
            // If user is actively hovering/interacting, don't flip pinned just yet —
            // wait until they leave / scroll to bottom.
            if (isMessageNavInteracting() && isMessageNavScrolledUp(4)) {
                messageNavPinnedToBottom = false;
                return;
            }
            messageNavPinnedToBottom = !isMessageNavScrolledUp(NAV_AUTOSCROLL_STICK_PX);
        }

        function autoScrollMessageNavToBottom(force) {
            const nav = frameDoc.getElementById('messageNav');
            if (!nav) return;
            if (nav.style.display === 'none') return;
            if (!force && !messageNavPinnedToBottom) return;
            if (!force && isMessageNavInteracting()) return;
            if (!force && isMessageNavScrolledUp(NAV_AUTOSCROLL_STICK_PX)) return;
            _ignoreNavScrollPinUntil = Date.now() + 150;
            nav.scrollTop = nav.scrollHeight;
            messageNavPinnedToBottom = true;
            frameWin.requestAnimationFrame(() => {
                nav.scrollTop = nav.scrollHeight;
                messageNavPinnedToBottom = true;
            });
        }

        /**
         * Follow new content only while the reader is parked at the bottom. Pin is
         * remembered from the last user scroll — measuring gap *after* inserting a
         * tall reply always looks like they scrolled away. Pass true for their own
         * actions (send, open chat, jump-to-bottom).
         */
        function autoScrollChatToBottom(force) {
            const box = frameDoc.getElementById('chatMessages');
            if (!box) return;
            // Message-ref jumps (CH-000430-99) must win over late bottom scrolls.
            if (Date.now() < _suppressAutoScrollUntil) {
                updateJumpToBottomVisibility();
                return;
            }
            if (!force && !chatPinnedToBottom) {
                updateJumpToBottomVisibility();
                return;
            }
            _ignoreChatScrollPinUntil = Date.now() + 150;
            box.scrollTop = box.scrollHeight;
            chatPinnedToBottom = true;
            clearUnseenBelow();
            frameWin.requestAnimationFrame(() => {
                if (Date.now() < _suppressAutoScrollUntil) {
                    updateJumpToBottomVisibility();
                    return;
                }
                box.scrollTop = box.scrollHeight;
                chatPinnedToBottom = true;
                clearUnseenBelow();
                updateJumpToBottomVisibility();
            });
        }

        function updateJumpToBottomVisibility() {
            const btn = frameDoc.getElementById('chatJumpBottom');
            const box = frameDoc.getElementById('chatMessages');
            if (!btn || !box) return;
            const area = frameDoc.getElementById('chatArea');
            const chatVisible = area && area.style.display !== 'none';
            const show = chatVisible && box.scrollHeight > box.clientHeight + 40 && isChatScrolledUp(140);
            btn.hidden = !show;
            btn.classList.toggle('is-visible', show);
            // Glow only when a finalized reply landed while scrolled up (not mere ack / scroll).
            const hasUnseen = show && unseenBelow();
            btn.classList.toggle('has-unseen', hasUnseen);
            btn.classList.toggle('has-unseen-error', hasUnseen && attentionIsError());
            btn.title = hasUnseen
                ? (attentionIsError() ? 'Error — jump to latest' : 'New messages — jump to latest')
                : 'Jump to latest';
            btn.setAttribute(
                'aria-label',
                hasUnseen
                    ? (attentionIsError() ? 'Error — jump to latest' : 'New messages — jump to latest')
                    : 'Jump to latest messages'
            );
        }

        function jumpToBottom(smooth) {
            const box = frameDoc.getElementById('chatMessages');
            if (!box) return;
            _suppressAutoScrollUntil = 0;
            chatPinnedToBottom = true;
            _ignoreChatScrollPinUntil = Date.now() + 400;
            clearUnseenBelow();
            activateViewport();
            box.scrollTo({
                top: box.scrollHeight,
                behavior: smooth === false ? 'auto' : 'smooth',
            });
            frameWin.requestAnimationFrame(() => {
                chatPinnedToBottom = true;
                clearUnseenBelow();
                updateJumpToBottomVisibility();
            });
        }

        function jumpToMessage(index, opts) {
            const messagesContainer = frameDoc.getElementById('chatMessages');
            const el = messagesContainer?.querySelector(`.message[data-message-index="${index}"]`);
            if (!el) return false;
            chatPinnedToBottom = false;
            _suppressAutoScrollUntil = Date.now() + 600;
            _ignoreChatScrollPinUntil = Date.now() + 600;
            const instant = !!(opts && opts.instant);
            // Scroll the chat box itself — scrollIntoView can move outer shell/iframe
            // ancestors and loses to pending autoScroll rAFs after session load.
            try {
                const boxRect = messagesContainer.getBoundingClientRect();
                const elRect = el.getBoundingClientRect();
                const delta = (elRect.top + elRect.height / 2) - (boxRect.top + boxRect.height / 2);
                messagesContainer.scrollTo({
                    top: Math.max(0, messagesContainer.scrollTop + delta),
                    behavior: instant ? 'auto' : 'smooth',
                });
            } catch (_) {
                try {
                    el.scrollIntoView({
                        behavior: instant ? 'auto' : 'smooth',
                        block: 'center',
                    });
                } catch (__) {}
            }
            if (!(opts && opts.skipFlash)) {
                el.classList.remove('message-ref-flash');
                // Restart CSS animation if the same bubble is targeted twice.
                void el.offsetWidth;
                el.classList.add('message-ref-flash');
                setTimeout(() => {
                    try { el.classList.remove('message-ref-flash'); } catch (_) {}
                }, 2200);
            }
            updateJumpToBottomVisibility();
            return true;
        }

        /** Pending ?msg=N (or CH-xxx-N) jump after the transcript finishes painting. */
        let pendingMessageJump = null;

        function queueMessageJump(messageIndex) {
            const idx = parseInt(messageIndex, 10);
            if (!Number.isFinite(idx) || idx < 1) {
                pendingMessageJump = null;
                return;
            }
            pendingMessageJump = idx;
            // Hold bottom-stick while the session paints so load-time scrolls can't win.
            _suppressAutoScrollUntil = Date.now() + 1200;
        }

        function consumePendingMessageJump() {
            const idx = pendingMessageJump;
            if (!idx) return;
            pendingMessageJump = null;
            _suppressAutoScrollUntil = Date.now() + 1200;
            // Indices are assigned in updateMessageNav — call after messages are in the DOM.
            // Older bubbles may still be behind the lazy frameWin; load pages until found.
            (async () => {
                const settle = (found) => {
                    if (!found) {
                        _clearMsgQueryParam();
                        return;
                    }
                    // Re-assert after layout/rAF so late autoScroll or image layout
                    // cannot yank the viewport back to the bottom.
                    frameWin.requestAnimationFrame(() => {
                        jumpToMessage(idx, { instant: true, skipFlash: true });
                        frameWin.requestAnimationFrame(() => {
                            jumpToMessage(idx, { instant: true, skipFlash: true });
                        });
                    });
                    setTimeout(() => jumpToMessage(idx, { instant: true, skipFlash: true }), 80);
                    setTimeout(() => jumpToMessage(idx, { instant: true, skipFlash: true }), 280);
                    _clearMsgQueryParam();
                };

                updateMessageNav();
                if (jumpToMessage(idx, { instant: true })) {
                    settle(true);
                    return;
                }
                let guard = 40;
                while (hasOlderMessages() && guard-- > 0) {
                    const loaded = await loadOlderMessages();
                    updateMessageNav();
                    if (jumpToMessage(idx, { instant: true })) {
                        settle(true);
                        return;
                    }
                    if (!loaded) break;
                }
                updateMessageNav();
                settle(!!jumpToMessage(idx, { instant: true }));
            })();
        }

        function _clearMsgQueryParam() {
            try {
                const url = new URL(frameWin.location.href);
                if (url.searchParams.has('msg')) {
                    url.searchParams.delete('msg');
                    frameWin.history.replaceState(null, '', url.pathname + url.search + url.hash);
                }
            } catch (_) {}
        }

        /** After a session paints: land on a pending `?msg=` jump, else the latest message. */
        function settleAfterLoad() {
            if (pendingMessageJump) consumePendingMessageJump();
            else {
                chatPinnedToBottom = true;
                _ignoreChatScrollPinUntil = Date.now() + 300;
                autoScrollChatToBottom(true);
                messageNavPinnedToBottom = true;
                _ignoreNavScrollPinUntil = Date.now() + 300;
                autoScrollMessageNavToBottom(true);
                frameWin.requestAnimationFrame(() => {
                    autoScrollChatToBottom(true);
                    autoScrollMessageNavToBottom(true);
                });
                setTimeout(() => {
                    autoScrollChatToBottom(true);
                    autoScrollMessageNavToBottom(true);
                }, 250);
            }
        }

        /** New/empty chat: follow the latest nav item again and repaint the rail. */
        function resetNav() {
            messageNavPinnedToBottom = true;
            updateMessageNav();
        }

        /** Ignore scroll-driven follow/unfollow for `ms` (programmatic scrolls). */
        function holdScrollPin(ms) {
            _ignoreChatScrollPinUntil = Date.now() + ms;
        }

        function isScrollPinHeld() {
            return Date.now() < _ignoreChatScrollPinUntil;
        }

        /** True while the reader follows the latest message (last user scroll). */
        function isFollowingLatest() {
            return chatPinnedToBottom;
        }

        return {
            formatMessageRef,
            parseMessageRef,
            getMessageRefForButton,
            buildMessageShareUrl,
            copyMessageRefToClipboard,
            updateMessageNav,
            isChatScrolledUp,
            autoScrollMessageNavToBottom,
            autoScrollChatToBottom,
            updateJumpToBottomVisibility,
            jumpToBottom,
            jumpToMessage,
            queueMessageJump,
            consumePendingMessageJump,
            settleAfterLoad,
            resetNav,
            holdScrollPin,
            isScrollPinHeld,
            isFollowingLatest,
            setupMessageNavScrollSync,
            isMessageNavScrolledUp,
            isMessageNavInteracting,
            syncMessageNavPinnedFromScroll,
        };
    }

    const api = { create };

    const ns = (root.CuttleChatTranscriptNav = root.CuttleChatTranscriptNav || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
