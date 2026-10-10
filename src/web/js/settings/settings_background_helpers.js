/* Background text selection; execution and capability validation belong to the server.

   Two explicit layers, no hidden chain:
   - the Default chain (the "Default — all tasks" profile) every task follows;
   - a per-task chain that overrides the Default until you Reset to Default. */
(function (root) {
    'use strict';
    function create({document: doc, fetch, notify, endpoint}) {
        const el = id => doc.getElementById(id);
        let targets = [], tasks = {}, preferences = {chain: [], tasks: {}}, profile = '', generation = 0, saving = false;
        const states = [];
        let legacy = {};
        const option = (select, value, label) => { const n = doc.createElement('option'); n.value = value; n.textContent = label; select.appendChild(n); };
        const node = (tag, text) => { const n = doc.createElement(tag); if (text) n.textContent = text; return n; };
        const isDefaultProfile = () => !profile;
        const taskOverride = () => (profile && Array.isArray(preferences.tasks[profile])) ? preferences.tasks[profile] : [];
        // Editor is live for the Default profile, or for a task with a custom chain.
        const editorActive = () => isDefaultProfile() || !!el('backgroundOverride').checked;
        function chain() { return isDefaultProfile() ? preferences.chain : taskOverride(); }
        function setChain(value) {
            if (isDefaultProfile()) preferences.chain = value;
            else if (value.length) preferences.tasks[profile] = value;
            else delete preferences.tasks[profile];
        }
        function remember() {
            if (!editorActive()) return;
            setChain(states.map(s => s.value()));
        }
        function summarize(list) {
            if (!list || !list.length) return 'the Providers settings (no Default chain set)';
            return list.map(t => (t.kind === 'cli' ? 'CLI · ' : t.kind === 'local' ? 'Local · ' : 'API · ') + t.target + (t.model ? ' (' + t.model + ')' : '')).join(' → ');
        }
        function switchProfile() { remember(); profile = el('backgroundTask').value; render(); }
        function render() {
            generation++;
            states.length = 0;
            const taskMode = !isDefaultProfile();
            const overrideRow = el('backgroundOverrideRow');
            if (overrideRow) overrideRow.hidden = !taskMode;
            if (taskMode) el('backgroundOverride').checked = taskOverride().length > 0;
            const hasOverride = taskMode && el('backgroundOverride').checked;
            const active = editorActive();

            el('backgroundChain').replaceChildren();
            el('backgroundAdd').disabled = !active;
            const reset = el('backgroundReset');
            if (reset) reset.hidden = !hasOverride;

            if (!taskMode) {
                el('backgroundInheritance').textContent =
                    'Default chain — every task follows this unless it sets its own.';
            } else if (hasOverride) {
                el('backgroundInheritance').textContent =
                    'This task uses its own chain. Reset to Default to follow the Default again.';
            } else {
                el('backgroundInheritance').textContent =
                    'This task follows the Default: ' + summarize(preferences.chain) + '.';
            }

            if (active) chain().forEach((saved, index) => paintRow(saved, index));
            if (!taskMode) {
                el('backgroundStatus').textContent = preferences.chain.length
                    ? 'Targets run in this order. Only listed fallbacks may be used.'
                    : 'No Default chain yet. Leave it empty to use the Providers settings (API order: '
                        + ((legacy.order || []).join(' → ') || 'none')
                        + '), or add a target to set a default for every task.';
            } else if (hasOverride) {
                el('backgroundStatus').textContent = 'Targets run in this order. Only listed fallbacks may be used.';
            } else {
                el('backgroundStatus').textContent = '';
            }
            updateVoiceNotice();
        }
        function updateVoiceNotice() {
            const notice = el('chatTtsBackgroundNotice');
            if (notice) {
                notice.hidden = !((preferences.tasks.narration || []).length || preferences.chain.length);
                const legacyNarrator = el('voiceDetails');
                if (legacyNarrator) legacyNarrator.hidden = !notice.hidden;
            }
        }
        function paintRow(saved, index) {
            const card = node('div'); card.className = 'background-executor';
            const controls = node('div'); controls.className = 'provider-picker';
            const target = node('select'), model = node('select'), custom = node('input'), effort = node('select'), auth = node('select'), temperature = node('input');
            [target, model, custom, effort, auth, temperature].forEach(n => n.className = 'setting-input');
            target.setAttribute('aria-label', 'Background executor ' + (index + 1));
            model.setAttribute('aria-label', 'Background model ' + (index + 1));
            custom.placeholder = 'Custom model ID'; custom.setAttribute('aria-label', 'Custom background model ' + (index + 1));
            temperature.type = 'number'; temperature.step = '.05'; temperature.placeholder = 'Task default';
            temperature.setAttribute('aria-label', 'Background temperature ' + (index + 1));
            targets.forEach(t => option(target, t.key, (t.kind === 'cli' ? 'Agent CLI · ' : t.kind === 'local' ? 'Local model · ' : 'API · ') + t.label + (t.configured ? '' : ' (not configured)')));
            const key = saved.kind + ':' + saved.target;
            if (!targets.some(t => t.key === key)) option(target, key, key + ' (unavailable)');
            target.value = key;
            const refresh = node('button', 'Refresh models'); refresh.type = 'button'; refresh.className = 'control-button';
            const remove = node('button', 'Remove'); remove.type = 'button'; remove.className = 'control-button';
            const up = node('button', 'Move up'); up.type = 'button'; up.disabled = index === 0; up.className = 'control-button';
            const status = node('div'), explanation = node('div'); status.className = explanation.className = 'setting-description'; status.setAttribute('role', 'status');
            const tuning = node('div'); tuning.className = 'provider-picker';
            const label = (text, input) => { const n = node('label', text + ' '); n.appendChild(input); return n; };
            tuning.appendChild(label('Authentication', auth)); tuning.appendChild(label('Reasoning effort', effort)); tuning.appendChild(label('Temperature', temperature));
            controls.append(target, model, custom, refresh, up, remove);
            card.append(controls, tuning, status, explanation); el('backgroundChain').appendChild(card);
            if (root.CuttleSearchableSelect) {
                root.CuttleSearchableSelect.enhance(target, {
                    label: target.getAttribute('aria-label') || 'Executor' });
                root.CuttleSearchableSelect.enhance(model, {
                    label: model.getAttribute('aria-label') || 'Model' });
            }
            let rows = [], defaultEfforts = [], ticket = 0, draft = {...saved};
            const current = () => targets.find(t => t.key === target.value);
            const valueModel = () => model.value === '__custom__' ? custom.value.trim() : model.value;
            function value() {
                const t = current();
                return {kind: t ? (t.kind === 'cli' ? 'cli' : 'api') : saved.kind, target: t ? t.target : saved.target,
                    model: valueModel(), effort: effort.value || '', auth: auth.value || '',
                    temperature: temperature.disabled || temperature.value === '' ? null : Number(temperature.value)};
            }
            states.push({value, valid: () => model.value !== '__custom__' || !!custom.value.trim()});
            function tunables(wantedEffort) {
                const t = current();
                const effective = valueModel() || (t && (t.effective_model || t.default_model)) || '';
                const row = rows.find(r => r.id === effective), caps = row && row.capabilities || {};
                const selected = wantedEffort === undefined ? effort.value : wantedEffort;
                effort.replaceChildren(); option(effort, '', 'Automatic');
                const levels = effective ? (caps.reasoning_efforts || []) : defaultEfforts;
                levels.forEach(e => option(effort, e, e));
                if (selected && !levels.includes(selected)) option(effort, selected, selected + ' (unverified; refresh models)');
                effort.value = selected || ''; effort.disabled = !levels.length && !selected;
                const control = caps.temperature_control || {};
                let supported = control.supported === true;
                let reason = control.reason || 'Temperature support has not been verified for this model.';
                if ((control.required_efforts || []).length) {
                    const actual = effort.value || ['none', 'minimal', 'low'].find(v => levels.includes(v));
                    supported = control.required_efforts.includes(actual);
                    if (!supported) reason = 'Temperature requires effort: ' + control.required_efforts.join(', ');
                }
                if (control.disabled_while_thinking && (control.thinking_required || ['xhigh','max'].includes(effort.value))) {
                    supported = false; reason = 'Temperature is unavailable with adaptive thinking.';
                }
                temperature.disabled = !supported; temperature.min = control.min === undefined ? 0 : control.min; temperature.max = control.max === undefined ? 2 : control.max;
                if (t && t.kind === 'cli') {
                    explanation.textContent = 'Uses the saved CLI login and its allowance. Login: ' + t.auth_command + '. Temperature is unavailable in this CLI. Helpers use isolated text requests with coding tools disabled.';
                } else explanation.textContent = supported ? 'Temperature: ' + temperature.min + '–' + temperature.max + '. Blank uses the task default.' : reason;
                if (t && t.model_from === 'env' && !valueModel()) explanation.textContent += ' Model environment override: ' + t.effective_model + '.';
            }
            function paint(wanted) {
                model.replaceChildren(); const t = current();
                option(model, '', (t && t.kind === 'cli' ? 'CLI default' : 'Provider default') + (t && t.effective_model ? ' — ' + t.effective_model : ''));
                const allowed = rows.filter(r => r.compatibility !== 'unsupported');
                allowed.forEach(r => option(model, r.id, (r.label || r.id) + (r.availability !== 'available' ? ' (access unverified)' : '')));
                if (wanted && !allowed.some(r => r.id === wanted)) option(model, wanted, wanted + ' (saved/custom)');
                option(model, '__custom__', 'Custom model ID…'); model.value = wanted || ''; custom.value = wanted || ''; custom.hidden = true;
                tunables(draft.effort || '');
            }
            async function discover(force) {
                const t = current(); if (!t) return;
                const mine = ++ticket, parent = generation, wanted = valueModel(), wasCustom = model.value === '__custom__';
                draft.effort = effort.value; refresh.disabled = true; status.textContent = 'Loading models…';
                try {
                    const kind = t.kind === 'cli' ? 'cli' : 'api';
                    const response = await fetch(endpoint + '/' + kind + '/' + encodeURIComponent(t.target) + '/models' + (force ? '?refresh=1' : ''));
                    const data = await response.json();
                    if (mine !== ticket || parent !== generation) return;
                    if (!response.ok || !data.success) throw new Error(data.error || 'Model discovery failed');
                    const latestModel = valueModel(), latestCustom = model.value === '__custom__';
                    draft.effort = effort.value;
                    rows = data.models || []; defaultEfforts = data.default_efforts || [];
                    paint(latestModel);
                    if (latestCustom) { model.value = '__custom__'; custom.hidden = false; custom.value = latestModel; tunables(); }
                    status.textContent = (t.kind === 'cli' ? 'CLI model catalog; plan access is checked when running. ' : data.source === 'provider_api' ? 'Account model list. ' : 'Suggestions or cached models; access is unverified. ') + (data.message || '');
                } catch (error) { if (mine === ticket && parent === generation) status.textContent = error.message + '. Your selection is preserved.'; }
                finally { if (mine === ticket && parent === generation) refresh.disabled = false; }
            }
            function chooseTarget() {
                ++ticket; const t = current();
                rows = ((t && t.suggested_models) || []).map(id => ({id})); defaultEfforts = [];
                draft.effort = ''; temperature.value = ''; auth.replaceChildren();
                if (t && t.kind === 'cli') (t.auth_modes || []).forEach(mode => option(auth, mode, mode === 'subscription' ? 'Subscription login' : 'CLI saved login (may use API billing)'));
                auth.hidden = !(t && t.kind === 'cli');
                auth.parentElement.hidden = auth.hidden;
                auth.value = 'subscription'; paint(''); discover(false);
            }
            target.addEventListener('change', chooseTarget);
            model.addEventListener('change', () => { custom.hidden = model.value !== '__custom__'; if (!custom.hidden) { custom.value = ''; custom.focus(); } draft.effort = ''; tunables(''); });
            custom.addEventListener('input', () => tunables()); effort.addEventListener('change', () => tunables());
            refresh.addEventListener('click', () => discover(true));
            remove.addEventListener('click', () => { remember(); const value = chain().slice(); value.splice(index, 1); setChain(value); render(); });
            up.addEventListener('click', () => { remember(); const value = chain().slice(); [value[index-1], value[index]] = [value[index],value[index-1]]; setChain(value); render(); });
            const t = current();
            auth.hidden = !(t && t.kind === 'cli');
            auth.parentElement.hidden = auth.hidden;
            if (t && t.kind === 'cli') (t.auth_modes || []).forEach(mode => option(auth, mode, mode === 'subscription' ? 'Subscription login' : 'CLI saved login (may use API billing)'));
            auth.value = saved.auth || 'subscription'; temperature.value = saved.temperature == null ? '' : saved.temperature;
            rows = ((t && t.suggested_models) || []).map(id => ({id})); paint(saved.model || ''); discover(false);
        }
        function defaultTarget() { const t = targets.find(t => t.configured) || targets[0]; return {kind: t.kind === 'cli' ? 'cli' : 'api', target: t.target, model: '', effort: '', temperature: null, auth: t.kind === 'cli' ? 'subscription' : ''}; }
        el('backgroundTask').addEventListener('change', switchProfile);
        el('backgroundOverride').addEventListener('change', () => {
            if (isDefaultProfile()) return;
            if (el('backgroundOverride').checked) {
                // New override starts from the Default (or one empty target) so
                // switching it on is useful immediately.
                setChain(preferences.chain.length ? preferences.chain.map(s => ({...s})) : [defaultTarget()]);
            } else {
                setChain([]);
            }
            render();
        });
        const resetBtn = el('backgroundReset');
        if (resetBtn) resetBtn.addEventListener('click', () => {
            if (isDefaultProfile()) return;
            delete preferences.tasks[profile];
            render();
        });
        el('backgroundAdd').addEventListener('click', () => { remember(); if (chain().length >= 8) { notify('At most eight fallback targets.', 'error'); return; } setChain([...chain(), defaultTarget()]); render(); });
        function accept(data) {
            targets = data.targets || []; tasks = data.tasks || {}; preferences = data.preferences || {}; legacy = data.legacy || {};
            preferences.chain ||= []; preferences.tasks ||= {};
            el('backgroundTask').replaceChildren(); option(el('backgroundTask'), '', 'Default — all tasks');
            Object.entries(tasks).forEach(([id, label]) => option(el('backgroundTask'), id, label)); el('backgroundTask').value = profile;
            profile = el('backgroundTask').value;
            render();
        }
        async function load() {
            try { const response = await fetch(endpoint), data = await response.json(); if (!response.ok || !data.success) throw new Error(data.error || 'Unable to load helpers'); accept(data); }
            catch (error) { el('backgroundStatus').textContent = error.message; }
        }
        async function save() {
            if (saving) return;
            if (states.some(s => !s.valid())) { notify('Enter a custom model ID or choose the default model.', 'error'); return; }
            remember();
            if ([preferences.chain, ...Object.values(preferences.tasks)].some(c => c.some(t => !t.target || (t.temperature !== null && !Number.isFinite(t.temperature))))) { notify('Choose valid executors and temperatures.', 'error'); return; }
            saving = true;
            const body = JSON.stringify(preferences);
            try {
                const response = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, body}), data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'Save failed');
                remember(); if (JSON.stringify(preferences) === body) accept(data);
                notify('Background helper settings saved.', 'success');
            } catch (error) { notify(error.message, 'error'); }
            finally { saving = false; }
        }
        return {load, save};
    }
    root.CuttleBackgroundHelpers = {create};
    if (typeof module !== 'undefined' && module.exports) module.exports = root.CuttleBackgroundHelpers;
})(typeof window !== 'undefined' ? window : globalThis);
