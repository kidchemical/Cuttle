/* ================================================================
   Cuttle Chat — composer / send-planning domain (chat_composer.js).
   Owner: send eligibility, keyboard-submit interpretation, sticky
   resolution after send, control-lane text classification, the
   send-dispatch plan, chip composition, and the sticky/shared-composer
   selection decisions (what to persist after send, what to restore on a
   chat switch, and how a server `composer_selection` snapshot merges
   into session prefs) as pure decisions over explicit inputs: no
   document, no window, no localStorage, no fetch here. Loaded before
   chat_page.js; the page owns textarea DOM, focus/caret, event
   wiring, draft persistence, fetch/SSE initiation, streaming,
   history persistence, and message rendering, and calls into
   `CuttleChatComposer.*`.

   Cross-domain rule: slash parsing/sticky behavior stays in
   `CuttleChatSlash` (injected as `stickyOf` / `isControl`
   callbacks); attachment note formatting stays in
   `CuttleChatAttachments`; follow-up queue interpretation stays in
   `CuttleChatActivity`. This module never duplicates their logic.

   Mirrors the page contract: bare sticky-agent tokens are not
   sendable, native control commands always are, the dispatch guard
   blocks only true double-fire (never queued follow-ups), and
   supervised/restart control-lane messages bypass the queue while
   generating.
   ================================================================ */
