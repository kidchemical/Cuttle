"""New viewport splits keep the Agent Feed rail icon.

The shell snapshots column-0 chrome into a template at boot, before the async
experimental-flags fetch clears the `display:none` baked into
`nav-agent-feed` in app_shell.html. Every split path builds the new pane from
that template, so without a re-apply the fresh pane's feed icon stays hidden
even while column 0 shows it. `mountNewLeafFrame` (the choke point for all
split mounts) must re-apply the live flag state.
"""
import shutil
from pathlib import Path

import pytest

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

REPO = Path(__file__).resolve().parents[3]
SHELL_SRC = REPO / "src/web/js/shell/app_shell.js"


def _extract_mount():
    source = SHELL_SRC.read_text(encoding="utf-8")
    start = source.index("function mountNewLeafFrame(newIdx")
    end = source.index("function mountSnapshotAsNestedGroup", start)
    return source[start:end]


def _extract_availability():
    source = SHELL_SRC.read_text(encoding="utf-8")
    start = source.index("function applyAgentFeedAvailability()")
    end = source.index("async function refreshAgentFeedAvailability", start)
    return source[start:end]


MOUNT_STUB = """
let availabilityCalls = 0;
function applyAgentFeedAvailability() { availabilityCalls++; }
function canonicalizeShellPage(p) { return p; }
function setState() {}
function updateColumnUI() {}
function armFrameReveal() {}
function stampComposerDraftScope() {}
function withCacheBust(p) { return p; }
function attachFrameLoadListener() {}
const frame = { classList: { remove() {} }, src: '' };
const mainEl = {};
"""


@node_only
def test_new_pane_mount_reapplies_agent_feed_visibility():
    import subprocess

    script = "const assert = require('node:assert/strict');\n"
    script += MOUNT_STUB + "\n" + _extract_mount() + "\n"
    script += """
mountNewLeafFrame(3, frame, mainEl, '/chat_page.html');
assert.equal(availabilityCalls, 1, 'mount applies live agent-feed visibility to the new pane');
"""
    subprocess.run(["node", "-e", script], check=True)


@node_only
def test_agent_feed_availability_clears_baked_in_hide():
    import subprocess

    script = "const assert = require('node:assert/strict');\n"
    script += "let agentFeedEnabled = true;\n"
    script += """
function makeFeedBtn() {
    const calls = [];
    return { calls, style: { setProperty(p, v, pr) { calls.push([p, v, pr]); } } };
}
const buttons = [makeFeedBtn(), makeFeedBtn()];
const document = { querySelectorAll: (sel) => (
    sel === '[data-id="nav-agent-feed"]' ? buttons : []) };
"""
    script += _extract_availability() + "\n"
    script += """
applyAgentFeedAvailability();
for (const btn of buttons) {
    assert.deepEqual(btn.calls, [['display', '', 'important']],
        'enabled flag clears the baked-in display:none on the cloned icon');
}
agentFeedEnabled = false;
for (const btn of buttons) btn.calls.length = 0;
applyAgentFeedAvailability();
for (const btn of buttons) {
    assert.deepEqual(btn.calls, [['display', 'none', 'important']],
        'disabled flag keeps the icon hidden');
}
"""
    subprocess.run(["node", "-e", script], check=True)


def test_mount_source_references_availability():
    # Static pin: the wiring test above runs only when node exists; this
    # fails plainly anywhere if the call is ever dropped from the mount path.
    assert "applyAgentFeedAvailability();" in _extract_mount()
