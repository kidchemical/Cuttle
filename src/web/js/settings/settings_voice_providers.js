/* Voice Settings orchestration over the shared provider/model picker.
   One Voice tab, two modes: Spoken replies (TTS) and Live voice (Realtime).
   The mode selector swaps which configuration is shown; the TTS controller
   (provider/model picker) and the Realtime controller (catalog + settings)
   stay independent. */
(function (root) {
    'use strict';
    function create({document: doc, fetch, notify, onSaved}) {
        const el = id => doc.getElementById(id);
        let providers = [], narrators = [], drafts = {}, pid = '', nid = '', voiceTicket = 0, saving = false;
        let mode = 'tts', realtime = {}, realtimeCatalog = null, realtimeSaving = false, liveCatalog = null;
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
        function applyRealtime(conf) {
            realtime = {...(conf || {})};
            if (realtimeCatalog) {
                const defaults = realtimeCatalog.defaults || {};
                el('voiceRealtimeModel').value = realtime.model || defaults.model || '';
                el('voiceRealtimeVoice').value = realtime.voice || defaults.voice || '';
                el('voiceRealtimeTranscription').value = realtime.transcription_model || defaults.transcription_model || '';
            }
            renderModeStatus();
        }
        function populateRealtime(catalog) {
            realtimeCatalog = catalog || null;
            const cat = catalog || {};
            options(el('voiceRealtimeModel'), cat.models || [], (cat.defaults || {}).model);
            options(el('voiceRealtimeVoice'), cat.voices || [], (cat.defaults || {}).voice);
            options(el('voiceRealtimeTranscription'), cat.transcription_models || [], (cat.defaults || {}).transcription_model);
            if (!catalog) el('voiceRealtimeStatus').textContent = 'Realtime settings unavailable.';
        }
        /** The whole Realtime surface is gated by the experimental flag: when it
         *  is off there is no Live voice mode option to see or pick. */
        function setRealtimeModeAvailable(on) {
            const rowEl = el('voiceModeRow');
            if (rowEl) rowEl.hidden = !on;
            const sel = el('voiceModeSelect');
            if (!sel) return;
            let opt = sel.querySelector('option[value="realtime"]');
            if (on && !opt) {
                opt = doc.createElement('option');
                opt.value = 'realtime';
                opt.textContent = 'Live voice · OpenAI Realtime';
                sel.appendChild(opt);
            } else if (!on && opt) {
                opt.remove();
                if (mode === 'realtime') mode = 'tts';
            }
        }
        function renderModeStatus() {
            const status = el('voiceModeStatus');
            if (!status) return;
            if (mode !== 'realtime') { status.textContent = ''; return; }
            if (!realtimeCatalog) { status.textContent = 'Realtime settings unavailable.'; return; }
            const notes = [];
            if (!realtimeCatalog.enabled) notes.push('Realtime is off — enable it in Settings → Experimental → Voice: OpenAI Realtime.');
            if (!realtimeCatalog.configured) notes.push('Add an OpenAI API key in Providers to use it.');
            if (!notes.length) notes.push('Realtime is enabled.');
            status.textContent = notes.join(' ');
            const notice = el('voiceRealtimeNotice');
            if (notice) {
                notice.textContent = realtimeCatalog.enabled
                    ? 'Live voice is ready. Billed per audio token in both directions.'
                    : 'Realtime is currently disabled; your model and voice choices are saved and apply once you enable the flag.';
            }
        }
        function changeMode() {
            mode = el('voiceModeSelect').value;
            el('voiceTtsConfig').hidden = mode !== 'tts';
            el('voiceRealtimeConfig').hidden = mode !== 'realtime';
            if (el('voiceLiveConfig')) el('voiceLiveConfig').hidden = mode !== 'live';
            renderModeStatus();
            if (liveCatalog) void saveEngine();
        }
        async function saveEngine() {
            try {
                const response=await fetch('/api/settings/voice-chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({engine:mode})});
                const data=await response.json();if(!response.ok||!data.success)throw new Error(data.error||'Could not select the voice engine');
                liveCatalog=data;
            } catch(error) {notify(error.message,'error');}
        }
        async function saveLive() {
            try {
                const response=await fetch('/api/settings/voice-chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({engine:'live',model:el('voiceLiveModel').value,voice:el('voiceLiveVoice').value,idle_seconds:Number(el('voiceLiveIdle').value)})});
                const data=await response.json();if(!response.ok||!data.success)throw new Error(data.error||'Could not save GPT-Live');
                liveCatalog=data;notify('GPT-Live settings saved','success');
            }catch(error){notify(error.message,'error');}
        }
        async function loadLive() {
            if(!el('voiceLiveConfig'))return;
            try {
                const response=await fetch('/api/settings/voice-chat',{cache:'no-store'}),data=await response.json();
                if(!response.ok||!data.success||!data.voice_chat||typeof data.live_enabled!=='boolean'||!data.engine)throw new Error('Voice settings unavailable');
                const sel=el('voiceModeSelect'),old=sel.querySelector('option[value="live"]');old?.remove();
                if(data.live_enabled){const option=doc.createElement('option');option.value='live';option.textContent='GPT-Live · recommended';sel.appendChild(option);}
                const prefs=data.voice_chat||{};
                options(el('voiceLiveModel'),data.models||[],prefs.model);options(el('voiceLiveVoice'),data.voices||[],prefs.voice);
                el('voiceLiveIdle').value=prefs.idle_seconds==null?150:prefs.idle_seconds;
                el('voiceLiveStatus').textContent=data.configured?'Uses this chat’s selected Cuttle agent for delegated work.':'Add an OpenAI API key in Settings → Providers.';
                el('voiceModeRow').hidden=!(data.live_enabled||data.realtime_enabled);
                mode=data.engine||'tts';sel.value=mode;
                // Load preferences without writing a new engine selection.
                liveCatalog=null;changeMode();liveCatalog=data;
            }catch(_){el('voiceLiveConfig').hidden=true;}
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
                notify('Live voice saved', 'success');
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
                await loadRealtime();
                await loadLive();
            } catch (error) { notify(error.message, 'error'); }
        }
        async function loadRealtime() {
            try {
                const response = await fetch('/api/voice-stt/realtime/settings', {cache: 'no-store'});
                const data = await response.json();
                if (!response.ok || !data.success) throw new Error(data.error || 'Realtime settings unavailable');
                populateRealtime(data);
                applyRealtime(data.voice_realtime);
                setRealtimeModeAvailable(!!data.enabled);
                mode = data.enabled ? 'realtime' : 'tts';
            } catch (_) {
                populateRealtime(null);
                setRealtimeModeAvailable(false);
                mode = 'tts';
            }
            el('voiceModeSelect').value = mode;
            changeMode();
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
        return {load, apply, read, save, changeProvider, changeNarrator, voices, changeMode, saveRealtime, saveLive,
                refreshSpeech: () => Promise.all([speech.discover(true), voices(true)]), refreshNarrator: () => text.discover(true)};
    }
    root.CuttleVoiceProviders = {create};
})(typeof window !== 'undefined' ? window : globalThis);
