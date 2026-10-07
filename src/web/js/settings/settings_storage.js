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
    async function load() {
        try {
            const data=await request('');host.replaceChildren();
            for(const store of data.stores) {
                const section=document.createElement('section');section.className='setting-item';
                const body=document.createElement('div');body.className='setting-label';
                body.append(node('h4',store.label),node('p',(store.bytes/1e6).toFixed(1)+' MB'+(store.events!=null?' · '+store.events+' steps':'')));
                const status=node('p',store.capture_error?'Capture error: '+store.capture_error:store.over_quota?'Over quota: active turns or retained metadata prevent further compaction.':'');status.setAttribute('role','status');
                if(store.configurable) {
                    const form=document.createElement('form');form.className='storage-policy';
                    const fields=[['retention_days','Full detail days'],['thinking_full_days','Full thinking days'],['quota_mb','Quota (decimal MB)'],['starred_retention_days','Starred chat days (-1 = same; 0 = age exemption)']];
                    const controls={};
                    for(const [key,label] of fields) {
                        const wrapper=node('label',label+' ');const input=document.createElement('input');input.type='number';input.min=key==='starred_retention_days'?'-1':key==='thinking_full_days'?'0':'1';input.value=store.policy[key];input.name=key;
                        wrapper.append(input);form.append(wrapper);controls[key]=input;
                    }
                    const modeLabel=node('label','Thinking storage ');const mode=document.createElement('select');
                    for(const [key,label] of [['full_then_summary','Full text, then short summary'],['summary_only','Short summary only']]) {
                        const option=node('option',label);option.value=key;mode.append(option);
                    }
                    mode.value=store.policy.thinking_mode;modeLabel.append(mode);form.append(modeLabel);
                    const save=node('button','Save policy');save.type='submit';form.append(save);
                    form.addEventListener('submit',async(event)=>{
                        event.preventDefault();save.disabled=true;
                        const policy={thinking_mode:mode.value};for(const key in controls)policy[key]=Number(controls[key].value);
                        try {await request('/'+store.id,policy);status.textContent='Policy saved. Expired detail cannot be restored.';}
                        catch(error){status.textContent=error.message;}finally{save.disabled=false;}
                    });body.append(form);
                    const quota=document.createElement('meter');quota.min=0;quota.max=store.policy.quota_mb*1e6;quota.value=store.bytes;quota.setAttribute('aria-label','Agent events quota usage');body.append(quota);
                    body.append(node('p','Quota can compact older completed steps before their retention window ends. Attribution metadata remains. Git snapshot objects use repository storage separately.'));
                    for(const operation of ['prune','vacuum']) {
                        const button=node('button',operation==='prune'?'Prune now':'Vacuum');button.type='button';
                        button.addEventListener('click',async()=>{button.disabled=true;status.textContent='Running…';try{const result=await request('/'+store.id+'/'+operation,{});status.textContent='Done · '+(result.result.bytes/1e6).toFixed(1)+' MB';}catch(error){status.textContent=error.message;}finally{button.disabled=false;}});body.append(button,' ');
                    }
                    if(store.reset) {
                        const resetForm=document.createElement('form');const label=node('label','Reset deletes stored agent activity and pending attribution. Type '+store.id+' to confirm: ');
                        const input=document.createElement('input');input.autocomplete='off';label.append(input);resetForm.append(label);
                        const reset=node('button','Reset agent events');reset.type='submit';reset.disabled=true;resetForm.append(reset);
                        input.addEventListener('input',()=>reset.disabled=input.value!==store.id);
                        resetForm.addEventListener('submit',async(event)=>{event.preventDefault();reset.disabled=true;try{await request('/'+store.id+'/reset',{confirm:input.value});await load();}catch(error){status.textContent=error.message;reset.disabled=false;}});body.append(resetForm);
                    }
                } else body.append(node('p','Size monitoring only. This store’s owner controls its lifecycle.'));
                body.append(status);section.append(body);host.append(section);
            }
        } catch(error) {host.textContent=error.message;}
    }
    load();
})();
