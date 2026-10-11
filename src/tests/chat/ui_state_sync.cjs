// Production owners/page adapters with isolated hosts; no real API or storage.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert/strict');
const root = path.resolve(__dirname, '../../..');
const asset = name => require(path.join(root, 'src/web/js', name));
const P = asset('shared/session_prefs.js'), T = asset('chat/chat_mutations.js');
const A = asset('chat/chat_activity.js'), Q = asset('chat/chat_followup_queue.js');
const Attention = asset('chat/chat_attention.js'), Broker = asset('shared/activity_broker.js');
const CuttleChatSession = asset('chat/chat_session.js');
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
function context(host) {
    // Session identity is owned by chat_session.js in production; the sliced
    // page functions read session.current(). Bridge the host's plain
    // `currentSessionId` variable onto the owner so host assignments keep
    // working (and are seen by the page's accessor reads).
    const session = CuttleChatSession.create();
    if (host.currentSessionId != null) session.adopt(host.currentSessionId);
    const sandbox = {followupWriteField:"followupWrite",followupClaimField:"followupClaim",...host, console:{warn(){}}};
    Object.defineProperty(sandbox, 'currentSessionId', {
        enumerable: true, configurable: true,
        get() { return session.current(); },
        set(v) { session.adopt(v); },
    });
    sandbox.session = session;
    return vm.createContext(sandbox);
}
function run(host, code) { return vm.runInContext(code, host); }
const tick = () => new Promise(resolve => setImmediate(resolve));

