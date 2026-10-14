/* Voice Settings orchestration over the shared provider/model picker.
   Spoken replies (TTS) are stock Cuttle and always configurable. The Voice
   conversation section belongs to the voice-engine experiments: it is built
   only from engines whose flags are on, so with every experiment off nothing
   experimental renders and nothing is written. Loading never saves. */
(function (root) {
    'use strict';
    function create({document: doc, fetch, notify, onSaved}) {
        const el = id => doc.getElementById(id);
        let providers = [], narrators = [], drafts = {}, pid = '', nid = '', voiceTicket = 0, saving = false;
        let mode = 'tts', realtimeCatalog = null, realtimeSaving = false, liveCatalog = null;
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
                option.disabled = true;
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
        /** GET JSON, or null when the owner route is absent/denied/failing. */
        async function optional(url) {
            try {
                const response = await fetch(url, {cache: 'no-store'});
                const data = await response.json();
                return response.ok && data && data.success ? data : null;
            } catch (_) { return null; }
        }
        function applyRealtime(conf) {
            const defaults = (realtimeCatalog && realtimeCatalog.defaults) || {}, saved = conf || {};
            el('voiceRealtimeModel').value = saved.model || defaults.model || '';
            el('voiceRealtimeVoice').value = saved.voice || defaults.voice || '';
            el('voiceRealtimeTranscription').value = saved.transcription_model || defaults.transcription_model || '';
        }
        function populateRealtime(cat) {
            options(el('voiceRealtimeModel'), cat.models || [], (cat.defaults || {}).model);
            options(el('voiceRealtimeVoice'), cat.voices || [], (cat.defaults || {}).voice);
            options(el('voiceRealtimeTranscription'), cat.transcription_models || [], (cat.defaults || {}).transcription_model);
            applyRealtime(cat.voice_realtime);
            el('voiceRealtimeStatus').textContent = cat.configured ? '' : 'Add an OpenAI API key in Settings → Providers to use it.';
        }
        function populateLive(data) {
            const prefs = data.voice_chat || {};
            options(el('voiceLiveModel'), data.models || [], prefs.model);
            options(el('voiceLiveVoice'), data.voices || [], prefs.voice);
            el('voiceLiveIdle').value = prefs.idle_seconds == null ? 150 : prefs.idle_seconds;
            el('voiceLiveSpeed').value = prefs.speed == null ? 1 : prefs.speed;
            el('voiceLiveSpeedValue').textContent = Number(el('voiceLiveSpeed').value).toFixed(2) + '×';
            el('voiceLiveProgress').value = prefs.progress_seconds == null ? 0 : prefs.progress_seconds;
            el('voiceLiveStatus').textContent = data.configured ? 'Uses this chat’s selected Cuttle agent for delegated work.' : 'Add an OpenAI API key in Settings → Providers.';
            livePreviewable();
        }
        /** Only Live voices that TTS also speaks can be previewed without a billed session. */
        function livePreviewable() {
            const voice = el('voiceLiveVoice').value;
            const row = ((liveCatalog && liveCatalog.voices) || []).find(v => v.id === voice);
            const ok = !!(row && row.preview);
            const status = el('voiceLivePreviewStatus'), none = 'No preview for this GPT-Live-only voice; text-to-speech cannot speak it.';
            el('voiceLivePreviewBtn').disabled = !ok;
            if (!ok) status.textContent = none;
            else if (status.textContent === none) status.textContent = '';
        }
        /** Build the Voice conversation section from enabled experiments only. */
        async function loadEngines() {
            const [chat, rt] = await Promise.all([optional('/api/settings/voice-chat'), optional('/api/voice-stt/realtime/settings')]);
            liveCatalog = chat && chat.live_enabled ? chat : null;
            realtimeCatalog = rt && rt.enabled ? rt : null;
            if (liveCatalog) populateLive(liveCatalog);
            if (realtimeCatalog) populateRealtime(realtimeCatalog);
            const engines = [{id: 'tts', label: 'Standard · Spoken replies voice'}];
            if (liveCatalog) engines.push({id: 'live', label: 'GPT-Live'});
            if (realtimeCatalog) engines.push({id: 'realtime', label: 'OpenAI Realtime'});
            options(el('voiceModeSelect'), engines);
            el('voiceConversationGroup').hidden = engines.length < 2;
            const resolved = chat ? chat.engine : realtimeCatalog ? 'realtime' : 'tts';
            mode = engines.some(e => e.id === resolved) ? resolved : 'tts';
            el('voiceModeSelect').value = mode;
            showMode();
        }
        function showMode() {
            el('voiceLiveConfig').hidden = mode !== 'live';
            el('voiceRealtimeConfig').hidden = mode !== 'realtime';
            el('voiceModeStatus').textContent = mode === 'tts'
                ? 'Voice chat listens, then reads replies with your Spoken replies voice.' : '';
        }
        /** User picked an engine: show it and persist the choice. */
        async function changeMode() {
            mode = el('voiceModeSelect').value;
            showMode();
            try {
                const response = await fetch('/api/settings/voice-chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({engine: mode})});
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'Could not select the voice engine');
            } catch (error) { notify(error.message, 'error'); }
        }
        async function saveLive() {
            try {
                const response = await fetch('/api/settings/voice-chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({engine: 'live',
                    model: el('voiceLiveModel').value, voice: el('voiceLiveVoice').value, idle_seconds: Number(el('voiceLiveIdle').value),
                    speed: Number(el('voiceLiveSpeed').value), progress_seconds: Number(el('voiceLiveProgress').value)})});
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'Could not save GPT-Live');
                liveCatalog = data; notify('GPT-Live settings saved', 'success');
            } catch (error) { notify(error.message, 'error'); }
        }
        /** POST a preview request, play the returned clip, and report cache/cost. */
        async function playPreview(url, body, button, status, note) {
            try {
                button.disabled = true;
                status.textContent = 'Synthesizing preview…';
                const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                const data = await response.json();
                if (!response.ok || !data.success || !data.audio_base64) throw new Error((data && data.error) || ('Preview failed (' + response.status + ')'));
                const bin = atob(data.audio_base64), bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                await new Audio(URL.createObjectURL(new Blob([bytes], {type: data.content_type || 'audio/mpeg'}))).play();
                const cost = data.cost != null ? data.cost : data.usage && data.usage.tts_cost;
                const bits = [note, data.cached ? 'served from cache (no charge)' : cost != null ? '≈$' + Number(cost).toFixed(4) + ' this preview' : ''].filter(Boolean);
                status.textContent = 'Preview played' + (bits.length ? ' — ' + bits.join(' · ') : '') + '.';
            } catch (error) {
                status.textContent = '';
                notify('Voice preview failed: ' + (error.message || error), 'error');
            } finally { button.disabled = false; }
        }
        function previewSpeech() {
            let ui;
            try { ui = read(); } catch (error) { notify('Voice preview failed: ' + error.message, 'error'); return; }
            return playPreview('/api/chat/tts', {text: 'Hello! This is how I will sound when I read replies aloud.', summarize: false,
                provider: ui.provider, tts_model: (ui.models || {})[ui.provider], voice: (ui.voices || {})[ui.provider],
                speed: ui.speed, stability: ui.stability}, el('chatTtsPreviewBtn'), el('chatTtsPreviewStatus'));
        }
        async function previewLive() {
            await playPreview('/api/voice-stt/live/preview', {voice: el('voiceLiveVoice').value, speed: Number(el('voiceLiveSpeed').value)},
                el('voiceLivePreviewBtn'), el('voiceLivePreviewStatus'), 'approximated with OpenAI text-to-speech');
            livePreviewable();
        }
        function previewRealtime() {
            return playPreview('/api/voice-stt/realtime/preview', {model: el('voiceRealtimeModel').value, voice: el('voiceRealtimeVoice').value},
                el('voiceRealtimePreviewBtn'), el('voiceRealtimePreviewStatus'));
        }
        function readRealtime() {
            return {
                model: el('voiceRealtimeModel').value,
                voice: el('voiceRealtimeVoice').value,
                transcription_model: el('voiceRealtimeTranscription').value,
            };
        }
        async function saveRealtime() {
            if (realtimeSaving) return;
            realtimeSaving = true;
            try {
                const response = await fetch('/api/voice-stt/realtime/settings', {method: 'POST',
                    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({voice_realtime: readRealtime()})});
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'Save failed');
                applyRealtime(data.voice_realtime);
                notify('Realtime settings saved', 'success');
            } catch (error) { notify(error.message, 'error'); }
            finally { realtimeSaving = false; }
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
            await loadEngines();
        }
        function read() {
            if (!speech.valid() || !text.valid()) throw new Error('Enter a custom model ID or choose Provider default.');
            if (!el('chatTtsCustomVoice').value.trim() && el('chatTtsVoice').selectedOptions?.[0]?.disabled) throw new Error('Choose a voice supported by the selected model.');
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
        el('voiceLiveVoice').addEventListener('change', livePreviewable);
        return {load, apply, read, save, changeProvider, changeNarrator, voices, changeMode, saveRealtime, saveLive,
                previewSpeech, previewLive, previewRealtime,
                refreshSpeech: () => Promise.all([speech.discover(true), voices(true)]), refreshNarrator: () => text.discover(true)};
    }
    root.CuttleVoiceProviders = {create};
})(typeof window !== 'undefined' ? window : globalThis);
