/* One shell-owned voice connection; chat iframe presenters may come and go. */
(function(root){
    'use strict';
    function create({win,navigate}){
        let active=null, muted=false, status='', usage=null, controller=null;
        const listeners=new Map();
        const doc=win.document;
        const bar=doc.createElement('div');bar.hidden=true;bar.className='shell-voice-controls';bar.setAttribute('role','region');bar.setAttribute('aria-label','Active voice conversation');
        const style=doc.createElement('style');style.textContent='.shell-voice-controls{position:fixed;z-index:1600;right:16px;bottom:16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;max-width:calc(100vw - 32px);padding:10px 14px;border:1px solid var(--border-color,#41414c);border-radius:14px;background:var(--bg-secondary,#20202a);color:var(--text-primary,#fff);box-shadow:0 4px 20px #0005}.shell-voice-controls[hidden]{display:none}.shell-voice-controls button{cursor:pointer;background:transparent;color:inherit;border:1px solid #7776;border-radius:8px;padding:6px 9px}.shell-voice-controls span{font-size:12px}';
        doc.head.appendChild(style);doc.body.appendChild(bar);
        const title=doc.createElement('button'),info=doc.createElement('span'),mute=doc.createElement('button'),end=doc.createElement('button');
        mute.textContent='Mute';end.textContent='End voice';bar.append(title,info,mute,end);
        title.onclick=()=>{if(active)navigate(active.session_id);};mute.onclick=()=>{muted=!muted;controller?.mute?.(muted);paint();};end.onclick=()=>void stop();
        function emit(kind,...args){const group=listeners.get(String(active?.session_id));for(const listener of group||[])try{if(listener.alive&&!listener.alive()){group.delete(listener);continue;}listener[kind]?.(...args);}catch(_){}}
        function paint(){
            bar.hidden=!active; if(!active)return;
            title.textContent=(active.engine==='live'?'GPT-Live':'Realtime')+' · CH-'+String(active.session_id).padStart(6,'0');
            info.textContent=(muted?'Muted · ':status+' · ')+(usage?(active.engine==='live'?Math.round(usage.duration_seconds||0)+'s':Math.round(usage.total_tokens||0)+' tokens')+' · ~$'+Number(usage.cost||0).toFixed(3):'Connecting');
            mute.textContent=muted?'Unmute':'Mute';mute.setAttribute('aria-pressed',String(muted));
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
            active={...context,transcripts:[]};muted=false;status='Connecting';usage=null;paint();
            const own=active;
            const Engine=context.engine==='live'?win.CuttleChatVoiceLive:win.CuttleChatVoiceRealtime;
            controller=Engine.create({fetch:(...args)=>win.fetch(...args),experimentalFlags:async()=>{
                const r=await win.fetch('/api/experimental/flags',{cache:'no-store'}),d=await r.json();
                if(!r.ok||!d.success)throw new Error('Flags unavailable');return new Set(d.flags.filter(f=>f.enabled).map(f=>f.id));},
                onStatus:text=>{if(active!==own)return;status=text;paint();emit('onStatus',text);if(text.startsWith('Disconnected')){active=null;paint();}},
                onError:text=>{if(active!==own)return;status=text;paint();emit('onError',text);},
                onTranscript:(...args)=>{if(active!==own)return;active.transcripts.push(args);if(active.transcripts.length>1000)active.transcripts.shift();emit('onTranscript',...args);},
                onUsage:value=>{if(active!==own)return;usage=value;paint();emit('onUsage',value);win.dispatchEvent(new win.Event('cuttle-voice-usage'));},
                delegate:context.delegate,realtimeUsage:async payload=>{
                    const response=await win.fetch('/api/voice-stt/realtime/usage',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                    const data=await response.json();if(!response.ok||!data.success)throw new Error('Usage recording failed');return data.usage;
                }});
            const instance=controller;
            try{await instance.start(context);}catch(err){if(active===own){status=err.message;emit('onError',status);active=null;paint();}throw err;}
        }
        doc.addEventListener('visibilitychange',()=>{if(doc.hidden)void stop();});
        win.addEventListener('pagehide',()=>void stop());
        const api={start,stop,attach,isOpen:sid=>!!(active&&String(active.session_id)===String(sid)&&controller?.isOpen()),
            engine:sid=>String(active?.session_id)===String(sid)?active.engine:null};
        win.cuttleVoiceRuntime=api;return api;
    }
    root.CuttleShellVoice={create};
})(typeof window!=='undefined'?window:globalThis);