// The follow-up queue lifecycle is owned by CuttleFollowupQueue.createController;
// the page supplies every effect as a host capability. Build one for a scenario.
function buildQueueHost(o) {
    o = o || {};
    const prefs = o.prefs || {};
    const mut = o.mut || T.create();
    return {
        activity: A,
        clock: { now: () => Date.now(), rand: () => Math.random() },
        sessionIdsEqual: A.sessionIdsEqual,
        canonicalizeSessionId: x => A.canonicalizeChatSessionId(x),
        toAuthDbSessionId: x => String(x),
        currentSession: o.currentSession || (() => o.session),
        isAuthMode: () => o.auth !== false,
        isSessionGenerating: () => !!o.generating,
        healStaleGenerating() {},
        followupAuthSid: () => ('authSid' in o ? o.authSid : o.session),
        getPrefs: sid => prefs[sid] || null,
        setPrefs: (sid, p) => { prefs[sid] = { ...prefs[sid], ...p }; },
        writeField: () => o.writeField || 'followupWrite',
        claimField: () => o.claimField || 'followupClaim',
        restoreQueue: () => null,
        serializeMutation: (k, f) => mut.enqueue(k, f),
        putFollowups: o.putFollowups || (async (sid, body) => (
            { ok: true, status: 200, data: { success: true, revision: 1, followups: body.followups } })),
        takeFollowups: o.takeFollowups || (async () => (
            { ok: true, status: 200, data: { success: true, revision: 1, followups: [], remaining: [] } })),
        steer: o.steer || (async () => null),
        render() {}, patchLiveSession() {}, broadcastActivity() {},
        showChatArea() {}, addUserMessage() {}, autoScroll() {}, saveUserMessage() {},
        recordPromptHistory() {}, processMessage: async () => {}, toast() {}, steerLabel() {},
        setTimeout: o.setTimeout || setTimeout, clearTimeout: o.clearTimeout || clearTimeout,
        formatWithAttachments: t => t, stickyPrefixFor: () => '',
    };
}

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
        CuttleChatAgentControls: asset('chat/chat_agent_controls.js'),
        slashPaletteSupplement:supplement, sessionMutationFetch:()=>deferred,
        document:{getElementById:()=>null}, window:{}});
    run(model, "var agentControls = CuttleChatAgentControls.create({state:slashPaletteSupplement,"
        + "getSessionId:()=>currentSessionId,beginFetch:()=>0,isCurrentFetch:()=>true,"
        + "captureMutation:(sid,label)=>sessionMutations.capture(sid,label,_loadSessionSeq),"
        + "isMutationCurrent:(t)=>sessionMutations.current(t,currentSessionId,_loadSessionSeq),"
        + "sessionMutationFetch:(u,o)=>sessionMutationFetch(u,o),"
        + "fetch:()=>{throw new Error('unexpected fetch')}});");
    run(model, "agentControls.persistMuseModelSelection('model-A')");
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
    let resolveQueue, qcur='101';
    const queueResponse = new Promise(r=>resolveQueue=r);
    const queueHost = buildQueueHost({prefs:{}, currentSession:()=>qcur,
        putFollowups:async()=>{const r=await queueResponse;const data=await r.json();
            return {ok:r.ok,status:200,data};}});
    const queueCtl = Q.createController(queueHost);
    queueCtl.adopt('101');
    const aState = queueCtl.getState();
    aState.items=[{id:'a',content:'A'}]; aState.base=[{id:'a',content:'A'}];
    const write = queueCtl.persist('101');
    await tick(); qcur='202'; queueCtl.adopt('202');
    resolveQueue({ok:true,json:async()=>({success:true,revision:1,followups:[{id:'a',content:'A'}]})});
    await write;
    assert.equal(queueCtl.getState().items.length,0); assert(!aState.dirty);
    qcur='101'; queueCtl.adopt('101');
    queueHost.putFollowups = async()=>{throw Error('offline');};
    assert.equal(await queueCtl.persist('101'),false);
    assert(aState.dirty);

    // A late catalog read cannot undo a newer successful pin save.
    let resolveCatalog;
    const catalog = new Promise(r => resolveCatalog = r);
    const pins = {museModels:[]};
    const pinHost = context({currentSessionId:'101', _loadSessionSeq:1, sessionMutations:T.create(),
        slashPaletteSupplement:pins, URLSearchParams, document:{getElementById:()=>null}, window:{},
        CuttleChatAgentControls: asset('chat/chat_agent_controls.js'),
        hasActiveMuseAgentChip:()=>true, fetch:()=>catalog,
        sessionMutationFetch:async()=>({json:async()=>({success:true,preferredModel:'new-pin'})}),
        renderSlashChips(){},repaintMuseUserBadges(){},persistStickySlashForCurrentSession(){},refreshHistoryAgentChips(){}});
    // Slice 2A: the real Muse loader now lives on the CuttleChatAgentControls
    // owner; wire a controller so the page delegate drives the same logic.
    run(pinHost, fn('beginSupplementFetch')+'\n'+fn('isCurrentSupplementFetch')+'\n'+fn('loadMuseModelsForPalette')+'\n'+fn('persistMuseModelSelection')
        +'\nvar agentControls = CuttleChatAgentControls.create({state:slashPaletteSupplement,'
        +'getSessionId:()=>currentSessionId,getProjectPath:()=>\'\','
        +'hasActiveMuse:()=>true,beginFetch:(f)=>beginSupplementFetch(f),'
        +'isCurrentFetch:(f,g)=>isCurrentSupplementFetch(f,g),'
        +'captureMutation:(sid,label)=>sessionMutations.capture(sid,label,_loadSessionSeq),'
        +'isMutationCurrent:(t)=>sessionMutations.current(t,currentSessionId,_loadSessionSeq),'
        +'sessionMutationFetch:(u,o)=>sessionMutationFetch(u,o),'
        +'renderSlashChips:()=>{},syncActiveSlashMenu:()=>{},repaintUserBadges:()=>{},'
        +'persistStickySlashForCurrentSession:()=>{},refreshHistoryAgentChips:()=>{},'
        +'fetch:(u,o)=>fetch(u,o),toast:()=>{}});');
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
    let resolveTake, restoredPayload, ccur='101';
    const taken = new Promise(r => resolveTake = r), claimedState = Q.createQueueState();
    claimedState.items=[{id:'claimed',content:'A queued'}]; claimedState.base=claimedState.items.slice();
    const claimPrefs = {};
    const claimHost = buildQueueHost({prefs:claimPrefs, currentSession:()=>ccur, session:'101',
        putFollowups:async(_sid,body)=>{restoredPayload=body;
            return {ok:true,status:200,data:{success:true,revision:2,followups:body.followups}};},
        takeFollowups:async()=>{const r=await taken;const data=await r.json();
            return {ok:r.ok,status:200,data};}});
    const claimCtl = Q.createController(claimHost);
    claimCtl.getState().items = claimedState.items; claimCtl.getState().base = claimedState.base;
    const claiming = claimCtl.drain();
    await tick(); ccur='202'; claimCtl.adopt('202');
    resolveTake({ok:true,json:async()=>({success:true,revision:1,remaining:[],followups:[{id:'claimed',content:'A queued'}]})});
    await claiming;
    assert.equal(restoredPayload.followups[0].id,'claimed');assert.equal(claimCtl.getState().items.length,0);
    assert.equal(claimPrefs['101']?.followupClaim || null,null);assert(!claimedState.dirty);

    // Empty identical snapshots still wake a saved lost-response claim after reload.
    let drains=0;
    const emptyHost=buildQueueHost({prefs:{'101':{followupClaim:'lost-response'}}, session:'101',
        setTimeout:()=>{drains++;return 0;}, clearTimeout:()=>{}});
    const emptyCtl=Q.createController(emptyHost);
    emptyCtl.applyServerList([],0);
    assert.equal(drains,1);

    // Prompts queued before the first server id belong to that turn, not the next draft.
    const draftQueue = Q.createQueueState(); draftQueue.items=[{id:'early',content:'queued before id'}];
    let persistedDraft;
    const adoptHost = buildQueueHost({session:'101',
        putFollowups:async(sid,body)=>{persistedDraft={sid,body};
            return {ok:true,status:200,data:{success:true,revision:1,followups:body.followups}};}});
    const adoptCtl=Q.createController(adoptHost);
    adoptCtl.getState().items=draftQueue.items;
    adoptCtl.adopt('101',{transfer:true});
    await tick();
    assert.equal(persistedDraft.sid,'101');assert.equal(persistedDraft.body.followups[0].id,'early');
    adoptCtl.adopt(null);assert.equal(adoptCtl.getState().items.length,0);

    // Passive clean snapshots cause zero migration writes, even on a large cold list.
    let migrationWrites=0;
    const cold=Attention.create({prefs:{get:()=>null,update:()=>migrationWrites++},id:()=>'',changed(){}});
    for(let i=0;i<1000;i++)cold.accept(String(i),{reply_id:0,read_id:0,hasUnread:false,revision:0});
    assert.equal(migrationWrites,0);

    // Two views retain independent failed queue edits for the same server session.
    const multiPrefs={}, leftState=Q.createQueueState(), rightState=Q.createQueueState();
    leftState.items=[{id:'left',content:'left edit'}];leftState.dirty=true;
    rightState.items=[{id:'right',content:'right edit'}];rightState.dirty=true;
    const leftHost=buildQueueHost({session:'101',writeField:'followupWrite:left',prefs:multiPrefs,
        putFollowups:async()=>{throw Error('offline');}});
    const rightHost=buildQueueHost({session:'101',writeField:'followupWrite:right',prefs:multiPrefs,
        putFollowups:async()=>{throw Error('offline');}});
    const leftCtl=Q.createController(leftHost), rightCtl=Q.createController(rightHost);
    leftCtl.getState().items=leftState.items; leftCtl.getState().dirty=true;
    rightCtl.getState().items=rightState.items; rightCtl.getState().dirty=true;
    await leftCtl.persist('101'); await rightCtl.persist('101');
    assert.equal(multiPrefs['101']['followupWrite:left'].items[0].id,'left');
    assert.equal(multiPrefs['101']['followupWrite:right'].items[0].id,'right');
    // A successful left write clears only the left field.
    leftHost.putFollowups=async()=>({ok:true,status:200,data:{success:true,revision:1,followups:leftCtl.getState().items}});
    await leftCtl.persist('101');
    assert.equal(multiPrefs['101']['followupWrite:left'] || null,null);
    assert.equal(multiPrefs['101']['followupWrite:right'].items[0].id,'right');

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
