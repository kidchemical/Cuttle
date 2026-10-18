/* Durable whole-chat usage, shared by composer, voice view and message footers. */
(function(root){
    'use strict';
    function create(host){
        let agents=null, voice=[], sessionKey='', pending=null, agentLoading=false,voiceUnavailable=false;
        const doc=host.document, Usage=root.CuttleChatUsage;
        function total(){return Usage.sum([...(agents||[]).map(e=>Usage.normalize(e?.usage,e?.metadata)),...voice.map(e=>e.usage)]);}
        function paint(){
            const panel=doc.getElementById('chatSessionUsage'),box=doc.getElementById('chatSessionUsageTotals');
            if(panel&&box){panel.hidden=host.storage.getItem('cuttleShowSessionUsageEstimate')!=='true';
                box.innerHTML=Usage.render(total(),host.escapeHtml)||(agents===null?(agentLoading?'Loading session usage…':'Session usage unavailable'):Usage.placeholder(host.escapeHtml));
                if(voiceUnavailable)box.innerHTML+='<span class="voice-usage-unavailable"> Voice usage unavailable; totals may be incomplete.</span>';}
            for(const bubble of doc.querySelectorAll('.message.assistant')){
                const id=Number(bubble.dataset.messageId||0),queryId=bubble.querySelector('.message-query-log-link')?.dataset.queryId;
                const rows=voice.filter(e=>(Number(e.message_id)===id&&id)||(queryId&&e.usage.query_id===queryId));
                let slot=bubble.querySelector('.message-voice-usage');
                if(!rows.length&&!bubble._cuttleVoiceUsage){slot?.remove();continue;}
                if(!slot){slot=doc.createElement('div');slot.className='message-voice-usage';(bubble.querySelector('.message-footer')||bubble.querySelector('.message-content-wrapper'))?.appendChild(slot);}
                slot.innerHTML=Usage.render(rows.length?Usage.sum(rows.map(e=>e.usage)):bubble._cuttleVoiceUsage,host.escapeHtml);
            }
            host.window.dispatchEvent(new host.window.Event('cuttle-session-usage-updated'));
        }
        function update(entries){
            const key=String(host.sessionId()||'');
            if(key!==sessionKey){sessionKey=key;voice=[];agents=null;voiceUnavailable=false;}
            agentLoading=entries===undefined;agents=Array.isArray(entries)?entries:null;paint();void refresh();
        }
        async function refresh(){
            const raw=host.sessionId(), match=String(raw||'').match(/^(?:db_session_|CH-0*)?(\d+)$/),sid=match&&Number(match[1]);
            if(!sid)return;
            if(pending&&pending.key===String(raw))return pending.promise;
            const key=String(raw);
            const promise=(async()=>{
                try{
                    const include=host.storage.getItem('cuttleSessionUsageIncludeSubagents')==='true'?'1':'0';
                    const response=await host.fetch('/api/voice-stt/usage/'+sid+'?include_subagents='+include,{cache:'no-store',credentials:'include'});
                    const data=await response.json();
                    if(String(host.sessionId())!==key)return;
                    voiceUnavailable=!(response.ok&&data.success);
                    if(!voiceUnavailable)voice=data.entries||[];
                    if(agents===null&&!agentLoading){
                        const r=await host.fetch('/api/auth/sessions/'+sid+'/messages?limit=1&usage_summary=1&include_subagents='+include,{cache:'no-store',credentials:'include'});
                        const d=await r.json();if(String(host.sessionId())===key&&r.ok&&Array.isArray(d.usage_summary))agents=d.usage_summary;
                    }
                    paint();
                }catch(_){if(String(host.sessionId())===key){voiceUnavailable=true;paint();}}
                finally{if(pending?.key===key)pending=null;}
            })();pending={key,promise};return promise;
        }
        const timer=root.setInterval(()=>{if(!doc.hidden)void refresh();},15000);
        host.window.addEventListener('cuttle-voice-usage',refresh);
        host.window.addEventListener('pagehide',()=>{root.clearInterval(timer);host.window.removeEventListener('cuttle-voice-usage',refresh);});
        return {update,total,refresh};
    }
    root.CuttleChatSessionUsage={create};
})(typeof window!=='undefined'?window:globalThis);
