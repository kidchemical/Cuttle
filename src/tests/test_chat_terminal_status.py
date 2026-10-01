"""Execute the page's remote-wait adapter with real activity decisions.

The shell hub passes an empty message list after a local reply clears its
in-flight prompt. Replaying the old status must not recreate a second bubble.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web" / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_hub_status_after_reply_does_not_resurrect_remote_bubble():
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const activity = require(process.argv[2]);
function extract(name) {
  const start = source.indexOf('    function ' + name + '(');
  assert(start >= 0, name);
  const end = source.indexOf('\n    }', start) + 6;
  return source.slice(start, end);
}
let remote = false, running = false;
let last = {classList: {contains: role => role === 'assistant'},
  dataset: {queryId: 'completed'},
  querySelector: () => ({dataset: {ts: '200000'}})};
const ctx = {
  CuttleChatActivity: activity,
  generation: {loading: false}, currentSessionId: '842',
  stopState: {}, runningSessionIds: new Set(),
  document: {getElementById: id => id === 'typing-indicator-remote' && remote ? {} : null},
  sessionIdsEqual: (a, b) => a === b,
  lastVisibleChatMessageEl: () => last,
  noteWidgetsRevision: () => {}, updateSupervisedTaskIndicator: () => {},
  transcriptEndsWithGenerationStop: () => false,
  thisTurnHasAssistantReply: messages => messages.some(m => m.role === 'assistant'),
  turnAlreadyShowsAssistantReply: () => false, // inFlightUserMessage cleared
  liveStatusLooksActive: live => activity.liveStatusLooksActive(live, 201000),
  setHistorySessionRunning: (sid, value) => {running = value},
  addRemoteWaitingIndicator: () => {remote = true},
  removeRemoteWaitingIndicator: () => {remote = false},
  updateRemoteWaitingStatus: () => {}, syncSubagentOrbs: () => {},
  scheduleFollowupDrain: () => {},
};
vm.createContext(ctx);
// On the pre-fix page this helper is absent; exercise the original adapter.
if (source.includes('function remoteLiveStatusLooksActive(')) {
  vm.runInContext(extract('remoteLiveStatusLooksActive'), ctx);
}
vm.runInContext(extract('updateRemoteWaitingFromMessages'), ctx);
for (const status of ['Starting Cursor Agent…', 'thinking: old thought', 'tool 1: grep']) {
  ctx.updateRemoteWaitingFromMessages([], {
    active: true, generating: true, status, updated_at: 199, query_id: 'completed',
  });
  assert.strictEqual(remote, false, 'old hub status recreated a bubble: ' + status);
  assert.strictEqual(running, false);
}
// A genuinely newer remote turn remains visible even before its user row syncs.
ctx.updateRemoteWaitingFromMessages([], {
  active: true, generating: true, status: 'new work', updated_at: 201, query_id: 'new',
});
assert.strictEqual(remote, true);
// Query identity wins over clock granularity/skew for a newer turn.
ctx.updateRemoteWaitingFromMessages([], {
  active: true, generating: true, updated_at: 199, query_id: 'new',
});
assert.strictEqual(remote, true);
// Meta/error replies can lack a query id; use the timestamp in that case.
ctx.updateRemoteWaitingFromMessages([], {
  active: true, generating: true, updated_at: 199,
});
assert.strictEqual(remote, false);
// Once its user row paints, old assistant completion must not suppress it.
last = {classList: {contains: role => role === 'user'}};
ctx.updateRemoteWaitingFromMessages([], {
  active: true, generating: true, updated_at: 199, query_id: 'completed',
});
assert.strictEqual(remote, true);
"""
    result = subprocess.run(
        ["node", "-e", script, str(WEB / "chat_page.js"),
         str(WEB / "chat_activity.js")], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
