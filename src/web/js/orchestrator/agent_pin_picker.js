/**
 * CuttleAgentPinPicker — "Runs on" (harness · model · effort) picker for app
 * pages. It is the composer's agent chip modal (CuttleChipConfig.openAgent)
 * fed from the /api/agents catalog, the same adapter shape Spaces uses for
 * group/space overrides. The shared searchable list pages lazily, so long
 * catalogs (OpenCode) stay responsive.
 *
 *   const picker = CuttleAgentPinPicker.create({ getCatalog, onChange });
 *   picker.open({ agent, model, effort });   // onChange(pin) on every edit
 */
(function (root) {
    'use strict';

    const M = root.CuttleOrchestrator;

    function create(opts) {
        const o = opts || {};
        const getCatalog = typeof o.getCatalog === 'function' ? o.getCatalog : () => [];
        const onChange = typeof o.onChange === 'function' ? o.onChange : () => {};
        let pin = { agent: 'none', model: '', effort: '' };
        let ctrl = null;

        function emit() { onChange(Object.assign({}, pin)); }

        function row(id) { return M.catalogRow(getCatalog(), id); }

        function entries() {
            return getCatalog().filter((a) => a && a.id).map((a) => ({
                id: a.id,
                prefix: '/' + a.id + ' ',
                label: (a.label || a.id) + (a.available === false ? ' (not installed)' : ''),
                category: a.id,
            }));
        }

        function reopen() {
            // The modal builds its fields once; a model change can change the
            // effort levels, so re-render it after the change event settles.
            setTimeout(() => { if (ctrl) ctrl.openAgent(pin.agent === 'none' ? '' : pin.agent); }, 0);
        }

        function spec(agentId) {
            const r = row(agentId);
            if (!r) return null;
            const out = {};
            const withDefault = (items) => [{ modelId: M.DEFAULT_PIN, label: 'CLI default' }].concat(items);
            if (r.supports_model_pin !== false) {
                out.models = {
                    build: () => withDefault(M.settingItems(r.models)),
                    load() {},
                    repaint() {},
                    current: () => pin.model || M.DEFAULT_PIN,
                    set(v) {
                        const before = M.effortLevels(r, pin.model).join(',');
                        pin.model = v === M.DEFAULT_PIN ? '' : String(v);
                        const levels = M.effortLevels(r, pin.model);
                        if (pin.effort && levels.indexOf(pin.effort) < 0) pin.effort = '';
                        emit();
                        if (levels.join(',') !== before) reopen();
                    },
                };
            }
            const levels = M.effortLevels(r, pin.model);
            if (levels.length) {
                out.efforts = {
                    build: () => withDefault(levels.map((e) => ({ modelId: e, label: e }))),
                    load() {},
                    repaint() {},
                    current: () => pin.effort || M.DEFAULT_PIN,
                    set(v) { pin.effort = v === M.DEFAULT_PIN ? '' : String(v); emit(); },
                };
            }
            return (out.models || out.efforts) ? out : null;
        }

        function ensure() {
            if (ctrl) return ctrl;
            if (!root.CuttleChipConfig) return null;
            ctrl = root.CuttleChipConfig.create({
                pickerMode: 'defaults',
                escapeHtml: M.esc,
                agentPickerTitle: o.title || 'Runs on',
                agentPickerSubtitle: o.subtitle || 'The harness, model and effort this profile pins. Same catalog as the chat composer.',
                clearChoiceLabel: 'Any harness — no pin',
                getAgentEntries: entries,
                getAgentSpec: spec,
                applyAgent(entry) {
                    pin = { agent: entry ? entry.id : 'none', model: '', effort: '' };
                    emit();
                },
                getProjects: () => [],
                worktreesEnabled: () => false,
            });
            return ctrl;
        }

        return {
            open(current) {
                pin = Object.assign({ agent: 'none', model: '', effort: '' }, current || {});
                if (!pin.agent) pin.agent = 'none';
                const c = ensure();
                if (c) c.openAgent(pin.agent === 'none' ? '' : pin.agent);
            },
            close() { if (ctrl) ctrl.close(); },
        };
    }

    root.CuttleAgentPinPicker = { create };
})(typeof window !== 'undefined' ? window : globalThis);
