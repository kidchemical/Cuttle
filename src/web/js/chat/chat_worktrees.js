/* ================================================================
   Cuttle Chat — worktree transport + pure helpers (chat_worktrees.js).
   Owner: the composer's worktree client surface. Feature-probe, list,
   create, and default-branch naming. No DOM and no page state here —
   chat_page owns the current checkout binding and passes project ids in.

   Server owner: api.worktrees (routes /api/worktrees, flag-gated).
   ================================================================ */
(function (root) {
    'use strict';

    let enabled = null; // null = unknown, true/false after a probe
    // Registry snapshots keep a checkout's path and branch under one identity.
    const checkoutByUid = Object.create(null);

    function rememberWorktrees(items) {
        (items || []).forEach((w) => {
            if (w && w.checkout_uid) checkoutByUid[String(w.checkout_uid)] = Object.assign({}, w);
        });
    }

    function branchForUid(uid) {
        const checkout = checkoutForUid(uid);
        return checkout ? String(checkout.branch || '') : '';
    }

    function checkoutForUid(uid) {
        return checkoutByUid[String(uid || '')] || null;
    }

    // Last server-side binding seen per chat, so a sync only re-applies a
    // binding the server changed (an agent `bind`), never a local pick in flight.
    const serverUidBySession = Object.create(null);

    /**
     * Note a messages payload's bound checkout. Returns true when the server's
     * binding for this chat changed since the last payload seen for it.
     */
    function observeSessionCheckout(sessionId, data) {
        if (!data || !Object.prototype.hasOwnProperty.call(data, 'checkout_uid')) return false;
        if (data.checkout && data.checkout.checkout_uid) rememberWorktrees([data.checkout]);
        const key = String(sessionId);
        const uid = String(data.checkout_uid || '');
        const changed = Object.prototype.hasOwnProperty.call(serverUidBySession, key)
            && serverUidBySession[key] !== uid;
        serverUidBySession[key] = uid;
        return changed;
    }

    function slug(name) {
        const s = String(name || '')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '');
        return s.slice(0, 40).replace(/-+$/g, '') || 'worktree';
    }

    /** Deterministic default branch name for a new worktree (editable in UI). */
    function defaultBranch(proj, prefix) {
        const p = String(prefix || 'cuttle').replace(/\/+$/g, '') || 'cuttle';
        const base = (proj && (proj.name || proj.path)) || 'worktree';
        return p + '/' + slug(base);
    }

    /** True only after a successful probe; unknown/failed → false (hidden UI). */
    function isEnabled() {
        return enabled === true;
    }

    /** Probe the flag once (the server 404s the surface when disabled). */
    async function probe() {
        try {
            const resp = await fetch('/api/worktrees', {
                credentials: 'include',
                cache: 'no-store',
            });
            if (resp.status === 200) {
                enabled = true;
                const data = await resp.json().catch(() => null);
                if (data && Array.isArray(data.worktrees)) rememberWorktrees(data.worktrees);
            } else {
                enabled = false;
            }
        } catch (_) {
            enabled = false;
        }
        return enabled;
    }

    async function list(projectId) {
        try {
            const qs = new URLSearchParams();
            if (projectId != null && String(projectId) !== '') qs.set('project_id', String(projectId));
            const resp = await fetch('/api/worktrees' + (qs.toString() ? '?' + qs.toString() : ''), {
                credentials: 'include',
                cache: 'no-store',
            });
            const data = await resp.json().catch(() => null);
            const items = data && data.success && Array.isArray(data.worktrees) ? data.worktrees : [];
            rememberWorktrees(items);
            return items;
        } catch (_) {
            return [];
        }
    }

    /** Create a worktree; resolves to the checkout object or null on failure. */
    async function create(projectId, branch, baseBranch) {
        try {
            const body = { project_id: projectId, branch: branch };
            if (baseBranch) body.base_branch = baseBranch;
            const resp = await fetch('/api/worktrees', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json().catch(() => null);
            if (resp.ok && data && data.success) {
                rememberWorktrees([data.worktree]);
                return data.worktree || null;
            }
            return { error: (data && data.error) || ('Worktree creation failed (' + resp.status + ')') };
        } catch (err) {
            return { error: (err && err.message) || 'Worktree creation failed' };
        }
    }

    async function remove(checkoutUid, force) {
        try {
            const qs = force ? '?force=1' : '';
            const resp = await fetch('/api/worktrees/' + encodeURIComponent(checkoutUid) + qs, {
                method: 'DELETE',
                credentials: 'include',
            });
            const data = await resp.json().catch(() => null);
            return !!(resp.ok && data && data.success);
        } catch (_) {
            return false;
        }
    }

    const api = {
        slug, defaultBranch, isEnabled, probe, list, create, remove, branchForUid, checkoutForUid,
        observeSessionCheckout,
    };
    const ns = (root.CuttleChatWorktrees = root.CuttleChatWorktrees || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
