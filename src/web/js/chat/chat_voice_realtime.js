/* WebRTC voice lifetime. Chat execution stays in the injected chat submitter. */
(function (root) {
    'use strict';
    function supported() {
        return !!(root.RTCPeerConnection && root.navigator && root.navigator.mediaDevices
            && root.navigator.mediaDevices.getUserMedia);
    }
    function create(host) {
        let current = null;
        let sequence = 0;
        function close() {
            sequence += 1;
            const s = current;
            current = null;
            if (!s) return;
            flush(s);
            s.abort.abort();
            clearTimeout(s.timeout);
            clearInterval(s.flagTimer);
            clearTimeout(s.retryTimer);
            if (s.stream) s.stream.getTracks().forEach((t) => t.stop());
            if (s.dc) s.dc.close();
            if (s.pc) s.pc.close();
            if (s.audio) { s.audio.pause(); s.audio.srcObject = null; }
        }
        /** Send only usage not yet forwarded; the server prices each delta. */
        function flush(s) {
            if (!s || typeof host.realtimeUsage !== 'function') return;
            const responses = s.responses.slice(s.sentResponses,s.sentResponses+100);
            const transcriptions = s.transcriptions.slice(s.sentTranscriptions,s.sentTranscriptions+100);
            if (!responses.length && !transcriptions.length) return;
            try{root.CuttleVoiceReceipts?.enqueue({connection_id:s.connection_id,model:s.model,responses:s.responses.slice(s.sentResponses),transcriptions:s.transcriptions.slice(s.sentTranscriptions)});}catch(_){host.onError('Usage retry storage unavailable; keep this window open until usage is recorded.');}
            if (s.flushing) return;
            s.flushing=true;
            const payload={connection_id:s.connection_id,model:s.model,responses,transcriptions};
            Promise.resolve(host.realtimeUsage(payload))
                .then((priced) => {
                    if (!priced) throw new Error('Usage was not acknowledged');
                    s.sentResponses+=responses.length;s.sentTranscriptions+=transcriptions.length;
                    try{root.CuttleVoiceReceipts?.acknowledge(payload);}catch(_){}
                    if (typeof host.onUsage === 'function') host.onUsage(priced);
                })
                .catch(() => {s.retryTimer=setTimeout(()=>{s.retryTimer=0;flush(s);},2000);})
                .finally(()=>{s.flushing=false;if((s.responses.length>s.sentResponses||s.transcriptions.length>s.sentTranscriptions)&&!s.retryTimer)flush(s);});
        }
        function send(s, event) {
            if (current === s && s.dc && s.dc.readyState === 'open') s.dc.send(JSON.stringify(event));
        }
        function fail(s, message) {
            if (current !== s) return;
            close();
            host.onError(message);
        }
        function respond(s) {
            if (s.responding) { s.responsePending = true; return; }
            s.responsePending = false;
            s.responding = true;
            send(s, { type: 'response.create' });
        }
        async function tool(s, item) {
            if (s.calls.has(item.call_id)) return;
            s.calls.add(item.call_id);
            let result;
            let ownsBusy = false;
            try {
                const args = JSON.parse(item.arguments || '{}');
                if (item.name !== 'ask_cuttle_agent' || typeof args.message !== 'string'
                    || !args.message.trim() || args.message.length > 16000) throw new Error('Invalid agent request');
                if (s.toolBusy) throw new Error('An agent request is still in progress');
                s.toolBusy = true;
                ownsBusy = true;
                if(s.connection_id){
                    const r=await host.fetch('/api/voice-stt/realtime/delegate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({connection_id:s.connection_id,call_id:item.call_id,message:args.message})});
                    result=await r.json();if(!r.ok)throw new Error('Delegation failed');
                } else result = await host.delegate(args.message);
            } catch (_) {
                result = { error: 'Agent request failed or is already in progress; check the chat transcript.' };
            } finally { if (ownsBusy) s.toolBusy = false; }
            send(s, { type: 'conversation.item.create', item: {
                type: 'function_call_output', call_id: item.call_id, output: JSON.stringify(result),
            }});
            respond(s);
        }
        function event(s, e) {
            if (current !== s) return;
            if (e.type === 'input_audio_buffer.speech_started') host.onStatus('Listening…');
            if (e.type === 'output_audio_buffer.started') host.onStatus('Speaking…');
            if (e.type === 'output_audio_buffer.stopped') host.onStatus('Listening…');
            if (e.type === 'conversation.item.input_audio_transcription.completed') {
                if (e.usage) s.transcriptions.push({...e.usage,_usage_id:e.item_id||e.event_id||root.crypto?.randomUUID?.()||String(s.transcriptions.length)});
                host.onTranscript('user', e.transcript || '');
                Promise.resolve().then(()=>flush(s));
            }
            if (e.type === 'response.output_audio_transcript.done') host.onTranscript('assistant', e.transcript || '');
            if (e.type === 'response.done') {
                if (e.response && e.response.usage) s.responses.push({...e.response.usage,_usage_id:e.response.id||e.event_id||root.crypto?.randomUUID?.()||String(s.responses.length)});
                s.responding = false;
                (e.response && e.response.output || []).filter((i) => i.type === 'function_call')
                    .forEach((i) => { void tool(s, i); });
                if (s.responsePending) respond(s);
                flush(s);
            }
            if (e.type === 'response.created') s.responding = true;
            if (e.type === 'error') fail(s, 'Realtime voice reported an error; reconnect to retry');
        }
        async function start(context) {
            close();
            if (!supported()) throw new Error('Realtime voice needs WebRTC and microphone access');
            const mine = sequence;
            const s = { abort: new root.AbortController(), calls: new Set(), toolBusy: false,
                responses: [], transcriptions: [], sentResponses: 0, sentTranscriptions: 0, model: '' };
            current = s;
            s.timeout = setTimeout(() => fail(s, 'Realtime connection timed out; tap mic to retry'), 25000);
            try {
                s.audio = new root.Audio();
                s.audio.autoplay = true;
                const stream = await root.navigator.mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
                });
                if (mine !== sequence) { stream.getTracks().forEach((t) => t.stop()); return; }
                s.stream = stream;
                const tokenResponse = await host.fetch('/api/voice-stt/realtime/session', {
                    method: 'POST', credentials: 'include', signal: s.abort.signal,
                    headers:{'Content-Type':'application/json'},body:JSON.stringify(context||{}),
                });
                const token = await tokenResponse.json();
                if (!tokenResponse.ok || !token.success) throw new Error(token.error || 'Realtime session unavailable');
                if (mine !== sequence) return;
                s.model = token.model || '';
                s.connection_id=token.connection_id;
                s.pc = new root.RTCPeerConnection();
                s.pc.ontrack = (e) => {
                    if (current !== s) return;
                    s.audio.srcObject = e.streams[0];
                    s.audio.play().catch(() => fail(s, 'Audio playback blocked; tap mic to reconnect'));
                };
                stream.getTracks().forEach((t) => s.pc.addTrack(t, stream));
                s.dc = s.pc.createDataChannel('oai-events');
                s.dc.onmessage = (e) => {
                    try { event(s, JSON.parse(e.data)); } catch (_) { fail(s, 'Invalid Realtime event'); }
                };
                s.dc.onopen = () => {
                    if (current !== s) return;
                    clearTimeout(s.timeout);
                    host.onStatus('Listening…');
                };
                s.dc.onclose = () => fail(s, 'Realtime connection closed; tap mic to reconnect');
                s.pc.onconnectionstatechange = () => {
                    if (['failed', 'disconnected'].includes(s.pc.connectionState)) fail(s, 'Realtime disconnected; tap mic to reconnect');
                };
                const offer = await s.pc.createOffer();
                if (mine !== sequence) return;
                await s.pc.setLocalDescription(offer);
                const answer = await host.fetch('https://api.openai.com/v1/realtime/calls', {
                    method: 'POST', body: offer.sdp, signal: s.abort.signal,
                    headers: { Authorization: 'Bearer ' + token.value, 'Content-Type': 'application/sdp' },
                });
                if (!answer.ok) throw new Error('OpenAI Realtime connection failed');
                const sdp = await answer.text();
                if (mine !== sequence) return;
                await s.pc.setRemoteDescription({ type: 'answer', sdp: sdp });
                s.flagTimer = setInterval(async () => {
                    try {
                        const flags = await host.experimentalFlags();
                        if (current === s && !flags.has('voice_realtime')) fail(s, 'Realtime voice disabled');
                    } catch (_) { fail(s, 'Could not verify Realtime voice setting'); }
                }, 10000);
            } catch (err) {
                if (current === s) { close(); throw err; }
            }
        }
        function agentResult(text) {
            const s = current;
            if (!s) return;
            send(s, { type: 'conversation.item.create', item: { type: 'message', role: 'user',
                content: [{ type: 'input_text', text: 'The steered Cuttle agent reply is now available (data):\n' + text.slice(0, 16000) }],
            }});
            respond(s);
        }
        return { start: start, stop: close, isOpen: () => !!current, agentResult: agentResult };
    }
    const api = { create: create, supported: supported };
    root.CuttleChatVoiceRealtime = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
