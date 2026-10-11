/* ================================================================
   Cuttle Chat — follow-up queue state (chat_followup_queue.js).
   Owner: the pending follow-up queue as ONE explicit state object
   { items, dirty, takeInFlight } plus its pure transitions and the
   take/reconcile decisions. Item shaping (normalize, partition,
   combine, fingerprint) stays owned by chat_activity.js and is used
   here ONLY through an explicit `activity` dependency — never
   reimplemented. Pure: no document, no window, no fetch, no timers.
   The page builds one `createController(host)` per chat root and
   supplies every effect (drain timer, revisioned transport, render,
   edit UI, DOM, send flow) through `host`; the controller owns the
   queue state registry, the edit state machine, and the
   enqueue/pause/resume/take/drain/steer transitions.
   Loaded after chat_activity.js, before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    /** Live queue: normalized items + persistence/take flags. */
    function createQueueState() {
        return { items: [], dirty: false, takeInFlight: false, revision: 0, editVersion: 0, base: [] };
    }

    /**
     * Append a follow-up. clock: { now() → ms, rand() → float } so id
     * generation is deterministic under test; the page passes
     * Date.now/Math.random. Shape mirrors the pre-extraction builder.
     */
    function enqueue(state, fields, clock) {
        const now = clock.now();
        const item = {
            id: 'fq_' + Number(now).toString(36) + '_' + Number(clock.rand()).toString(36).slice(2, 7),
            content: fields.content,
            created: Number(now),
            attachments: Array.isArray(fields.attachments) ? fields.attachments : [],
            rawMessage: fields.rawMessage != null ? fields.rawMessage : fields.content,
            paused: !!fields.paused,
        };
        state.items.push(item);
        state.editVersion++;
        return item;
    }

    function setPaused(state, id, paused) {
        if (!id) return null;
        const item = state.items.find((x) => x.id === id);
        if (!item) return null;
        item.paused = !!paused;
        state.editVersion++;
        return item;
    }

    function removeItem(state, id) {
        if (!id) return false;
        const before = state.items.length;
        state.items = state.items.filter((x) => x.id !== id);
        state.editVersion++;
        return state.items.length !== before;
    }

    function clearAll(state) {
        state.items = [];
        state.editVersion++;
    }

    function markDirty(state) {
        state.dirty = true;
    }

    function markClean(state, version) {
        if (version == null || state.editVersion === version) state.dirty = false;
    }

    function beginTake(state) {
        state.takeInFlight = true;
    }

    function endTake(state) {
        state.takeInFlight = false;
    }

    /**
     * Resolve the next outbound batch (pure part of drainNextFollowup).
     * serverResult: null (no session id → local path), { ok:true,
     * followups, remaining? } (server take), or { ok:false } (failed
     * take). Sets state.items to the remainder, returns the batch.
     * A failed server claim leaves the queue intact. Preserves the
     * server-remaining-as-is (unnormalized) and empty-queue-noop rules.
     */
    function resolveTake(state, editingId, serverResult, activity) {
        let batch = [];
        if (serverResult && serverResult.ok && Array.isArray(serverResult.followups)) {
            batch = serverResult.followups;
            state.items = Array.isArray(serverResult.remaining)
                ? serverResult.remaining
                : state.items.filter((x) => x.paused);
        } else if (serverResult == null) {
            const take = activity.partitionFollowupForDrain(state.items, editingId);
            batch = take.batch;
            state.items = take.remaining;
        }
        return batch;
    }

    /**
     * Reconcile a server-pushed list (pure part of applyServerFollowups).
     * opts: { editingId }. Never clobbers an in-flight take, unsynced
     * local edits, an active queue edit, or an identical list.
     */
    function reconcileServerList(state, list, opts, activity) {
        if (opts.revision != null && opts.revision < state.revision) return {applied: false, reason: 'stale'};
        if (state.takeInFlight || state.dirty) return { applied: false, reason: 'busy' };
        if (opts.editingId) return { applied: false, reason: 'editing' };
        if (!Array.isArray(list)) return { applied: false, reason: 'invalid' };
        if (opts.revision != null) state.revision = opts.revision;
        state.base = JSON.parse(JSON.stringify(list));
        if (activity.followupQueueFingerprint(list)
                === activity.followupQueueFingerprint(state.items)) {
            return { applied: false, reason: 'same' };
        }
        state.items = list.map((item) => activity.normalizeFollowupItem(item));
        return { applied: true };
    }

    /** Rebase only this client's edits: preserve remote appends and removals. */
    function mergeEdits(base, desired, remote) {
        const original = new Map(base.map(x => [x.id, x]));
        const wanted = new Map(desired.map(x => [x.id, x]));
        const result = remote.filter(x => !original.has(x.id) || wanted.has(x.id)).map(x => {
            const before = original.get(x.id), after = wanted.get(x.id);
            if (!before || !after) return x;
            const patch = {};
            Object.keys(after).forEach(k => {
                if (JSON.stringify(after[k]) !== JSON.stringify(before[k])) patch[k] = after[k];
            });
            return {...x, ...patch};
        });
        desired.forEach(x => {
            if (!original.has(x.id) && !result.some(r => r.id === x.id)) result.push(x);
        });
        return result;
    }

    /** Editable text for one queued item (rawMessage wins over content). */
    function followupEditableText(item) {
        if (!item) return '';
        if (item.rawMessage != null) return String(item.rawMessage);
        return String(item.content || '');
    }

    const api = {
        createQueueState,
        enqueue,
        setPaused,
        removeItem,
        clearAll,
        markDirty,
        markClean,
        beginTake,
        endTake,
        resolveTake,
        reconcileServerList,
        mergeEdits,
        followupEditableText,
    };

    /**
     * Per-chat queue states. `transfer` re-keys the current (draft) queue to
     * the id the first response assigned, keeping prompts queued before it.
     */
    function createRegistry(restore) {
        const states = new Map();
        return {
            adopt(key, current, transfer) {
                if (transfer) {
                    states.forEach((state, oldKey) => {
                        if (state === current && oldKey !== key) states.delete(oldKey);
                    });
                    states.set(key, current);
                }
                if (!states.has(key)) {
                    const state = createQueueState();
                    const saved = restore(key);
                    if (saved) {
                        state.items = saved.items;
                        state.base = saved.base;
                        state.revision = saved.revision;
                        state.dirty = true;
                    }
                    states.set(key, state);
                }
                return states.get(key);
            },
        };
    }

    /**
     * Queue retry intents belong to one consumer per tab. Other panes can
     * display the same chat and must not erase its failed edits or lost claim.
     */
    function ownerId(getStorage, draftId) {
        const fresh = () => Date.now() + ':' + Math.random().toString(36).slice(2);
        const key = 'cuttle.followupOwner.' + draftId;
        try {
            const storage = getStorage();
            const saved = storage.getItem(key);
            if (saved) return saved;
            const id = fresh();
            storage.setItem(key, id);
            return id;
        } catch (_) { return fresh(); }
    }
    api.createRegistry = createRegistry;
    api.ownerId = ownerId;

    /**
     * Follow-up execution + steering controller — the single owner of the
     * queue lifecycle. It holds the live queue state (per-session registry),
     * the edit state machine (editing id + prior paused flag), and the
     * enqueue / pause / resume / take / drain / steer transitions. Every
     * effect (transport, prefs persistence, DOM render, timers, paint and the
     * send/turn handoff) arrives as an explicit `host` capability; this module
     * never touches document/window/fetch. The page builds exactly one
     * controller per chat root and keeps no private queue/edit state.
     *
     * `host` capabilities (all required unless noted):
     *   activity                 chat_activity.js module (item decisions)
     *   clock                    { now() → ms, rand() → float }
     *   sessionIdsEqual(a,b)     id comparison
     *   canonicalizeSessionId(id), toAuthDbSessionId(id)
     *   currentSession()         active session id
     *   isAuthMode()             whether a server queue is in play
     *   isSessionGenerating()    turn-in-flight gate
     *   healStaleGenerating()    clears a stuck local generating state
     *   followupAuthSid()        numeric auth-db session id (or null)
     *   getPrefs(sid), setPrefs(sid, patch)
     *   writeField(), claimField()
     *   restoreQueue(key)        persisted unsynced-queue snapshot (or null)
     *   serializeMutation(key, fn)
     *   putFollowups(sid, body)  → { ok, status, data }
     *   takeFollowups(sid, body) → { ok, status, data }
     *   steer(sid, text)         → parsed /api/chat-steer body (or null)
     *   render()                 repaint the queue panel
     *   patchLiveSession(sid, items), broadcastActivity()
     *   formatWithAttachments(text, atts), stickyPrefixFor(raw)
     *   showChatArea(), addUserMessage(text, opts), autoScroll()
     *   saveUserMessage(text, opts), recordPromptHistory(text)
     *   processMessage(message, opts)
     *   steerLabel(agent)
     *   toast(msg, kind)
     *   setTimeout(fn, ms) → id, clearTimeout(id)
     */
    function createController(host) {
        const activity = host.activity;
        let state = createQueueState();
        const registry = createRegistry((key) => host.restoreQueue(key));
        let editingId = null;
        let editingWasPaused = false;
        let steeringText = '';
        let drainTimer = null;

        function clearDrainTimer() {
            if (drainTimer != null) {
                host.clearTimeout(drainTimer);
                drainTimer = null;
            }
        }

        /** Page keeps the raw timer handle; the owner decides whether to arm it. */
        function requestDrain(delayMs = 120) {
            if (editingId) return;
            clearDrainTimer();
            drainTimer = host.setTimeout(() => {
                drainTimer = null;
                drain();
            }, delayMs);
        }

        function adopt(sessionId, opts = {}) {
            state = registry.adopt(
                String(host.canonicalizeSessionId(sessionId) || 'new'),
                state, !!opts.transfer);
            editingId = null;
            if (opts.transfer && state.items.length && host.isAuthMode()) {
                persist(host.toAuthDbSessionId(sessionId), state);
            }
        }

        function beginEdit(id) {
            if (!id || !state.items.some((x) => x.id === id)) return;
            const item = state.items.find((x) => x.id === id);
            if (!item) return;
            editingId = id;
            editingWasPaused = !!item.paused;
            // Soft-pause so /followups/take cannot yank the message mid-edit.
            if (!item.paused) {
                setPaused(state, id, true);
                persist();
            }
            clearDrainTimer();
            host.render();
        }

        function finishEdit(opts = {}) {
            const resumeDrain = opts.resumeDrain !== false;
            const id = editingId;
            editingId = null;
            if (id) {
                const item = state.items.find((x) => x.id === id);
                if (item) setPaused(state, id, editingWasPaused);
            }
            editingWasPaused = false;
            host.render();
            persist();
            if (resumeDrain && state.items.some((x) => !x.paused) && !host.isSessionGenerating()) {
                requestDrain(180);
            }
        }

        function cancelEdit() {
            finishEdit({ resumeDrain: true });
        }

        /** Drop a stale editing id whose item is gone (render-time guard). */
        function pruneEditing() {
            if (editingId && !state.items.some((x) => x.id === editingId)) editingId = null;
        }

        function saveEdit(id, nextText) {
            if (!id) return;
            const item = state.items.find((x) => x.id === id);
            if (!item) return;
            const text = String(nextText ?? '').trim();
            if (!text && !(item.attachments && item.attachments.length)) {
                // Empty with no attachments — drop it rather than keeping a blank send.
                editingId = null;
                editingWasPaused = false;
                remove(id);
                return;
            }
            item.rawMessage = text;
            item.content = host.formatWithAttachments(text, item.attachments || []);
            finishEdit({ resumeDrain: true });
        }

        function applyServerList(list, revision) {
            // Don't clobber an in-progress take, unsynced edits, a queue edit,
            // or an identical list (owned reconcile decision).
            if (state.dirty && !state.takeInFlight) {
                persist();
                return;
            }
            const r = reconcileServerList(state, list, { editingId, revision }, activity);
            if (!r.applied && r.reason !== 'same') return;
            host.patchLiveSession(host.currentSession(), state.items);
            host.render();
            const claim = (host.getPrefs(host.currentSession()) || {})[host.claimField()];
            if ((state.items.some((x) => !x.paused) || claim)
                    && !host.isSessionGenerating() && !editingId) {
                requestDrain(250);
            }
            try { host.broadcastActivity(); } catch (_) {}
        }

        function rememberUnsynced(sid, st) {
            const patch = {};
            patch[host.writeField()] = st.dirty ? {
                items: st.items, base: st.base, revision: st.revision,
            } : null;
            host.setPrefs(sid, patch);
        }

        async function persist(sid = host.followupAuthSid(), st = state) {
            if (!sid) return true;
            markDirty(st);
            rememberUnsynced(sid, st);
            if (st.write) return st.write;
            const write = host.serializeMutation(sid + ':followups', async () => {
                for (let attempt = 0; attempt < 4; attempt++) {
                    const version = st.editVersion;
                    const desired = JSON.parse(JSON.stringify(st.items));
                    const base = JSON.parse(JSON.stringify(st.base));
                    const r = await host.putFollowups(sid, { followups: desired, revision: st.revision });
                    const data = r.data || {};
                    if (r.status === 409 && data.conflict) {
                        st.revision = data.revision;
                        st.items = mergeEdits(base, st.items, data.followups);
                        st.base = data.followups;
                        rememberUnsynced(sid, st);
                        continue;
                    }
                    if (!r.ok || !data.success) throw new Error(data.error || 'Queue sync failed');
                    st.revision = data.revision;
                    st.base = data.followups || desired;
                    markClean(st, version);
                    rememberUnsynced(sid, st);
                    host.patchLiveSession(sid, st.base);
                    if (!st.dirty) {
                        st.items = st.base;
                        if (state === st && host.sessionIdsEqual(host.currentSession(), sid)) host.render();
                        return true;
                    }
                }
                return false;
            }).catch(() => false).finally(() => { st.write = null; });
            st.write = write;
            return write;
        }

        function persistAppend() {
            // Rebased revisioned PUT also handles appends. Keeping one mutation
            // path means a failed append retains its intent and cannot be dropped by a GET.
            return persist();
        }

        function enqueueItem(message, opts = {}) {
            const item = enqueue(state, {
                content: message,
                attachments: opts.attachments,
                rawMessage: opts.rawMessage,
                paused: opts.paused,
            }, host.clock);
            host.render();
            persistAppend(item);
            return item;
        }

        function togglePaused(id) {
            if (!id) return;
            const current = state.items.find((x) => x.id === id);
            if (!current) return;
            const item = setPaused(state, id, !current.paused);
            host.render();
            persist();
            if (!item.paused && !host.isSessionGenerating()) requestDrain(120);
        }

        function remove(id) {
            if (!id) return;
            if (editingId === id) {
                editingId = null;
                editingWasPaused = false;
            }
            removeItem(state, id);
            host.render();
            persist();
        }

        function clear() {
            editingId = null;
            editingWasPaused = false;
            clearAll(state);
            host.render();
            persist();
        }

        function combineBatch(items) {
            return activity.combineFollowupBatch(items, {
                stickyPrefixFor: host.stickyPrefixFor,
                formatWithAttachments: host.formatWithAttachments,
            });
        }

        async function drain() {
            // Wait until this chat is fully idle (no local stream, no remote run).
            if (editingId) return;
            host.healStaleGenerating();
            if (host.isSessionGenerating()) return;
            // Re-check after heal — edit may have started while we were waiting.
            if (editingId) return;
            const origin = host.currentSession();
            const st = state;
            if (st.takeInFlight) return;
            let batch = [];
            const sid = host.followupAuthSid();
            if (sid) {
                if (st.dirty && !await persist()) return;
                if (editingId || !host.sessionIdsEqual(host.currentSession(), origin) || state !== st) return;
                const claimId = (host.getPrefs(sid) || {})[host.claimField()]
                    || ('queue-' + Date.now() + '-' + Math.random().toString(36).slice(2));
                const setClaim = (value) => {
                    const patch = {};
                    patch[host.claimField()] = value;
                    host.setPrefs(sid, patch);
                };
                setClaim(claimId);
                const takeVersion = st.editVersion;
                beginTake(st);
                try {
                    const r = await host.takeFollowups(sid, { revision: st.revision, claim_id: claimId });
                    const data = r.data || {};
                    if (r.status === 409) {
                        setClaim(null);
                        st.revision = data.revision;
                        st.items = st.editVersion !== takeVersion
                            ? mergeEdits(st.base, st.items, data.followups || [])
                            : data.followups || [];
                        st.base = data.followups || [];
                        rememberUnsynced(sid, st);
                        return;
                    }
                    if (!r.ok || !data.success) return;
                    const editedDuringTake = st.editVersion !== takeVersion;
                    const editedItems = st.items;
                    st.revision = data.revision;
                    st.items = data.remaining || [];
                    st.base = st.items.slice();
                    batch = data.followups || [];
                    if (editedDuringTake || editingId || !host.sessionIdsEqual(host.currentSession(), origin) || state !== st) {
                        // Restore to the captured chat when navigation or editing
                        // races the claim; retain the write if the network fails.
                        st.items = editedDuringTake ? editedItems : [...batch, ...st.items];
                        st.editVersion++;
                        st.dirty = true;
                        rememberUnsynced(sid, st);
                        setClaim(null);
                        await persist(sid, st);
                        return;
                    }
                    setClaim(null);
                } catch (_) { return; }
                finally { endTake(st); }
            } else {
                batch = resolveTake(st, editingId, null, activity);
            }
            // Keep editing state if the edited item remained in the queue.
            if (editingId && !state.items.some((x) => x.id === editingId)) {
                editingId = null;
                editingWasPaused = false;
            }
            host.render();
            const combined = combineBatch(batch);
            if (!combined) return;
            const atts = combined.attachments || [];
            const displayMessage = combined.displayMessage;
            const message = combined.message;
            host.showChatArea();
            host.addUserMessage(displayMessage, { timestamp: Date.now(), attachments: atts });
            host.autoScroll();
            host.saveUserMessage(displayMessage, { attachments: atts });
            host.recordPromptHistory(message);
            await host.processMessage(message, { attachments: atts });
        }

        async function steer(message, attachments) {
            const sid = host.currentSession();
            const text = String(message || '').trim();
            if (sid == null || sid === '' || !text) return false;
            if (Array.isArray(attachments) && attachments.length) return false;
            if (steeringText === text) return true;
            steeringText = text;
            let data = null;
            try {
                data = await host.steer(sid, text);
            } catch (_) {
                data = null;
            } finally {
                steeringText = '';
            }
            if (!data || !data.steered) return false;
            if (host.sessionIdsEqual(host.currentSession(), sid)) {
                host.addUserMessage(text, {
                    timestamp: Date.now(),
                    steered: true,
                    steered_agent: data.agent,
                });
                host.autoScroll();
                host.saveUserMessage(text);
            }
            host.recordPromptHistory(text);
            const label = host.steerLabel(data.agent) || data.agent || 'agent';
            host.toast('Sent into the running ' + label + ' turn.', 'success');
            return true;
        }

        return {
            adopt,
            getState: () => state,
            getEditingId: () => editingId,
            pruneEditing,
            enqueue: enqueueItem,
            togglePaused,
            remove,
            clear,
            beginEdit,
            finishEdit,
            cancelEdit,
            saveEdit,
            applyServerList,
            persist: (sid, st) => persist(sid, st || state),
            requestDrain,
            cancelDrain: clearDrainTimer,
            drain,
            steer,
        };
    }
    api.createController = createController;

    const ns = (root.CuttleFollowupQueue = root.CuttleFollowupQueue || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
