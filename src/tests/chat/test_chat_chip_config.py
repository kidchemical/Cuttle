"""Chip config module pure helpers (chat_chip_config.js) under node.

The modal DOM/transport is a controller (composed by the page with a `host`);
these tests pin only the pure decision helpers that feed its dropdowns.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_chip_config.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};

// agentSettingOptions: strips (current) + the 'Effort ' prefix.
const spec = {
  models: { build: () => [
    { modelId: 'opus', label: 'Opus (current)' },
    { modelId: '', label: 'Loading…', disabled: true },
  ] },
  efforts: { build: () => [
    { modelId: 'high', label: 'Effort high (current)' },
    { modelId: 'low', label: 'Effort low' },
  ] },
};
out.modelOpts = A.agentSettingOptions(spec, 'models');
out.effortOpts = A.agentSettingOptions(spec, 'efforts');
out.missing = A.agentSettingOptions(spec, 'nope');
out.badBuild = A.agentSettingOptions({ models: { build: () => { throw new Error('x'); } } }, 'models');

// stickyAgentEntries: sticky rows only, de-duped by agent id, classifier-injected.
const commands = [
  { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor', stickySession: true },
  { prefix: '/codex ', label: 'Codex', category: 'codex', stickySession: true },
  { prefix: '/restart ', label: 'Restart', category: 'command', controlCommand: true },
];
const classify = (c) => (c.category === 'cursor' ? 'cursor'
  : c.category === 'codex' ? 'codex' : '');
out.entries = A.stickyAgentEntries(commands, classify);

// create() is a controller factory (DOM required); it must expose the API shape.
out.hasCreate = typeof A.create === 'function';
const ctrl = A.create({});
out.api = Object.keys(ctrl).sort();
out.branchEmpty = A.branchFor('no-such-path');

// repoChoice: Repository selector only for 2+ repos; dirty repos sort first;
// remembered pick wins while still detected, else the first work tree.
const two = [
  { repo_root: '/p/A', label: 'A', branch: 'master', clean: true, pending_file_count: 0 },
  { repo_root: '/p/B', label: 'B', branch: 'dev', clean: true, pending_file_count: 0 },
];
const twoOneDirty = [
  { repo_root: '/p/A', label: 'A', branch: 'main', clean: true, pending_file_count: 0 },
  { repo_root: '/p/B', label: 'B', branch: 'master', clean: false, pending_file_count: 4 },
];
const twoBothDirty = [
  { repo_root: '/p/A', label: 'A', branch: 'main', clean: false, pending_file_count: 1 },
  { repo_root: '/p/B', label: 'B', branch: 'master', clean: false, pending_file_count: 4 },
];
const twoSameBranch = [
  { repo_root: '/p/A', label: 'A', branch: 'main', clean: true },
  { repo_root: '/p/B', label: 'B', branch: 'main', clean: true },
];
out.repoOne = A.repoChoice([two[0]], '');
out.repoTwo = A.repoChoice(two, '/p/B');
out.repoStale = A.repoChoice(two, '/p/gone');
out.repoNone = A.repoChoice([], '');
out.repoDirtyFirst = A.repoChoice(twoOneDirty, '');
// Chip label: pinned repo wins, else sole dirty repo, else shared branch or "N repos".
out.labelPicked = A.activeRepoBranchLabel(two, '/p/B');
out.labelPickedOverridesDirty = A.activeRepoBranchLabel(twoOneDirty, '/p/A');
out.labelMerged = A.activeRepoBranchLabel(two, '');
out.labelSoleDirty = A.activeRepoBranchLabel(twoOneDirty, '');
out.labelBothDirty = A.activeRepoBranchLabel(twoBothDirty, '');
out.labelSameBranch = A.activeRepoBranchLabel(twoSameBranch, '');
out.labelSingle = A.activeRepoBranchLabel([two[1]], '/p/A');
let syncChanges = 0;
A.syncRepoStates('/p', twoOneDirty, () => { syncChanges += 1; });
out.syncBranch = A.branchFor('/p');
out.syncChanges = syncChanges;
out.clearChoice = A.withClearChoice([{ value: '7', label: 'Demo' }]);
// projectNameFromPath: Add project's default name = the folder's last segment.
out.names = ['/home/u/Dev/Cuttle/', 'C:\\\\Dev\\\\Game', 'Proj', '/', 'D:\\\\', '~/', ''].map(A.projectNameFromPath);

process.stdout.write(JSON.stringify(out));
"""


def _run():
    return subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={"MOD_JS": str(MOD_JS), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


@node_only
def test_helper_outputs():
    proc = _run()
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hasCreate"] is True
    assert out["api"] == [
        "bindRow", "close", "openAddProject", "openAgent", "openCustom", "openProject",
    ]
    assert out["names"] == ["Cuttle", "Game", "Proj", "", "", "", ""]
    assert out["branchEmpty"] == ""
    assert out["modelOpts"] == [
        {"value": "opus", "label": "Opus", "disabled": False},
    ]
    assert out["effortOpts"] == [
        {"value": "high", "label": "high", "disabled": False},
        {"value": "low", "label": "low", "disabled": False},
    ]
    assert out["missing"] == []
    assert out["badBuild"] == []
    assert [e["id"] for e in out["entries"]] == ["cursor", "codex"]
    assert out["entries"][0]["prefix"] == "/cursor "
    assert out["repoOne"]["show"] is False
    assert out["repoNone"] == {"show": False, "options": [], "selected": "", "count": 0}
    assert out["repoTwo"]["show"] is True
    assert out["repoTwo"]["count"] == 2
    assert out["repoTwo"]["selected"] == "/p/B"
    assert [o["value"] for o in out["repoTwo"]["options"]] == ["/p/A", "/p/B"]
    assert out["repoTwo"]["options"][1]["label"] == "B  (dev)"
    assert out["repoStale"]["selected"] == "/p/A"
    assert [o["value"] for o in out["repoDirtyFirst"]["options"]] == ["/p/B", "/p/A"]
    assert out["repoDirtyFirst"]["selected"] == "/p/B"
    assert out["repoDirtyFirst"]["options"][0]["label"] == "B  (master · 4 changes)"
    assert out["labelPicked"] == "dev"
    assert out["labelPickedOverridesDirty"] == "main"
    assert out["labelMerged"] == "2 repos"
    assert out["labelSoleDirty"] == "master"
    assert out["labelBothDirty"] == "2 repos"
    assert out["labelSameBranch"] == "main"
    assert out["labelSingle"] == "dev"
    assert out["syncBranch"] == "master"
    assert out["syncChanges"] == 1
    assert out["clearChoice"][0] == {"value": "", "label": "None — use starred default"}
    assert out["clearChoice"][1]["value"] == "7"


@node_only
def test_module_loads_without_dom():
    """Requiring the module in node (no document) must not throw."""
    proc = subprocess.run(
        ["node", "-e", "require(process.env.MOD_JS); process.stdout.write('ok')"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={"MOD_JS": str(MOD_JS), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ok"
