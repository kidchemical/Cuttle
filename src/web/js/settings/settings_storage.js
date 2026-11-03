/* Server database settings: UI renders registry capabilities, never guesses reset safety. */
(function() {
    'use strict';
    const host=document.getElementById('storageStores');
    if(!host)return;
    function node(tag,text) {const el=document.createElement(tag);el.textContent=text;return el;}
    async function request(path,body) {
        const response=await fetch('/api/settings/storage'+path,{credentials:'include',cache:'no-store',
            ...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
        const data=await response.json();if(!response.ok)throw new Error(data.error||'Database operation failed');return data;
    }
    const FIELD_LABELS={'retention_days':'Full detail days','thinking_full_days':'Full thinking days','quota_mb':'Quota (decimal MB)','starred_retention_days':'Starred chat days (-1 = same; 0 = age exemption)'};
    function formatBytes(bytes) {
        const mb=(Number(bytes)||0)/1e6;
        return mb>=1000?(mb/1000).toFixed(2)+' GB':mb.toFixed(1)+' MB';
    }
    /* Summary card: small label, big size, quota bar + caption (when the store has a quota). */
    function statCard(store) {
        const stat=document.createElement('div');stat.className='settings-stat';
        const label=node('h4',store.label);label.className='settings-stat-label';
        const value=node('p',formatBytes(store.bytes));value.className='settings-stat-value';
        stat.append(label,value);
        const quotaMb=store.policy&&typeof store.policy.quota_mb==='number'?store.policy.quota_mb:null;
        const foot=document.createElement('div');foot.className='settings-stat-foot';
        if(quotaMb) {
            const pct=Math.max(0,Math.min(100,Math.round(store.bytes/(quotaMb*1e6)*100)));
            const bar=document.createElement('div');bar.className='settings-stat-bar';
            bar.setAttribute('role','meter');bar.setAttribute('aria-label',store.label+' quota usage');
            bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');bar.setAttribute('aria-valuenow',String(pct));
            if(store.over_quota||pct>=100)bar.classList.add('is-over');
            const fill=document.createElement('span');fill.style.width=pct+'%';bar.append(fill);stat.append(bar);
            foot.append(node('span',pct+'% of quota'),node('span',formatBytes(quotaMb*1e6)+' quota'));
        } else if(store.events!=null) {
            foot.append(node('span',Number(store.events).toLocaleString()+' steps'));
        }
        if(foot.childNodes.length)stat.append(foot);
        return stat;
    }
    function summarize(result) {
        if(result==null||typeof result!=='object')return String(result);
        if(typeof result.bytes==='number')return (result.bytes/1e6).toFixed(1)+' MB';
        if(typeof result.deleted==='number')return result.deleted+' removed';
        return Object.entries(result).map(([key,value])=>key+': '+value).join(', ');
    }
    async function load() {
        try {
            const data=await request('');host.replaceChildren();
            for(const store of data.stores) {
                const section=document.createElement('section');section.className='setting-item storage-store';
                const body=document.createElement('div');body.className='setting-label';
                body.append(statCard(store));
                const status=node('p',store.capture_error?'Capture error: '+store.capture_error:store.over_quota?'Over quota: active turns or retained metadata prevent further compaction.':'');status.setAttribute('role','status');
                let controls=false;
                if(store.configurable&&store.policy) {
                    controls=true;
                    const form=document.createElement('form');form.className='storage-policy';
                    const inputs={};
                    for(const key of Object.keys(FIELD_LABELS)) {
                        if(!(key in store.policy))continue;
                        const wrapper=node('label',FIELD_LABELS[key]+' ');const input=document.createElement('input');input.type='number';input.name=key;
                        input.min=key==='starred_retention_days'?'-1':key==='thinking_full_days'?'0':'1';input.value=store.policy[key];
                        wrapper.append(input);form.append(wrapper);inputs[key]=input;
                    }
                    let mode=null;
                    if('thinking_mode' in store.policy) {
                        const modeLabel=node('label','Thinking storage ');mode=document.createElement('select');
                        for(const [key,label] of [['full_then_summary','Full text, then short summary'],['summary_only','Short summary only']]) {
                            const option=node('option',label);option.value=key;mode.append(option);
                        }
                        mode.value=store.policy.thinking_mode;modeLabel.append(mode);form.append(modeLabel);
                    }
                    const save=node('button','Save policy');save.type='submit';form.append(save);
                    form.addEventListener('submit',async(event)=>{
                        event.preventDefault();save.disabled=true;
                        const policy={};for(const key in inputs)policy[key]=Number(inputs[key].value);
                        if(mode)policy.thinking_mode=mode.value;
                        try {await request('/'+store.id,policy);status.textContent='Policy saved. Expired detail cannot be restored.';}
                        catch(error){status.textContent=error.message;}finally{save.disabled=false;}
                    });body.append(form);
                    if(typeof store.policy.quota_mb==='number') {
                        const note=node('p','Quota can compact older completed steps before their retention window ends. Attribution metadata remains. Git snapshot objects use repository storage separately.');
                        note.className='setting-description';body.append(note);
                    }
                }
                for(const operation of ['prune','vacuum']) {
                    if(!store['supports_'+operation])continue;
                    controls=true;
                    const button=node('button',operation==='prune'?'Prune now':'Vacuum');button.type='button';
                    button.addEventListener('click',async()=>{button.disabled=true;status.textContent='Running…';try{const result=await request('/'+store.id+'/'+operation,{});status.textContent='Done · '+summarize(result.result);}catch(error){status.textContent=error.message;}finally{button.disabled=false;}});body.append(button,' ');
                }
                if(store.reset_policy&&store.reset_policy!=='never') {
                    controls=true;
                    const resetForm=document.createElement('form');
                    const reset=node('button','Reset '+store.label);reset.type='submit';resetForm.append(reset);
                    if(store.reset_policy==='typed_confirm') {
                        const label=node('label','Reset deletes stored '+store.label.toLowerCase()+'. Type '+store.id+' to confirm: ');
                        const input=document.createElement('input');input.autocomplete='off';label.append(input);resetForm.prepend(label);
                        reset.disabled=true;
                        input.addEventListener('input',()=>reset.disabled=input.value!==store.id);
                        resetForm.addEventListener('submit',async(event)=>{event.preventDefault();reset.disabled=true;try{await request('/'+store.id+'/reset',{confirm:input.value});await load();}catch(error){status.textContent=error.message;reset.disabled=false;}});
                    } else {
                        resetForm.addEventListener('submit',async(event)=>{event.preventDefault();reset.disabled=true;try{await request('/'+store.id+'/reset',{});await load();}catch(error){status.textContent=error.message;reset.disabled=false;}});
                    }
                    body.append(resetForm);
                }
                if(!controls)body.append(node('p','Size monitoring only. This store’s owner controls its lifecycle.'));
                body.append(status);section.append(body);host.append(section);
            }
        } catch(error) {host.textContent=error.message;}
    }
    load();
})();
