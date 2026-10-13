/* Persist unacknowledged Realtime usage; receipts contain counts, never audio or text. */
(function(root){
    'use strict';
    const key='cuttle-realtime-usage-v1';let running=false,timer;
    function read(){try{const rows=JSON.parse(root.localStorage.getItem(key)||'{}');return rows&&typeof rows==='object'&&!Array.isArray(rows)?rows:{};}catch(_){return {};}}
    function write(rows){root.localStorage.setItem(key,JSON.stringify(rows));}
    function enqueue(payload){
        if(!payload.connection_id||!root.localStorage)return;
        const rows=read(),old=rows[payload.connection_id]||{...payload,responses:[],transcriptions:[]};
        for(const kind of ['responses','transcriptions']){
            const seen=new Set(old[kind].map(event=>event._usage_id));
            for(const event of payload[kind]||[])if(!seen.has(event._usage_id)){old[kind].push(event);seen.add(event._usage_id);}
        }
        rows[payload.connection_id]=old;write(rows);
    }
    function acknowledge(payload){
        if(!root.localStorage)return;
        const rows=read(),old=rows[payload.connection_id];if(!old)return;
        for(const kind of ['responses','transcriptions']){
            const ids=new Set((payload[kind]||[]).map(event=>event._usage_id));
            old[kind]=old[kind].filter(event=>!ids.has(event._usage_id));
        }
        if(!old.responses.length&&!old.transcriptions.length)delete rows[payload.connection_id];write(rows);
    }
    async function recover(fetcher){
        if(running||!root.localStorage)return;running=true;clearTimeout(timer);
        try{
            for(const row of Object.values(read())){
                while(true){
                    const latest=read()[row.connection_id];if(!latest)break;
                    const payload={...latest,responses:latest.responses.slice(0,100),transcriptions:latest.transcriptions.slice(0,100)};
                    const response=await fetcher('/api/voice-stt/realtime/usage',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                    const data=await response.json();if(!response.ok||!data.success)break;
                    acknowledge(payload);
                    if(root.Event)root.dispatchEvent?.(new root.Event('cuttle-voice-usage'));
                }
            }
        }catch(_){}finally{
            running=false;if(Object.keys(read()).length)timer=setTimeout(()=>void recover(fetcher),30000);
        }
    }
    root.CuttleVoiceReceipts={enqueue,acknowledge,recover};
    if(root.document&&root.fetch)void recover((...args)=>root.fetch(...args));
})(typeof window!=='undefined'?window:globalThis);
