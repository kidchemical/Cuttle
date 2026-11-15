/* Settings → Voice → Speech to text (api.voice_stt.settings, key `voice_stt`).
   `CuttleSettingsVoiceStt.mount(container)` reads /api/settings/voice-stt and
   builds the form: which provider turns recorded speech into text (composer
   voice clips and voice chat's server transcription), its model, whether the
   words are typed into the composer while recording, and the longest clip.
   Loading never saves. */
(function (root) {
    'use strict';

    const HINTS = {
        openai: 'Audio is sent to OpenAI for transcription. Uses the OpenAI API key under Providers.',
        elevenlabs: 'Audio is sent to ElevenLabs (Scribe) for transcription. Uses the ElevenLabs API key under Providers.',
        local: 'Audio goes only to your own server: any OpenAI-compatible speech-to-text endpoint (whisper.cpp server, faster-whisper-server / Speaches, LocalAI). Cuttle does not start or install it.',
        browser: 'Uses the speech recognizer built into your browser: no API key and nothing passes through this server. Chrome and Edge send the audio to their own cloud service; Safari uses Apple’s. Firefox, the Cuttle desktop app and the Android app have none, so clips there are attached without text. Voice chat keeps using its own browser listening.',
    };

    let container = null;
    let generation = 0;

    function el(tag, text, cls) {
        const n = document.createElement(tag);
        if (text) n.textContent = text;
        if (cls) n.className = cls;
        return n;
    }

    function row(label, description, control) {
        const item = el('div', '', 'setting-item');
        const lab = el('div', '', 'setting-label');
        lab.appendChild(el('strong', label));
        if (description) lab.appendChild(el('div', description, 'setting-description'));
        const ctl = el('div', '', 'setting-control setting-control--stack');
        ctl.append(...[].concat(control));
        item.append(lab, ctl);
        return item;
    }

    function notify(message, kind) {
        (root.showToast || root.showNotification || function () {})(message, kind);
    }

    function build(data) {
        const prefs = data.voice_stt || {};
        const providers = data.providers || [];
        const models = Object.assign({}, prefs.models || {});
        const range = data.clip_minutes_range || [1, 30];
        let current = '';

        const provider = el('select', '', 'setting-input');
        provider.setAttribute('aria-label', 'Speech-to-text provider');
        for (const p of providers) {
            const o = el('option', p.label + (p.configured ? '' : ' (no API key)'));
            o.value = p.id;
            provider.appendChild(o);
        }
        provider.value = prefs.provider || 'openai';
        const hint = el('div', '', 'setting-description');
        hint.setAttribute('role', 'status');

        const model = el('select', '', 'setting-input');
        model.setAttribute('aria-label', 'Speech-to-text model');
        const custom = el('input', '', 'setting-input');
        custom.type = 'text';
        custom.placeholder = 'Model ID';
        custom.hidden = true;
        custom.setAttribute('aria-label', 'Custom speech-to-text model ID');
        const modelStatus = el('div', '', 'setting-description');
        modelStatus.setAttribute('role', 'status');
        const picker = root.CuttleProviderPicker.create({ document, fetch: root.fetch.bind(root),
            select: model, custom, status: modelStatus, operation: 'audio_transcription',
            provider: () => (provider.value === 'browser' ? '' : provider.value) });
        const modelRow = row('Model', 'Provider default is the provider’s recommended transcription model.', [model, custom, modelStatus]);

        const address = el('input', '', 'setting-input');
        address.type = 'url';
        address.placeholder = 'http://127.0.0.1:8000/v1';
        address.value = prefs.local_base_url || '';
        address.setAttribute('aria-label', 'Local speech-to-text server address');
        const addressRow = row('Local server address', 'Base URL ending in /v1. Leave empty to use the local model server Cuttle already knows.', address);

        const live = el('label', '', 'toggle-switch' + (prefs.live_text !== false ? ' active' : ''));
        const liveInput = el('input');
        liveInput.type = 'checkbox';
        liveInput.checked = prefs.live_text !== false;
        liveInput.style.display = 'none';
        live.setAttribute('role', 'switch');
        live.tabIndex = 0;
        live.setAttribute('aria-label', 'Type what I say into the message');
        const paintLive = () => {
            live.classList.toggle('active', liveInput.checked);
            live.setAttribute('aria-checked', String(liveInput.checked));
        };
        const flip = (ev) => { ev.preventDefault(); liveInput.checked = !liveInput.checked; paintLive(); };
        live.addEventListener('click', flip);
        live.addEventListener('keydown', (ev) => { if (ev.key === ' ' || ev.key === 'Enter') flip(ev); });
        live.append(liveInput, el('span', '', 'slider'));
        paintLive();

        const minutes = el('input', '', 'setting-input');
        minutes.type = 'number';
        minutes.min = String(range[0]);
        minutes.max = String(range[1]);
        minutes.step = '1';
        minutes.value = String(prefs.max_clip_minutes == null ? 10 : prefs.max_clip_minutes);
        minutes.style.width = '6em';
        minutes.setAttribute('aria-label', 'Longest voice clip in minutes');

        function remember() {
            if (current && current !== 'browser') {
                if (picker.value()) models[current] = picker.value();
                else delete models[current];
            }
        }
        function changeProvider() {
            remember();
            current = provider.value;
            const p = providers.find((x) => x.id === current) || {};
            hint.textContent = (HINTS[current] || '') + (p.configured === false ? ' Add its API key in Settings → Providers first.' : '');
            modelRow.hidden = current === 'browser';
            addressRow.hidden = current !== 'local';
            if (current !== 'browser') picker.set(p, models[current] || '');
        }
        provider.addEventListener('change', changeProvider);

        const save = el('button', 'Save speech to text', 'control-button btn-start');
        save.type = 'button';
        save.addEventListener('click', async () => {
            if (current !== 'browser' && !picker.valid()) { notify('Enter a custom model ID or choose Provider default.', 'error'); return; }
            remember();
            save.disabled = true;
            try {
                const response = await root.fetch('/api/settings/voice-stt', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ voice_stt: { provider: provider.value, models, local_base_url: address.value.trim(),
                        live_text: liveInput.checked, max_clip_minutes: Number(minutes.value) } }) });
                const saved = await response.json();
                if (!response.ok || !saved.success) throw new Error(saved.error || 'Save failed');
                notify('Speech to text saved', 'success');
            } catch (error) { notify(error.message, 'error'); }
            finally { save.disabled = false; }
        });

        container.replaceChildren(
            row('Provider', 'Used for composer voice clips and for voice chat’s server transcription.', [provider, hint]),
            modelRow, addressRow,
            row('Type what I say into the message', 'While you record a voice clip, your words appear in the message box so you can fix them before sending; removing the clip removes its words. Off: the clip is transcribed for the agent when you send, and you only see the audio.', live),
            row('Longest voice clip', 'Minutes (' + range[0] + '–' + range[1] + '). A recording that reaches this is stopped and attached. The Android app’s microphone records uncompressed audio and stops at 12 minutes.', minutes),
            row('', '', save));
        changeProvider();
    }

    async function mount(target) {
        if (target) container = target;
        if (!container) return;
        const mine = ++generation;
        try {
            const response = await root.fetch('/api/settings/voice-stt', { cache: 'no-store' });
            const data = await response.json();
            if (mine !== generation) return;
            if (!response.ok || !data.success) throw new Error(data.error || 'Could not load speech-to-text settings');
            build(data);
        } catch (error) {
            if (mine === generation) container.replaceChildren(el('p', error.message, 'setting-description'));
        }
    }

    root.CuttleSettingsVoiceStt = { mount, HINTS };
})(typeof window !== 'undefined' ? window : globalThis);
