// Production owners/page adapters with isolated hosts; no real API or storage.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert/strict');
const root = path.resolve(__dirname, '../../..');
const asset = name => require(path.join(root, 'src/web/js', name));
const P = asset('shared/session_prefs.js'), T = asset('chat/chat_mutations.js');
const A = asset('chat/chat_activity.js'), Q = asset('chat/chat_followup_queue.js');
const Attention = asset('chat/chat_attention.js'), Broker = asset('shared/activity_broker.js');
const source = fs.readFileSync(path.join(root, 'src/web/js/chat/chat_page.js'), 'utf8');
function fn(name) {
    const start = source.search(new RegExp('    (?:async )?function ' + name + '\\('));
    assert(start >= 0, name);
    const end = source.indexOf('\n    }', start) + '\n    }'.length;
    return source.slice(start, end);
}
function storage() {
    const data = new Map();
    return {getItem:k=>data.get(k) || null, setItem:(k,v)=>data.set(k,v),
        get length(){return data.size;}, key:i=>[...data.keys()][i]};
}
function context(host) { return vm.createContext({followupWriteField:"followupWrite",followupClaimField:"followupClaim",...host, console:{warn(){}}}); }
function run(host, code) { return vm.runInContext(code, host); }
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    // Interleaved cached frames cannot erase unrelated chats OR fields.
    const backing = storage(), one = P.create(backing), two = P.create(backing);
    backing.setItem(P.LEGACY, JSON.stringify({'101':{stickyCleared:true}}));
    one.map(); two.map();
    one.update('101', {hasUnread:true});
    two.update('202', {stickyChips:[{prefix:'/codex '}]});
    two.update('101', {composerDraft:{chips:[]}});
    const recovered = P.create(backing);
    assert(recovered.get('101').hasUnread);
    assert(recovered.get('101').stickyCleared);
    assert(recovered.get('101').composerDraft);
    assert(recovered.get('202').stickyChips);
    recovered.update('draft:new:leaf.1', {composerDraft:{chips:['left']}});
    assert.equal(P.create(backing).get('draft:new:leaf.1').composerDraft.chips[0], 'left');

    // A->B->A still invalidates the earlier lifetime; earlier same-resource picks lose.
    const mutations = T.create();
    const old = mutations.capture('101', 'model', 1);
    assert(!mutations.current(old, '202', 2));
    assert(!mutations.current(old, '101', 3));
    const latest = mutations.capture('101', 'model', 1);
    assert(!mutations.current(old, '101', 1));
    assert(mutations.current(latest, '101', 1));

    // Production Muse mutation does not update B when A's response arrives.
    let resolve;
    const deferred = new Promise(r => resolve = r);
    const supplement = {museModels:[]};
    const model = context({currentSessionId:'101', _loadSessionSeq:1, sessionMutations:T.create(),
        slashPaletteSupplement:supplement, beginSupplementFetch(){}, sessionMutationFetch:()=>deferred,
        repaintMuseUserBadges(){},persistStickySlashForCurrentSession(){},refreshHistoryAgentChips(){}});
    run(model, fn('persistMuseModelSelection'));
    run(model, "persistMuseModelSelection('model-A')");
    model.currentSessionId='202'; model._loadSessionSeq=2;
    supplement.museModel='model-B'; supplement.museModelDirty=true;
    resolve({json:async()=>({success:true})});
    await tick();
    assert.equal(supplement.museModel,'model-B'); assert(supplement.museModelDirty);

    // Failed attention write survives reload and blocks older server snapshots.
    const prefs = P.create(storage()); let offline = true, calls=0;
    const host = {prefs,id:()=>String(++calls),changed(){},request:async()=>{
        if (offline) throw Error('offline');
        return {attention:{reply_id:7,read_id:7,hasUnread:false,revision:2}};
    }};
    const attention = Attention.create(host);
    attention.accept('101',{reply_id:7,read_id:0,hasUnread:true,revision:1});
    await attention.mark('101',{through_id:7});
    assert(prefs.get('101').attentionWrite);
    const restored = Attention.create(host);
    offline=false;
    restored.accept('101',{reply_id:7,read_id:0,hasUnread:true,revision:1});
    await restored.flush('101');
    assert.equal(prefs.get('101')?.attentionWrite || null,null);
    restored.accept('101',{reply_id:7,read_id:0,hasUnread:true,revision:1});
    assert.equal(restored.get('101').hasUnread,false);

    // Queue rebase preserves remote appends and respects remote removals.
    assert.deepEqual(Q.mergeEdits([{id:'old',paused:false}], [{id:'old',paused:true}],
        [{id:'old',paused:false},{id:'remote',content:'remote'}]),
        [{id:'old',paused:true},{id:'remote',content:'remote'}]);
    assert.deepEqual(Q.mergeEdits([{id:'taken',content:'gone'}], [{id:'taken',content:'gone'}], []), []);
    const queue = Q.createQueueState();
    queue.editVersion=2; queue.dirty=true; Q.markClean(queue,1); assert(queue.dirty);

    // A pending queue write owns A's state even after navigation, and failed writes stay dirty.
    const stateA=Q.createQueueState(), stateB=Q.createQueueState();
    stateA.items=[{id:'a',content:'A'}]; stateB.items=[{id:'b',content:'B'}];
    let resolveQueue;
    const queueResponse = new Promise(r=>resolveQueue=r);
    const queueHost = context({currentSessionId:'101',followupQueue:stateA,CuttleFollowupQueue:Q,
        sessionMutations:T.create(),followupAuthSid:()=> '101',updateSessionPrefs(){},
        fetch:()=>queueResponse,patchLiveSessionFollowupQueue(){},renderFollowupQueue(){},
        sessionIdsEqual:A.sessionIdsEqual});
    run(queueHost, fn('rememberUnsyncedQueue')+'\n'+fn('persistFollowupPut'));
    const write=run(queueHost,'persistFollowupPut()');
    await tick(); queueHost.currentSessionId='202'; queueHost.followupQueue=stateB;
    resolveQueue({ok:true,json:async()=>({success:true,revision:1,followups:[{id:'a',content:'A'}]})});
    await write;
    assert.equal(stateB.items[0].id,'b'); assert(!stateA.dirty);
    queueHost.currentSessionId='101'; queueHost.followupQueue=stateA;
    queueHost.fetch=async()=>{throw Error('offline');};
    assert.equal(await run(queueHost,'persistFollowupPut()'),false);
    assert(stateA.dirty);

    // A late catalog read cannot undo a newer successful pin save.
    let resolveCatalog;
    const catalog = new Promise(r => resolveCatalog = r);
    const pins = {museModels:[]};
    const pinHost = context({currentSessionId:'101', _loadSessionSeq:1, sessionMutations:T.create(),
        slashPaletteSupplement:pins, URLSearchParams, document:{getElementById:()=>null}, window:{},
        hasActiveMuseAgentChip:()=>true, fetch:()=>catalog,
        sessionMutationFetch:async()=>({json:async()=>({success:true,preferredModel:'new-pin'})}),
        renderSlashChips(){},repaintMuseUserBadges(){},persistStickySlashForCurrentSession(){},refreshHistoryAgentChips(){}});
    run(pinHost, fn('beginSupplementFetch')+'\n'+fn('isCurrentSupplementFetch')+'\n'+fn('loadMuseModelsForPalette')+'\n'+fn('persistMuseModelSelection'));
    run(pinHost,'loadMuseModelsForPalette(true);persistMuseModelSelection("new-pin")');
    await tick(); await tick();
    assert(!pins.museModelDirty);
    resolveCatalog({json:async()=>({success:true,preferredModel:'old-pin',models:[{id:'old-pin'}]})});
    await tick();
    assert.equal(pins.museModel,'new-pin'); assert.equal(pins.museModels.length,1);

    // Failed next-send chip publication keeps its durable intent after navigation/reload.
    const composerPrefs = P.create(storage()); let composerOffline=true;
    const composerHost = context({currentSessionId:'101',sharedComposerState:new Map(),
        isAuthMode:()=>true,toAuthDbSessionId:String,canonicalizeChatSessionId:A.canonicalizeChatSessionId,
        getSessionPrefs:id=>composerPrefs.get(id),updateSessionPrefs:(id,p)=>composerPrefs.update(id,p),
        fetch:async()=>{if(composerOffline)throw Error('offline');return {ok:true,json:async()=>({success:true,composer_selection:{revision:3}})};}});
    run(composerHost, fn('publishSharedComposerSelection'));
    run(composerHost,'publishSharedComposerSelection({stickyChips:[{prefix:"/codex "}],stickyCleared:false})');
    await tick(); await tick();
    assert(composerPrefs.get('101').composerWrite);
    assert(composerHost.sharedComposerState.get('101').unsynced);
    composerHost.currentSessionId='202'; composerHost.sharedComposerState=new Map(); composerOffline=false;
    composerHost.saved=composerPrefs.get('101').composerWrite;
    run(composerHost,'publishSharedComposerSelection(saved.selection,"101",saved.id)');
    await tick(); await tick();
    assert.equal(composerPrefs.get('101')?.composerWrite || null,null);
    assert.equal(composerHost.currentSessionId,'202');

    // A claim racing navigation is restored immediately to A, never sent from B.
    let resolveTake, restoredPayload;
    const taken = new Promise(r => resolveTake = r), claimedState = Q.createQueueState();
    claimedState.items=[{id:'claimed',content:'A queued'}]; claimedState.base=claimedState.items.slice();
    const claimPrefs = P.create(storage());
    const claimHost = context({currentSessionId:'101',followupQueue:claimedState,editingFollowupId:null,
        CuttleFollowupQueue:Q,CuttleChatActivity:A,sessionMutations:T.create(),
        healStaleGeneratingState(){},isSessionGenerating:()=>false,followupAuthSid:()=> '101',
        getSessionPrefs:id=>claimPrefs.get(id),updateSessionPrefs:(id,p)=>claimPrefs.update(id,p),
        sessionIdsEqual:A.sessionIdsEqual,patchLiveSessionFollowupQueue(){},renderFollowupQueue(){},
        fetch:async(_url,options)=>{if(options.method==='POST')return taken;
            restoredPayload=JSON.parse(options.body);return {ok:true,json:async()=>({success:true,revision:2,followups:restoredPayload.followups})};}});
    run(claimHost,fn('rememberUnsyncedQueue')+'\n'+fn('persistFollowupPut')+'\n'+fn('drainNextFollowup'));
    const claiming=run(claimHost,'drainNextFollowup()');
    await tick(); claimHost.currentSessionId='202'; claimHost.followupQueue=Q.createQueueState();
    resolveTake({ok:true,json:async()=>({success:true,revision:1,remaining:[],followups:[{id:'claimed',content:'A queued'}]})});
    await claiming;
    assert.equal(restoredPayload.followups[0].id,'claimed');assert.equal(claimHost.followupQueue.items.length,0);
    assert.equal(claimPrefs.get('101')?.followupClaim || null,null);assert(!claimedState.dirty);

    // Empty identical snapshots still wake a saved lost-response claim after reload.
    let drains=0;
    const emptyHost=context({followupQueue:Q.createQueueState(),currentSessionId:'101',editingFollowupId:null,
        CuttleFollowupQueue:Q,CuttleChatActivity:A,getSessionPrefs:()=>({followupClaim:'lost-response'}),
        patchLiveSessionFollowupQueue(){},renderFollowupQueue(){},isSessionGenerating:()=>false,
        scheduleFollowupDrain:()=>drains++,scheduleChatActivityBroadcast(){}});
    run(emptyHost,fn('applyServerFollowups'));run(emptyHost,'applyServerFollowups([],0)');
    assert.equal(drains,1);

    // Prompts queued before the first server id belong to that turn, not the next draft.
    const draftQueue = Q.createQueueState(); draftQueue.items=[{id:'early',content:'queued before id'}];
    let persistedDraft;
    const adoptHost = context({followupQueue:draftQueue,followupQueues:Q.createRegistry(()=>null),
        editingFollowupId:null,canonicalizeChatSessionId:A.canonicalizeChatSessionId,
        CuttleFollowupQueue:Q,getSessionPrefs:()=>null,isAuthMode:()=>true,toAuthDbSessionId:String,
        persistFollowupPut:(sid,state)=>{persistedDraft={sid,state};}});
    run(adoptHost,fn('adoptFollowupQueue'));run(adoptHost,'adoptFollowupQueue("101",{transfer:true})');
    assert.equal(persistedDraft.sid,'101');assert.equal(persistedDraft.state.items[0].id,'early');
    run(adoptHost,'adoptFollowupQueue(null)');assert.equal(adoptHost.followupQueue.items.length,0);

    // Passive clean snapshots cause zero migration writes, even on a large cold list.
    let migrationWrites=0;
    const cold=Attention.create({prefs:{get:()=>null,update:()=>migrationWrites++},id:()=>'',changed(){}});
    for(let i=0;i<1000;i++)cold.accept(String(i),{reply_id:0,read_id:0,hasUnread:false,revision:0});
    assert.equal(migrationWrites,0);

    // Two views retain independent failed queue edits for the same server session.
    const multiPrefs=P.create(storage()), leftState=Q.createQueueState(), rightState=Q.createQueueState();
    leftState.items=[{id:'left',content:'left edit'}];leftState.dirty=true;
    rightState.items=[{id:'right',content:'right edit'}];rightState.dirty=true;
    const leftHost=context({followupWriteField:'followupWrite:left',state:leftState,
        updateSessionPrefs:(sid,p)=>multiPrefs.update(sid,p)});
    const rightHost=context({followupWriteField:'followupWrite:right',state:rightState,
        updateSessionPrefs:(sid,p)=>multiPrefs.update(sid,p)});
    run(leftHost,fn('rememberUnsyncedQueue'));run(rightHost,fn('rememberUnsyncedQueue'));
    run(leftHost,'rememberUnsyncedQueue("101",state)');run(rightHost,'rememberUnsyncedQueue("101",state)');
    leftState.dirty=false;run(leftHost,'rememberUnsyncedQueue("101",state)');
    assert.equal(multiPrefs.get('101')['followupWrite:right'].items[0].id,'right');

    // Concurrent consumers share one snapshot request; only one publish occurs.
    let resolveLoad, loads=0, published=0;
    const load=new Promise(r=>resolveLoad=r);
    const broker=Broker.create({now:()=>0,setTimeout,clearTimeout,
        load:()=>{loads++;return load;},publish:()=>published++});
    const r1=broker.sessions(), r2=broker.sessions();
    assert.equal(loads,1); resolveLoad([{id:101}]);
    await Promise.all([r1,r2]); assert.equal(published,1); broker.dispose();
    // An account change fences an in-flight snapshot and invalidates cached sessions.
    let account='first', finishOld, scopeLoads=0; const delivered=[];
    const oldAccountRead=new Promise(r=>finishOld=r);
    const scoped=Broker.create({scope:()=>account,now:()=>0,setTimeout,clearTimeout,
        load:()=>{scopeLoads++;return scopeLoads===1?oldAccountRead:Promise.resolve([{id:'new-account-chat'}]);},
        publish:rows=>delivered.push(rows)});
    const oldRead=scoped.sessions();const rejected=assert.rejects(oldRead,/previous account/);
    account='second';const newRead=scoped.sessions();finishOld([{id:'old-account-chat'}]);
    await rejected;assert.equal((await newRead)[0].id,'new-account-chat');
    assert.equal(delivered.length,1);assert.equal(delivered[0][0].id,'new-account-chat');scoped.dispose();
    // Progress wakeups stay bounded; storage failure retains the live optimistic intent.
    let timerDelay, timerFn, pulseLoads=0;
    const pulses=Broker.create({now:()=>0,setTimeout:(fn,ms)=>{timerDelay=ms;timerFn=fn;return 1;},clearTimeout(){},
        load:async()=>{pulseLoads++;return [{id:101}];},publish(){}});
    await pulses.sessions();for(let i=0;i<100;i++)pulses.refresh();
    assert.equal(pulseLoads,1);assert.equal(timerDelay,2000);pulses.dispose();
    const blockedStorage=storage();blockedStorage.setItem=()=>{throw Error('quota');};
    const volatile=P.create(blockedStorage);volatile.update('101',{attentionWrite:{through_id:7}});
    assert.equal(volatile.get('101').attentionWrite.through_id,7);
    console.log('ui-state-sync: 17 regression scenarios passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
