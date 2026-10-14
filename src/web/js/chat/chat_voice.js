/*
 * Voice mode controller (same chat session; Web Speech STT + spoken replies).
 * Owns voice state, overlay DOM, recognition, tap/hold mic input and phrase
 * bubbles. Chat send/steer/queue and TTS playback arrive as host capabilities.
 */
(function (root) {
    'use strict';

    const Segments = root.CuttleChatVoiceSegments;
    const Stars = root.CuttleChatVoiceStars;
    const Narrator = root.CuttleChatVoiceNarrator;
    const Recorder = root.CuttleChatVoiceRecorder;
    const Realtime = root.CuttleChatVoiceRealtime;
    const Live = root.CuttleChatVoiceLive;
    const Waveform = root.CuttleChatVoiceWaveform || null;
    const Activity = root.CuttleChatVoiceActivity || null;

    /** Give up waiting for in-flight phrase transcriptions after this long. */
    const TRANSCRIBE_WAIT_MS = 15000;

    /** Hold this long before a press becomes hold-to-talk (shorter presses stay taps). */
    const HOLD_MS = 280;

    /** Within this many pixels of the end, the transcript still counts as pinned. */
    const BOTTOM_SLACK_PX = 48;

    function byId(id) {
        return document.getElementById(id);
    }

    function speechRecognitionCtor() {
        return root.SpeechRecognition || root.webkitSpeechRecognition || null;
    }

    function isMobileShell() {
        // The chat page is an iframe inside the shell, so the native marker
        // lives on the parent window there (same-origin read; cross-origin throws).
        try {
            const parent = (root.parent && root.parent !== root) ? root.parent : null;
            return !!(root.isCuttleMobile || (root.cuttleMobile && root.cuttleMobile.isNative)
                || (parent && (parent.isCuttleMobile || (parent.cuttleMobile && parent.cuttleMobile.isNative))));
        } catch (_) {
            return !!(root.isCuttleMobile || (root.cuttleMobile && root.cuttleMobile.isNative));
        }
    }

    function speakKey(messageEl) {
        if (!messageEl) return '';
        if (messageEl.dataset && messageEl.dataset.messageId) {
            return 'id:' + messageEl.dataset.messageId;
        }
        const raw = String((messageEl.dataset && messageEl.dataset.rawContent) || '').trim();
        return raw ? ('raw:' + raw.slice(0, 120)) : '';
    }

    /**
     * @param {object} host
     * @param {() => void} host.closeMenus
     * @param {() => boolean} host.isGenerating
     * @param {(spoken: string) => string} host.compose  outbound text incl. sticky slash chip
     * @param {(message: string) => boolean} host.isSendable
     * @param {(message: string) => boolean} host.isControlLane
     * @param {(message: string) => Promise<boolean>} host.steer  into the running turn
     * @param {(message: string, spoken: string) => void} host.enqueue  follow-up queue
     * @param {(message: string, spoken: string) => Promise<void>} host.submit  new turn
     * @param {() => (HTMLElement|null)} host.lastAssistantMessage
     * @param {(el: HTMLElement) => Promise<({url: string, spoken: string}|null)>} host.speechFor
     * @param {(el: HTMLElement, speech: object) => Promise<'ended'|'stopped'|'blocked'|'error'>} host.play
     * @param {() => void} host.stopSpeech
     * @param {(url: string) => Promise<'ended'|'stopped'|'blocked'|'error'>} host.playClip  narration audio, same channel as replies
     * @param {() => (number|null)} [host.playbackLevel]  current TTS RMS for the ring, null when unknown
     * @param {(el: HTMLElement) => (object|null)} [host.usageFor]  agent usage stashed on a chat bubble
     * @param {(parts: object[]) => (object|null)} [host.sumUsage]  add usage payloads (agent + voice layers)
     * @param {(usage: object) => string} [host.usageHtml]  same footer markup as normal chat bubbles
     * @param {() => (object|null)} [host.sessionUsage]  summed usage across the session incl. voice layers
     * @param {() => Promise<Set<string>>} host.experimentalFlags  enabled flag ids (`voice_narrator`, `voice_server_stt`)
     * @param {typeof fetch} host.fetch
     * @param {(message: string, variant?: string, opts?: object) => void} host.toast
     * @param {(...args: any[]) => void} host.logError
     */
    function create(host) {
        let active = false;
        /** @type {'idle'|'listening'|'processing'|'speaking'} */
        let phase = 'idle';
        let recognition = null;
        let wantListening = false;
        let closing = false;
        let lastSpokenKey = '';
        let speakToken = 0;
        /** A voice turn was sent and its reply should speak aloud even if the overlay closed mid-run. */
        let replyPending = false;
        /** Reply that finished while the user was talking: `{opts, el}`. */
        let deferredReply = null;
        let escapeHandler = null;
        let stars = null;
        let waveform = null;
        let micMonitor = null;
        /** Chat-style processing bubble in the transcript while the agent runs. */
        let activityEl = null;
        let activityStatusEl = null;
        const phrases = Segments.createState();
        let narratorOn = false;
        let narrateTimer = 0;
        const narration = Narrator.createPlan();
        /** 'webspeech' (browser recognizer) or 'server' (record + /api/voice-stt). */
        let engine = 'webspeech';
        let recorder = null;
        let realtime = null;
        let live = null, sharedVoice = null, detachVoice = null, voiceChatId = null;
        /** Document-lifetime runtime for a chat page opened without the shell. */
        let pageRuntime = null;
        let realtimeQueued = false;
        /** Priced Realtime session usage, summed from server-priced deltas. */
        let realtimeUsage = null;
        let speaking = false;
        let transcribing = 0;
        /** Engine + narrator flags resolve on enter; listening waits for them. */
        let flagsReady = Promise.resolve();

        let holdTimer = 0;
        let holdDown = false;
        let holdOwner = false;
        let suppressClick = false;

        function overlayEl() {
            return byId('voiceModeOverlay');
        }

        /** Voice mode is per chat; remember it so the shell's space-switch frame
         *  reload re-enters voice view instead of dropping back to chat view. */
        function voiceModeStorageKey() {
            try {
                const search = root.location && root.location.search ? root.location.search : '';
                const params = new root.URLSearchParams(search);
                const id = params.get('chat') || params.get('session');
                return id ? 'cuttle.voiceMode.' + id : '';
            } catch (_) { return ''; }
        }
        function rememberVoiceMode(on) {
            const key = voiceModeStorageKey();
            if (!key) return;
            try {
                if (on) root.sessionStorage.setItem(key, '1');
                else root.sessionStorage.removeItem(key);
            } catch (_) {}
        }
        function voiceModeRemembered() {
            const key = voiceModeStorageKey();
            if (!key) return false;
            try { return root.sessionStorage.getItem(key) === '1'; } catch (_) { return false; }
        }

        function setStatus(text) {
            const el = byId('voiceModeStatus');
            if (el) el.textContent = String(text || '');
        }

        function listeningText() {
            if (deferredReply) return 'Reply ready — it plays after you send';
            return host.isGenerating() ? 'Listening… tap the orb to send to the running agent' : 'Listening… tap the orb to send';
        }

        function setPhase(next, statusText) {
            const prev = phase;
            phase = next || 'idle';
            const overlay = overlayEl();
            if (overlay) {
                overlay.classList.toggle('is-listening', phase === 'listening');
                overlay.classList.toggle('is-processing', phase === 'processing');
                overlay.classList.toggle('is-speaking', phase === 'speaking');
            }
            const busy = phase === 'processing' || phase === 'speaking';
            const mic = byId('voiceModeMicBtn');
            if (mic) {
                mic.disabled = closing;
                mic.setAttribute('aria-pressed', phase === 'listening' ? 'true' : 'false');
                if (phase === 'listening') mic.title = host.isGenerating() ? 'Tap to send to the running agent' : 'Tap to send';
                else if (phase === 'speaking') mic.title = 'Tap to interrupt and talk';
                else if (busy) mic.title = 'Tap to talk to the running agent';
                else mic.title = 'Tap to talk';
                if ((engine === 'live' && live?.isOpen()) || (engine === 'realtime' && realtime && realtime.isOpen())) mic.title = 'Disconnect voice conversation';
                mic.setAttribute('aria-label', mic.title);
            }
            const watch = byId('voiceModeWatchBtn');
            if (watch) watch.hidden = !(active && busy);
            // Chat-style processing bubble: only while the agent runs, one at a
            // time; entering processing shows it, leaving processing clears it.
            if (phase === 'processing') {
                if (prev !== 'processing') showActivity(statusText);
            } else if (prev === 'processing') {
                hideActivity();
            }
            if (statusText != null) setStatus(statusText);
            else if (phase === 'idle') setStatus('Tap the orb to talk');
            else if (phase === 'listening') setStatus(listeningText());
            else if (phase === 'processing') setStatus('Thinking…');
            else if (phase === 'speaking') setStatus('Speaking…');
        }

        /** A silent fallback to the chiming browser recognizer is undiagnosable on a phone. */
        function showEngine(wantServer, missing) {
            const hint = byId('voiceModeHint');
            if (hint) {
                if (!hint.dataset.base) hint.dataset.base = hint.textContent;
                const label = engine === 'live' ? 'GPT-Live · tap the orb to connect/disconnect' : engine === 'realtime' ? 'OpenAI Realtime · tap the orb to connect/disconnect'
                    : engine === 'server'
                    ? (Recorder.sourceKind() === 'native' ? 'Server transcription (app mic)' : 'Server transcription')
                    : (wantServer ? 'Browser speech — server transcription needs ' + missing.join(', ') : '');
                hint.textContent = label ? hint.dataset.base + ' · ' + label : hint.dataset.base;
            }
            if (wantServer && missing.length) {
                host.logError('Voice server transcription unavailable; missing', missing);
                host.toast('Server transcription unavailable here (needs ' + missing.join(', ') + ')', 'error');
            }
        }

        /** RMS for the waveform: live mic while listening, playback while speaking. */
        function voiceLevel() {
            try {
                if (phase === 'listening') {
                    if (engine === 'server' && recorder && typeof recorder.level === 'function') {
                        const v = recorder.level();
                        if (v > 0) return v;
                    }
                    if (micMonitor) return micMonitor.level();
                    return null;
                }
                if (phase === 'speaking' && typeof host.playbackLevel === 'function') {
                    const v = host.playbackLevel();
                    return v == null ? null : v;
                }
            } catch (_) {
                return null;
            }
            return null;
        }

        /** Analysis-only mic tap for the browser recognizer (best effort). */
        function startMicMonitor() {
            if (!Waveform || micMonitor) return;
            try {
                micMonitor = Waveform.createMicMonitor();
                micMonitor.start().catch(() => {
                    micMonitor = null;
                });
            } catch (_) {
                micMonitor = null;
            }
        }

        async function stopMicMonitor() {
            const m = micMonitor;
            micMonitor = null;
            if (m) {
                try { await m.stop(); } catch (_) {}
            }
        }

        /** Combined agent + summarize-LLM + TTS footer, same markup as chat bubbles. */
        function voiceUsageHtml(messageEl, speech) {
            if (typeof host.usageHtml !== 'function') return '';
            let sum = null;
            try {
                const parts = [];
                if (typeof host.usageFor === 'function') {
                    const agent = host.usageFor(messageEl);
                    if (agent) parts.push(agent);
                }
                if (speech && speech.voiceUsage) parts.push(speech.voiceUsage);
                sum = typeof host.sumUsage === 'function' ? host.sumUsage(parts) : (parts[0] || null);
            } catch (_) {
                sum = null;
            }
            if (!sum) return '';
            try {
                return host.usageHtml(sum) || '';
            } catch (_) {
                return '';
            }
        }

        /** Session-total strip; hidden until some usage is known. */
        function renderSessionUsage() {
            const connection = byId('voiceModeConnectionUsage');
            if(connection){
                const value=realtimeUsage&&host.usageHtml?.({...realtimeUsage,usage_source:'voice'});
                connection.hidden=!active||!['live','realtime'].includes(engine)||!value;
                connection.innerHTML=value?'<span class="voice-mode-session-usage-label">Voice connection</span>'+value:'';
            }
            const box = byId('voiceModeSessionUsage');
            if (!box) return;
            let html = '';
            try {
                if (typeof host.usageHtml === 'function') {
                    let total = null;
                    if (typeof host.sessionUsage === 'function') total = host.sessionUsage();
                    // Production totals already include the durable voice ledger.
                    if (realtimeUsage && !host.refreshUsage) total = host.sumUsage ? host.sumUsage([total, realtimeUsage]) : (total || realtimeUsage);
                    if (total) html = host.usageHtml(total) || '';
                }
            } catch (_) {
                html = '';
            }
            box.textContent = '';
            if (!html || !html.match(/message-usage/)) {
                box.hidden = true;
                return;
            }
            box.hidden = false;
            try {
                const label = document.createElement('span');
                label.className = 'voice-mode-session-usage-label';
                label.textContent = 'Session total';
                box.appendChild(label);
                const wrap = document.createElement('span');
                wrap.innerHTML = html;
                const node = typeof wrap.querySelector === 'function'
                    ? wrap.querySelector('.message-usage')
                    : null;
                box.appendChild(node || wrap);
            } catch (_) {
                box.hidden = true;
            }
        }

        function renderPhrases() {
            const box = byId('voiceModeSegments');
            if (!box) return;
            // Capture the pin before rebuild: clearing resets scrollTop, which
            // would otherwise always read as "at bottom".
            const follow = scrolledToBottom(box);
            box.textContent = '';
            phrases.segments.forEach((seg) => {
                const row = document.createElement('div');
                row.className = 'voice-mode-segment';
                const text = document.createElement('span');
                text.className = 'voice-mode-segment-text';
                text.textContent = seg.text;
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'voice-mode-segment-remove';
                remove.dataset.segmentId = String(seg.id);
                remove.title = 'Remove this phrase';
                remove.setAttribute('aria-label', 'Remove phrase: ' + seg.text);
                remove.textContent = '×';
                row.appendChild(text);
                row.appendChild(remove);
                box.appendChild(row);
            });
            const indicator = phrases.live
                || (speaking ? 'Listening…' : (transcribing ? 'Transcribing…' : ''));
            if (indicator) {
                const live = document.createElement('div');
                live.className = 'voice-mode-segment is-live';
                live.textContent = indicator;
                box.appendChild(live);
            }
            box.hidden = Segments.isEmpty(phrases) && !indicator;
            stickToBottom(box, follow);
        }

        /** True while the box sits within a few pixels of its newest line. */
        function scrolledToBottom(box) {
            if (!box) return false;
            return (box.scrollHeight - box.scrollTop - box.clientHeight) <= BOTTOM_SLACK_PX;
        }

        /** Follow new content only for a reader already at the bottom; a reader
         *  who scrolled up keeps their place. Follows instantly: smooth scrolling
         *  animates behind a streamed reply and then snaps to the end. */
        function stickToBottom(box, wasPinned) {
            if (!box || !wasPinned) return;
            const top = box.scrollHeight;
            if (typeof box.scrollTo === 'function') box.scrollTo({ top: top, behavior: 'auto' });
            else box.scrollTop = top;
        }

        function onPhraseClick(e) {
            const btn = e.target && e.target.closest ? e.target.closest('.voice-mode-segment-remove') : null;
            if (!btn) return;
            e.preventDefault();
            e.stopPropagation();
            Segments.remove(phrases, Number(btn.dataset.segmentId));
            renderPhrases();
        }

        function clearPhrases() {
            Segments.clear(phrases);
            renderPhrases();
        }

        /**
         * Chat-style agent processing bubble inside the voice transcript. It
         * reuses the regular chat view's typing-orbit markup so "thinking" and
         * tool activity read the same as a normal assistant bubble.
         */
        function showActivity(statusText) {
            if (!Activity) return;
            const box = byId('voiceModeTranscript');
            if (!box) return;
            const follow = scrolledToBottom(box);
            if (!activityEl) {
                activityEl = document.createElement('div');
                activityEl.className = 'voice-mode-line is-activity';
                const indicator = document.createElement('div');
                indicator.className = 'typing-indicator';
                indicator.setAttribute('aria-hidden', 'true');
                indicator.innerHTML = Activity.orbitHtml();
                activityStatusEl = document.createElement('div');
                activityStatusEl.className = 'typing-status';
                activityStatusEl.setAttribute('aria-live', 'polite');
                activityStatusEl.setAttribute('role', 'status');
                activityStatusEl.textContent = Activity.normalizeStatus(statusText);
                activityEl.appendChild(indicator);
                activityEl.appendChild(activityStatusEl);
                box.appendChild(activityEl);
            } else if (activityStatusEl) {
                activityStatusEl.textContent = Activity.normalizeStatus(statusText);
            }
            stickToBottom(box, follow);
        }

        function updateActivity(statusText) {
            if (!Activity) return;
            if (!activityEl) { showActivity(statusText); return; }
            if (activityStatusEl) activityStatusEl.textContent = Activity.normalizeStatus(statusText);
        }

        function hideActivity() {
            if (!activityEl) return;
            const box = activityEl.parentNode;
            const follow = box ? scrolledToBottom(box) : false;
            activityEl.remove();
            activityEl = null;
            activityStatusEl = null;
            if (box) stickToBottom(box, follow);
        }

        function appendTranscriptLine(role, text, usageHtml) {
            // The reply (or its error fallback) supersedes the processing bubble.
            if (role === 'assistant') hideActivity();
            const box = byId('voiceModeTranscript');
            if (!box) return;
            const follow = scrolledToBottom(box);
            const line = document.createElement('div');
            line.className = 'voice-mode-line is-' + (role === 'user' ? 'user' : 'assistant')
                + (role === 'narrator' ? ' is-narration' : '');
            const label = document.createElement('span');
            label.className = 'voice-mode-line-label';
            label.textContent = role === 'user' ? 'You' : 'Cuttle';
            const body = document.createElement('div');
            body.textContent = String(text || '').trim();
            line.appendChild(label);
            line.appendChild(body);
            if (usageHtml && role === 'assistant') {
                try {
                    const usage = document.createElement('div');
                    usage.className = 'voice-mode-line-usage';
                    usage.innerHTML = usageHtml;
                    line.appendChild(usage);
                } catch (_) {}
            }
            box.appendChild(line);
            stickToBottom(box, follow);
        }

        /** The still-open streaming line for one spoken item (role + provider item id). */
        function openTranscriptLine(box, role, itemId) {
            if (!box || !itemId) return null;
            for (let i = box.children.length - 1; i >= 0; i--) {
                const el = box.children[i];
                if (el.dataset && el.dataset.liveRole === role && el.dataset.liveItem === itemId) return el;
            }
            return null;
        }

        /**
         * Streaming transcript deltas must grow one bubble per spoken item, not
         * one bubble per delta. Deltas of the same utterance are grouped by the
         * provider `itemId` (falling back to the trailing role) so a late user
         * transcript arriving between two assistant deltas cannot split the
         * assistant's phrase in half.
         */
        function appendLiveTranscript(role, text, partial, itemId) {
            const box = byId('voiceModeTranscript');
            if (!box) return;
            if (partial && !String(text || '').length) return;
            if (partial) {
                const last = box.lastElementChild;
                let line = (last && last.dataset.liveRole === role
                    && (!itemId || last.dataset.liveItem === itemId)) ? last : null;
                if (!line) line = openTranscriptLine(box, role, itemId);
                if (line) {
                    const follow = scrolledToBottom(box);
                    const body = line.lastElementChild;
                    if (body) body.textContent += text;
                    stickToBottom(box, follow);
                    return;
                }
            }
            appendTranscriptLine(role, text);
            const line = box.lastElementChild;
            if (partial && line) {
                line.dataset.liveRole = role;
                if (itemId) line.dataset.liveItem = itemId;
            }
        }

        async function voiceContext() {
            let id = host.sessionId?.();
            if (!id && host.adoptSession) {
                const r = await host.fetch('/api/auth/sessions', {method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_name:'Voice conversation'})});
                const d = await r.json();
                if (!r.ok || !d.success) throw new Error('Could not create the originating voice chat');
                id = d.session_id; host.adoptSession(id); rememberVoiceMode(true);
            }
            if (!id) throw new Error('Open or create a chat before connecting voice');
            voiceChatId = String(id).replace(/^db_session_/, '').replace(/^CH-0*/, '');
            const probe = '__voice_request__', composed = host.compose(probe);
            return {session_id:voiceChatId, engine, prefix:composed.endsWith(probe) ? composed.slice(0,-probe.length) : '', chat_request:host.requestContext?.()||{}};
        }

        /** The one runtime that owns the call: the shell's when this chat page is
         *  an iframe presenter, otherwise a document-lifetime runtime so leaving
         *  voice view does not hang up a call in a standalone/browser tab. */
        function voiceRuntime() {
            try {
                const parent = root.parent;
                if (parent && parent !== root) {
                    const shared = parent.cuttleVoiceRuntime;
                    if (shared) return shared;
                }
            } catch (_) {}
            if (!pageRuntime && root.CuttleShellVoice && typeof root.CuttleShellVoice.create === 'function') {
                try {
                    pageRuntime = root.CuttleShellVoice.create({
                        win:root,
                        navigate:(sid)=>{try{if(sid)root.location.href='/chat_page.html?chat='+encodeURIComponent(sid);}catch(_){}},
                    });
                } catch (_) { pageRuntime = null; }
            }
            return pageRuntime;
        }

        function conversationCallbacks() {
            return {onTranscript:appendLiveTranscript, alive:()=>{try{return root.parent===root||!!root.frameElement?.isConnected;}catch(_){return false;}},
                onStatus:text=>{if(active)setPhase(text.startsWith('Disconnected')?'idle':text.startsWith('Speaking')?'speaking':'listening',text);},
                onError:text=>{if(active)setPhase('idle',text);},
                onUsage:priced=>{realtimeUsage=priced;host.refreshUsage?.();renderSessionUsage();}};
        }

        async function startLive() {
            host.stopSpeech();
            const context = await voiceContext();
            sharedVoice = voiceRuntime();
            if (sharedVoice) {
                detachVoice?.(); detachVoice=sharedVoice.attach(context.session_id,conversationCallbacks());
                live={start:()=>sharedVoice.start(context),stop:()=>sharedVoice.engine(context.session_id)?sharedVoice.stop():Promise.resolve(),isOpen:()=>sharedVoice.isOpen(context.session_id)};
            } else if (!live) live=Live.create({fetch:host.fetch,...conversationCallbacks()});
            setPhase('processing','Connecting GPT-Live…');
            try { await live.start(context); }
            catch(err){if(active)setPhase('idle',err.message||'GPT-Live connection failed');}
        }

        function stopRecognition() {
            const rec = recognition;
            recognition = null;
            if (!rec) return;
            try {
                rec.onresult = null;
                rec.onerror = null;
                rec.onend = null;
                rec.stop();
            } catch (_) {
                try { rec.abort(); } catch (_2) {}
            }
        }

        /**
         * Stop whichever engine is listening. `flush` keeps the words in
         * progress as a phrase; resolves once their text has landed.
         */
        async function stopInput(flush) {
            if (engine === 'server') {
                if (!recorder) return;
                let timeout = 0;
                await Promise.race([
                    recorder.stop({ flush: flush }),
                    new Promise((r) => { timeout = setTimeout(r, TRANSCRIBE_WAIT_MS); }),
                ]);
                clearTimeout(timeout);
                return;
            }
            if (flush) Segments.endSession(phrases);
            stopRecognition();
        }

        function serverRecorder() {
            if (recorder) return recorder;
            recorder = Recorder.create({
                sessionId: host.sessionId,
                fetch: host.fetch,
                onSpeech: (on) => {
                    speaking = on;
                    renderPhrases();
                },
                onPhraseText: (text) => {
                    Segments.commit(phrases, text);
                    renderPhrases();
                },
                onPending: (n) => {
                    transcribing = n;
                    renderPhrases();
                },
                onError: (message) => {
                    host.logError('Voice transcription failed', message);
                    if (active && phase === 'listening') setStatus('Could not transcribe that — keep talking or retry');
                },
            });
            return recorder;
        }

        // ── Microphone permission ───────────────────────────────────────
        function promptMicPermission(message) {
            setStatus(message || 'Microphone permission needed — tap toast or mic');
            host.toast(
                (message || 'Microphone permission needed') + ' — tap to allow',
                'error',
                { actionId: 'voice-mic-retry' }
            );
            try {
                if (root.cuttleMobile && typeof root.cuttleMobile.requestMicrophone === 'function') {
                    root.cuttleMobile.requestMicrophone();
                }
            } catch (_) {}
        }

        /**
         * Ask for mic access before SpeechRecognition.
         * Android WebView only shows the system dialog when RECORD_AUDIO is in the
         * APK manifest + getUserMedia / native requestMicrophone runs.
         */
        async function ensureMicAccess() {
            try {
                if (root.cuttleMobile && typeof root.cuttleMobile.requestMicrophone === 'function') {
                    root.cuttleMobile.requestMicrophone();
                }
            } catch (_) {}
            const md = root.navigator && root.navigator.mediaDevices;
            if (!md || typeof md.getUserMedia !== 'function') return { ok: true };
            try {
                const stream = await md.getUserMedia({ audio: true });
                try { stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
                return { ok: true };
            } catch (err) {
                const name = String((err && err.name) || '');
                const msg = String((err && err.message) || err || '');
                host.logError('Voice getUserMedia failed', name, msg);
                return { ok: false, name, message: msg };
            }
        }

        function onMicToastAction() {
            if (!active || closing || phase === 'listening') return;
            try {
                if (root.cuttleMobile && typeof root.cuttleMobile.openAppSettings === 'function') {
                    ensureMicAccess().then((mic) => {
                        if (mic && mic.ok) {
                            startListening();
                            return;
                        }
                        try { root.cuttleMobile.openAppSettings(); } catch (_) {}
                        setStatus('Enable Microphone for Cuttle in Android Settings');
                    });
                    return;
                }
            } catch (_) {}
            startListening();
        }

        // ── Listening ───────────────────────────────────────────────────
        /** Unsent phrases survive a pause, a screen lock and a re-tap; only send/exit clears them. */
        async function startListening() {
            await flagsReady;
            if (!active) return;
            if (engine === 'live') { await startLive(); return; }
            if (engine === 'realtime') {
                sharedVoice = host.sessionId ? voiceRuntime() : null;
                if (sharedVoice) {
                    const context=await voiceContext();detachVoice?.();detachVoice=sharedVoice.attach(context.session_id,conversationCallbacks());
                    realtime={start:()=>sharedVoice.start(context),stop:()=>sharedVoice.engine(context.session_id)?sharedVoice.stop():Promise.resolve(),isOpen:()=>sharedVoice.isOpen(context.session_id)};
                    try{await realtime.start();}catch(err){if(active)setPhase('idle',err.message);}
                    return;
                }
                host.stopSpeech();
                if (!realtime) realtime = Realtime.create({
                    fetch: host.fetch, experimentalFlags: host.experimentalFlags,
                    onTranscript: appendLiveTranscript,
                    onStatus: (text) => { if (active) setPhase('listening', text); },
                    onError: (text) => { if (active) setPhase('idle', text); },
                    realtimeUsage: async (payload) => {
                        try {
                            const res = await host.fetch('/api/voice-stt/realtime/usage', {
                                method: 'POST', credentials: 'include', keepalive: true,
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify(payload),
                            });
                            const data = await res.json();
                            return data && data.success ? data.usage : null;
                        } catch (_) { return null; }
                    },
                    onUsage: (priced) => {
                        realtimeUsage = host.sumUsage ? host.sumUsage([realtimeUsage, priced]) : priced;
                        renderSessionUsage();
                    },
                    delegate: async (spoken) => {
                        const message = host.compose(spoken);
                        if (!host.isSendable(message)) return { error: 'Empty request' };
                        if (host.isGenerating() && !host.isControlLane(message)) {
                            if (await host.steer(message)) {
                                realtimeQueued = true;
                                return { status: 'steered; awaiting reply' };
                            }
                            host.enqueue(message, spoken);
                            return { status: 'queued; the result will appear in the chat transcript' };
                        }
                        const before = host.lastAssistantMessage();
                        await host.submit(message, spoken);
                        const after = host.lastAssistantMessage();
                        return after && after !== before
                            ? { reply: String(after.dataset.rawContent || after.textContent || '').slice(0, 16000) }
                            : { error: 'No new reply available; check the chat transcript' };
                    },
                });
                setPhase('processing', 'Connecting Realtime voice…');
                try { await realtime.start(host.sessionId ? await voiceContext() : undefined); }
                catch (err) { if (active) setPhase('idle', err.message || 'Realtime connection failed'); }
                if (!active) realtime.stop();
                return;
            }
            const Ctor = engine === 'server' ? null : speechRecognitionCtor();
            if (engine !== 'server' && !Ctor) {
                host.toast('Voice input needs Chrome or Edge (Web Speech API)', 'error');
                setStatus('Speech recognition not available in this browser');
                return;
            }
            // Barge-in: silence narration / the spoken reply; a running turn keeps going.
            speakToken += 1;
            host.stopSpeech();
            wantListening = true;
            closing = false;
            setPhase('idle', 'Checking microphone…');

            if (engine === 'server') {
                try {
                    await serverRecorder().start();
                } catch (e) {
                    host.logError('Voice recording failed to start', e);
                    wantListening = false;
                    if (e && e.name === 'NativeMicError') {
                        settlePhase(e.message + ' — tap mic to retry');
                        return;
                    }
                    promptMicPermission('Allow microphone access for voice mode');
                    settlePhase();
                    return;
                }
                if (!wantListening || !active) {
                    recorder.stop({ flush: false });
                    return;
                }
                setPhase('listening');
                return;
            }

            const mic = await ensureMicAccess();
            if (!mic.ok) {
                wantListening = false;
                promptMicPermission('Allow microphone access for voice mode');
                settlePhase();
                return;
            }
            if (!wantListening || !active) return;
            startMicMonitor();
            beginRecognition(Ctor);
        }

        function restartSoon(Ctor, ms) {
            setTimeout(() => {
                if (wantListening && active && phase === 'listening' && !recognition) {
                    beginRecognition(Ctor);
                }
            }, ms);
        }

        function beginRecognition(Ctor) {
            if (!Ctor || !wantListening || !active) return;
            if (phase === 'processing' || phase === 'speaking') return;

            stopRecognition();
            const rec = new Ctor();
            recognition = rec;
            // Mobile WebView continuous mode stacks cumulative finals; one-shot + restart is reliable.
            rec.continuous = !isMobileShell();
            rec.interimResults = true;
            rec.lang = (root.navigator && root.navigator.language) || 'en-US';
            rec.maxAlternatives = 1;
            Segments.beginSession(phrases);

            rec.onresult = function (event) {
                Segments.applyResults(phrases, event.results);
                renderPhrases();
                if (phase === 'listening') setStatus(listeningText());
            };
            rec.onerror = function (event) {
                const err = (event && event.error) || 'error';
                if (err === 'aborted') return;
                if (err === 'no-speech') {
                    // Pause — onend restarts recognition; only a mic tap sends.
                    return;
                }
                host.logError('Voice recognition error', err);
                if (err === 'not-allowed' || err === 'service-not-allowed') {
                    wantListening = false;
                    promptMicPermission('Allow microphone access for voice mode');
                    stopRecognition();
                    settlePhase();
                    return;
                }
                setStatus('Could not hear that — try again');
            };
            rec.onend = function () {
                if (recognition !== rec) return;
                recognition = null;
                if (!wantListening || !active || phase !== 'listening') return;
                // The recognizer ends on pauses; keep the phrase and listen again.
                Segments.endSession(phrases);
                renderPhrases();
                if (document.hidden) {
                    pauseListening();
                    return;
                }
                restartSoon(Ctor, 120);
            };
            try {
                rec.start();
                setPhase('listening');
            } catch (e) {
                host.logError('Voice recognition start failed', e);
                recognition = null;
                if (phase !== 'listening') {
                    wantListening = false;
                    settlePhase('Could not start microphone — tap mic to retry');
                    promptMicPermission(String((e && e.message) || e));
                    return;
                }
                if (document.hidden) {
                    pauseListening();
                    return;
                }
                restartSoon(Ctor, 500);
            }
        }

        /** Back to rest: "working" while the agent still runs (narration resumes), else idle. */
        function settlePhase(text) {
            if (!active) return;
            if (host.isGenerating()) {
                setPhase('processing', text || 'Working…');
                scheduleProgress();
                return;
            }
            setPhase('idle', text);
        }

        async function finishListeningAndSend() {
            if (engine === 'live') { await live?.stop(); setPhase('idle','GPT-Live disconnected — tap mic to reconnect'); return; }
            if (engine === 'realtime') {
                if (realtime) realtime.stop();
                setPhase('idle', 'Realtime disconnected — tap mic to reconnect');
                return;
            }
            if (closing) return;
            closing = true;
            wantListening = false;
            if (engine === 'server') setPhase('processing', 'Finishing transcription…');
            await stopInput(true);
            await stopMicMonitor();
            const spoken = Segments.utterance(phrases);
            clearPhrases();
            if (recorder) recorder.resetContext();
            closing = false;
            if (!active) return;
            const held = deferredReply;
            deferredReply = null;
            if (!spoken) {
                if (held) speakReply(held.opts, held.el);
                else settlePhase('Nothing to send — tap mic to talk');
                return;
            }
            await send(spoken);
            if (held) speakReply(held.opts, held.el);
        }

        /** Screen off / app backgrounded: stop the mic but keep every phrase. */
        async function pauseListening() {
            if (engine === 'live') {
                // The shell owns backgrounding. An iframe presenter can disappear
                // during a Space switch without affecting the shell's call.
                // On the phone the microphone foreground service keeps the native
                // call alive with the screen off, so a screen lock must not hang up.
                if (!sharedVoice && !isMobileShell()) await live?.stop();
                return;
            }
            if (engine === 'realtime') {
                if (!sharedVoice && realtime) realtime.stop();
                settlePhase('Realtime paused — tap mic to reconnect');
                return;
            }
            if (phase !== 'listening' || closing) return;
            wantListening = false;
            await stopInput(true);
            await stopMicMonitor();
            renderPhrases();
            if (!active) return;
            settlePhase(Segments.isEmpty(phrases)
                ? 'Paused — tap mic to talk'
                : 'Paused — tap mic to keep talking; your phrases are kept');
        }

        function onVisibilityChange() {
            if (!document.hidden || !active) return;
            // The phone's microphone foreground service keeps a full-duplex call
            // (GPT-Live / Realtime) alive with the screen dimmed or off. Only a
            // desktop tab switch pauses/stops it; pausing here left the call open
            // while the orb showed "paused", so the next tap disconnected it.
            if (isMobileShell() && (engine === 'realtime' || engine === 'live')) return;
            if (engine === 'realtime') { pauseListening(); return; }
            if (phase === 'listening') pauseListening();
        }

        function toggleListening() {
            if (!active || closing) return;
            if (engine === 'live' && live?.isOpen()) { void finishListeningAndSend(); return; }
            if (engine === 'realtime' && realtime && realtime.isOpen()) {
                finishListeningAndSend();
                return;
            }
            if (phase === 'listening') {
                finishListeningAndSend();
                return;
            }
            startListening();
        }

        async function send(spoken) {
            const message = host.compose(spoken);
            if (!message || !host.isSendable(message)) {
                settlePhase('Nothing to send');
                return;
            }
            replyPending = true;
            if (host.isGenerating() && !host.isControlLane(message)) {
                appendTranscriptLine('user', spoken);
                if (!narration.open) Narrator.beginTurn(narration, message, Date.now());
                if (await host.steer(message)) {
                    setPhase('processing', 'Added to the running reply…');
                    narrateAck(message, 'steer');
                    return;
                }
                host.enqueue(message, spoken);
                setPhase('processing', 'Queued — waiting for current reply…');
                narrateAck(message, 'queue');
                return;
            }
            appendTranscriptLine('user', spoken);
            setPhase('processing', 'Thinking…');
            Narrator.beginTurn(narration, message, Date.now());
            narrateAck(message, 'new');
            try {
                await host.submit(message, spoken);
            } catch (e) {
                host.logError('Voice send failed', e);
                if (active) setPhase('idle', 'Send failed — tap mic to retry');
            }
        }

        // ── Spoken reply ────────────────────────────────────────────────
        async function speakReply(opts, heldEl) {
            if (!active && !replyPending) return;
            if (opts && opts.isError) {
                replyPending = false;
                if (active) settlePhase('Reply failed — tap mic to try again');
                return;
            }
            const messageEl = heldEl || host.lastAssistantMessage();
            if (!messageEl) { replyPending = false; return; }
            if (active && (phase === 'listening' || closing)) {
                // Never talk over the user: the reply plays once they send (or stop).
                deferredReply = { opts: opts || {}, el: messageEl };
                setStatus(listeningText());
                return;
            }
            const key = speakKey(messageEl);
            if (key && key === lastSpokenKey) return;
            if (key) lastSpokenKey = key;

            const token = ++speakToken;
            if (active) setPhase('speaking', 'Preparing speech…');
            let speech;
            try {
                speech = await host.speechFor(messageEl);
            } catch (e) {
                host.logError('Voice TTS failed', e);
                replyPending = false;
                if (token === speakToken && active) {
                    const raw = String((messageEl.dataset && messageEl.dataset.rawContent) || '');
                    appendTranscriptLine('assistant', raw.slice(0, 600));
                    settlePhase('Could not speak — transcript shown');
                }
                host.toast(String((e && e.message) || e), 'error');
                return;
            }
            if (!speech) {
                replyPending = false;
                settlePhase();
                return;
            }
            if (token !== speakToken || (!active && !replyPending)) return;

            let usageHtml = '';
            if (active) {
                usageHtml = voiceUsageHtml(messageEl, speech);
                appendTranscriptLine('assistant', speech.spoken, usageHtml);
                setPhase('speaking', 'Speaking…');
            }
            const outcome = await host.play(messageEl, speech);
            replyPending = false;
            if (active) renderSessionUsage();
            if (outcome === 'error') host.toast('Audio playback failed', 'error');
            if (token !== speakToken || !active) return;
            if (outcome === 'blocked') {
                setPhase('idle', 'Tap to enable sound, then use the mic');
                host.toast('Browser blocked autoplay — tap the mic once, then try again', 'error');
                return;
            }
            settlePhase('Tap the orb to talk');
        }

        function onGenerationStarted() {
            if (active && (phase === 'idle' || phase === 'processing')) setPhase('processing', 'Thinking…');
        }

        /** Live agent status line — the chat view's typing-status text. It lands
         *  in the transcript activity bubble, never as a bare center caption. */
        function onAgentStatus(text) {
            if (active && phase === 'processing') updateActivity(text);
            if (narratorOn && Narrator.noteStatus(narration, text)) scheduleProgress();
        }

        // ── Narration (experimental): acknowledgment + progress lines ────
        function clearNarrateTimer() {
            if (narrateTimer) {
                clearTimeout(narrateTimer);
                narrateTimer = 0;
            }
        }

        function endNarration() {
            clearNarrateTimer();
            Narrator.endTurn(narration);
        }

        /** Narration may speak while the agent works, never over the user or the reply. */
        function narrationAllowed(turn) {
            if (!Narrator.isCurrent(narration, turn)) return false;
            if (active) return phase === 'processing';
            return replyPending;
        }

        async function playNarration(result, turn) {
            if (!result) return;
            if (result.disabled) {
                narratorOn = false;
                endNarration();
                return;
            }
            if (!narrationAllowed(turn)) return;
            Narrator.noteSaid(narration, result.text, Date.now());
            if (active) appendTranscriptLine('narrator', result.text);
            Narrator.setSpeaking(narration, true);
            await host.playClip(result.url);
            if (Narrator.isCurrent(narration, turn)) {
                Narrator.setSpeaking(narration, false);
                scheduleProgress();
            }
        }

        async function narrateAck(message, mode) {
            if (!narratorOn) return;
            const turn = narration.turn;
            const result = await Narrator.request(host.fetch, { kind: 'ack', message: message, mode: mode, session_id:host.sessionId?.() });
            await playNarration(result, turn);
            scheduleProgress();
        }

        function scheduleProgress() {
            if (narrateTimer || !narratorOn) return;
            const wait = Narrator.dueIn(narration, Date.now());
            if (wait == null) return;
            narrateTimer = setTimeout(runProgress, wait);
        }

        async function runProgress() {
            narrateTimer = 0;
            const turn = narration.turn;
            if (!narrationAllowed(turn)) return;
            const payload = Narrator.takeProgress(narration, Date.now());
            if (!payload) {
                scheduleProgress();
                return;
            }
            const result = await Narrator.request(host.fetch, {...payload, session_id:host.sessionId?.()});
            if (!Narrator.isCurrent(narration, turn)) return;
            Narrator.finishRequest(narration);
            await playNarration(result, turn);
            scheduleProgress();
        }

        function onGenerationEnded(opts) {
            endNarration();
            if (engine === 'live') return;
            if (engine === 'realtime') {
                if (active && realtimeQueued && realtime) {
                    realtimeQueued = false;
                    const el = host.lastAssistantMessage();
                    if (el) realtime.agentResult(String(el.dataset.rawContent || el.textContent || ''));
                }
                return;
            }
            if (!active && !replyPending) return;
            const trySpeak = (attempt) => {
                if (!active && !replyPending) return;
                if (!host.lastAssistantMessage() && attempt < 6) {
                    setTimeout(() => trySpeak(attempt + 1), 180);
                    return;
                }
                speakReply(opts || {});
            };
            setTimeout(() => trySpeak(0), 100);
        }

        // ── Enter / exit ────────────────────────────────────────────────
        function enter(event) {
            if (event) {
                event.preventDefault();
                event.stopPropagation();
            }
            host.closeMenus();
            if (active) return;
            const overlay = overlayEl();
            if (!overlay) {
                host.toast('Voice mode UI missing — hard-refresh', 'error');
                return;
            }
            active = true;
            rememberVoiceMode(true);
            document.body.classList.add('voice-mode');
            overlay.hidden = false;
            overlay.setAttribute('aria-hidden', 'false');
            const canvas = byId('voiceModeStars');
            if (canvas && !stars) stars = Stars.create(canvas, overlay, () => phase);
            if (stars) stars.start();
            if (Waveform && !waveform) {
                waveform = Waveform.create({
                    bg: byId('voiceModeWaveBg'),
                    ring: byId('voiceModeWaveRing'),
                    level: voiceLevel,
                    phase: () => phase,
                });
            }
            if (waveform) {
                try { waveform.start(); } catch (_) {}
            }
            renderSessionUsage();
            const exitBtn = byId('voiceModeExitBtn');
            if (exitBtn) exitBtn.hidden = false;
            lastSpokenKey = '';
            replyPending = false;
            deferredReply = null;
            realtimeUsage = null;
            endNarration();
            narratorOn = false;
            flagsReady = host.experimentalFlags().then(async (on) => {
                if (!active) return;
                narratorOn = on.has('voice_narrator');
                const wantServer = on.has('voice_server_stt');
                const wantRealtime = on.has('voice_realtime');
                let selected = 'auto';
                if (Live) {
                    try { const r=await host.fetch('/api/settings/voice-chat',{cache:'no-store'});const d=await r.json();if(r.ok&&d.success)selected=d.engine; } catch (_) {}
                    if (!active) return;
                }
                const missing = wantServer ? Recorder.missingSupport() : [];
                engine = wantServer && !missing.length ? 'server' : 'webspeech';
                if (on.has('voice_live') && selected !== 'tts' && Live?.supported()) {
                    engine='live';narratorOn=false;
                    try {
                        const runtime=voiceRuntime();
                        const id=String(host.sessionId?.()||'').replace(/^db_session_/, '');
                        if(runtime?.isOpen(id)&&runtime.engine(id)==='live') {
                            sharedVoice=runtime;voiceChatId=id;detachVoice?.();detachVoice=runtime.attach(id,conversationCallbacks());
                            live={start:()=>runtime.start({session_id:id,engine:'live'}),stop:()=>runtime.engine(id)?runtime.stop():Promise.resolve(),isOpen:()=>runtime.isOpen(id)};
                        }
                    } catch (_) {}
                } else if (wantRealtime && selected !== 'tts' && Realtime && Realtime.supported()) {
                    engine = 'realtime';
                    narratorOn = false;
                    try {
                        const runtime=voiceRuntime();
                        const id=String(host.sessionId?.()||'').replace(/^db_session_/, '');
                        if(runtime?.isOpen(id)&&runtime.engine(id)==='realtime') {
                            sharedVoice=runtime;voiceChatId=id;detachVoice?.();detachVoice=runtime.attach(id,conversationCallbacks());
                            realtime={start:()=>runtime.start({session_id:id,engine:'realtime'}),stop:()=>runtime.engine(id)?runtime.stop():Promise.resolve(),isOpen:()=>runtime.isOpen(id)};
                        }
                    } catch (_) {}
                } else if (selected !== 'tts' && (wantRealtime || on.has('voice_live'))) {
                    // Realtime needs a secure context (HTTPS) for getUserMedia —
                    // on cleartext LAN HTTP the mobile WebView hides it entirely.
                    host.toast(root.isSecureContext === false
                        ? 'The selected voice engine needs a secure connection or an updated Android app; using existing voice input'
                        : 'The selected voice engine needs microphone support; using existing voice input', 'error');
                }
                showEngine(wantServer, missing);
            }, () => {});
            hideActivity();
            clearPhrases();
            if (host.isGenerating()) setPhase('processing', 'Working…');
            else setPhase('idle');
            if (!escapeHandler) {
                escapeHandler = function (e) {
                    if (e.key === 'Escape' && active) {
                        e.preventDefault();
                        exit();
                    }
                };
                document.addEventListener('keydown', escapeHandler);
            }
        }

        function exit(event) {
            if (event) {
                event.preventDefault();
                event.stopPropagation();
            }
            if (!active) return;
            const wasRunning = phase === 'processing';
            active = false;
            rememberVoiceMode(false);
            // Leaving voice view only detaches this presenter. A shell-owned
            // call keeps running (the shell bar carries Mute/End, and the mic
            // entry re-attaches). Stopping it here is what dropped the call
            // whenever the user looked at the chat.
            if (!sharedVoice) {
                if (realtime) realtime.stop();
                if (live) void live.stop();
            }
            detachVoice?.();detachVoice=null;
            realtimeQueued = false;
            wantListening = false;
            stopInput(false);
            stopMicMonitor();
            speaking = false;
            host.stopSpeech();
            speakToken += 1;
            deferredReply = null;
            if (!wasRunning) {
                replyPending = false;
                endNarration();
            }
            clearPhrases();
            hideActivity();
            setPhase('idle');
            document.body.classList.remove('voice-mode');
            if (stars) stars.stop();
            if (waveform) {
                try { waveform.stop(); } catch (_) {}
            }
            const exitBtn = byId('voiceModeExitBtn');
            if (exitBtn) exitBtn.hidden = true;
            const overlay = overlayEl();
            if (overlay) {
                overlay.hidden = true;
                overlay.setAttribute('aria-hidden', 'true');
                overlay.classList.remove('is-listening', 'is-processing', 'is-speaking');
            }
            if (escapeHandler) {
                document.removeEventListener('keydown', escapeHandler);
                escapeHandler = null;
            }
        }

        // ── Mic button: tap toggles, press-and-hold talks until release ──
        function clearHoldTimer() {
            if (holdTimer) {
                clearTimeout(holdTimer);
                holdTimer = 0;
            }
        }

        function onMicPointerDown(e) {
            if (!active) return;
            if (engine === 'realtime') return;
            if (e && e.pointerType === 'mouse' && typeof e.button === 'number' && e.button !== 0) return;
            if (closing) return;
            holdDown = true;
            holdOwner = false;
            clearHoldTimer();
            holdTimer = setTimeout(() => {
                holdTimer = 0;
                if (!holdDown || !active || closing) return;
                if (phase !== 'listening') toggleListening();
                holdOwner = true;
            }, HOLD_MS);
            try {
                const mic = byId('voiceModeMicBtn');
                if (mic && e && e.pointerId !== undefined && mic.setPointerCapture) {
                    mic.setPointerCapture(e.pointerId);
                }
            } catch (_) {}
        }

        function onMicPointerUp() {
            if (!holdDown && !holdOwner) return;
            holdDown = false;
            clearHoldTimer();
            if (holdOwner) {
                holdOwner = false;
                suppressClick = true;
                if (active && phase === 'listening') finishListeningAndSend();
            }
        }

        function wire() {
            root.addEventListener('cuttle-session-usage-updated',renderSessionUsage);
            root.addEventListener('pagehide',()=>{detachVoice?.();detachVoice=null;if(!sharedVoice)void live?.stop();});
            const mic = byId('voiceModeMicBtn');
            if (mic) {
                mic.addEventListener('click', function (e) {
                    e.preventDefault();
                    if (suppressClick) {
                        suppressClick = false;
                        return;
                    }
                    toggleListening();
                });
                mic.addEventListener('pointerdown', onMicPointerDown);
                mic.addEventListener('pointerup', onMicPointerUp);
                mic.addEventListener('pointercancel', onMicPointerUp);
                mic.addEventListener('contextmenu', function (e) { e.preventDefault(); });
            }
            const segments = byId('voiceModeSegments');
            if (segments) segments.addEventListener('click', onPhraseClick);
            document.addEventListener('visibilitychange', onVisibilityChange);
            const exitBtn = byId('voiceModeExitBtn');
            if (exitBtn) exitBtn.addEventListener('click', exit);
            const watchBtn = byId('voiceModeWatchBtn');
            if (watchBtn) {
                watchBtn.addEventListener('click', function (e) {
                    e.preventDefault();
                    exit();
                });
            }
            root.addEventListener('cuttle-toast-action', function (e) {
                if (e && e.detail && e.detail.actionId === 'voice-mic-retry') onMicToastAction();
            });
            root.addEventListener('message', function (e) {
                if (e && e.data && e.data.type === 'cuttle-toast-action'
                    && e.data.actionId === 'voice-mic-retry') {
                    onMicToastAction();
                }
            });
        }

        wire();

        // The shell reloads this chat's frame on a space switch; restore the
        // voice view the user left (the live mic session itself is re-opened on
        // the next mic tap, never silently).
        if (voiceModeRemembered()) { try { enter(); } catch (_) {} }

        return {
            enter: enter,
            exit: exit,
            isActive: () => active,
            onGenerationStarted: onGenerationStarted,
            onGenerationEnded: onGenerationEnded,
            onAgentStatus: onAgentStatus,
        };
    }

    const api = { create: create, speakKey: speakKey };
    root.CuttleChatVoice = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
