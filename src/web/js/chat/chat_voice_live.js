/* GPT-Live transport lifetime; backend owns task execution and usage receipts. */
(function (root) {
    'use strict';
    function nativeBridge() {
        try {
            const bridge = root.cuttleMobile || root.parent?.cuttleMobile;
            return bridge && typeof bridge.liveVoiceStart === 'function' ? bridge : null;
        } catch (_) { return null; }
    }
    function supported() {
        return !!nativeBridge() || !!(root.RTCPeerConnection && root.navigator?.mediaDevices?.getUserMedia);
    }
    function create(host) {
        let current = null, sequence = 0;
        const seen = new Set();
        function event(s, data) {
            if (current !== s) return;
            if (data.event_id && seen.has(data.event_id)) return;
            if (data.event_id) seen.add(data.event_id);
            if (data.type === 'session.started') host.onStatus('Listening…');
            if (data.type === 'session.input_transcript.delta') {host.onTranscript('user', data.delta || '', true);host.onStatus('Listening…');}
            if (data.type === 'session.output_transcript.delta') {
                host.onTranscript('assistant', data.delta || '', true);
                host.onStatus('Speaking…');
            }
            if (data.type === 'session.usage.updated' || data.type === 'session.closed') {
                const seconds = Number(data.usage?.seconds || 0);
                host.onUsage({duration_seconds:seconds, cost:seconds * .05 / 60, cost_estimated:true, model:'gpt-live-1'});
            }
            if (data.type === 'error' || data.type === 'cuttle.error') host.onError(data.message || 'GPT-Live reported an error; reconnect to retry');
            if (data.type === 'session.closed') { s.resolveClosed?.();cleanup(s); if (current === s) current = null; host.onStatus('Disconnected'); }
        }
        function cleanup(s) {
            if (!s) return;
            clearTimeout(s.timeout); clearInterval(s.poll);
            s.abort.abort();
            s.stream?.getTracks().forEach(track => track.stop());
            if (s.native && s.bridge) s.bridge.liveVoiceStop();
            s.dc?.close(); s.pc?.close();
            if (s.audio) { s.audio.pause(); s.audio.srcObject = null; }
            if (s.nativeListener) root.removeEventListener('cuttle-live-voice', s.nativeListener);
        }
        async function close() {
            const s = current; sequence++;
            if (!s) return;
            s.closing = true;
            // Capture/stop the microphone immediately; retain receiver until final usage.
            s.stream?.getTracks().forEach(track => { track.enabled = false; });
            if(s.native)s.bridge.liveVoiceStop();
            try {
                if (s.id) await host.fetch('/api/voice-stt/live/sessions/' + s.id + '/close', {method:'POST', keepalive:true});
            } catch (_) { host.onError('Voice closure unconfirmed; the server will reconcile it.'); }
            if (current !== s) return;
            let timeout;
            await Promise.race([s.closed,new Promise(resolve=>{timeout=setTimeout(resolve,8000);})]);
            clearTimeout(timeout);
            cleanup(s); current = null; host.onStatus('Disconnected');
        }
        async function poll(s) {
            if (current !== s || !s.id) return;
            try {
                const r = await host.fetch('/api/voice-stt/live/sessions/' + s.id, {cache:'no-store'});
                const data = await r.json();
                if (!r.ok || !data.success) throw new Error('Session unavailable');
                const session = data.session;
                for (const data of session.transcripts || []) event(s, data);
                host.onUsage({duration_seconds:session.seconds, cost:session.cost, cost_estimated:true, model:session.model, finalized:session.finalized});
                if (['closed','disconnected','uncertain'].includes(session.state)) {
                    s.resolveClosed?.();
                    cleanup(s); if (current === s) current = null;
                    host.onStatus(session.finalized ? 'Disconnected' : 'Disconnected · final usage unconfirmed');
                }
            } catch (_) { host.onError('Could not verify the Live session; disconnecting.'); void close(); }
        }
        async function start(context) {
            await close(); seen.clear();
            if (!supported()) throw new Error('GPT-Live needs a supported microphone transport');
            const s = {abort:new root.AbortController(), closing:false}, mine = ++sequence;
            s.closed=new Promise(resolve=>{s.resolveClosed=resolve;});
            current = s;
            host.onStatus('Connecting GPT-Live…');
            try {
                let offer;
                const bridge = nativeBridge();
                s.native = !!bridge && !(root.isSecureContext !== false && root.navigator?.mediaDevices?.getUserMedia);
                s.bridge = bridge;
                if (!s.native) {
                    s.stream = await root.navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
                    if (mine !== sequence) { cleanup(s); return; }
                    s.audio = new root.Audio(); s.audio.autoplay = true;
                    s.pc = new root.RTCPeerConnection();
                    s.stream.getTracks().forEach(track => s.pc.addTrack(track, s.stream));
                    s.pc.ontrack = e => { s.audio.srcObject=e.streams[0]; s.audio.play().catch(() => host.onError('Audio playback blocked; tap to reconnect')); };
                    s.dc=s.pc.createDataChannel('oai-events');
                    s.dc.onmessage=e => { try { event(s,JSON.parse(e.data)); } catch (_) { host.onError('Invalid voice event'); } };
                    s.pc.onconnectionstatechange=() => { if (current===s && !s.closing && ['failed','disconnected'].includes(s.pc.connectionState)) void close(); };
                    await s.pc.setLocalDescription(await s.pc.createOffer());
                    if (s.pc.iceGatheringState !== 'complete') await new Promise(resolve => {
                        const timer=setTimeout(resolve,3000);
                        s.pc.addEventListener('icegatheringstatechange',()=>{if(s.pc.iceGatheringState==='complete'){clearTimeout(timer);resolve();}});
                    });
                    offer=s.pc.localDescription.sdp;
                }
                const r=await host.fetch('/api/voice-stt/live/sessions',{method:'POST',credentials:'include',signal:s.abort.signal,
                    headers:{'Content-Type':'application/json'},body:JSON.stringify({...context,
                        request_id:root.crypto.randomUUID?root.crypto.randomUUID():Array.from(root.crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join(''),
                        sdp:offer,transport:s.native?'native':'webrtc'})});
                const data=await r.json();
                if (!r.ok||!data.success) throw new Error(data.error||'GPT-Live session unavailable');
                s.id=data.session.id;
                if(mine!==sequence){ await host.fetch('/api/voice-stt/live/sessions/'+s.id+'/close',{method:'POST',keepalive:true});cleanup(s);return; }
                if (s.native) {
                    s.nativeListener=e=>{try {event(s,typeof e.detail==='string'?JSON.parse(e.detail):e.detail);}catch(_){}};
                    root.addEventListener('cuttle-live-voice',s.nativeListener);
                    const result=bridge.liveVoiceStart(s.id);
                    if(result!=='ok') throw new Error(result==='denied'?'Allow microphone access in Android Settings':result);
                } else await s.pc.setRemoteDescription({type:'answer',sdp:data.session.sdp});
                s.poll=setInterval(()=>void poll(s),3000); void poll(s);
            } catch(err) {
                if(s.id) {try{await host.fetch('/api/voice-stt/live/sessions/'+s.id+'/close',{method:'POST',keepalive:true});}catch(_){}}
                cleanup(s); if(current===s)current=null; throw err;
            }
        }
        function mute(on) {
            const s=current;if(!s)return;
            if(s.stream)s.stream.getTracks().forEach(track=>{track.enabled=!on;});
            if(s.native)s.bridge.liveVoiceMute(!!on);
            else if(s.dc?.readyState==='open')s.dc.send(JSON.stringify({type:'session.input_audio.'+(on?'mute':'unmute')}));
        }
        return {start,stop:close,mute,isOpen:()=>!!current};
    }
    root.CuttleChatVoiceLive={create,supported};
    if(typeof module!=='undefined'&&module.exports)module.exports=root.CuttleChatVoiceLive;
})(typeof window!=='undefined'?window:globalThis);
