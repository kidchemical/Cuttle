/* ================================================================
   Cuttle Chat — sub-agent launchers on chat bubbles (chat_subagent_launchers.js).
   Owner: the launcher row under a reply (child chats) or a child's user
   bubble (its parent): list normalization, launcher markup (fleet cards via
   the pure `CuttleChatSubagentFleet` when the server marks a fleet), the
   shared agent chip for those cards, in-place DOM mounting that keeps
   unchanged cards (and focus) mounted across history polls, and the live
   list painted onto the in-progress typing bubbles.

   Interface: `create(deps)` → one controller per chat frame.
   deps: { win, escapeHtmlInline, parseChatHandleToken, avatarHtml,
           chipCategoryClass }. The page keeps chat-handle parsing, avatars
   and slash chip styling, and click → open chat (`data-chat-handle`).
   Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * One launcher controller per chat frame.
     * @param {{win: Window, escapeHtmlInline: Function, parseChatHandleToken: Function,
     *          avatarHtml: Function, chipCategoryClass: Function}} deps
     */
    function create(deps) {
        const frameDoc = deps.win.document;
        const escapeHtmlInline = deps.escapeHtmlInline;
        const parseChatHandleToken = deps.parseChatHandleToken;
        const avatarHtml = deps.avatarHtml;
        const chipCategoryClass = deps.chipCategoryClass;
        /** Sub-agents attached to the in-progress turn (live status). */
        let liveList = [];

        function normalizeSubagentList(raw) {
            if (!raw) return [];
            let list = raw;
            if (typeof list === 'string') {
                try { list = JSON.parse(list); } catch (_) { return []; }
            }
            if (!Array.isArray(list)) return [];
            const out = [];
            list.forEach((item) => {
                if (!item || typeof item !== 'object') return;
                const handleRaw = String(item.handle || '').trim();
                const sid = item.session_id != null ? String(item.session_id) : '';
                const token = handleRaw || (sid ? ('CH-' + String(sid).padStart(6, '0')) : '');
                const parsed = parseChatHandleToken(token);
                if (!parsed) return;
                out.push({
                    handle: parsed.display,
                    sessionId: parsed.sessionId,
                    label: String(item.label || item.title || item.display_name || parsed.display).trim() || parsed.display,
                    agent: String(item.agent || '').trim(),
                    model: String(item.model || '').trim(),
                    status: String(item.status || '').trim(),
                    generating: !!item.generating,
                    avatar: String(item.avatar || '').trim(),
                    // Fleet-card fields (server-owned outcome; see chat_subagent_fleet.js).
                    fleet: !!item.fleet,
                    outcome: String(item.outcome || '').trim(),
                    summary: String(item.summary || ''),
                    detail: String(item.detail || ''),
                    effort: String(item.effort || '').trim(),
                    displayName: String(item.display_name || '').trim(),
                    startedAt: String(item.started_at || ''),
                    finishedAt: String(item.finished_at || ''),
                    liveStatusAt: String(item.live_status_at || ''),
                });
            });
            return out;
        }

        function parentRefsFromOpts(opts) {
            if (!opts) return [];
            const handleRaw = String(opts.parent_handle || '').trim();
            const sid = opts.parent_session_id != null && opts.parent_session_id !== ''
                ? String(opts.parent_session_id)
                : '';
            if (!handleRaw && !sid) return [];
            return [{
                handle: handleRaw,
                session_id: sid,
                label: String(opts.parent_label || 'Parent chat').trim() || 'Parent chat',
                agent: '',
                generating: false,
                avatar: String(opts.avatar || 'cuttle').trim(),
            }];
        }

        /**
         * Minimal shared agent chip for fleet cards: agent label only, with
         * `{agent} | {model} | {effort}` in the tip. Same `slash-command-chip`
         * chrome as the header/composer badges (label from the
         * `CuttleChatSlash.SLASH_COMMANDS` registry, palette class from the
         * harness id) — never the old bespoke fleet pill.
         */
        function fleetAgentChipHtml(s) {
            const agentId = String((s && s.agent) || '').trim().toLowerCase();
            if (!agentId) return '';
            const reg = globalThis.CuttleChatSlash && globalThis.CuttleChatSlash.SLASH_COMMANDS;
            const hit = Array.isArray(reg)
                ? reg.find((c) => String((c && c.prefix) || '').trim().toLowerCase() === '/' + agentId)
                : null;
            const rawAgent = String(s.agent || '').trim();
            const label = (hit && hit.label) ? String(hit.label) : rawAgent;
            const bits = [label, s.model, s.effort].map((x) => String(x || '').trim()).filter(Boolean);
            const tip = bits.join(' | ');
            return '<span class="slash-command-chip slash-command-chip--header'
                + chipCategoryClass(agentId)
                + ' slash-command-chip--no-shorten" role="img" aria-label="'
                + escapeHtmlInline('Agent: ' + tip) + '" title="'
                + escapeHtmlInline(tip) + '" data-full-label="'
                + escapeHtmlInline(label) + '" data-tip="'
                + escapeHtmlInline(tip) + '">'
                + '<span class="slash-chip-label">' + escapeHtmlInline(label) + '</span></span>';
        }

        function renderSubagentLaunchersHtml(subagents, opts) {
            const list = normalizeSubagentList(subagents);
            if (!list.length) return '';
            const isParent = !!(opts && opts.kind === 'parent');
            const fleet = globalThis.CuttleChatSubagentFleet;
            if (!isParent && fleet && fleet.hasFleet(list)) {
                return fleet.renderFleetHtml(list, { esc: escapeHtmlInline, avatarHtml, chipHtml: fleetAgentChipHtml });
            }
            const buttons = list.map((s) => {
                const handle = escapeHtmlInline(s.handle);
                const label = escapeHtmlInline(s.label);
                const title = escapeHtmlInline((isParent ? 'Open parent ' : 'Open ') + s.handle + (s.label ? ' — ' + s.label : ''));
                const agent = (!isParent && s.agent) ? escapeHtmlInline(s.agent) : '';
                const busy = s.generating ? ' is-generating' : '';
                const kindClass = isParent ? ' is-parent' : '';
                return (
                    '<button type="button" class="subagent-launcher' + kindClass + busy + '" data-chat-handle="' + handle + '" title="' + title + '">' +
                    '<span class="subagent-launcher-orb" aria-hidden="true"></span>' +
                    '<span class="subagent-launcher-label">' + label + '</span>' +
                    (agent ? '<span class="subagent-launcher-agent">' + agent + '</span>' : '') +
                    '</button>'
                );
            }).join('');
            const aria = isParent ? 'Parent chats' : 'Sub-agent chats';
            const wrapClass = isParent ? 'subagent-launchers is-parent' : 'subagent-launchers';
            return '<div class="' + wrapClass + '" role="group" aria-label="' + aria + '">' + buttons + '</div>';
        }

        function mountSubagentLaunchers(messageEl, subagents, opts) {
            if (!messageEl) return;
            const wrap = messageEl.querySelector('.message-content-wrapper');
            if (!wrap) return;
            const html = renderSubagentLaunchersHtml(subagents, opts);
            const existing = wrap.querySelector('.subagent-launchers');
            if (!html) {
                if (existing) existing.remove();
                return;
            }
            // Tooltips promote title attributes after mounting. Comparing outerHTML
            // to our source then replaces unchanged cards on every history poll.
            if (existing && existing.__cuttleLaunchersHtml === html) return;
            const template = frameDoc.createElement('template');
            template.innerHTML = html;
            const next = template.content.firstElementChild;
            if (existing) {
                // Keep the group and its buttons mounted. Re-focusing replacement
                // buttons can scroll an iPad iframe back to a fleet above the reader.
                existing.className = next.className;
                existing.setAttribute('aria-label', next.getAttribute('aria-label'));
                const cards = new Map(Array.from(existing.children).map((el) => [el.getAttribute('data-chat-handle'), el]));
                const kept = new Set();
                let cursor = existing.firstElementChild;
                Array.from(next.children).forEach((wanted) => {
                    const handle = wanted.getAttribute('data-chat-handle');
                    const card = handle && cards.get(handle);
                    const node = card || wanted;
                    const source = wanted.outerHTML;
                    if (card && card.__cuttleLauncherHtml !== source) {
                        Array.from(card.attributes).forEach((attr) => {
                            if (!wanted.hasAttribute(attr.name)) card.removeAttribute(attr.name);
                        });
                        Array.from(wanted.attributes).forEach((attr) => {
                            if (card.getAttribute(attr.name) !== attr.value) card.setAttribute(attr.name, attr.value);
                        });
                        if (card.__cuttleLauncherBodyHtml !== wanted.innerHTML) card.innerHTML = wanted.innerHTML;
                    }
                    node.__cuttleLauncherHtml = source;
                    node.__cuttleLauncherBodyHtml = wanted.innerHTML;
                    kept.add(node);
                    if (node !== cursor) existing.insertBefore(node, cursor);
                    cursor = node.nextElementSibling;
                });
                Array.from(existing.children).forEach((el) => {
                    if (!kept.has(el)) el.remove();
                });
                existing.__cuttleLaunchersHtml = html;
                return;
            }
            next.__cuttleLaunchersHtml = html;
            Array.from(next.children).forEach((el) => {
                el.__cuttleLauncherHtml = el.outerHTML;
                el.__cuttleLauncherBodyHtml = el.innerHTML;
            });
            const footer = wrap.querySelector('.message-footer');
            if (footer) wrap.insertBefore(next, footer);
            else wrap.appendChild(next);
        }

        function mountMessageLaunchers(messageEl, role, opts) {
            if (!messageEl) return;
            if (role === 'assistant') {
                mountSubagentLaunchers(messageEl, opts && opts.subagents);
                return;
            }
            if (role === 'user') {
                mountSubagentLaunchers(messageEl, parentRefsFromOpts(opts), { kind: 'parent' });
            }
        }

        function rememberSubagentOrbList(raw) {
            if (raw === undefined) return;
            liveList = normalizeSubagentList(raw);
        }

        // Orbs live inside the fleet cards now (running/queued cards render the
        // mini orbit); the indicator keeps only the parent orbit + status line.
        // Badges still mount onto the in-progress bubble as soon as a child attaches.
        function paintSubagentOrbs() {
            mountLiveSubagentBadges();
        }

        function syncSubagentOrbs(subagents) {
            rememberSubagentOrbList(subagents);
            paintSubagentOrbs();
        }

        function clearSubagentOrbs() {
            liveList = [];
            paintSubagentOrbs();
        }

        function mountLiveSubagentBadges() {
            if (typeof frameDoc === 'undefined' || !frameDoc.querySelectorAll) return;
            // Badges link to the child sessions as soon as they attach — mount
            // onto the in-progress bubble, not just the finalized reply.
            // Empty list unmounts (mountSubagentLaunchers removes on empty html).
            frameDoc.querySelectorAll('#typing-indicator, #typing-indicator-remote').forEach((el) => {
                try { mountSubagentLaunchers(el, liveList); } catch (_) {}
            });
        }

        return {
            normalizeList: normalizeSubagentList,
            parentRefs: parentRefsFromOpts,
            agentChipHtml: fleetAgentChipHtml,
            renderHtml: renderSubagentLaunchersHtml,
            mount: mountSubagentLaunchers,
            mountForMessage: mountMessageLaunchers,
            syncLive: syncSubagentOrbs,
            paintLive: paintSubagentOrbs,
            clearLive: clearSubagentOrbs,
        };
    }

    const api = { create };

    const ns = (root.CuttleChatSubagentLaunchers = root.CuttleChatSubagentLaunchers || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
