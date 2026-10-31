'use strict';
// Node unit checks for the worktree checkout client model.
const path = require('path');
const project = require(path.join(__dirname, '..', '..', 'web', 'js', 'chat', 'chat_project.js'));
const worktrees = require(path.join(__dirname, '..', '..', 'web', 'js', 'chat', 'chat_worktrees.js'));

let pass = 0;
function eq(actual, expected, label) {
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        console.error('FAIL ' + label + ': ' + JSON.stringify(actual) + ' != ' + JSON.stringify(expected));
        process.exit(1);
    }
    pass += 1;
}

// --- project_fields: checkout_uid read from server rows ---
eq(project.projectFieldsFromServerSession({ project_id: 7, project_path: '/p', checkout_uid: 'abc' }).checkoutUid, 'abc', 'snake checkout_uid');
eq(project.projectFieldsFromServerSession({ projectId: 7, checkoutUid: 'xyz' }).checkoutUid, 'xyz', 'camel checkoutUid');
eq(project.projectFieldsFromServerSession({ checkout_uid: 'only' }).checkoutUid, 'only', 'checkout-only row is not null');
eq(project.projectFieldsFromServerSession({}), null, 'empty row is null');

// --- outbound request stamping ---
const body = {};
project.applyOutboundProjectToRequest(body, { id: 7, name: 'Demo', path: '/p' }, 'abc');
eq(body, { project_id: 7, project_name: 'Demo', project_path: '/p', checkout_uid: 'abc' }, 'outbound stamps checkout_uid');

const noCheckout = {};
project.applyOutboundProjectToRequest(noCheckout, { id: 7, name: 'Demo', path: '/p' }, '');
eq('checkout_uid' in noCheckout, false, 'no checkout_uid key when local');

const fromProj = {};
project.applyOutboundProjectToRequest(fromProj, { id: 7, checkoutUid: 'fromproj' }, '');
eq(fromProj.checkout_uid, 'fromproj', 'falls back to proj.checkoutUid');

// --- pure helpers ---
eq(worktrees.slug('Hello World!'), 'hello-world', 'slug');
eq(worktrees.defaultBranch({ name: 'My Game' }, 'cuttle'), 'cuttle/my-game', 'default branch');
eq(worktrees.defaultBranch({ name: 'Game' }), 'cuttle/game', 'default prefix');

// Checkout paths cannot be borrowed from a different browser-stored ID.
const stale = { checkoutUid: 'old', checkoutPath: '/old' };
eq(project.resolveCheckoutBinding({ serverSession: { checkout_uid: 'new' }, prefs: stale }),
    { uid: 'new', path: '' }, 'different stale path rejected');
eq(project.resolveCheckoutBinding({ serverSession: { checkout_uid: null }, prefs: stale }),
    { uid: '', path: '' }, 'server local checkout beats stale device');
eq(project.resolveCheckoutBinding({ serverSession: {}, prefs: stale }),
    { uid: 'old', path: '/old' }, 'legacy row falls back to paired local identity');
eq(project.resolveCheckoutBinding({ prefs: { checkoutUid: '' }, stored: stale }),
    { uid: '', path: '' }, 'local preference clear beats older storage');
eq(project.resolveCheckoutBinding({ serverSession: { checkout_uid: 'old' }, prefs: stale }),
    { uid: 'old', path: '/old' }, 'matching offline path retained');

// --- a checkout lives only as long as the chat stays on its project ---
const demo = { id: 7, name: 'Demo', path: 'E:\\Demo' };
const cuttleProj = { id: 4, name: 'Cuttle', path: '/c' };
const wt = { uid: 'wt71', path: '/wt/71' };
const reg = (uid) => (uid === 'wt71' ? { checkout_uid: 'wt71', project_id: 7, path: '/wt/71' } : null);
eq(project.checkoutAfterProjectChange(demo, { id: '7', path: 'E:/Demo/' }, wt, reg), wt, 'same project (id) keeps checkout');
eq(project.checkoutAfterProjectChange(demo, cuttleProj, wt, reg), { uid: '', path: '' }, 'project change drops checkout');
eq(project.checkoutAfterProjectChange(demo, null, wt, reg), { uid: '', path: '' }, 'clearing the project drops checkout');
eq(project.checkoutAfterProjectChange(null, demo, wt, reg), { uid: '', path: '' }, 'no prior project: nothing to inherit');
eq(project.checkoutAfterProjectChange({ path: 'E:\\Demo' }, { path: 'e:/demo/' }, wt, null), wt, 'path identity when ids are absent');
eq(project.checkoutAfterProjectChange({ id: 4, path: '/c' }, { id: 4, path: '/c' }, wt, reg), { uid: '', path: '' },
    'registered checkout of another project never kept');
eq(project.resolveCheckoutBinding({ prefs: { checkoutUid: 'wt71', checkoutPath: '/wt/71' }, project: cuttleProj, checkoutForUid: reg }),
    { uid: '', path: '' }, 'restore drops a foreign checkout');
eq(project.resolveCheckoutBinding({ prefs: { checkoutUid: 'wt71' }, project: demo, checkoutForUid: reg }),
    { uid: 'wt71', path: '/wt/71' }, 'restore keeps its own project checkout');

// --- server-side binding changes (agent `python -m api.worktrees bind`) ---
eq(worktrees.observeSessionCheckout('9', { checkout_uid: null }), false, 'first payload is the baseline');
eq(worktrees.observeSessionCheckout('9', { checkout_uid: null }), false, 'unchanged server binding');
eq(worktrees.observeSessionCheckout('9', {
    checkout_uid: 'agent', checkout: { checkout_uid: 'agent', path: '/wt/agent', branch: 'dev/agent' },
}), true, 'server rebind is a change');
eq(worktrees.checkoutForUid('agent').path, '/wt/agent', 'payload checkout remembered without a probe');
eq(worktrees.observeSessionCheckout('9', {}), false, 'payload without checkout_uid ignored');
eq(project.hydratedSessionSnapshot('9', { project_id: 1, checkout_uid: 'agent' }),
    { id: '9', project_id: 1, project_name: undefined, project_path: undefined, checkout_uid: 'agent' },
    'snapshot carries checkout_uid');
eq(project.hydratedSessionSnapshot('9', { checkout_uid: 'x' }), null, 'no project → no snapshot');

(async () => {
    global.fetch = async () => ({ status: 200, json: async () => ({
        worktrees: [{ checkout_uid: 'new', path: '/new', branch: 'refactor/work' }],
    }) });
    await worktrees.probe();
    eq(project.resolveCheckoutBinding({ serverSession: { checkout_uid: 'new' }, prefs: stale,
        checkoutForUid: worktrees.checkoutForUid }),
        { uid: 'new', path: '/new' }, 'cold load registry resolves server checkout');
    eq(worktrees.branchForUid('new'), 'refactor/work', 'branch belongs to resolved checkout');
    console.log(pass + ' worktree checkout scenarios passed');
})().catch((err) => { console.error(err); process.exit(1); });
