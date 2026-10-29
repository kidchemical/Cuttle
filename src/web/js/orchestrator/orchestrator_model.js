/**
 * CuttleOrchestrator — pure model/markup for the Orchestrator and Agents Apps
 * (no DOM, no fetch). Page controllers own transport and events. The server
 * (api.orchestrator / api.subagents.profiles) is authoritative; checks here
 * are for immediate form feedback only.
 */
(function (root) {
    'use strict';

    const ID_RE = /^[a-z][a-z0-9_-]{0,39}$/;
    const ROLES = ['worker', 'escalation', 'expert'];
    const ROLE_LABELS = { worker: 'Worker', escalation: 'Escalation', expert: 'Expert' };
    const LIMIT_FIELDS = [
        ['solo_attempts', 'Solo attempts before delegating', 1, 20],
        ['solo_minutes', 'Solo minutes before delegating', 5, 600],
        ['child_retries', 'Same-tier retries before escalating', 0, 5],
        ['setup_retries', 'Setup failures before reporting', 1, 5],
    ];

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function slug(value) {
        return String(value == null ? '' : value).trim().toLowerCase().replace(/\s+/g, '-');
    }

    function validId(value) {
        return ID_RE.test(String(value || ''));
    }

    function clone(value) {
        return JSON.parse(JSON.stringify(value == null ? null : value));
    }

    // ── Agent catalog / profile pins ──────────────────────────────────────

    function catalogRow(catalog, agentId) {
        return (catalog || []).find((row) => row && row.id === agentId) || null;
    }

    function modelIds(row) {
        return ((row && row.models) || []).map((m) => (m && typeof m === 'object' ? m.id : m))
            .filter(Boolean).map(String);
    }

    /** Models + effort levels the pickers offer for a harness/model pair. */
    function pinOptions(row, model) {
        if (!row) return { models: [], efforts: [], supportsModel: false, supportsEffort: false };
        const models = modelIds(row);
        const supportsModel = row.supports_model_pin !== false;
        const perModel = row.model_efforts || {};
        let efforts = [];
        if (row.supports_effort !== false) {
            if (model && Object.prototype.hasOwnProperty.call(perModel, model)) efforts = perModel[model] || [];
            else efforts = row.efforts || [];
        }
        return { models, efforts: efforts.slice(), supportsModel, supportsEffort: efforts.length > 0 };
    }

    /** Client-side pin check mirroring the server rule; '' when it looks fine. */
    function pinProblem(row, agent, model, effort) {
        if (!agent || agent === 'none') return (model || effort) ? 'Choose a harness to pin a model or effort.' : '';
        if (!row) return `Unknown agent ${agent}.`;
        const opts = pinOptions(row, model);
        if (model && !opts.supportsModel) return `${row.label || agent} does not take a model pin.`;
        if (effort && !opts.supportsEffort) return `${row.label || agent} has no effort levels for this model.`;
        if (effort && opts.efforts.indexOf(effort) < 0) return `Effort ${effort} is not offered for this model.`;
        return '';
    }

    function profileSummary(profile) {
        if (!profile) return 'missing profile';
        const agent = profile.agent && profile.agent !== 'none' ? profile.agent : 'any harness';
        const parts = [agent];
        if (profile.model) parts.push(profile.model);
        if (profile.effort) parts.push(profile.effort);
        return parts.join(' · ');
    }

    /** profile id → [playbook names] that reference it. */
    function profileUsage(playbooks) {
        const usage = {};
        (playbooks || []).forEach((pb) => {
            (pb.tiers || []).forEach((tier) => {
                (tier.profiles || []).forEach((pid) => {
                    usage[pid] = usage[pid] || [];
                    const label = pb.name || pb.id;
                    if (usage[pid].indexOf(label) < 0) usage[pid].push(label);
                });
            });
        });
        return usage;
    }

    function emptyProfile() {
        return { id: '', name: '', avatar: '', agent: 'none', model: '', effort: '' };
    }

    // ── Avatars ───────────────────────────────────────────────────────────

    const EMOJI_GROUPS = [
        ['Roles', ['🤖', '🧠', '🦉', '🐜', '🐝', '🦊', '🐙', '🦑', '🐺', '🦅', '🐢', '🐇', '🦄', '🐉', '🦁', '🐻', '🐼', '🐧', '🦖', '🐳']],
        ['Work', ['🛠️', '🔧', '🔨', '⚙️', '🧰', '🧪', '🔬', '🔭', '📐', '🧭', '🗺️', '📦', '🚀', '🛰️', '⚡', '🔥', '💡', '🧩', '🎯', '🏗️']],
        ['People', ['🧑‍💻', '👩‍💻', '👨‍💻', '🧑‍🔬', '🧑‍🏫', '🧑‍⚖️', '🕵️', '🧙', '🥷', '🦸', '👷', '🧑‍🚀', '🧑‍🎨', '🧑‍🍳', '💂', '🤠', '😎', '🤓', '🧐', '🫡']],
        ['Symbols', ['⭐', '🌟', '✨', '💎', '🔮', '🪄', '🎲', '♟️', '🏆', '🥇', '🛡️', '⚔️', '🗝️', '🔒', '📜', '📚', '🧾', '🔍', '⚖️', '🧬']],
        ['Nature', ['🌊', '🌋', '🌙', '☀️', '🌈', '❄️', '🍀', '🌵', '🌲', '🍄', '🌸', '🌻', '🪐', '☄️', '🌪️', '🍋', '🍉', '🍒', '🌶️', '🥑']],
    ];

    /** 'mascot' | 'image' | 'emoji' — mirrors the chat bubble renderer. */
    function avatarKind(avatar) {
        const a = String(avatar || '').trim();
        if (!a || a.toLowerCase() === 'cuttle') return 'mascot';
        if (/^https?:\/\//i.test(a) || (a.charAt(0) === '/' && a.charAt(1) !== '/')) return 'image';
        return 'emoji';
    }

    /** Same sources the chat bubble accepts: http(s) or Cuttle's /output/. */
    function safeImageSrc(src) {
        const u = String(src || '').trim();
        if (/^(javascript|data|vbscript):/i.test(u)) return false;
        return /^https?:\/\//i.test(u) || u.startsWith('/output/');
    }

    function avatarHtml(avatar, className) {
        const cls = className ? ' ' + className : '';
        const kind = avatarKind(avatar);
        if (kind === 'image' && safeImageSrc(avatar)) {
            return `<img class="orch-av-img${cls}" src="${esc(String(avatar).trim())}" alt="" referrerpolicy="no-referrer">`;
        }
        if (kind === 'emoji') return `<span class="orch-av-emoji${cls}" aria-hidden="true">${esc(String(avatar).trim())}</span>`;
        return `<img class="orch-av-img is-mascot${cls}" src="/img/cuttle-mascot.png" alt="">`;
    }

    // ── Agent pins (harness · model · effort) ─────────────────────────────

    const DEFAULT_PIN = '__cli_default__';

    function settingItems(list) {
        return (list || []).map((item) => {
            if (item == null) return null;
            if (typeof item === 'string' || typeof item === 'number') return { modelId: String(item), label: String(item) };
            const v = item.id || item.modelId || item.value;
            return v == null || v === '' ? null : { modelId: String(v), label: String(item.label || v) };
        }).filter(Boolean);
    }

    /** Effort levels offered for a harness row + model ([] when it has no effort flag). */
    function effortLevels(row, model) {
        return pinOptions(row, model).efforts;
    }

    /** "Claude Code · claude-opus-5-5 · high" (or "Any harness"). */
    function pinLabel(catalog, profile) {
        const p = profile || {};
        if (!p.agent || p.agent === 'none') return 'Any harness';
        const row = catalogRow(catalog, p.agent);
        return [row ? (row.label || p.agent) : p.agent, p.model || 'default model', p.effort].filter(Boolean).join(' · ');
    }

    function agentIcon(catalog, agentId) {
        const row = catalogRow(catalog, agentId);
        return (row && row.icon) || (agentId && agentId !== 'none' ? '◆' : '✦');
    }

    const ROLE_META = {
        worker: { icon: '⚒', hint: 'Every task starts here. Cheap and fast.' },
        escalation: { icon: '⤴', hint: 'Takes over when a worker task fails after a retry.' },
        expert: { icon: '◈', hint: 'Plans and reviews. Never grinds.' },
    };

    // ── Playbooks ─────────────────────────────────────────────────────────

    function newTier(existing) {
        const ids = new Set((existing || []).map((t) => t.id));
        let n = (existing || []).length + 1;
        while (ids.has(`tier-${n}`)) n += 1;
        return { id: `tier-${n}`, label: `Tier ${n}`, role: 'worker', profiles: [], use_for: '' };
    }

    function newPlaybook(id) {
        return {
            schema: 1, id: slug(id) || 'new-playbook', name: 'New playbook', description: '',
            tiers: [{ id: 'worker', label: 'Worker', role: 'worker', profiles: [], use_for: '' }],
            limits: { solo_attempts: 2, solo_minutes: 40, child_retries: 1, setup_retries: 2 },
            supervisor: { every_sec: 900, agent: '', instructions: '', require_branch: '', require_path: '', forbid_branches: [] },
            notes: '',
        };
    }

    function moveItem(list, index, delta) {
        const out = (list || []).slice();
        const to = index + delta;
        if (index < 0 || index >= out.length || to < 0 || to >= out.length) return out;
        const [item] = out.splice(index, 1);
        out.splice(to, 0, item);
        return out;
    }

    function addProfileToTier(tier, profileId) {
        const out = clone(tier);
        if (profileId && out.profiles.indexOf(profileId) < 0) out.profiles.push(profileId);
        return out;
    }

    function removeProfileFromTier(tier, profileId) {
        const out = clone(tier);
        out.profiles = out.profiles.filter((p) => p !== profileId);
        return out;
    }

    function parseBranches(text) {
        return String(text || '').split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
    }

    /** Problems the server would reject; empty array when saveable. */
    function playbookProblems(pb, knownProfileIds) {
        const problems = [];
        const known = new Set(knownProfileIds || []);
        if (!validId(pb && pb.id)) problems.push('Playbook id must start with a letter and use a-z 0-9 _ - (max 40).');
        const tiers = (pb && pb.tiers) || [];
        if (!tiers.length) problems.push('Add at least one tier.');
        if (!tiers.some((t) => t.role === 'worker')) problems.push('Add a worker tier — every task starts there.');
        const seen = new Set();
        tiers.forEach((tier, i) => {
            const name = tier.label || tier.id || `Tier ${i + 1}`;
            if (!validId(tier.id) || seen.has(tier.id)) problems.push(`${name}: tier id is invalid or duplicated.`);
            seen.add(tier.id);
            if (ROLES.indexOf(tier.role) < 0) problems.push(`${name}: choose a role.`);
            if (!(tier.profiles || []).length) problems.push(`${name}: add at least one agent profile.`);
            (tier.profiles || []).forEach((pid) => {
                if (known.size && !known.has(pid)) problems.push(`${name}: profile "${pid}" does not exist.`);
            });
        });
        LIMIT_FIELDS.forEach(([key, label, low, high]) => {
            const v = Number(((pb && pb.limits) || {})[key]);
            if (!Number.isInteger(v) || v < low || v > high) problems.push(`${label} must be ${low}–${high}.`);
        });
        const every = Number(((pb && pb.supervisor) || {}).every_sec);
        if (!Number.isInteger(every) || every < 60 || every > 86400) problems.push('Supervisor interval must be 60–86400 seconds.');
        return problems;
    }

    function formatTime(ts) {
        const n = Number(ts);
        if (!ts || !Number.isFinite(n)) return '—';
        return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
            .format(new Date(n * 1000));
    }

    const api = {
        ROLES, ROLE_LABELS, LIMIT_FIELDS,
        esc, slug, validId, clone,
        catalogRow, modelIds, pinOptions, pinProblem, profileSummary, profileUsage, emptyProfile,
        EMOJI_GROUPS, avatarKind, safeImageSrc, avatarHtml,
        DEFAULT_PIN, settingItems, effortLevels, pinLabel, agentIcon, ROLE_META,
        newTier, newPlaybook, moveItem, addProfileToTier, removeProfileFromTier, parseBranches,
        playbookProblems, formatTime,
    };
    root.CuttleOrchestrator = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