(function (root) {
    'use strict';

    /**
     * Enter submits (Shift+Enter never does). Touch composers need
     * Ctrl/Meta+Enter. `touchMode` is gathered by the page.
     */
    function enterSubmits(parts) {
        const p = parts || {};
        if (p.key !== 'Enter' || p.shiftKey) return false;
        if (p.touchMode) return !!(p.ctrlKey || p.metaKey);
        return true;
    }

    /**
     * True when composed text (plus optional attachments) is worth sending.
     * Sticky-agent chips alone (``/cursor``, ``/cursor /model auto``, …) are
     * not a prompt — Enter must no-op. Native control commands (``/restart``)
     * and real user text remain sendable.
     * `deps`: { stickyOf(message) → {prefix,…}|null, isControl(text) → bool }.
     */
    function isSendableComposerMessage(message, attachments, deps) {
        const d = deps || {};
        const stickyOf = typeof d.stickyOf === 'function' ? d.stickyOf : null;
        const isControl = typeof d.isControl === 'function' ? d.isControl : null;
        if (attachments && attachments.length) return true;
        let rest = String(message || '').trim();
        if (!rest) return false;
        if (isControl && isControl(rest)) return true;
        // Strip leading sticky agent prefixes (bare token or spaced prefix).
        for (;;) {
            const cmd = stickyOf ? stickyOf(rest) : null;
            if (!cmd) break;
            const token = String(cmd.prefix || '').replace(/\s+$/, '');
            const low = rest.toLowerCase();
            if (low === token.toLowerCase()) return false;
            if (low.startsWith(token.toLowerCase() + ' ')) {
                rest = rest.slice(token.length).trim();
                continue;
            }
            if (rest.startsWith(cmd.prefix)) {
                rest = rest.slice(cmd.prefix.length).trim();
                continue;
            }
            break;
        }
        if (!rest) return false;
        // ``/cursor /restart status`` after strip — control still wins.
        if (isControl && isControl(rest)) return true;
        // Nested agent one-shots / mode cmds are real turns (CH-000482).
        if (/^\/(usage|cost|usage-live|about|clear|agent)(\s|$)/i.test(rest)) return true;
        if (/^(usage|cost|usage-live|about|clear|agent)(\s|$)/i.test(rest)) return true;
        if (/^\/(plan|ask|sandbox)(\s|$)/i.test(rest)) return true;
        if (/^\/model\s+refresh\b/i.test(rest)) return true;
        // Model setting chips alone (/model auto) are not a user prompt either.
        if (/^\/model(\s+\S+)?$/i.test(rest)) return false;
        if (/^\/model\s+\S+/i.test(rest)) {
            rest = rest.replace(/^\/model\s+\S+\s*/i, '').trim();
        }
        return !!rest;
    }

    /**
     * Supervised / restart control-lane text classification (the page
     * checks `window.CuttleSupervised` first; this is the fallback core).
     * Control-lane messages must never enter the pending-prompt queue.
     */
    function isImmediateControlLaneText(text) {
        const raw = String(text || '').trim();
        if (!raw.startsWith('/')) return false;
        // Drop sticky agent chip if present: /cursor /coordinate status
        const t = raw.replace(
            /^\/(?:cursor|codex|claude|hermes|muse|deepseek)(?:\s+[^\s/]+)?\s+(?=\/(?:coordinate|coordinator|restart|branch)\b)/i,
            ''
        ).trim().toLowerCase();
        if (t === '/restart' || t.startsWith('/restart ')) return true;
        if (t === '/branch' || t.startsWith('/branch ')) return true;
        if (t === '/coordinator' || t.startsWith('/coordinator ')) {
            const rest = t.slice('/coordinator'.length).trim();
            if (!rest) return true;
            const head = rest.split(/\s+/)[0];
            return ['status', 'show', 'mode', 'profile', 'worker', 'review-loops', 'review_loops', 'followups', 'reset'].includes(head);
        }
        if (t === '/coordinate' || t.startsWith('/coordinate ')) {
            const rest = t.slice('/coordinate'.length).trim();
            if (!rest) return true;
            const head = rest.split(/\s+/)[0];
            return ['status', 'show', 'cancel', 'stop', 'abort', 'followup', 'follow-up', 'follow_up'].includes(head);
        }
        return false;
    }

    /**
     * Sticky command surviving a send (null → page clears all chips).
     * A native control command is a one-off handled by the page (it must
     * not evict the sticky agent chip); Bare `/cursor` re-resolves so the
     * badge stays pinned instead of looking like a user removal.
     */
    function stickyCommandAfterSend(parts, deps) {
        const p = parts || {};
        const d = deps || {};
        const stickyOf = typeof d.stickyOf === 'function' ? d.stickyOf : null;
        if (!stickyOf) return null;
        return stickyOf(String(p.message || '')) || null;
    }

    /**
     * Send-dispatch plan. The page gathers, executes, and owns every side
     * effect (clear, persist, render, fetch, queue). Inputs are already
     * resolved: `sendable` via isSendableComposerMessage, `control` via
     * the control-lane check on the raw message, and the normalized
     * strings via the message domain's content normalization
     * (null in-flight normalizes to '' — the page must normalize, never
     * pass null, to preserve duplicate semantics exactly).
     *
     * `generatingBeforeHeal` preserves the guard's original pre-heal read:
     * the guard blocks only true double-fire before a turn claims the
     * generating slot; once generating, follow-ups fall through to queue.
     *
     * Returns { action } where action is one of:
     *   ignore-guard | ignore-empty | ignore-duplicate | followup | normal
     * `normal` carries { outbound, controlLane }; `followup` and
     * `ignore-duplicate` carry { outbound } for the page's queue/clear flow.
     */
    function composerSendPlan(parts) {
        const p = parts || {};
        const message = String(p.message || '');
        if (p.guardSet && !p.generatingBeforeHeal) return { action: 'ignore-guard' };
        if (!p.sendable) return { action: 'ignore-empty' };
        const control = !!p.control;
        const outbound = message || '(see attached files)';
        if (p.generating && !control) {
            const norm = String(p.normalizedMessage || '');
            const dup = (p.inFlightMessage === norm)
                || (Array.isArray(p.queuedMessages) && p.queuedMessages.some((q) => q === norm));
            if (dup) return { action: 'ignore-duplicate', outbound };
            return { action: 'followup', outbound };
        }
        return { action: 'normal', outbound, controlLane: message ? control : false };
    }

    /** A draft keeps every staged chip, including one-shot commands and
     * Cursor's model companion; sticky-only prefs describe sent chats. */
    function draftChips(chips) {
        return (Array.isArray(chips) ? chips : [])
            .filter((chip) => chip && typeof chip.prefix === 'string')
            .map((chip) => ({ ...chip }));
    }

    function draftWithSharedAgent(draft, selection, commands) {
        if (!draft) return null;
        return {
            ...draft,
            chips: draftChips(draft.chips).filter((chip) => !(commands || []).some(
                (cmd) => cmd.stickySession && cmd.prefix === chip.prefix
            )).concat(draftChips(selection.stickyChips)),
        };
    }

    /**
     * Compose the outbound message from resolved composer chips and body.
     * `parts` = { body, chips } (the page resolves the active composer's
     * chips, with welcome fallback, and owns slashCtx/DOM); `deps` =
     * { isControl(text) → bool }. A trailing control chip (or a typed
     * control body) outranks stacked sticky agent chips. Returns the body
     * unchanged when no chips apply. Mirrors the page's original shape.
     */
    function composeMessageWithSlashChip(parts, deps) {
        const p = parts || {};
        const d = deps || {};
        const isControl = typeof d.isControl === 'function' ? d.isControl : null;
        const body = String(p.body || '').trim();
        let chips = Array.isArray(p.chips) ? p.chips.slice() : [];
        if (!chips.length) return body;
        const controlIdx = chips.findIndex((c) => isControl && isControl(c.prefix));
        if (controlIdx >= 0) {
            chips = chips.slice(controlIdx);
        } else if (isControl && isControl(body)) {
            return body;
        }
        const prefixPart = chips.map((c) => c.prefix).join('');
        if (!body) {
            const t = prefixPart.replace(/\s+$/, '').trim();
            return t || prefixPart.trim();
        }
        const last = chips[chips.length - 1];
        if (last.prefix.endsWith(' ')) return prefixPart + body;
        return prefixPart + ' ' + body;
    }

    /**
     * Sticky/session persisted selection from the current composer chips.
     * `deps` = { commands, liveLabel(chip) → label, paletteCategory(chip)
     * → category, clearedPending }. Only registry entries marked
     * `stickySession` survive; when none do, the cleared flag carries
     * through. Mirrors the page's post-send persistence shape.
     */
    function stickySessionSelection(chips, deps) {
        const d = deps || {};
        const commands = Array.isArray(d.commands) ? d.commands : [];
        const sticky = (Array.isArray(chips) ? chips : []).filter((c) => {
            const match = commands.find((s) => s.prefix === c.prefix);
            return match && match.stickySession;
        });
        return {
            stickyChips: sticky.map((c) => ({
                prefix: c.prefix,
                label: (typeof d.liveLabel === 'function' && d.liveLabel(c)) || c.label,
                category: (typeof d.paletteCategory === 'function' && d.paletteCategory(c))
                    || c.category || 'command',
            })),
            stickyCleared: sticky.length ? false : !!d.clearedPending,
        };
    }

    /**
     * Chips to restore when a chat opens or switches. `prefs` is the
     * session-prefs snapshot; when it holds no sticky chips and the user
     * never cleared the badge, recent history may re-seed one through
     * `deps.infer(messages) → chips[]`. Returns { cleared, chips, inferred }.
     */
    function stickyChipsToRestore(prefs, messages, deps) {
        const d = deps || {};
        const cleared = !!(prefs && prefs.stickyCleared);
        let chips = prefs && Array.isArray(prefs.stickyChips) ? prefs.stickyChips.slice() : [];
        let inferred = false;
        if (!chips.length && !cleared) {
            const found = typeof d.infer === 'function' ? d.infer(messages) : [];
            if (found && found.length) {
                chips = found;
                inferred = true;
            }
        }
        return { cleared, chips, inferred };
    }

    /**
     * Draft restore plan: the chips/pins to reapply from a saved composer
     * draft. Returns null when there is no draft. `deps.draftChips(chips)`
     * clones the staged chips; the cursor model companion is reported so
     * the page can seed its model supplement.
     */
    function composerDraftRestorePlan(draft, deps) {
        if (!draft) return null;
        const d = deps || {};
        const chips = typeof d.draftChips === 'function'
            ? d.draftChips(draft.chips)
            : draftChips(draft.chips);
        const cursorModel = chips.find((c) => c.category === 'cursor-model') || null;
        return { chips, pins: draft.pins || null, cursorModel };
    }

    /**
     * Session-prefs patch adopted from a server `composer_selection`
     * snapshot. The saved draft merges through draftWithSharedAgent so a
     * sticky chip already staged locally is not duplicated.
     */
    function sharedSelectionPrefsPatch(incoming, existingDraft, deps) {
        const d = deps || {};
        const selection = incoming || {};
        return {
            stickyChips: selection.stickyChips,
            stickyCleared: !!selection.stickyCleared,
            composerDraft: draftWithSharedAgent(existingDraft, selection, d.commands || []),
        };
    }

    const api = {
        draftWithSharedAgent,
        draftChips,
        enterSubmits,
        isSendableComposerMessage,
        isImmediateControlLaneText,
        stickyCommandAfterSend,
        composerSendPlan,
        composeMessageWithSlashChip,
        stickySessionSelection,
        stickyChipsToRestore,
        composerDraftRestorePlan,
        sharedSelectionPrefsPatch,
    };

    const ns = (root.CuttleChatComposer = root.CuttleChatComposer || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
