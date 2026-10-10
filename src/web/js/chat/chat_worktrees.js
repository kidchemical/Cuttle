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
    // uid -> branch, populated from every probe/list/create so other surfaces
    // (history rows, message chips) can label a bound checkout without a fetch.
    const branchByUid = {};

    function rememberWorktrees(items) {
        (items || []).forEach((w) => {
            if (w && w.checkout_uid) branchByUid[String(w.checkout_uid)] = String(w.branch || '');
        });
    }

    function branchForUid(uid) {
        return branchByUid[String(uid || '')] || '';
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

    const api = { slug, defaultBranch, isEnabled, probe, list, create, remove, branchForUid };
    const ns = (root.CuttleChatWorktrees = root.CuttleChatWorktrees || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
