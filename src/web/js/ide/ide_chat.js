/* IDE App — AI chat panel. Hosts the real Cuttle chat page (same agents,
 * router, streaming, action forms) in a side frame bound to the workspace
 * project, and hands it editor context: "Add to chat" and inline-edit
 * requests are typed into that chat's composer and sent through its own
 * send path. No second execution route. */
(function (root) {
    'use strict';

    function create(host) {
        const frameHost = host.frameHost;
        let frame = null;
        let sessionId = null;
        let workspace = null;

        async function json(url, init) {
            const response = await fetch(url, Object.assign({ credentials: 'include', cache: 'no-store' }, init || {}));
            const data = await response.json().catch(() => null);
            if (!response.ok || !data || data.success === false) throw new Error((data && data.error) || ('HTTP ' + response.status));
            return data;
        }
        async function createSession() {
            const ws = workspace || {};
            const created = await json('/api/auth/sessions', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_name: 'IDE · ' + (ws.name || 'project') }),
            });
            const id = String(created.session_id);
            // Bind the chat to the workspace so agents run in the project folder.
            await json('/api/auth/sessions/' + encodeURIComponent(id), {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_id: ws.id == null ? null : ws.id, project_name: ws.name || null, project_path: ws.path }),
            });
            return id;
        }
        async function sessionExists(id) {
            try {
                await json('/api/auth/sessions/' + encodeURIComponent(id) + '/messages?limit=1');
                return true;
            } catch (_) { return false; }
        }
        function mount(id) {
            sessionId = id;
            host.onSession(id);
            if (!frame) {
                frame = document.createElement('iframe');
                frame.className = 'ide-chat-frame';
                frame.title = 'Cuttle chat';
                frameHost.appendChild(frame);
            }
            const src = '/chat_page.html?chat=' + encodeURIComponent(id);
            if (frame.getAttribute('src') !== src) frame.setAttribute('src', src);
        }
        async function ensure() {
            if (!workspace) throw new Error('Open a project first');
            if (frame && sessionId) return sessionId;
            const saved = host.savedSession();
            if (saved && await sessionExists(saved)) { mount(saved); return saved; }
            const id = await createSession();
            mount(id);
            return id;
        }
        /** Composer of the embedded chat once it has booted (same origin). */
        function composer(timeoutMs) {
            const deadline = Date.now() + (timeoutMs || 15000);
            return new Promise((resolve, reject) => {
                const tick = () => {
                    let doc = null;
                    try { doc = frame && frame.contentDocument; } catch (_) { doc = null; }
                    if (doc) {
                        const visible = (el) => el && !el.disabled && el.getClientRects().length > 0;
                        const chat = doc.getElementById('chatInput');
                        const welcome = doc.getElementById('welcomeChatInput');
                        const input = visible(chat) ? chat : (visible(welcome) ? welcome : null);
                        if (input) {
                            const send = doc.getElementById(input === chat ? 'sendButton' : 'welcomeSendButton');
                            return resolve({ input: input, send: send, win: frame.contentWindow });
                        }
                    }
                    if (Date.now() > deadline) return reject(new Error('Chat is still loading — try again'));
                    setTimeout(tick, 150);
                };
                tick();
            });
        }
        async function insert(text, opts) {
            await ensure();
            const c = await composer();
            const input = c.input;
            const current = String(input.value || '');
            input.value = current.trim() ? current.replace(/\s+$/, '') + '\n\n' + text : text;
            input.dispatchEvent(new c.win.Event('input', { bubbles: true }));
            if (opts && opts.send && c.send) {
                c.send.click();
            } else {
                input.focus();
                try { input.setSelectionRange(input.value.length, input.value.length); } catch (_) {}
            }
        }
        return {
            setWorkspace(ws) {
                const changed = !workspace || !ws || workspace.path !== ws.path;
                workspace = ws;
                if (changed && frame) { frame.remove(); frame = null; sessionId = null; }
            },
            ensure: ensure,
            async newChat() {
                const id = await createSession();
                mount(id);
            },
            insert: insert,
            /** The embedded chat moved to another session (history, new chat). */
            adoptSession(id, source) {
                if (!frame || source !== frame.contentWindow || !id) return;
                sessionId = String(id);
                host.onSession(sessionId);
            },
            isSource: (source) => !!(frame && source === frame.contentWindow),
            sessionId: () => sessionId,
            focus() { if (frame) frame.focus(); },
        };
    }

    root.CuttleIdeChat = { create: create };
})(globalThis);
