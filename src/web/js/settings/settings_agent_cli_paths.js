/* Host executable/wrapper path control for an agent's settings detail. */
(function (root) {
    'use strict';

    function create(agent, descriptor, onSaved) {
        if (!descriptor) return null;
        const section = document.createElement('form');
        section.className = 'agent-cli-path';
        const label = document.createElement('label');
        label.textContent = 'CLI executable or wrapper';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'setting-input';
        input.value = descriptor.path || '';
        input.placeholder = 'Automatic discovery';
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.setAttribute('aria-label', (agent.label || agent.id) + ' CLI executable or wrapper');
        label.appendChild(input);
        section.appendChild(label);

        const hint = document.createElement('div');
        hint.className = 'setting-description';
        hint.textContent = 'Absolute path on the Cuttle host. Arguments belong in your wrapper. '
            + 'Blank uses ' + descriptor.env_var + ', then automatic discovery. '
            + 'Applies to new launches; running agents keep their current executable.';
        section.appendChild(hint);
        if (descriptor.env_path) {
            const env = document.createElement('div');
            env.className = 'setting-description agent-cli-path-env';
            env.textContent = descriptor.env_var + ': ' + descriptor.env_path;
            section.appendChild(env);
        }

        const actions = document.createElement('div');
        actions.className = 'agent-config-actions';
        const save = document.createElement('button');
        save.type = 'submit';
        save.className = 'control-button';
        save.textContent = 'Save path';
        const reset = document.createElement('button');
        reset.type = 'button';
        reset.className = 'control-button';
        reset.textContent = 'Use default';
        actions.append(save, reset);
        section.appendChild(actions);
        const status = document.createElement('div');
        status.className = 'setting-description';
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        status.textContent = descriptor.error || '';
        section.appendChild(status);

        async function persist(path) {
            save.disabled = reset.disabled = input.disabled = true;
            status.textContent = 'Saving…';
            try {
                const response = await fetch('/api/settings/agent-adapters', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ cli_paths: { [agent.id]: path } }),
                });
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'Save failed');
                descriptor = data.cli_paths[agent.id];
                input.value = descriptor.path || '';
                status.textContent = (path ? 'Path saved for new launches.' : 'Default discovery restored.')
                    + ' Refresh Settings to recheck availability.';
                if (onSaved) onSaved(data);
            } catch (error) {
                status.textContent = 'Could not save path: ' + (error.message || error);
            } finally {
                save.disabled = reset.disabled = input.disabled = false;
            }
        }
        section.addEventListener('submit', event => {
            event.preventDefault();
            persist(input.value.trim());
        });
        reset.addEventListener('click', () => persist(''));
        return section;
    }

    root.CuttleSettingsAgentCliPaths = { create };
})(typeof window !== 'undefined' ? window : globalThis);
