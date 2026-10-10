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

console.log(pass + ' worktree checkout scenarios passed');
