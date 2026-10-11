/* Shared operation-aware model picker. Catalog IDs and capabilities are server data. */
(function (root) {
    'use strict';
    const CUSTOM = '__custom_model__';
    function option(doc, select, value, label) {
        const node = doc.createElement('option'); node.value = value; node.textContent = label; select.appendChild(node);
    }
    function paintEffort(doc, select, caps, saved) {
        if (!select) return;
        select.replaceChildren(); option(doc, select, '', 'Automatic');
        const levels = (caps && caps.reasoning_efforts) || [];
        levels.forEach(e => option(doc, select, e, e));
        if (saved && !levels.includes(saved)) option(doc, select, saved, saved + ' (no longer supported — clear to save)');
        select.value = saved || ''; select.disabled = !levels.length && !saved;
        select.title = levels.length ? 'Effort for this provider and model.' : 'This model does not advertise named effort levels.';
    }
    function create({document: doc, fetch, select, custom, effort, status, operation, provider, changed}) {
        if (root.CuttleSearchableSelect && select) {
            root.CuttleSearchableSelect.enhance(select, {
                label: (select.getAttribute && select.getAttribute('aria-label')) || 'Model',
                floating: true,
            });
        }
        let rows = [], ticket = 0, current = {}, effortModel = '', efforts = {};
        const value = () => select.value === CUSTOM ? custom.value.trim() : select.value;
        const effective = () => value() || current.effective_model || current.default_model || '';
        function rememberEffort() { if (effort && effortModel) efforts[effortModel] = effort.value; }
        function details() {
            rememberEffort(); effortModel = effective();
            const row = rows.find(r => r.id === effortModel);
            paintEffort(doc, effort, row && row.capabilities, efforts[effortModel]);
        }
        function paint(newRows, wanted) {
            rows = newRows; select.replaceChildren();
            option(doc, select, '', 'Provider default' + ((current.effective_model || current.default_model) ? ' — ' + (current.effective_model || current.default_model) : ''));
            const allowed = rows.filter(r => r.compatibility !== 'unsupported');
            allowed.forEach(r => option(doc, select, r.id, (r.label || r.id) + (r.availability !== 'available' ? ' (access unverified)' : r.compatibility === 'unknown' ? ' (compatibility unknown)' : '')));
            if (wanted && !allowed.some(r => r.id === wanted)) option(doc, select, wanted, wanted + ' (saved/custom)');
            option(doc, select, CUSTOM, 'Custom model ID…'); select.value = wanted || '';
            custom.value = wanted || ''; custom.hidden = true; details();
        }
        function set(row, wanted, savedEfforts) {
            ++ticket; current = row || {}; efforts = savedEfforts || {}; effortModel = '';
            const suggestions = row && row.models ? row.models : ((row && row.suggested_models) || []).map(id => ({id}));
            paint(suggestions.map(r => ({...r, compatibility: 'unknown'})), wanted);
            return discover(false);
        }
        async function discover(force) {
            const id = provider(); if (!id) return;
            const mine = ++ticket; status.textContent = 'Loading models…';
            try {
                const url = '/api/settings/model-providers/' + encodeURIComponent(id) + '/models?operation=' + operation + (force ? '&refresh=1' : '');
                const response = await fetch(url); const data = await response.json();
                if (mine !== ticket || provider() !== id) return;
                if (!response.ok || !data.success) throw new Error(data.error || 'Model discovery failed');
                const wanted = value(), wasCustom = select.value === CUSTOM;
                paint(data.models || [], wanted);
                if (wasCustom) { select.value = CUSTOM; custom.value = wanted; custom.hidden = false; details(); }
                status.textContent = (data.source === 'provider_api' ? 'Models listed by your provider.' : 'Showing suggestions or cached models; account access is unverified.') + (data.message ? ' ' + data.message : '') +
                    (current.model_from === 'env' && operation === 'text_generation' ? ' The background default is overridden by ' + current.model_env + '.' : '');
            } catch (error) {
                if (mine === ticket) status.textContent = error.message + '. Your selection is preserved.';
            }
        }
        select.addEventListener('change', () => {
            custom.hidden = select.value !== CUSTOM;
            if (!custom.hidden) { custom.value = ''; custom.focus(); }
            details(); if (changed) changed();
        });
        custom.addEventListener('input', () => { details(); if (changed) changed(); });
        if (effort) effort.addEventListener('change', rememberEffort);
        return {set, discover, value, effective, efforts: () => { rememberEffort(); return {...efforts}; },
                valid: () => select.value !== CUSTOM || !!custom.value.trim()};
    }
    root.CuttleProviderPicker = {create, paintEffort};
})(typeof window !== 'undefined' ? window : globalThis);
