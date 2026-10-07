(function() {
    'use strict';
    const rowsEl=document.getElementById('feedRows'),status=document.getElementById('feedStatus');
    const form=document.getElementById('feedFilters'),followButton=document.getElementById('feedFollow');
    let rows=[],filters={},following=true,pending=[],before=0,source,streamCursor=0;
    const expanded=new Map();
    const motion=window.matchMedia('(prefers-reduced-motion: reduce)');
    const animations=new Set();
    let renderFrame=0;
    motion.addEventListener('change',()=>{
        if(motion.matches) {animations.forEach(animation=>animation.cancel());animations.clear();}
    });
    const params=new URLSearchParams(location.search);
    for (const input of form.elements) if(input.name) input.value=params.get(input.name)||'';
    function node(tag,text) {const el=document.createElement(tag);el.textContent=text;return el;}
    function render(live=false) {
        if(renderFrame) {cancelAnimationFrame(renderFrame);renderFrame=0;}
        const focused=rowsEl.contains(document.activeElement)?document.activeElement:null;
        const oldSteps=new Map([...rowsEl.querySelectorAll('.feed-step')].map(el=>[Number(el.dataset.id),el]));
        const canAnimate=live && !motion.matches && !document.hidden && typeof rowsEl.animate==='function';
        const positions=new Map();
        if(canAnimate) for(const el of rowsEl.children) {
            const rect=el.getBoundingClientRect();
            if(rect.bottom>=0 && rect.top<=innerHeight) positions.set(el.dataset.motionKey,rect.top);
        }
        // Read interrupted visual positions before cancelling: a busy feed never
        // queues animations or snaps back to the previous layout between bursts.
        animations.forEach(animation=>animation.cancel());animations.clear();
        rowsEl.querySelectorAll('.feed-step[open]').forEach(el=>expanded.set(Number(el.dataset.id),el.querySelector('pre').textContent));
        const fragment=document.createDocumentFragment();
        const visible=rows.filter(row=>CuttleAgentFeed.matches(row,filters)).slice(0,200);
        let previous='';
        const groups=new Map();
        for (const row of visible) {
            if(row.query_id!==previous) {
                const header=node('h2','');header.className='feed-run';header.append(node('strong',row.agent_id||'Agent'),node('span',row.model||'default'));
                const occurrence=groups.get(row.query_id)||0;groups.set(row.query_id,occurrence+1);
                header.dataset.motionKey='run:'+row.query_id+':'+occurrence;
                const link=node('a',row.query_id);link.href='/query_log.html?id='+encodeURIComponent(row.query_id);
                header.append(' · ',link);
                if(row.chat_session_id) {
                    const chat=node('a','CH-'+String(row.chat_session_id).padStart(6,'0'));
                    chat.href='/chat_page.html?session_id='+encodeURIComponent(row.chat_session_id);header.append(' · ',chat);
                }
                fragment.append(header);previous=row.query_id;
            }
            const existing=oldSteps.get(Number(row.id));
            if(existing && existing.dataset.rev===String(row.rev)) {fragment.append(existing);continue;}
            const details=document.createElement('details');details.className='feed-step';details.dataset.id=row.id;details.dataset.kind=row.kind;
            details.dataset.rev=row.rev;details.dataset.motionKey='step:'+row.id;
            const summary=node('summary','');
            const icons={edit:'±',tool:'⌘',thinking:'◌',writing:'≡','run.start':'▶','run.resume':'↻',finish:'✓',status:'·'};
            for(const [cls,text] of [['feed-icon',icons[row.kind]||'·'],['feed-kind',row.kind.replace('run.','')],['feed-title',row.summary||'Agent activity'],['feed-time',new Date(row.ts*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})]]) {
                const part=node('span',text);part.className=cls;summary.append(part);
            }
            details.append(summary);
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
                    body.textContent=item.text||item.output||JSON.stringify(item,null,2);
                    if(row.kind==='edit' && (item.patch||item.text) && window.CuttleEventDiff) {
                        let diffHost=details.querySelector('.feed-diff');
                        if(!diffHost){diffHost=document.createElement('div');diffHost.className='feed-diff';details.insertBefore(diffHost,body);}
                        CuttleEventDiff.render(diffHost,item.patch||item.text);
                        body.hidden=true;
                        const raw=node('button','Show raw event');raw.type='button';raw.className='feed-raw-toggle';
                        raw.addEventListener('click',()=>{body.hidden=!body.hidden;raw.textContent=body.hidden?'Show raw event':'Hide raw event';});
                        details.insertBefore(raw,body);
                    }
                } catch(error) {body.textContent=error.message;delete details.dataset.loaded;}
            });fragment.append(details);
        }
        if(!visible.length) {const empty=node('p','Your agents’ next steps will appear here.');empty.className='feed-empty';fragment.append(empty);}
        rowsEl.replaceChildren(fragment);
        if(focused && focused.isConnected && document.activeElement!==focused) focused.focus({preventScroll:true});
        if(canAnimate) {
            const effects=[];
            // Batch geometry reads before starting compositor-only effects.
            for(const el of rowsEl.children) {
                const rect=el.getBoundingClientRect();
                if(rect.bottom<0 || rect.top>innerHeight) continue;
                const oldTop=positions.get(el.dataset.motionKey);
                if(oldTop!==undefined) {
                    const delta=oldTop-rect.top;
                    if(Math.abs(delta)>1) effects.push([el,[{transform:'translateY('+delta+'px)'},{transform:'translateY(0)'}]]);
                } else if(el.classList.contains('feed-step') && !oldSteps.has(Number(el.dataset.id))) {
                    effects.push([el,[{opacity:0,clipPath:'inset(0 0 100% 0)',transform:'translateY(-6px)'},{opacity:1,clipPath:'inset(0)',transform:'translateY(0)'}]]);
                }
            }
            for(const [el,keyframes] of effects.slice(0,40)) {
                const animation=el.animate(keyframes,{duration:250,easing:'cubic-bezier(.25,.1,.25,1)'});
                animations.add(animation);
                animation.onfinish=()=>animations.delete(animation);
            }
        }
    }
    function applyIncoming(incoming) {
        if(!following) {pending=CuttleAgentFeed.merge(pending,incoming);followButton.textContent=pending.length+' updated steps · Resume live';return;}
        rows=CuttleAgentFeed.merge(rows,incoming).slice(0,1000);
        if(!renderFrame) renderFrame=requestAnimationFrame(()=>{renderFrame=0;render(true);});
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
        filters=Object.fromEntries([...new FormData(form)].filter(([,value])=>value.trim()));
        const count=['agent','model','chat','project'].filter(key=>filters[key]).length;
        document.getElementById('feedFilterCount').textContent=count?'· '+count:'';
        history.replaceState(null,'','?'+new URLSearchParams(filters));
    }
    form.addEventListener('submit',async(event)=>{event.preventDefault();readFilters();rows=[];before=0;form.querySelector('.feed-advanced').open=false;await load().then(connectStream).catch(error=>status.textContent=error.message);});
    function resume() {following=true;followButton.setAttribute('aria-pressed','true');followButton.textContent='Following live';applyIncoming(pending);pending=[];}
    followButton.addEventListener('click',()=>{if(following){following=false;followButton.textContent='Paused · Resume live';followButton.setAttribute('aria-pressed','false');}else resume();});
    form.querySelector('[name=kind]').addEventListener('change',()=>form.requestSubmit());
    document.getElementById('feedOlder').addEventListener('click',async()=>{following=false;followButton.setAttribute('aria-pressed','false');followButton.textContent='Resume live';rows=[];await load().catch(error=>status.textContent=error.message);});
    readFilters();
    function connectStream() {
        if(source)source.close();
        const streamParams=new URLSearchParams(filters);streamParams.set('after',streamCursor);
        source=new EventSource('/api/agent-events/stream?'+streamParams);
        source.onmessage=event=>{status.textContent='Live · latest activity first';applyIncoming(JSON.parse(event.data).events);};
        source.onerror=()=>{status.textContent='Reconnecting…';};
        source.onopen=()=>{status.textContent='Live · latest activity first';};
    }
    load().then(connectStream).catch(error=>status.textContent=error.message);
    window.addEventListener('pagehide',()=>{
        if(source)source.close();
        if(renderFrame)cancelAnimationFrame(renderFrame);
        animations.forEach(animation=>animation.cancel());animations.clear();
    });
})();
