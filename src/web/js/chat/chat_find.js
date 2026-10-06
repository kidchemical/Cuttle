'use strict';

/**
 * Electron-only find-in-chat. Searches the current chat iframe's transcript
 * so split panes never share Chromium's window-wide Ctrl+F highlight.
 */

function cuttleIsElectronApp(ua, electronApi) {
    try {
        if (electronApi && electronApi.isElectron) return true;
    } catch (_) {}
    return /Electron/i.test(String(ua || ''));
}

function cuttleChatFindActionFromDomEvent(e) {
    if (!e || e.altKey || e.isComposing) return null;
    const key = String(e.key || '');
    const ctrl = !!(e.ctrlKey || e.metaKey);
    if (ctrl && !e.shiftKey && (key === 'f' || key === 'F')) return 'open';
    if (ctrl && (key === 'g' || key === 'G')) return e.shiftKey ? 'prev' : 'next';
    if (key === 'F3') return e.shiftKey ? 'prev' : 'next';
    return null;
}

function cuttleChatFindActionFromElectronInput(input) {
    if (!input || input.type !== 'keyDown' || input.alt) return null;
    const key = String(input.key || '');
    const ctrl = !!(input.control || input.meta);
    if (ctrl && !input.shift && (key === 'f' || key === 'F')) return 'open';
    if (ctrl && (key === 'g' || key === 'G')) return input.shift ? 'prev' : 'next';
    if (key === 'F3') return input.shift ? 'prev' : 'next';
    return null;
}

function cuttleChatFindMatchOffsets(text, query) {
    const hay = String(text || '');
    const needle = String(query || '');
    if (!needle) return [];
    const lowerHay = hay.toLowerCase();
    const lowerNeedle = needle.toLowerCase();
    const out = [];
    let i = 0;
    while (i <= lowerHay.length - lowerNeedle.length) {
        const j = lowerHay.indexOf(lowerNeedle, i);
        if (j < 0) break;
        out.push({ start: j, end: j + needle.length });
        i = j + Math.max(needle.length, 1);
    }
    return out;
}

function cuttleChatFindStepIndex(current, delta, total) {
    if (!total) return 0;
    return (current + delta + total * 1000) % total;
}

const CuttleChatFindApi = {
    isElectronApp: cuttleIsElectronApp,
    actionFromDomEvent: cuttleChatFindActionFromDomEvent,
    actionFromElectronInput: cuttleChatFindActionFromElectronInput,
    matchOffsets: cuttleChatFindMatchOffsets,
    stepIndex: cuttleChatFindStepIndex,
};

if (typeof module === 'object' && module.exports) {
    module.exports = CuttleChatFindApi;
}

