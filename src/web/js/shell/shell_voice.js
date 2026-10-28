/* One shell-owned voice connection; chat iframe presenters may come and go.
 *
 * The call controls render as a docked strip in the shell layout (the host's
 * #shellVoiceStrip slot, below the panes). Its height is published as the
 * root CSS variable --shell-voice-strip-h so fixed overlays (side panels,
 * their backdrop, toasts) stop above it instead of covering End voice.
 */
(function(root){
    'use strict';
    const STRIP_HEIGHT_VAR='--shell-voice-strip-h';
    const SVG='<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
    const MIC_ICON=SVG+'<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1"/><line x1="12" y1="18" x2="12" y2="22"/></svg>';
    const MIC_OFF_ICON=SVG+'<line x1="2" y1="2" x2="22" y2="22"/><path d="M9 9v2a3 3 0 0 0 5.12 2.12M15 9.34V5a3 3 0 0 0-5.94-.6"/><path d="M17 16.95A7 7 0 0 1 5 11v-1m14 0v1a7 7 0 0 1-.11 1.23"/><line x1="12" y1="18" x2="12" y2="22"/></svg>';
    const HANG_UP_ICON='<svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" aria-hidden="true"><path d="M12 9c-1.6 0-3.15.25-4.6.72v3.1c0 .39-.23.74-.56.9-.98.49-1.87 1.12-2.66 1.85-.18.18-.43.28-.7.28-.28 0-.53-.11-.71-.29L.29 13.08a.956.956 0 0 1-.29-.7c0-.28.11-.53.29-.71C3.34 8.78 7.46 7 12 7s8.66 1.78 11.71 4.67c.18.18.29.43.29.71 0 .28-.11.53-.29.71l-2.48 2.48c-.18.18-.43.29-.71.29-.27 0-.52-.11-.7-.28a11.27 11.27 0 0 0-2.67-1.85.996.996 0 0 1-.56-.9v-3.1C15.15 9.25 13.6 9 12 9z"/></svg>';
    function create({win,mount}){
        let active=null, muted=false, status='', failed=false, usage=null, controller=null;
        const listeners=new Map();
        const doc=win.document;
        function isMobileShell(){try{return !!(win.isCuttleMobile||(win.cuttleMobile&&win.cuttleMobile.isNative));}catch(_){return false;}}
        let bar=mount||(typeof doc.getElementById==='function'?doc.getElementById('shellVoiceStrip'):null);
        if(!bar){bar=doc.createElement('div');doc.body.appendChild(bar);}
        bar.hidden=true;bar.className='shell-voice-strip';bar.setAttribute('role','region');bar.setAttribute('aria-label','Active voice call');
        const mute=doc.createElement('button'),end=doc.createElement('button');
        mute.type='button';mute.className='shell-voice-btn shell-voice-mute';mute.setAttribute('aria-label','Mute');
        end.type='button';end.className='shell-voice-btn shell-voice-end';end.setAttribute('aria-label','End voice');end.title='End call';
        end.innerHTML=HANG_UP_ICON;bar.append(mute,end);
        mute.onclick=()=>{muted=!muted;controller?.mute?.(muted);paint();};end.onclick=()=>void stop();
        function publishHeight(){
            try{
                const h=bar.hidden||typeof bar.getBoundingClientRect!=='function'?0:Math.ceil(bar.getBoundingClientRect().height);
                doc.documentElement.style.setProperty(STRIP_HEIGHT_VAR,h+'px');
            }catch(_){}
        }
        if(typeof win.ResizeObserver==='function'){try{new win.ResizeObserver(publishHeight).observe(bar);}catch(_){}}
        function emit(kind,...args){const group=listeners.get(String(active?.session_id));for(const listener of group||[])try{if(listener.alive&&!listener.alive()){group.delete(listener);continue;}listener[kind]?.(...args);}catch(_){}}
        function stateName(){
            if(failed)return 'error';
            if(muted)return 'muted';
            return !usage&&/^connecting/i.test(status||'Connecting')?'connecting':'live';
        }
        function paint(){
            const wasHidden=bar.hidden;
            bar.hidden=!active;
            if(active){
                bar.setAttribute('data-state',stateName());
                mute.innerHTML=muted?MIC_OFF_ICON:MIC_ICON;mute.title=muted?'Unmute':'Mute';mute.setAttribute('aria-pressed',String(muted));
            }
            if(wasHidden!==bar.hidden)publishHeight();
        }
        function attach(sessionId,listener){
            const key=String(sessionId); if(!listeners.has(key))listeners.set(key,new Set());listeners.get(key).add(listener);
            if(active&&String(active.session_id)===key){listener.onStatus?.(status);if(usage)listener.onUsage?.(usage);for(const line of active.transcripts)listener.onTranscript?.(...line);}
            return ()=>{listeners.get(key)?.delete(listener);if(!listeners.get(key)?.size)listeners.delete(key);};
        }
        async function stop(){
            const old=active;if(controller)await controller.stop();
            if(active!==old)return;emit('onStatus','Disconnected');active=null;controller=null;usage=null;paint();
        }
        async function start(context){
            if(active&&String(active.session_id)===String(context.session_id)&&active.engine===context.engine&&controller?.isOpen())return;
            await stop();
            active={...context,transcripts:[]};muted=false;failed=false;status='Connecting';usage=null;paint();
            const own=active;
            const Engine=context.engine==='live'?win.CuttleChatVoiceLive:win.CuttleChatVoiceRealtime;
            controller=Engine.create({fetch:(...args)=>win.fetch(...args),experimentalFlags:async()=>{
                const r=await win.fetch('/api/experimental/flags',{cache:'no-store'}),d=await r.json();
                if(!r.ok||!d.success)throw new Error('Flags unavailable');return new Set(d.flags.filter(f=>f.enabled).map(f=>f.id));},
                onStatus:text=>{if(active!==own)return;status=text;failed=false;paint();emit('onStatus',text);if(text.startsWith('Disconnected')){active=null;paint();}},
                onError:text=>{if(active!==own)return;status=text;failed=true;paint();emit('onError',text);},
                onTranscript:(...args)=>{if(active!==own)return;active.transcripts.push(args);if(active.transcripts.length>1000)active.transcripts.shift();emit('onTranscript',...args);},
                onUsage:value=>{if(active!==own)return;usage=value;paint();emit('onUsage',value);win.dispatchEvent(new win.Event('cuttle-voice-usage'));},
                delegate:context.delegate,realtimeUsage:async payload=>{
                    const response=await win.fetch('/api/voice-stt/realtime/usage',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                    const data=await response.json();if(!response.ok||!data.success)throw new Error('Usage recording failed');return data.usage;
                }});
            const instance=controller;
            try{await instance.start(context);}catch(err){if(active===own){status=err.message;emit('onError',status);active=null;paint();}throw err;}
        }
        // Desktop: leaving the tab stops the call. Phone: the microphone
        // foreground service keeps the native call alive with the screen off,
        // so a screen lock must NOT hang up (only pagehide / explicit End do).
        doc.addEventListener('visibilitychange',()=>{if(doc.hidden&&!isMobileShell())void stop();});
        win.addEventListener('pagehide',()=>void stop());
        const api={start,stop,attach,isOpen:sid=>!!(active&&String(active.session_id)===String(sid)&&controller?.isOpen()),
            engine:sid=>String(active?.session_id)===String(sid)?active.engine:null};
        win.cuttleVoiceRuntime=api;return api;
    }
    root.CuttleShellVoice={create};
})(typeof window!=='undefined'?window:globalThis);
