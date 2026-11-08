/* ================================================================
   Cuttle Chat — orchestration header chip model (chat_orchestration_model.js).
   Pure: what the "Orchestrating · <playbook>" chip and its dialog show,
   from GET /api/orchestrator/bindings/<session> and /api/orchestrator/playbooks.
   No DOM, no fetch; node-testable. Controller: chat_orchestration.js.
   ================================================================ */
(function (root) {
    'use strict';

    const ID_RE = /^[a-z][a-z0-9_-]{0,39}$/;

    /** Chip view for a binding response, or null when the chat is not orchestrating. */
    function chip(binding) {
        const b = binding || {};
        if (!b.bound) return null;
        const name = String(b.playbook_name || b.playbook_id || 'playbook');
        return {
            text: 'Orchestrating · ' + name,
            playbookId: String(b.playbook_id || ''),
            playbookName: name,
            goal: String(b.goal || ''),
            missing: !!b.playbook_missing,
            boundAt: Number(b.bound_at) || 0,
            agents: agentChips(b.agents),
        };
    }

    const STATUS = { pending: 'Queued', running: 'Running', done: 'Done', failed: 'Failed',
        cancelled: 'Cancelled', closed: 'Closed' };

    /** The orchestrator's sub-agents as chips: initial or emoji avatar, name, status. */
    function agentChips(list) {
        return (Array.isArray(list) ? list : []).filter((a) => a && /^CH-\d+$/.test(String(a.handle || '')))
            .map((a) => {
                const name = String(a.name || a.handle);
                const avatar = String(a.avatar || '').trim();
                const status = STATUS[a.status] ? a.status : 'done';
                return {
                    handle: String(a.handle),
                    name,
                    mark: avatar && avatar.toLowerCase() !== 'cuttle' && [...avatar].length <= 2 ? avatar
                        : (name.replace(/[^\p{L}\p{N}]/gu, '')[0] || '?').toUpperCase(),
                    status,
                    statusLabel: STATUS[status],
                    live: status === 'running' || status === 'pending',
                    tip: [String(a.handle), [a.agent, a.model, a.effort].filter(Boolean).join(' · '), STATUS[status]]
                        .filter(Boolean).join(' — '),
                };
            });
    }

    /** Dialog fields: editing a bound chat keeps its playbook and goal; a new one uses the default. */
    function dialogDefaults(binding, playbooks) {
        const b = binding || {};
        const list = (playbooks && playbooks.playbooks) || [];
        const def = (playbooks && playbooks.default_id) || (list[0] && list[0].id) || 'default';
        return {
            editing: !!b.bound,
            playbookId: b.bound ? String(b.playbook_id || def) : def,
            goal: b.bound ? String(b.goal || '') : '',
            playbooks: list.filter((p) => p && p.id).map((p) => ({
                id: String(p.id),
                label: String(p.name || p.id) + (p.id === def ? ' (default)' : ''),
                problem: (p.missing_profiles || []).length ? 'missing profiles: ' + p.missing_profiles.join(', ') : '',
            })),
        };
    }

    /**
     * Validate the dialog. Start → the `/orchestrate @id goal` message the
     * composer sends (the server binds it before the agent runs); edit → the
     * PUT body with only what changed.
     */
    function submitPlan(defaults, fields) {
        const d = defaults || {};
        const f = fields || {};
        const id = String(f.playbookId || '').trim().toLowerCase();
        const goal = String(f.goal || '').trim();
        const errors = [];
        if (!ID_RE.test(id)) errors.push('Pick a playbook.');
        const pb = (d.playbooks || []).find((p) => p.id === id);
        if (pb && pb.problem) errors.push('That playbook has ' + pb.problem + '.');
        if (!d.editing && !goal) errors.push('Give the orchestrator a goal.');
        if (errors.length) return { ok: false, errors };
        if (!d.editing) return { ok: true, kind: 'send', text: '/orchestrate @' + id + ' ' + goal };
        const body = {};
        if (id !== d.playbookId) body.playbook_id = id;
        if (goal !== d.goal) body.goal = goal;
        return { ok: true, kind: Object.keys(body).length ? 'put' : 'none', body };
    }

    const api = { chip, agentChips, dialogDefaults, submitPlan };
    if (typeof module === 'object' && module.exports) module.exports = api;
    root.CuttleChatOrchestrationModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