if (typeof window !== 'undefined') {
    window.CuttleChatFind = CuttleChatFindApi;
    (function mountChatFindBar() {
        if (!document.querySelector) return;

        const HIGHLIGHT_ALL = 'cuttle-find';
        const HIGHLIGHT_CUR = 'cuttle-find-current';

        let enabled = cuttleIsElectronApp(
            typeof navigator !== 'undefined' ? navigator.userAgent : '',
            typeof window !== 'undefined' ? window.electron : null
        );
        let bar = null;
        let input = null;
        let countEl = null;
        let matches = [];
        let current = 0;
        let observer = null;
        let debounceTimer = null;
        let lastQuery = '';

        function searchRoot() {
            return document.getElementById('chatMessages') || document.querySelector('.chat-main');
        }

        function ensureBar() {
            if (bar) return bar;
            const host = document.querySelector('.chat-main') || document.body;
            bar = document.createElement('div');
            bar.id = 'chatFindBar';
            bar.className = 'chat-find-bar';
            bar.hidden = true;
            bar.setAttribute('role', 'search');
            bar.setAttribute('data-chat-find-skip', '1');
            bar.innerHTML =
                '<input type="search" id="chatFindInput" class="chat-find-input" placeholder="Find in chat" autocomplete="off" spellcheck="false" aria-label="Find in chat">'
                + '<span id="chatFindCount" class="chat-find-count" aria-live="polite"></span>'
                + '<button type="button" class="chat-find-btn" id="chatFindPrev" title="Previous (Shift+Enter)">↑</button>'
                + '<button type="button" class="chat-find-btn" id="chatFindNext" title="Next (Enter)">↓</button>'
                + '<button type="button" class="chat-find-btn chat-find-close" id="chatFindClose" title="Close (Esc)">×</button>';
            host.appendChild(bar);
            input = bar.querySelector('#chatFindInput');
            countEl = bar.querySelector('#chatFindCount');
            bar.querySelector('#chatFindPrev').addEventListener('click', () => step(-1));
            bar.querySelector('#chatFindNext').addEventListener('click', () => step(1));
            bar.querySelector('#chatFindClose').addEventListener('click', close);
            input.addEventListener('input', () => runSearch(true));
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    e.stopPropagation();
                    if (e.shiftKey) step(-1);
                    else step(1);
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    e.stopPropagation();
                    close();
                }
            });
            return bar;
        }

        function isOpen() {
            return !!(bar && !bar.hidden);
        }

        function clearHighlights() {
            matches = [];
            current = 0;
            try {
                if (window.CSS && CSS.highlights) {
                    CSS.highlights.delete(HIGHLIGHT_ALL);
                    CSS.highlights.delete(HIGHLIGHT_CUR);
                }
            } catch (_) {}
            document.querySelectorAll('.chat-find-message-active').forEach((el) => {
                el.classList.remove('chat-find-message-active');
            });
        }

        function collectRanges(query) {
            const root = searchRoot();
            if (!root || !query) return [];
            const ranges = [];
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
                acceptNode(node) {
                    if (!node || !node.nodeValue) return NodeFilter.FILTER_REJECT;
                    const p = node.parentElement;
                    if (!p) return NodeFilter.FILTER_REJECT;
                    const tag = p.tagName;
                    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') {
                        return NodeFilter.FILTER_REJECT;
                    }
                    if (p.closest && p.closest('[data-chat-find-skip], #chatFindBar, .chat-find-bar')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                },
            });
            let node;
            while ((node = walker.nextNode())) {
                const offsets = cuttleChatFindMatchOffsets(node.nodeValue, query);
                for (let i = 0; i < offsets.length; i++) {
                    try {
                        const range = document.createRange();
                        range.setStart(node, offsets[i].start);
                        range.setEnd(node, offsets[i].end);
                        ranges.push(range);
                    } catch (_) {}
                }
            }
            return ranges;
        }

        function paintHighlights() {
            if (!(window.CSS && CSS.highlights)) return;
            try {
                if (typeof Highlight !== 'function') return;
                if (!matches.length) {
                    CSS.highlights.delete(HIGHLIGHT_ALL);
                    CSS.highlights.delete(HIGHLIGHT_CUR);
                    return;
                }
                const all = new Highlight();
                matches.forEach((range) => all.add(range));
                CSS.highlights.set(HIGHLIGHT_ALL, all);
                CSS.highlights.set(HIGHLIGHT_CUR, new Highlight(matches[current]));
            } catch (_) {}
        }

        function revealCurrent() {
            document.querySelectorAll('.chat-find-message-active').forEach((el) => {
                el.classList.remove('chat-find-message-active');
            });
            const range = matches[current];
            if (!range) return;
            try {
                const node = range.startContainer;
                const el = node.nodeType === 1 ? node : node.parentElement;
                const msg = el && el.closest ? el.closest('.message') : null;
                if (msg) msg.classList.add('chat-find-message-active');
                const target = msg || el;
                if (target && typeof target.scrollIntoView === 'function') {
                    target.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
                }
            } catch (_) {}
        }

        function updateCount() {
            if (!countEl) return;
            if (!lastQuery) {
                countEl.textContent = '';
                return;
            }
            if (!matches.length) {
                countEl.textContent = '0/0';
                return;
            }
            countEl.textContent = (current + 1) + '/' + matches.length;
        }

        function runSearch(resetIndex) {
            lastQuery = input ? String(input.value || '') : '';
            clearHighlights();
            if (!lastQuery.trim()) {
                updateCount();
                return;
            }
            matches = collectRanges(lastQuery);
            if (resetIndex || current >= matches.length) current = 0;
            paintHighlights();
            revealCurrent();
            updateCount();
        }

        function step(delta) {
            if (!isOpen()) return;
            if (!matches.length) {
                runSearch(true);
                return;
            }
            current = cuttleChatFindStepIndex(current, delta, matches.length);
            paintHighlights();
            revealCurrent();
            updateCount();
        }

        function watchMessages() {
            const root = searchRoot();
            if (!root || observer) return;
            observer = new MutationObserver(() => {
                if (!isOpen()) return;
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(() => runSearch(false), 120);
            });
            observer.observe(root, { childList: true, subtree: true, characterData: true });
        }

        function unwatchMessages() {
            if (observer) {
                try { observer.disconnect(); } catch (_) {}
                observer = null;
            }
            clearTimeout(debounceTimer);
            debounceTimer = null;
        }

        function open() {
            if (!enabled) return;
            if (!document.querySelector('.chat-main') && !document.getElementById('chatMessages')) return;
            ensureBar();
            bar.hidden = false;
            document.body.classList.add('chat-find-open');
            watchMessages();
            runSearch(true);
            try {
                input.focus();
                input.select();
            } catch (_) {}
        }

        function close() {
            if (!bar) return;
            bar.hidden = true;
            document.body.classList.remove('chat-find-open');
            unwatchMessages();
            clearHighlights();
            lastQuery = '';
            if (countEl) countEl.textContent = '';
        }

        function handleAction(action) {
            if (!action) return;
            enabled = true;
            if (action === 'close') {
                close();
                return;
            }
            if (action === 'open') {
                open();
                return;
            }
            if (!isOpen()) {
                open();
                return;
            }
            if (action === 'next') step(1);
            else if (action === 'prev') step(-1);
        }

        function onKeyDown(e) {
            if (e.key === 'Escape' && isOpen()) {
                e.preventDefault();
                e.stopPropagation();
                close();
                return;
            }
            if (!enabled && !cuttleIsElectronApp(
                typeof navigator !== 'undefined' ? navigator.userAgent : '',
                window.electron
            )) return;
            const action = cuttleChatFindActionFromDomEvent(e);
            if (!action) return;
            enabled = true;
            e.preventDefault();
            e.stopPropagation();
            handleAction(action);
        }

        function boot() {
            if (!document.querySelector('.chat-main') && !document.getElementById('chatMessages')) return;
            document.addEventListener('keydown', onKeyDown, true);
            document.addEventListener('focusin', () => {
                try {
                    if (window.parent && window.parent !== window) {
                        window.parent.postMessage({ type: 'cuttle-pane-activity' }, '*');
                    }
                } catch (_) {}
            }, true);
            window.addEventListener('message', (e) => {
                const data = e && e.data;
                if (!data || typeof data !== 'object') return;
                if (data.type === 'cuttle-electron' && data.enabled) {
                    enabled = true;
                    return;
                }
                if (data.type === 'cuttle-chat-find') {
                    handleAction(data.action);
                    return;
                }
                if (data.type === 'cuttle-pane-focus' && data.focused === false) {
                    close();
                }
            });
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', boot);
        } else {
            boot();
        }
    })();
}
