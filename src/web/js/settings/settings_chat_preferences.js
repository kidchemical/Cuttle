/* Prompt editors and experimental final-response configuration. */
(function (root) {
    'use strict';
    function create({document: doc, fetch, notify}) {
        let processorContainer = null, processorGeneration = 0;
        const el = (tag, text, cls) => {
            const n = doc.createElement(tag);
            if (text) n.textContent = text;
            if (cls) n.className = cls;
            return n;
        };
        function info(label, description) {
            const button = el('button', 'i', 'cuttle-info');
            button.type = 'button'; button.dataset.tooltip = description;
            button.setAttribute('aria-label', 'About ' + label);
            return button;
        }
        async function request(url, body) {
            const r = await fetch(url, body === undefined ? {cache: 'no-store'} : {
                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
            });
            const data = await r.json();
            if (!r.ok || !data.success) throw new Error(data.error || 'Request failed');
            return data;
        }
        function promptEditor(container, value, labelText, hintText, save) {
            const form = el('form', '', 'chat-prompt-editor');
            const heading = el('div', '', 'setting-label');
            heading.append(el('strong', labelText), info(labelText, hintText));
            const input = el('textarea', '', 'setting-input');
            input.value = value || ''; input.rows = 5; input.maxLength = 32000;
            input.setAttribute('aria-label', labelText);
            form.append(heading, input);
            const button = el('button', 'Save prompt', 'control-button'); button.type = 'submit';
            const status = el('div', '', 'setting-description'); status.setAttribute('role', 'status');
            form.append(button, status);
            form.addEventListener('submit', async event => {
                event.preventDefault(); button.disabled = input.disabled = true;
                status.textContent = 'Saving…';
                try {
                    await save(input.value);
                    status.textContent = 'Saved. Applies to subsequent turns, including existing chats.';
                } catch (error) { status.textContent = 'Could not save prompt: ' + error.message; }
                finally { button.disabled = input.disabled = false; }
            });
            container.appendChild(form);
            return form;
        }
        async function mountGlobal(container) {
            if (!container || container.dataset.mounted) return;
            container.dataset.mounted = 'true';
            try {
                const data = await request('/api/settings/chat-prompts');
                promptEditor(container, data.prompts.global, 'Shared system prompt',
                    'Applies to every chat agent on this server. Agent-specific instructions are added after this prompt. CLI agents receive these alongside their project briefing; native system instructions still apply. Blank clears it.',
                    global => request('/api/settings/chat-prompts', {global}));
            } catch (error) { container.appendChild(el('p', error.message, 'setting-description')); delete container.dataset.mounted; }
        }
        function agentEditor(agent, preferences) {
            if (!preferences) return null;
            const container = el('div');
            promptEditor(container, (preferences.agents || {})[agent.id], (agent.label || agent.id) + ' system prompt',
                'Added after the shared prompt in General; agent-specific preferences take precedence when they conflict. Blank uses only the shared prompt.',
                text => request('/api/settings/chat-prompts', {agents: {[agent.id]: text}}).then(data => {
                    preferences.agents = data.prompts.agents;
                }));
            return container;
        }
        async function mountProcessor(container, refresh = false) {
            if (!container || (container.dataset.mounted && !refresh)) return;
            processorContainer = container;
            const section = doc.getElementById('responsePostprocessingSettings');
            const mine = ++processorGeneration;
            function hide() {
                if (section) section.hidden = true;
                container.replaceChildren();
                delete container.dataset.mounted;
            }
            try {
                const data = await request('/api/settings/response-postprocessing');
                if (mine !== processorGeneration) return;
                if (data.available !== true) { hide(); return; }
                if (section) section.hidden = false;
                if (container.dataset.mounted) return; // Preserve an enabled editor's draft.
                container.dataset.mounted = 'true';
                const form = el('form', '', 'chat-prompt-editor');
                const activeRow = el('div', '', 'setting-item');
                const activeTitle = el('div', '', 'setting-label');
                activeTitle.append(el('strong', 'Process completed replies'), info('post-processing',
                    'Runs an extra text pass after each successful reply. Adds response time and model usage. Failures retain the original; interactive cards are skipped.'));
                const activeLabel = el('label', '', 'toggle-switch');
                const active = el('input'); active.type = 'checkbox'; active.checked = data.preferences.enabled;
                active.style.display = 'none';
                activeLabel.setAttribute('role', 'switch'); activeLabel.tabIndex = 0;
                activeLabel.setAttribute('aria-label', 'Process completed replies');
                const syncToggle = () => {
                    activeLabel.classList.toggle('active', active.checked);
                    activeLabel.setAttribute('aria-checked', String(active.checked));
                };
                activeLabel.addEventListener('click', event => {
                    event.preventDefault(); if (active.disabled) return;
                    active.checked = !active.checked; syncToggle();
                });
                activeLabel.append(active, el('span', '', 'slider')); syncToggle();
                const activeControl = el('div', '', 'setting-control');
                activeControl.appendChild(activeLabel);
                activeRow.append(activeTitle, activeControl);
                const label = el('label', 'Instructions');
                const prompt = el('textarea', '', 'setting-input'); prompt.rows = 4; prompt.maxLength = 32000;
                prompt.required = true; prompt.value = data.preferences.prompt; prompt.setAttribute('aria-label', 'Post-processing instructions'); label.appendChild(prompt);
                const modeLabel = el('label', 'Output '), mode = el('select', '', 'setting-input');
                mode.setAttribute('aria-label', 'Post-processing output');
                [['append', 'Append — collapse the original reply'], ['replace', 'Replace displayed reply']].forEach(([value, text]) => {
                    const option = el('option', text); option.value = value; mode.appendChild(option);
                });
                mode.value = data.preferences.mode; modeLabel.appendChild(mode);
                const executor = el('div', '', 'postprocessing-executors');
                const executorTitle = el('div', '', 'setting-label');
                executorTitle.append(el('strong', 'Executor'), info('post-processing executor',
                    'This chain belongs only to post-processing. Choose an API provider or restricted agent CLI, model, effort and fallback order. An empty chain skips the pass; Background helpers defaults never apply.'));
                const elements = {
                    backgroundChain: el('div'), backgroundAdd: el('button', 'Add executor', 'control-button'),
                    backgroundInheritance: el('div'), backgroundStatus: el('div', '', 'setting-description')
                };
                elements.backgroundAdd.type = 'button';
                elements.backgroundStatus.setAttribute('role', 'status');
                executor.append(executorTitle, elements.backgroundChain, elements.backgroundAdd,
                    elements.backgroundInheritance, elements.backgroundStatus);
                const button = el('button', 'Save post-processing', 'control-button'); button.type = 'submit';
                const status = el('div', '', 'setting-description'); status.setAttribute('role', 'status');
                form.append(activeRow, label, modeLabel, executor, button, status);
                container.appendChild(form);
                const editor = root.CuttleBackgroundHelpers.create({document: doc, fetch, notify,
                    endpoint: '/api/settings/response-postprocessing', elements, standalone: true});
                editor.configure({targets: data.targets || [], preferences: {chain: data.preferences.chain || [], tasks: {}}});
                form.addEventListener('submit', async event => {
                    event.preventDefault();
                    let chain;
                    try { chain = editor.value().chain; }
                    catch (error) { status.textContent = error.message; return; }
                    const controls = [button, active, prompt, mode, ...executor.querySelectorAll('input, select, button')];
                    const disabled = controls.map(n => n.disabled); controls.forEach(n => { n.disabled = true; });
                    try {
                        const saved = await request('/api/settings/response-postprocessing', {enabled: active.checked, prompt: prompt.value, mode: mode.value, chain});
                        if (!container.contains(form)) return;
                        if (saved.available !== true) { hide(); return; }
                        status.textContent = 'Post-processing saved for subsequent replies.';
                        if (notify) notify('Post-processing settings saved.', 'success');
                    } catch (error) { status.textContent = error.message; }
                    finally { controls.forEach((n, i) => { n.disabled = disabled[i]; }); }
                });
            } catch (error) { if (mine === processorGeneration) hide(); }
        }
        function refreshProcessorAvailability() { return mountProcessor(processorContainer, true); }
        return {mountGlobal, agentEditor, mountProcessor, refreshProcessorAvailability};
    }
    root.CuttleSettingsChatPreferences = {create};
    if (typeof module !== 'undefined' && module.exports) module.exports = root.CuttleSettingsChatPreferences;
})(typeof window !== 'undefined' ? window : globalThis);
