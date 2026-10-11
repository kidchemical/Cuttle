/* Voice Settings orchestration over the shared provider/model picker. */
(function (root) {
    'use strict';
    function create({document: doc, fetch, notify, onSaved}) {
        const el = id => doc.getElementById(id);
        let providers = [], narrators = [], drafts = {}, pid = '', nid = '', voiceTicket = 0, saving = false;
        const row = () => providers.find(p => p.id === el('chatTtsProvider').value);
        const narrator = () => narrators.find(p => p.id === el('chatTtsNarratorProvider').value);
        function options(select, items, selected) {
            select.replaceChildren();
            for (const item of items) {
                const option = doc.createElement('option'); option.value = item.id;
                option.textContent = item.label || item.id; select.appendChild(option);
            }
            if (selected && !items.some(v => v.id === selected)) {
                const option = doc.createElement('option'); option.value = selected;
                option.textContent = selected + ' (saved/custom; availability unverified)'; select.appendChild(option);
            }
            if (selected) select.value = selected;
        }
        const speech = root.CuttleProviderPicker.create({document: doc, fetch,
            select: el('chatTtsModel'), custom: el('chatTtsCustomModel'), status: el('chatTtsModelStatus'),
            operation: 'speech_generation', provider: () => el('chatTtsProvider').value,
            changed: () => { rememberSpeech(); voices(false); }});
        const text = root.CuttleProviderPicker.create({document: doc, fetch,
            select: el('chatTtsNarratorModel'), custom: el('chatTtsNarratorCustomModel'), effort: el('chatTtsNarratorEffort'),
            status: el('chatTtsNarratorStatus'), operation: 'text_generation', provider: () => el('chatTtsNarratorProvider').value});
        function rememberSpeech() {
            if (!pid) return;
            (drafts.models ||= {})[pid] = speech.value();
            (drafts.voices ||= {})[pid] = el('chatTtsCustomVoice').value.trim() || el('chatTtsVoice').value;
        }
        function rememberNarrator() {
            if (!nid) return;
            (drafts.summarize_models ||= {})[nid] = text.value();
            (drafts.summarize_efforts ||= {})[nid] = text.efforts();
        }
        function tunables() {
            const p = row();
            el('chatTtsSpeedRow').hidden = !(p && p.speed_range);
            if (p && p.speed_range) {
                const speed = el('chatTtsSpeed'); speed.min = p.speed_range[0]; speed.max = p.speed_range[1];
                speed.value = Math.min(p.speed_range[1], Math.max(p.speed_range[0], Number(drafts.speed || 1)));
                el('chatTtsSpeedValue').textContent = Number(speed.value).toFixed(2) + '×';
            }
            el('chatTtsStabilityRow').hidden = !(p && p.supports_stability);
            el('chatTtsFetchVoicesBtn').hidden = !(p && p.supports_fetch_voices);
        }
        async function changeProvider() {
            rememberSpeech(); pid = el('chatTtsProvider').value; ++voiceTicket;
            const p = row();
            el('chatTtsCustomVoice').value = '';
            options(el('chatTtsVoice'), (p && p.voices) || [], (drafts.voices || {})[pid]);
            tunables();
            const modelLoad = speech.set(p, (drafts.models || {})[pid] || '');
            await Promise.all([modelLoad, voices(false)]);
        }
        async function changeNarrator() {
            rememberNarrator(); nid = el('chatTtsNarratorProvider').value;
            return text.set(narrator(), (drafts.summarize_models || {})[nid] || '', (drafts.summarize_efforts || {})[nid] || {});
        }
        async function voices(force) {
            const id = el('chatTtsProvider').value, model = speech.effective();
            const mine = ++voiceTicket;
            const wanted = el('chatTtsCustomVoice').value.trim() || el('chatTtsVoice').value;
            try {
                const response = await fetch('/api/tts/voices?provider=' + encodeURIComponent(id) + '&model=' + encodeURIComponent(model) + (force ? '&refresh=1' : ''));
                const data = await response.json();
                if (mine !== voiceTicket || id !== el('chatTtsProvider').value || model !== speech.effective()) return;
                if (!response.ok || !data.success) throw new Error(data.error || 'Voice discovery failed');
                // Preserve an edit made while the request was in flight.
                const current = el('chatTtsCustomVoice').value.trim() || el('chatTtsVoice').value;
                options(el('chatTtsVoice'), data.voices || [], current || wanted);
                el('chatTtsProviderStatus').textContent = (data.source === 'provider_api' ? 'Account voices loaded.' :
                    data.source === 'provider_manifest' ? 'Built-in voices for this model.' : 'Showing suggested or cached voices; access is unverified.') + (data.message ? ' ' + data.message : '') +
                    (current && !(data.voices || []).some(v => v.id === current) ? ' The selected voice is not in this model’s list; choose a listed voice or verify your custom ID.' : '');
            } catch (error) {
                if (mine === voiceTicket) el('chatTtsProviderStatus').textContent = error.message + '. Your selection is preserved.';
            }
        }
        async function apply(conf) {
            ++voiceTicket; pid = nid = '';
            drafts = JSON.parse(JSON.stringify(conf || {}));
            if (onSaved) onSaved(drafts);
            for (const key of ['enabled', 'summarize']) {
                const suffix = key === 'enabled' ? 'Enabled' : 'Summarize';
                el('chatTts' + suffix).checked = drafts[key] !== false;
                el('chatTts' + suffix + 'Toggle').classList.toggle('active', drafts[key] !== false);
            }
            el('chatTtsProvider').value = drafts.provider || 'openai';
            el('chatTtsNarratorProvider').value = drafts.summarize_provider || 'openai';
            el('chatTtsSpeed').value = drafts.speed == null ? 1 : drafts.speed;
            el('chatTtsStability').value = drafts.stability == null ? .5 : drafts.stability;
            el('chatTtsStabilityValue').textContent = Number(el('chatTtsStability').value).toFixed(2);
            return Promise.all([changeProvider(), changeNarrator()]);
        }
        async function load() {
            try {
                const results = await Promise.all([fetch('/api/tts/providers', {cache: 'no-store'}), fetch('/api/settings/chat-tts', {cache: 'no-store'})]);
                const [catalog, settings] = await Promise.all(results.map(r => r.json()));
                if (!results.every(r => r.ok) || !catalog.success || !settings.success) throw new Error(catalog.error || settings.error || 'Could not load voice settings');
                providers = catalog.providers || []; narrators = catalog.summarize_providers || [];
                options(el('chatTtsProvider'), providers.map(p => ({id: p.id, label: p.label + (p.configured ? '' : ' (no API key)')})));
                options(el('chatTtsNarratorProvider'), narrators.map(p => ({id: p.id, label: p.label + (p.configured ? '' : ' (not configured)')})));
                await apply(settings.chat_tts);
                const notice = el('chatTtsBackgroundNotice');
                if (notice) {
                    try {
                        const helpers = await fetch('/api/settings/background-helpers');
                        const data = await helpers.json(), prefs = data.preferences || {};
                        notice.hidden = !(data.success && ((prefs.chain || []).length || ((prefs.tasks || {}).narration || []).length));
                        const legacyNarrator = el('voiceDetails');
                        if (legacyNarrator) legacyNarrator.hidden = !notice.hidden;
                    } catch (_) { notice.hidden = true; }
                }
            } catch (error) { notify(error.message, 'error'); }
        }
        function read() {
            if (!speech.valid() || !text.valid()) throw new Error('Enter a custom model ID or choose Provider default.');
            rememberSpeech(); rememberNarrator();
            return {...drafts, provider: pid, summarize_provider: nid,
                enabled: el('chatTtsEnabled').checked, summarize: el('chatTtsSummarize').checked,
                speed: Number(el('chatTtsSpeed').value), stability: Number(el('chatTtsStability').value)};
        }
        async function save() {
            if (saving) return;
            saving = true;
            try {
                const body = read(); const serialized = JSON.stringify(body);
                const response = await fetch('/api/settings/chat-tts', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: serialized});
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'Save failed');
                if (JSON.stringify(read()) === serialized) await apply(data.chat_tts);
                notify('Chat voice saved', 'success');
            } catch (error) { notify(error.message, 'error'); }
            finally { saving = false; }
        }
        el('chatTtsVoice').addEventListener('change', () => { el('chatTtsCustomVoice').value = ''; rememberSpeech(); });
        return {load, apply, read, save, changeProvider, changeNarrator, voices,
                refreshSpeech: () => Promise.all([speech.discover(true), voices(true)]), refreshNarrator: () => text.discover(true)};
    }
    root.CuttleVoiceProviders = {create};
})(typeof window !== 'undefined' ? window : globalThis);
