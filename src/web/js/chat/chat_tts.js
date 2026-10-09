/* ================================================================
   Cuttle Chat — bubble speech playback (chat_tts.js).
   Owner: chat TTS prefs (`/api/settings/chat-tts`: speaker visibility),
   the bubble speaker button (`/api/chat/tts` fetch, per-bubble audio cache,
   play / stop toggle), the spoken Transcript layout, and the ONE shared audio
   channel that bubble speakers, voice-mode replies and narration clips all
   play through (plus its playback level meter for the voice ring).

   Voice mode (`chat_voice.js`) does not own audio: the page hands it this
   controller's speechForMessage / playSpeech / playAudio / stop /
   playbackLevel as host capabilities, so starting one sound always stops
   the other.

   Interface: `create(deps)` → one controller per chat frame.
   deps: { win, fetch, toast(message, variant), speakTextFor(btn),
           normalizeUsage(usage) }. The page keeps message text extraction
   (speakTextFor) and usage normalization; it calls loadPrefs() at boot and
   exports speakFromButton as `window.chatPageSpeakMessage`.
   Loaded before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    const TTS_PLAY_ICON = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>`;
    const TTS_STOP_ICON = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>`;
    const TTS_LOADING_ICON = `<svg class="message-tts-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9" opacity="0.25"/><path d="M21 12a9 9 0 0 0-9-9"/></svg>`;

    /**
     * One TTS controller per chat frame.
     * @param {{win: Window, fetch: Function, toast: Function,
     *          speakTextFor: Function, normalizeUsage: Function}} deps
     */
    function create(deps) {
        const frameWin = deps.win;
        const frameDoc = frameWin.document;
        const fetchFn = deps.fetch;
        const toast = deps.toast;
        const speakTextFor = deps.speakTextFor;
        const normalizeUsage = deps.normalizeUsage;

        /** Cached chat TTS prefs (play button visibility). */
        let chatTtsPrefs = { enabled: true };

        function applyChatTtsPrefsToUi() {
            const root = frameDoc.getElementById('chatMessages');
            if (root) root.classList.toggle('chat-tts-disabled', !chatTtsPrefs.enabled);
            frameDoc.querySelectorAll('.message-tts-btn').forEach((btn) => {
                btn.hidden = !chatTtsPrefs.enabled;
            });
        }

        function loadChatTtsPrefs() {
            fetchFn('/api/settings/chat-tts')
                .then((r) => (r.ok ? r.json() : null))
                .then((d) => {
                    if (d && d.success && d.chat_tts) {
                        chatTtsPrefs = {
                            enabled: d.chat_tts.enabled !== false,
                            tts_model: d.chat_tts.tts_model,
                            voice: d.chat_tts.voice,
                            summarize: d.chat_tts.summarize !== false,
                        };
                    }
                    applyChatTtsPrefsToUi();
                })
                .catch(() => {});
        }

        function getTtsPlayButtonHtml() {
            const hidden = chatTtsPrefs && chatTtsPrefs.enabled === false ? ' hidden' : '';
            return (
                `<button type="button" class="message-tts-btn message-icon-btn"${hidden} ` +
                `title="Listen (summarize + speak)" aria-label="Listen to message" ` +
                `onclick="window.chatPageSpeakMessage(this)">${TTS_PLAY_ICON}</button>`
            );
        }

        let _chatTtsAudio = null;
        let _chatTtsActiveBtn = null;

        function _resetTtsButton(btn) {
            if (!btn) return;
            btn.classList.remove('is-loading', 'is-playing');
            btn.innerHTML = TTS_PLAY_ICON;
            btn.title = 'Listen (summarize + speak)';
            btn.disabled = false;
        }

        function stopChatTtsPlayback(exceptBtn) {
            if (_chatTtsAudio) {
                try {
                    _chatTtsAudio.pause();
                    _chatTtsAudio.onended = null;
                    _chatTtsAudio.onerror = null;
                    _chatTtsAudio.removeAttribute('src');
                    _chatTtsAudio.load();
                } catch (_) {}
                _chatTtsAudio = null;
            }
            if (_chatTtsActiveBtn && _chatTtsActiveBtn !== exceptBtn) {
                _resetTtsButton(_chatTtsActiveBtn);
            }
            if (_chatTtsActiveBtn !== exceptBtn) {
                _chatTtsActiveBtn = null;
            }
        }

        /**
         * On first successful speak: show expanded Transcript (spoken text) above
         * Progress, and collapse the original body under "Full summary".
         */
        function applyTtsTranscriptLayout(messageEl, spokenText) {
            const content = messageEl && messageEl.querySelector
                ? messageEl.querySelector('.message-content')
                : null;
            const spoken = String(spokenText || '').trim();
            if (!content || !spoken) return;

            let transcript = content.querySelector(':scope > .tts-transcript-block');
            if (transcript) {
                const line = transcript.querySelector('.tts-transcript-body');
                if (line) line.textContent = spoken;
                transcript.open = true;
                const full = content.querySelector(':scope > .tts-full-summary-block');
                if (full) full.open = false;
                return;
            }

            const keepClass = (el) =>
                el.classList.contains('thinking-block')
                || el.classList.contains('tool-block')
                || el.classList.contains('pipeline-trace-block')
                || el.classList.contains('tts-transcript-block')
                || el.classList.contains('tts-full-summary-block');

            const toWrap = [];
            Array.from(content.childNodes).forEach((node) => {
                if (node.nodeType === 1) {
                    if (!keepClass(node)) toWrap.push(node);
                } else if (node.nodeType === 3 && String(node.textContent || '').trim()) {
                    toWrap.push(node);
                }
            });

            transcript = frameDoc.createElement('details');
            transcript.className = 'thinking-block tts-transcript-block';
            transcript.open = true;
            const tSum = frameDoc.createElement('summary');
            tSum.textContent = 'Transcript';
            const tInner = frameDoc.createElement('div');
            tInner.className = 'thinking-inner';
            const tLine = frameDoc.createElement('div');
            tLine.className = 'thinking-line tts-transcript-body';
            tLine.textContent = spoken;
            tInner.appendChild(tLine);
            transcript.appendChild(tSum);
            transcript.appendChild(tInner);

            const full = frameDoc.createElement('details');
            full.className = 'thinking-block tts-full-summary-block';
            full.open = false;
            const fSum = frameDoc.createElement('summary');
            fSum.textContent = 'Full summary';
            const fBody = frameDoc.createElement('div');
            fBody.className = 'tts-full-summary-body thinking-inner';
            toWrap.forEach((n) => fBody.appendChild(n));
            full.appendChild(fSum);
            full.appendChild(fBody);

            // Transcript first (above Progress); full body last (collapsed).
            content.insertBefore(transcript, content.firstChild);
            content.appendChild(full);
            messageEl.classList.add('has-tts-transcript');
        }

        async function speakMessageFromButton(btn) {
            const messageEl = btn && btn.closest ? btn.closest('.message') : null;
            if (!messageEl || !messageEl.classList.contains('assistant')) return;

            // Toggle stop if this bubble is already playing.
            if (btn.classList.contains('is-playing') && _chatTtsActiveBtn === btn) {
                stopChatTtsPlayback();
                _resetTtsButton(btn);
                return;
            }
            stopChatTtsPlayback(btn);

            // Reuse cached audio for this bubble.
            const cachedUrl = messageEl._cuttleTtsUrl;
            if (cachedUrl) {
                if (messageEl._cuttleTtsSpoken) {
                    applyTtsTranscriptLayout(messageEl, messageEl._cuttleTtsSpoken);
                }
                _playTtsUrl(btn, cachedUrl, {
                    summarized: !!messageEl._cuttleTtsSummarized,
                });
                return;
            }

            const text = speakTextFor(btn);
            if (!text || !String(text).trim()) {
                toast('Nothing to speak in this message', 'error');
                return;
            }

            btn.classList.add('is-loading');
            btn.disabled = true;
            btn.innerHTML = TTS_LOADING_ICON;
            btn.title = 'Generating speech…';
            _chatTtsActiveBtn = btn;

            try {
                const r = await fetchFn('/api/chat/tts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text }),
                });
                const d = await r.json().catch(() => ({}));
                if (!r.ok || !d.success || !d.audio_base64) {
                    throw new Error((d && d.error) || ('TTS failed (' + r.status + ')'));
                }
                const bin = atob(d.audio_base64);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                const blob = new Blob([bytes], { type: d.content_type || 'audio/mpeg' });
                const url = URL.createObjectURL(blob);
                messageEl._cuttleTtsUrl = url;
                messageEl._cuttleTtsSpoken = d.spoken_text || '';
                messageEl._cuttleTtsSummarized = !!d.summarized;
                applyTtsTranscriptLayout(messageEl, messageEl._cuttleTtsSpoken);
                _playTtsUrl(btn, url, { summarized: !!d.summarized });
            } catch (e) {
                _resetTtsButton(btn);
                _chatTtsActiveBtn = null;
                toast(String(e.message || e), 'error');
            }
        }

        function _playTtsUrl(btn, url, meta) {
            meta = meta || {};
            stopChatTtsPlayback(btn);
            const audio = new Audio(url);
            _chatTtsAudio = audio;
            _chatTtsActiveBtn = btn;
            btn.classList.remove('is-loading');
            btn.classList.add('is-playing');
            btn.disabled = false;
            btn.innerHTML = TTS_STOP_ICON;
            btn.title = meta.summarized ? 'Playing summary — click to stop' : 'Stop';
            audio.onended = function () {
                if (_chatTtsActiveBtn === btn) {
                    _resetTtsButton(btn);
                    _chatTtsActiveBtn = null;
                }
                _chatTtsAudio = null;
                if (typeof meta.onEnded === 'function') {
                    try { meta.onEnded(); } catch (_) {}
                }
            };
            audio.onerror = function () {
                _resetTtsButton(btn);
                _chatTtsActiveBtn = null;
                _chatTtsAudio = null;
                toast('Audio playback failed', 'error');
                if (typeof meta.onError === 'function') {
                    try { meta.onError(); } catch (_) {}
                }
            };
            audio.play().catch((err) => {
                _resetTtsButton(btn);
                _chatTtsActiveBtn = null;
                _chatTtsAudio = null;
                toast(String(err.message || err), 'error');
                if (typeof meta.onError === 'function') {
                    try { meta.onError(err); } catch (_) {}
                }
            });
            if (typeof meta.onPlay === 'function') {
                try { meta.onPlay(audio); } catch (_) {}
            }
        }

        /** Cached per bubble, shared with the bubble's speaker button. */
        async function voiceSpeechForMessage(messageEl) {
            if (messageEl._cuttleTtsUrl) {
                return {
                    url: messageEl._cuttleTtsUrl,
                    spoken: messageEl._cuttleTtsSpoken || '',
                    summarized: !!messageEl._cuttleTtsSummarized,
                    voiceUsage: messageEl._cuttleVoiceUsage || null,
                };
            }
            const btn = messageEl.querySelector('.message-tts-btn');
            const text = btn
                ? speakTextFor(btn)
                : String(messageEl.dataset.rawContent || '').trim();
            if (!text) return null;
            const r = await fetchFn('/api/chat/tts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok || !d.success || !d.audio_base64) {
                throw new Error((d && d.error) || ('TTS failed (' + r.status + ')'));
            }
            const bin = atob(d.audio_base64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            const blob = new Blob([bytes], { type: d.content_type || 'audio/mpeg' });
            messageEl._cuttleTtsUrl = URL.createObjectURL(blob);
            messageEl._cuttleTtsSpoken = d.spoken_text || text;
            messageEl._cuttleTtsSummarized = !!d.summarized;
            try {
                messageEl._cuttleVoiceUsage = (d.usage && typeof d.usage === 'object')
                    ? normalizeUsage(d.usage)
                    : null;
            } catch (_) {
                messageEl._cuttleVoiceUsage = null;
            }
            applyTtsTranscriptLayout(messageEl, messageEl._cuttleTtsSpoken);
            return {
                url: messageEl._cuttleTtsUrl,
                spoken: messageEl._cuttleTtsSpoken,
                summarized: messageEl._cuttleTtsSummarized,
                voiceUsage: messageEl._cuttleVoiceUsage || null,
            };
        }

        function playVoiceSpeech(messageEl, speech) {
            const btn = messageEl.querySelector('.message-tts-btn');
            return playVoiceAudio(speech.url, btn, !!speech.summarized);
        }

        /** Playback analyser feeding the voice ring; null-safe when unavailable. */
        let _voiceAudioCtx = null;
        let _voicePlaybackAnalyser = null;
        let _voicePlaybackFrame = null;

        function voicePlaybackLevel() {
            if (!_voicePlaybackAnalyser || !_voicePlaybackFrame) return null;
            try {
                _voicePlaybackAnalyser.getFloatTimeDomainData(_voicePlaybackFrame);
                let sum = 0;
                for (let i = 0; i < _voicePlaybackFrame.length; i++) {
                    sum += _voicePlaybackFrame[i] * _voicePlaybackFrame[i];
                }
                return Math.sqrt(sum / (_voicePlaybackFrame.length || 1));
            } catch (_) {
                return null;
            }
        }

        /** One audio channel for replies, narration and bubble speakers. */
        function playVoiceAudio(url, btn, summarized) {
            stopChatTtsPlayback(btn || null);
            const audio = new Audio(url);
            _chatTtsAudio = audio;
            try {
                const Ctx = frameWin.AudioContext || frameWin.webkitAudioContext;
                if (Ctx) {
                    if (!_voiceAudioCtx) _voiceAudioCtx = new Ctx();
                    if (_voiceAudioCtx && _voiceAudioCtx.state === 'suspended') {
                        _voiceAudioCtx.resume().catch(() => {});
                    }
                    const src = _voiceAudioCtx.createMediaElementSource(audio);
                    const analyser = _voiceAudioCtx.createAnalyser();
                    analyser.fftSize = 1024;
                    src.connect(analyser);
                    analyser.connect(_voiceAudioCtx.destination);
                    _voicePlaybackAnalyser = analyser;
                    _voicePlaybackFrame = new Float32Array(analyser.fftSize);
                }
            } catch (_) {
                _voicePlaybackAnalyser = null;
                _voicePlaybackFrame = null;
            }
            if (btn) {
                _chatTtsActiveBtn = btn;
                btn.classList.remove('is-loading');
                btn.classList.add('is-playing');
                btn.disabled = false;
                btn.innerHTML = TTS_STOP_ICON;
                btn.title = summarized ? 'Playing summary — click to stop' : 'Stop';
            }
            return new Promise((resolve) => {
                let settled = false;
                const finish = (outcome) => {
                    if (settled) return;
                    settled = true;
                    if (btn && _chatTtsActiveBtn === btn) {
                        _resetTtsButton(btn);
                        _chatTtsActiveBtn = null;
                    }
                    if (_chatTtsAudio === audio) _chatTtsAudio = null;
                    _voicePlaybackAnalyser = null;
                    _voicePlaybackFrame = null;
                    resolve(outcome);
                };
                audio.onended = () => finish('ended');
                audio.onerror = () => finish('error');
                // stopChatTtsPlayback pauses and drops onended; settle so the caller never hangs.
                audio.addEventListener('pause', () => finish(audio.ended ? 'ended' : 'stopped'));
                audio.play().catch(() => finish('blocked'));
            });
        }

        return {
            loadPrefs: loadChatTtsPrefs,
            playButtonHtml: getTtsPlayButtonHtml,
            speakFromButton: speakMessageFromButton,
            stop: stopChatTtsPlayback,
            speechForMessage: voiceSpeechForMessage,
            playSpeech: playVoiceSpeech,
            playAudio: playVoiceAudio,
            playbackLevel: voicePlaybackLevel,
        };
    }

    const api = { create };

    const ns = (root.CuttleChatTts = root.CuttleChatTts || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
})(globalThis);
