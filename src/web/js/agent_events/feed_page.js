(function() {
    'use strict';
    const rowsEl=document.getElementById('feedRows'),status=document.getElementById('feedStatus');
    const form=document.getElementById('feedFilters'),followButton=document.getElementById('feedFollow');
    let rows=[],filters={},following=true,pending=[],before=0,source,streamCursor=0;
    const expanded=new Map();
    const params=new URLSearchParams(location.search);
    for (const input of form.elements) if(input.name) input.value=params.get(input.name)||'';
    function node(tag,text) {const el=document.createElement(tag);el.textContent=text;return el;}
    function render() {
        rowsEl.querySelectorAll('details[open]').forEach(el=>expanded.set(Number(el.dataset.id),el.querySelector('pre').textContent));
        const fragment=document.createDocumentFragment();
        const visible=rows.filter(row=>CuttleAgentFeed.matches(row,filters)).slice(0,200);
        let previous='';
        for (const row of visible) {
            if(row.query_id!==previous) {
                const header=node('h2',(row.agent_id||'Agent')+' · '+(row.model||'default'));
                const link=node('a',row.query_id);link.href='/query_log.html?id='+encodeURIComponent(row.query_id);
                header.append(' · ',link);
                if(row.chat_session_id) {
                    const chat=node('a','CH-'+String(row.chat_session_id).padStart(6,'0'));
                    chat.href='/chat_page.html?session_id='+encodeURIComponent(row.chat_session_id);header.append(' · ',chat);
                }
                fragment.append(header);previous=row.query_id;
            }
            const details=document.createElement('details');details.className='feed-step';details.dataset.id=row.id;
            details.append(node('summary',new Date(row.ts*1000).toLocaleTimeString()+' · '+row.kind+' · '+(row.summary||'')));
            const link=node('a','Open step in query log');link.href='/query_log.html?id='+encodeURIComponent(row.query_id)+'&seq='+row.seq;
            details.append(link);
            const body=node('pre',expanded.get(row.id)||'');details.append(body);
            if(expanded.has(row.id))details.open=true;
            details.addEventListener('toggle',async()=>{
                if(!details.open) {expanded.delete(row.id);return;}
                if(details.dataset.loaded) return;
                details.dataset.loaded='1';body.textContent='Loading full detail…';
                try {
                    const response=await fetch('/api/agent-events/events/'+row.id,{credentials:'include',cache:'no-store'});
                    if(!response.ok) throw new Error('Detail unavailable');
                    const item=(await response.json()).detail;
                    body.textContent=JSON.stringify(item,null,2);
                    if(row.kind==='edit' && (item.patch||item.text) && window.CuttleEventDiff) {
                        let diffHost=details.querySelector('.feed-diff');
                        if(!diffHost){diffHost=document.createElement('div');diffHost.className='feed-diff';details.insertBefore(diffHost,body);}
                        CuttleEventDiff.render(diffHost,item.patch||item.text);
                    }
                } catch(error) {body.textContent=error.message;delete details.dataset.loaded;}
            });fragment.append(details);
        }
        if(!visible.length) fragment.append(node('p','No matching activity yet.'));
        rowsEl.replaceChildren(fragment);
    }
    function applyIncoming(incoming) {
        if(!following) {pending=CuttleAgentFeed.merge(pending,incoming);followButton.textContent=pending.length+' updated steps · Resume live';return;}
        rows=CuttleAgentFeed.merge(rows,incoming).slice(0,1000);render();
    }
    async function load() {
        const query=new URLSearchParams(filters);query.set('direction','desc');query.set('limit','200');
        if(before)query.set('before',before);
        const response=await fetch('/api/agent-events?'+query,{credentials:'include',cache:'no-store'});
        if(!response.ok)throw new Error('Activity could not be loaded');
        const data=await response.json();streamCursor=data.stream_cursor||streamCursor;
        before=data.next_cursor||before;rows=CuttleAgentFeed.merge(rows,data.events);render();
        document.getElementById('feedOlder').disabled=data.events.length<200;
    }
    function readFilters() {
        filters=Object.fromEntries(new FormData(form));
        history.replaceState(null,'','?'+new URLSearchParams(filters));
    }
    form.addEventListener('submit',async(event)=>{event.preventDefault();readFilters();rows=[];before=0;await load().then(connectStream).catch(error=>status.textContent=error.message);});
    function resume() {following=true;followButton.setAttribute('aria-pressed','true');followButton.textContent='Following live';applyIncoming(pending);pending=[];}
    followButton.addEventListener('click',()=>{if(following){following=false;followButton.textContent='Paused · Resume live';followButton.setAttribute('aria-pressed','false');}else resume();});
    window.addEventListener('wheel',()=>{if(following){following=false;followButton.setAttribute('aria-pressed','false');followButton.textContent='Paused · Resume live';}},{passive:true});
    document.getElementById('feedOlder').addEventListener('click',async()=>{rows=[];await load().catch(error=>status.textContent=error.message);});
    readFilters();
    function connectStream() {
        if(source)source.close();
        const streamParams=new URLSearchParams(filters);streamParams.set('after',streamCursor);
        source=new EventSource('/api/agent-events/stream?'+streamParams);
        source.onmessage=event=>{status.textContent='Live · latest activity first';applyIncoming(JSON.parse(event.data).events);};
        source.onerror=()=>{status.textContent='Live stream unavailable. Enable Agent Feed in Experimental settings, or wait for reconnection.';};
        source.onopen=()=>{status.textContent='Live · latest activity first';};
    }
    load().then(connectStream).catch(error=>status.textContent=error.message);
    window.addEventListener('pagehide',()=>source&&source.close());
})();
