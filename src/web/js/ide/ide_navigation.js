/* Serialize file requests, including requests received while the editor boots. */
(function (root) {
    'use strict';
    function create(host) {
        const { win, open, fail } = host;
        const requests = [];
        let ready = false;
        let running = false;
        async function drain() {
            if (!ready || running) return;
            running = true;
            try {
                while (requests.length) {
                    try { await open(requests.shift()); } catch (err) { fail(err); }
                }
            } finally { running = false; }
        }
        win.addEventListener('message', e => {
            const d = e.data;
            if (e.origin !== win.location.origin || !d ||
                !['cuttle-open-editor', 'cuttle-ide-open-file', 'cuttle-navigate'].includes(d.type) ||
                typeof d.page !== 'string' ||
                (e.source !== win.parent && e.source !== win && !host.isChatSource(e.source))) return;
            const url = new URL(d.page, win.location.origin);
            if (url.origin !== win.location.origin || url.pathname !== '/ide_page.html') return;
            requests.push(url.searchParams);
            drain();
        });
        return { ready() {
            ready = true;
            if (win.parent !== win) win.parent.postMessage({ type: 'cuttle-ide-ready' }, win.location.origin);
            drain();
        } };
    }
    root.CuttleIdeNavigation = { create: create };
})(globalThis);
